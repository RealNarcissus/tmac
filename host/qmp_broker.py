#!/usr/bin/env python3
"""QMP broker: one upstream QMP connection, many downstream clients.

QEMU's QMP chardev serves one client at a time.  The viewer and the MCP server
therefore cannot both hold `qmp.sock`.  This broker owns the single upstream
connection and exposes a Unix socket that any number of local clients can use:

    VM qmp.sock  <--- broker --->  client A (viewer)
                                   +-> client B (MCP / status)

Protocol behaviour
  * every client receives the QMP greeting on connect;
  * `qmp_capabilities` is answered locally, so each client can negotiate it;
  * commands are serialised upstream (QMP is serial anyway) and each response
    is matched to its request with an `id` (the client's id is preserved);
  * asynchronous QMP events are read and discarded;
  * a client disconnect while waiting does not affect other clients;
  * a per-command timeout returns a JSON error, drops the upstream connection
    and reconnects on the next command (avoids a desynchronised stream);
  * an upstream disconnect returns an error and reconnects on the next command.

CLI
    python3 host/qmp_broker.py --machine gui-proof
    python3 host/qmp_broker.py --qmp /path/qmp.sock --broker /path/broker.sock

The default broker path is `<machine_dir>/broker.sock`.  `--home` defaults to
`$TMAC_HOME` or `~/.local/share/tmac`.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import signal
import socket
import sys
import threading
import time
import uuid
from pathlib import Path

DEFAULT_HOME = os.environ.get("TMAC_HOME") or os.path.expanduser("~/.local/share/tmac")
MAX_LINE = 1 << 20  # 1 MiB per QMP line; commands are tiny


class BrokerError(RuntimeError):
    pass


def _now() -> float:
    return time.monotonic()


class Request:
    __slots__ = ("message", "done", "reply", "internal_id", "client_had_id")

    def __init__(self, message: dict):
        self.message = message
        self.done = threading.Event()
        self.reply: dict | None = None
        self.internal_id: str | None = None
        self.client_had_id = "id" in message


class QMPBroker:
    def __init__(self, qmp_path: str | Path, broker_path: str | Path,
                 command_timeout: float = 10.0, connect_timeout: float = 5.0,
                 verbose: bool = False):
        self.qmp_path = Path(qmp_path)
        self.broker_path = Path(broker_path)
        self.command_timeout = max(0.05, float(command_timeout))
        self.connect_timeout = max(0.05, float(connect_timeout))
        self.verbose = verbose

        self._queue: queue.Queue[Request | None] = queue.Queue()
        self._stop = threading.Event()
        self._upstream_lock = threading.Lock()
        self._clients_lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._file = None
        self._greeting: dict | None = None
        self._server: socket.socket | None = None
        self._dispatcher: threading.Thread | None = None
        self._acceptor: threading.Thread | None = None
        self._clients: set[socket.socket] = set()
        self._seq = 0

    # -- logging ---------------------------------------------------------
    def _log(self, message: str) -> None:
        if self.verbose:
            print(f"qmp_broker[{os.getpid()}]: {message}", file=sys.stderr, flush=True)

    # -- upstream --------------------------------------------------------
    def _connect_upstream_locked(self) -> None:
        """Open QMP and negotiate capabilities.  Caller holds _upstream_lock."""
        if self._sock is not None:
            return
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.connect_timeout)
        f = None
        try:
            sock.connect(str(self.qmp_path))
            f = sock.makefile("rwb", buffering=0)
            line = f.readline(MAX_LINE)
            if not line:
                raise BrokerError("QMP closed before greeting")
            greeting = json.loads(line)
            if "QMP" not in greeting:
                raise BrokerError(f"not a QMP greeting: {greeting!r}")
            f.write(json.dumps({"execute": "qmp_capabilities"}).encode() + b"\n")
            while True:
                line = f.readline(MAX_LINE)
                if not line:
                    raise BrokerError("QMP closed during capabilities")
                obj = json.loads(line)
                if "event" in obj:
                    continue
                if "error" in obj:
                    raise BrokerError(f"qmp_capabilities: {obj['error']}")
                break
            sock.settimeout(self.command_timeout)
        except Exception:
            if f is not None:
                try:
                    f.close()
                except OSError:
                    pass
            try:
                sock.close()
            except OSError:
                pass
            raise
        self._sock, self._file, self._greeting = sock, f, greeting
        self._log(f"upstream connected to {self.qmp_path}")

    def _close_upstream_locked(self) -> None:
        """Caller holds _upstream_lock."""
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._file = None

    def _forward_locked(self, req: Request) -> dict:
        """Send one command and return its non-event response.

        Caller holds _upstream_lock.  One command is in flight at a time, and
        every forwarded command carries an id so late responses can be ignored.
        """
        if self._sock is None or self._file is None:
            self._connect_upstream_locked()
        assert self._sock is not None and self._file is not None

        msg = dict(req.message)
        rid = msg.get("id")
        if rid is None:
            self._seq += 1
            rid = f"broker-{os.getpid()}-{self._seq}"
            msg["id"] = rid
        req.internal_id = rid
        self._file.write(json.dumps(msg).encode() + b"\n")

        deadline = _now() + self.command_timeout
        while True:
            remaining = deadline - _now()
            if remaining <= 0:
                raise TimeoutError(f"no QMP response within {self.command_timeout:.3f}s")
            self._sock.settimeout(remaining)
            line = self._file.readline(MAX_LINE)
            if not line:
                raise ConnectionError("QMP upstream closed")
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "event" in obj:
                continue
            oid = obj.get("id")
            if oid is not None and oid != rid:
                # A late response from an earlier, timed-out request.
                continue
            if not req.client_had_id:
                obj.pop("id", None)
            return obj

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            req = self._queue.get()
            if req is None:
                break
            with self._upstream_lock:
                try:
                    reply = self._forward_locked(req)
                except TimeoutError as e:
                    self._log(f"timeout: {e}")
                    self._close_upstream_locked()
                    reply = {"error": {"class": "GenericError",
                                       "desc": f"qmp_broker timeout: {e}"}}
                except Exception as e:  # upstream closed, bad JSON, ...
                    self._log(f"upstream error: {e}")
                    self._close_upstream_locked()
                    reply = {"error": {"class": "GenericError",
                                       "desc": f"qmp_broker upstream error: {e}"}}
            req.reply = reply
            req.done.set()

    # -- clients ---------------------------------------------------------
    def _write(self, f, obj: dict) -> None:
        f.write(json.dumps(obj).encode() + b"\n")

    def _client_loop(self, conn: socket.socket) -> None:
        with self._clients_lock:
            self._clients.add(conn)
        try:
            f = conn.makefile("rwb", buffering=0)
            assert self._greeting is not None
            self._write(f, self._greeting)
            while not self._stop.is_set():
                line = f.readline(MAX_LINE)
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    self._write(f, {"error": {"class": "GenericError",
                                              "desc": "invalid JSON"}})
                    continue
                if not isinstance(msg, dict) or "execute" not in msg:
                    self._write(f, {"error": {"class": "GenericError",
                                              "desc": "invalid QMP command"}})
                    continue
                if msg.get("execute") == "qmp_capabilities":
                    reply = {"return": {}}
                    if "id" in msg:
                        reply["id"] = msg["id"]
                    self._write(f, reply)
                    continue
                req = Request(msg)
                self._queue.put(req)
                while not req.done.wait(0.05):
                    if self._stop.is_set():
                        break
                if req.done.is_set():
                    self._write(f, req.reply or {"error": {
                        "class": "GenericError", "desc": "no reply"}})
                else:
                    self._write(f, {"error": {"class": "GenericError",
                                              "desc": "qmp_broker stopping"}})
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with self._clients_lock:
                self._clients.discard(conn)
            try:
                conn.close()
            except OSError:
                pass

    def _accept_loop(self) -> None:
        assert self._server is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._client_loop, args=(conn,),
                             name="qmp-broker-client", daemon=True).start()

    # -- lifecycle -------------------------------------------------------
    def _prepare_socket(self) -> None:
        if self.broker_path.exists():
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.settimeout(0.2)
            try:
                probe.connect(str(self.broker_path))
            except OSError:
                pass  # stale socket
            else:
                raise BrokerError(f"broker socket already in use: {self.broker_path}")
            finally:
                probe.close()
            try:
                self.broker_path.unlink()
            except FileNotFoundError:
                pass

    def start(self) -> "QMPBroker":
        if self._server is not None:
            return self
        with self._upstream_lock:
            self._connect_upstream_locked()
        self.broker_path.parent.mkdir(parents=True, exist_ok=True)
        self._prepare_socket()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.broker_path))
        os.chmod(self.broker_path, 0o600)
        server.listen(16)
        server.settimeout(0.2)
        self._server = server
        self._dispatcher = threading.Thread(target=self._dispatch_loop,
                                            name="qmp-broker-dispatch", daemon=True)
        self._dispatcher.start()
        self._acceptor = threading.Thread(target=self._accept_loop,
                                          name="qmp-broker-accept", daemon=True)
        self._acceptor.start()
        self._log(f"broker listening on {self.broker_path}")
        return self

    def serve_forever(self) -> None:
        if self._server is None:
            self.start()
        try:
            while not self._stop.wait(0.2):
                pass
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        server = self._server
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        with self._clients_lock:
            clients = list(self._clients)
        for conn in clients:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass
        self._queue.put(None)
        if self._dispatcher is not None:
            self._dispatcher.join(2.0)
        if self._acceptor is not None:
            self._acceptor.join(2.0)
        with self._upstream_lock:
            self._close_upstream_locked()
        try:
            self.broker_path.unlink()
        except FileNotFoundError:
            pass
        self._log("stopped")


def machine_paths(machine: str, home: str | Path = DEFAULT_HOME) -> tuple[Path, Path]:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", machine or ""):
        raise BrokerError(f"invalid machine name: {machine!r}")
    d = Path(home) / "machines" / machine
    return d / "qmp.sock", d / "broker.sock"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Broker one QMP socket to many local clients.")
    ap.add_argument("--machine", help="machine name under $TMAC_HOME/machines")
    ap.add_argument("--home", default=DEFAULT_HOME,
                    help="TMAC_HOME (default: $TMAC_HOME or ~/.local/share/tmac)")
    ap.add_argument("--qmp", help="override the upstream qmp.sock path")
    ap.add_argument("--broker", help="override the downstream broker.sock path")
    ap.add_argument("--timeout", type=float, default=10.0,
                    help="per-command timeout in seconds (default 10)")
    ap.add_argument("--connect-timeout", type=float, default=5.0,
                    help="upstream connect/greeting timeout (default 5)")
    ap.add_argument("--verbose", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    qmp = args.qmp
    broker = args.broker
    if not qmp or not broker:
        if not args.machine:
            ap.error("provide --machine, or both --qmp and --broker")
        try:
            mq, mb = machine_paths(args.machine, args.home)
        except BrokerError as e:
            ap.error(str(e))
        qmp = qmp or str(mq)
        broker = broker or str(mb)
    b = QMPBroker(qmp, broker, command_timeout=args.timeout,
                  connect_timeout=args.connect_timeout, verbose=args.verbose)

    def stop(signum, frame):
        b.stop()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        b.start()
    except Exception as e:
        print(f"qmp_broker: {e}", file=sys.stderr)
        return 1
    print(json.dumps({"broker": str(b.broker_path), "qmp": str(b.qmp_path),
                      "pid": os.getpid()}), flush=True)
    try:
        b.serve_forever()
    finally:
        b.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
