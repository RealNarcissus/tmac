#!/usr/bin/env python3
"""Unit tests for host/qmp_broker.py.  No QEMU required: a fake QMP server."""
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))
import qmp_broker  # noqa: E402

GREETING = {
    "QMP": {
        "version": {"qemu": {"major": 11, "minor": 1, "micro": 2}, "package": ""},
        "capabilities": ["oob"],
    }
}


class FakeQMP:
    """A tiny single-connection-at-a-time QMP server with test commands."""

    def __init__(self, path: str):
        self.path = path
        self.server: socket.socket | None = None
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.connections = 0
        self.capabilities = 0
        self.requests: list[dict] = []
        self.conns: set[socket.socket] = set()

    def start(self) -> "FakeQMP":
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.path)
        self.server.listen(8)
        self.server.settimeout(0.2)
        self.thread = threading.Thread(target=self._accept, name="fake-qmp-accept",
                                       daemon=True)
        self.thread.start()
        return self

    def _accept(self) -> None:
        assert self.server is not None
        while not self.stop_event.is_set():
            try:
                conn, _ = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self.lock:
                self.connections += 1
                self.conns.add(conn)
            threading.Thread(target=self._handle, args=(conn,),
                             name="fake-qmp-client", daemon=True).start()

    def _reply(self, f, obj: dict, rid) -> None:
        if rid is not None:
            obj = dict(obj)
            obj["id"] = rid
        try:
            f.write(json.dumps(obj).encode() + b"\n")
        except OSError:
            pass

    def _handle(self, conn: socket.socket) -> None:
        f = conn.makefile("rwb", buffering=0)
        self._reply(f, GREETING, None)
        try:
            while not self.stop_event.is_set():
                line = f.readline(1 << 20)
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                with self.lock:
                    self.requests.append(msg)
                ex = msg.get("execute")
                rid = msg.get("id")
                if ex == "qmp_capabilities":
                    with self.lock:
                        self.capabilities += 1
                    self._reply(f, {"return": {}}, rid)
                elif ex == "query-status":
                    self._reply(f, {"return": {"status": "running",
                                               "running": True}}, rid)
                elif ex == "slow":
                    time.sleep(0.2)
                    self._reply(f, {"return": {"slow": True}}, rid)
                elif ex == "late":
                    time.sleep(0.5)
                    self._reply(f, {"return": {"late": True}}, rid)
                elif ex == "event":
                    f.write(json.dumps({"event": "RESET", "data": {}}).encode() + b"\n")
                    self._reply(f, {"return": {"after_event": True}}, rid)
                elif ex == "die":
                    break
                elif ex == "hang":
                    # Deliberately no reply; the broker must time out.
                    pass
                else:
                    self._reply(f, {"error": {"class": "CommandNotFound",
                                              "desc": f"unknown {ex}"}}, rid)
        except OSError:
            pass
        finally:
            with self.lock:
                self.conns.discard(conn)
            try:
                conn.close()
            except OSError:
                pass

    def stop(self) -> None:
        self.stop_event.set()
        if self.server is not None:
            try:
                self.server.close()
            except OSError:
                pass
        with self.lock:
            conns = list(self.conns)
        for c in conns:
            try:
                c.close()
            except OSError:
                pass
        if self.thread is not None:
            self.thread.join(2.0)


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="qmpb-")
        self.qmp_path = os.path.join(self.dir, "qmp.sock")
        self.broker_path = os.path.join(self.dir, "broker.sock")
        self.fake = FakeQMP(self.qmp_path).start()
        self.broker = qmp_broker.QMPBroker(
            self.qmp_path, self.broker_path,
            command_timeout=1.0, connect_timeout=2.0)
        self.broker.start()
        self.clients: list[tuple[socket.socket, object]] = []

    def tearDown(self):
        for s, f in self.clients:
            try:
                f.close()
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass
        self.broker.stop()
        self.fake.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(3.0)
        s.connect(self.broker_path)
        f = s.makefile("rwb", buffering=0)
        greeting = json.loads(f.readline())
        self.clients.append((s, f))
        return s, f, greeting

    def call(self, f, obj: dict) -> dict:
        f.write(json.dumps(obj).encode() + b"\n")
        line = f.readline()
        self.assertTrue(line, "broker closed the connection")
        return json.loads(line)

    def test_greeting_and_capabilities_per_client(self):
        s1, f1, g1 = self.connect()
        self.assertIn("QMP", g1)
        self.assertEqual(self.call(f1, {"execute": "qmp_capabilities"}),
                         {"return": {}})
        s2, f2, g2 = self.connect()
        self.assertIn("QMP", g2)
        self.assertEqual(self.call(f2, {"execute": "qmp_capabilities",
                                        "id": "c2"}),
                         {"return": {}, "id": "c2"})
        # Only the broker's startup negotiation reached the upstream.
        self.assertEqual(self.fake.capabilities, 1)

    def test_concurrent_commands_are_matched(self):
        _, f1, _ = self.connect()
        self.call(f1, {"execute": "qmp_capabilities"})
        _, f2, _ = self.connect()
        self.call(f2, {"execute": "qmp_capabilities"})
        out = {}
        barrier = threading.Barrier(2)

        def run(name, f, cmd):
            barrier.wait(2.0)
            out[name] = self.call(f, cmd)

        t1 = threading.Thread(target=run, args=("status", f1,
                                                {"execute": "query-status"}))
        t2 = threading.Thread(target=run, args=("slow", f2,
                                                {"execute": "slow"}))
        t1.start(); t2.start(); t1.join(5); t2.join(5)
        self.assertFalse(t1.is_alive()); self.assertFalse(t2.is_alive())
        self.assertEqual(out["status"]["return"]["status"], "running")
        self.assertEqual(out["slow"]["return"]["slow"], True)
        self.assertNotIn("id", out["status"])
        self.assertNotIn("id", out["slow"])
        self.assertEqual(self.fake.connections, 1)

    def test_client_id_is_preserved(self):
        _, f, _ = self.connect()
        r = self.call(f, {"execute": "query-status", "id": "my-id"})
        self.assertEqual(r["id"], "my-id")
        self.assertEqual(r["return"]["status"], "running")

    def test_async_events_are_ignored(self):
        _, f, _ = self.connect()
        r = self.call(f, {"execute": "event"})
        self.assertEqual(r, {"return": {"after_event": True}})
        self.assertNotIn("event", r)

    def test_timeout_then_recovery(self):
        _, f, _ = self.connect()
        self.broker.command_timeout = 0.2
        t0 = time.monotonic()
        r = self.call(f, {"execute": "late"})
        elapsed = time.monotonic() - t0
        self.assertIn("error", r)
        self.assertLess(elapsed, 1.0)
        # The next command reconnects and must not receive the late reply.
        r2 = self.call(f, {"execute": "query-status"})
        self.assertEqual(r2["return"]["status"], "running")
        self.assertGreaterEqual(self.fake.connections, 2)

    def test_client_disconnect_does_not_break_others(self):
        s, f, _ = self.connect()
        self.call(f, {"execute": "qmp_capabilities"})
        f.write(json.dumps({"execute": "slow"}).encode() + b"\n")
        f.close(); s.close()
        time.sleep(0.3)
        _, f2, _ = self.connect()
        self.call(f2, {"execute": "qmp_capabilities"})
        r = self.call(f2, {"execute": "query-status"})
        self.assertEqual(r["return"]["status"], "running")

    def test_upstream_disconnect_then_reconnect(self):
        _, f, _ = self.connect()
        r = self.call(f, {"execute": "die"})
        self.assertIn("error", r)
        r2 = self.call(f, {"execute": "query-status"})
        self.assertEqual(r2["return"]["status"], "running")
        self.assertGreaterEqual(self.fake.connections, 2)

    def test_invalid_json_keeps_connection(self):
        _, f, _ = self.connect()
        f.write(b"this is not json\n")
        r = json.loads(f.readline())
        self.assertIn("error", r)
        r2 = self.call(f, {"execute": "query-status"})
        self.assertEqual(r2["return"]["status"], "running")

    def test_broker_socket_mode(self):
        mode = os.stat(self.broker_path).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_machine_paths(self):
        q, b = qmp_broker.machine_paths("gui-proof", "/tmp/home")
        self.assertEqual(str(q), "/tmp/home/machines/gui-proof/qmp.sock")
        self.assertEqual(str(b), "/tmp/home/machines/gui-proof/broker.sock")
        with self.assertRaises(qmp_broker.BrokerError):
            qmp_broker.machine_paths("../etc", "/tmp/home")


if __name__ == "__main__":
    unittest.main()
