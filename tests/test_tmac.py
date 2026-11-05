#!/usr/bin/env python3
"""Unit tests for the Linux host port's pure logic (no QEMU needed)."""

import base64
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))
import tmac  # noqa: E402


class FakeQMP:
    def __init__(self):
        self.events = []
        self.sends = []
        self.calls = 0

    def command(self, name, args=None):
        self.calls += 1
        if name == "input-send-event":
            self.events.extend(args["events"])
        elif name == "send-key":
            self.sends.append(args["keys"])
        return {}


class TmacTests(unittest.TestCase):
    def test_placement_maps_center_to_image_center(self):
        p = tmac.Placement(1280, 800, 80, 24)
        x, y = p.point(p.col + p.cols // 2, p.row + p.rows // 2)
        # A cell center is within half a displayed cell of the image center.
        self.assertLess(abs(x - p.width // 2), p.cell_w / p.scale)
        self.assertLess(abs(y - p.height // 2), p.cell_h / p.scale / 2)

    def test_placement_never_upscales(self):
        p = tmac.Placement(1280, 800, 200, 80, 8.0, 19.0)
        self.assertEqual(p.scale, 1.0)
        self.assertEqual((p.cols, p.rows), (160, 43))
        self.assertEqual(p.point(p.col + 160, p.row + 43), None)
        self.assertIsNotNone(p.point(p.col + 159, p.row + 42, clamp=True))

    def test_placement_rejects_outside(self):
        p = tmac.Placement(1280, 800, 80, 24)
        self.assertIsNone(p.point(1, 1))

    def test_kitty_chunking(self):
        payload = b"x" * 10000
        seq = tmac.kitty_png(payload, 40, 20)
        self.assertTrue(seq.startswith("\x1b_Ga=T,f=100"))
        self.assertIn("c=40,r=20", seq)
        self.assertTrue(seq.endswith("\x1b\\"))
        chunks = seq.count("\x1b_G")
        encoded = base64.b64encode(payload).decode()
        self.assertEqual(chunks, (len(encoded) + 4095) // 4096)

    def test_shifted_and_upper_text(self):
        q = FakeQMP()
        tmac.text_events(q, "A!")
        keys = [[k["data"] for k in send] for send in q.sends]
        self.assertIn(["shift", "a"], keys)
        self.assertIn(["shift", "1"], keys)
        self.assertEqual(sum(1 for k in keys if k[0] == "shift"), 2)

    def test_key_chord_meta(self):
        q = FakeQMP()
        tmac.key_chord(q, "cmd+space")
        names = [(e["data"]["key"]["data"], e["data"]["down"]) for e in q.events]
        self.assertEqual(names, [("meta_l", True), ("spc", True), ("spc", False), ("meta_l", False)])

    def test_unicode_rejected(self):
        with self.assertRaises(tmac.TmacError):
            tmac.text_events(FakeQMP(), "é")

    def test_text_uses_hold_time_send_key_per_character(self):
        q = FakeQMP()
        tmac.text_events(q, "Hi!")
        # One send-key per character; send-key itself holds and releases the key.
        self.assertEqual(q.calls, 3)
        self.assertEqual(len(q.sends), 3)

    def test_key_chord_sends_separate_transitions(self):
        q = FakeQMP()
        tmac.key_chord(q, "cmd+shift+a")
        self.assertEqual(q.calls, 6)
        self.assertEqual(len(q.events), 6)

    def test_ppm_decode(self):
        header = b"P6\n2 3\n255\n"
        raster = bytes(range(18))
        p = Path("/tmp/tmac-test.ppm"); p.write_bytes(header + raster)
        w, h, body = tmac.read_ppm(p)
        self.assertEqual((w, h), (2, 3))
        self.assertEqual(body, raster)

    def test_kitty_shm_header_and_cleanup(self):
        seq, path = tmac.kitty_shm(b"\x00" * (2 * 2 * 3), 2, 2, 2, 2, 7, 6)
        self.assertIn("t=s", seq)
        self.assertIn("f=24", seq)
        self.assertIn("S=12", seq)
        self.assertIn("d=I,i=6", seq)
        self.assertTrue(Path(path).exists())
        Path(path).unlink()

    def test_png_size(self):
        # Minimal 2x3 PNG header is enough for png_size.
        raw = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (2).to_bytes(4, "big") + (3).to_bytes(4, "big")
        p = Path("/tmp/tmac-size-test.png")
        p.write_bytes(raw)
        self.assertEqual(tmac.png_size(p), (2, 3))

    def test_name_validation(self):
        self.assertTrue(tmac.machine_dir("work-1").name == "work-1")
        with self.assertRaises(tmac.TmacError):
            tmac.machine_dir("../etc")


class ExternalQMPTests(unittest.TestCase):
    """A Reims/QEMU VM registered by an existing QMP socket has no pid here."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tmac-external-"))
        self.old_home = tmac.HOME
        tmac.HOME = self.tmp

    def tearDown(self):
        tmac.HOME = self.old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    class FakeQMP:
        seen: list[str] = []

        def __init__(self, path, timeout=10.0):
            self.path = str(path)
            type(self).seen.append(self.path)

        def command(self, name, args=None):
            if name == "query-status":
                return {"running": True}
            return {}

        def close(self):
            pass

    def test_attach_writes_external_record_and_probes_status(self):
        sock = self.tmp / "qmp.sock"
        sock.touch()
        with mock.patch.object(tmac, "QMP", self.FakeQMP):
            result = tmac.attach_machine(
                "tahoe", sock, width=1280, height=800,
                ssh_port=2224, guest_user="builder")
        self.assertEqual(result["state"], "running")
        m = tmac.load_machine("tahoe")
        self.assertEqual(m["mode"], "external")
        self.assertEqual(m["qmp_socket"], str(sock))
        self.assertEqual(m["ssh_port"], 2224)
        self.assertNotIn("pid", m)

    def test_external_qmp_uses_its_own_socket(self):
        sock = self.tmp / "qmp.sock"
        sock.touch()
        m = {"name": "tahoe", "mode": "external", "qmp_socket": str(sock)}
        with mock.patch.object(tmac, "QMP", self.FakeQMP):
            q = tmac.qmp_for(m)
        self.assertEqual(q.path, str(sock))

    def test_external_status_does_not_require_pid(self):
        sock = self.tmp / "qmp.sock"
        sock.touch()
        tmac.save_machine({"name": "tahoe", "mode": "external", "qmp_socket": str(sock),
                           "width": 1280, "height": 800})
        with mock.patch.object(tmac, "QMP", self.FakeQMP):
            status = tmac.machine_status("tahoe")
        self.assertEqual(status["state"], "running")
        self.assertTrue(status["external"])
        self.assertNotIn("pid", status)

    def test_external_cannot_be_started_or_stopped(self):
        sock = self.tmp / "qmp.sock"
        sock.touch()
        tmac.save_machine({"name": "tahoe", "mode": "external", "qmp_socket": str(sock)})
        with self.assertRaises(tmac.TmacError):
            tmac.start_machine("tahoe")
        with self.assertRaises(tmac.TmacError):
            tmac.stop_machine("tahoe")

    def test_external_ssh_port_is_used(self):
        self.assertEqual(tmac.guest_port({"ssh_port": 2224}), 2224)

    def test_attach_requires_existing_socket(self):
        with self.assertRaises(tmac.TmacError):
            tmac.attach_machine("tahoe", self.tmp / "missing.sock")


if __name__ == "__main__":
    unittest.main()
