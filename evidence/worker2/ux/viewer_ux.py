#!/usr/bin/env python3
"""terminal-mac Linux viewer prototype (worker2, independent).

Modes
  --mode proto     QMP raw PPM -> POSIX shm -> kitty t=s (f=24), alternating
                   image ids 2/3, old id deleted only after the new one is sent,
                   no ESC[2J, cursor hidden once, placement done once.
  --mode baseline  faithful copy of host/tmac.py Viewer: new QMP connection per
                   frame, PNG screendump with the 50 ms stable-size poll,
                   base64 PNG in 4096-char chunks, ESC[?25l ESC[2J ESC[H every
                   frame.

Sources
  --source vm      live gui-proof QEMU via QMP (default)
  --source test    synthetic animated RGB frames (renderer ceiling)

Output goes to stdout (a kitty pty in the tests). Per-frame JSONL goes to
--log, summary JSON to --summary. No files outside /dev/shm and the log paths.
"""
import argparse
import base64
import hashlib
import json
import mmap
import os
import re
import select
import socket
import sys
import termios
import time
import tty
import uuid

ROOT = "<repo>"
SHM = "/dev/shm"
Z_UNDER_TEXT = -1073741825


# ---------------------------------------------------------------------------
# QMP


class QMP:
    def __init__(self, path, timeout=15.0):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect(str(path))
        self.f = self.sock.makefile("rwb", buffering=0)
        greeting = json.loads(self.f.readline())
        if "QMP" not in greeting:
            raise RuntimeError(f"not QMP: {greeting!r}")
        self.command("qmp_capabilities")

    def command(self, name, arguments=None):
        msg = {"execute": name}
        if arguments is not None:
            msg["arguments"] = arguments
        self.f.write((json.dumps(msg) + "\n").encode())
        while True:
            line = self.f.readline()
            if not line:
                raise RuntimeError("QMP closed")
            obj = json.loads(line)
            if "event" in obj:
                continue
            if "error" in obj:
                raise RuntimeError(f"QMP {name}: {obj['error']}")
            return obj.get("return")

    def close(self):
        try:
            self.f.close()
        finally:
            self.sock.close()


def ppm_header(path):
    with open(path, "rb") as fh:
        head = fh.read(64)
    if head[:2] != b"P6":
        raise ValueError(f"not P6: {head[:16]!r}")
    i = 2
    vals = []
    while len(vals) < 3:
        while head[i:i + 1].isspace():
            i += 1
        if head[i:i + 1] == b"#":
            while head[i:i + 1] != b"\n":
                i += 1
            continue
        j = i
        while not head[j:j + 1].isspace():
            j += 1
        vals.append(int(head[i:j]))
        i = j
    i += 1
    w, h, maxval = vals
    if maxval != 255:
        raise ValueError(f"maxval {maxval}")
    return w, h, i


def wait_complete(path, expected, deadline=2.0):
    """QEMU's reply usually implies complete; verify and wait if not."""
    t0 = time.perf_counter()
    size = os.path.getsize(path)
    polls = 0
    while size < expected and time.perf_counter() - t0 < deadline:
        time.sleep(0.001)
        size = os.path.getsize(path)
        polls += 1
    return size, polls, time.perf_counter() - t0


# ---------------------------------------------------------------------------
# Kitty transport


def b64(s):
    return base64.b64encode(s.encode()).decode()


def shm_create(name, size):
    path = os.path.join(SHM, name.lstrip("/"))
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    os.ftruncate(fd, size)
    return fd, path


def shm_fill_from_file(fd, src_path, offset, size):
    src = os.open(src_path, os.O_RDONLY)
    try:
        done = 0
        while done < size:
            n = os.copy_file_range(src, fd, size - done, offset + done, 0)
            if n <= 0:
                raise OSError(f"copy_file_range stalled at {done}/{size}")
            done += n
    finally:
        os.close(src)


def kitty_shm_escape(name, width, height, image_id):
    return (f"\x1b_Ga=T,f=24,t=s,s={width},v={height},S={width * height * 3},"
            f"i={image_id},q=2,C=1,z={Z_UNDER_TEXT};{b64(name)}\x1b\\")


def kitty_delete(image_id):
    return f"\x1b_Ga=d,d=I,i={image_id},q=2;\x1b\\"


class ShmFrames:
    """Alternating ids; delete the previous image only after the new one."""

    def __init__(self):
        self.seq = 0
        self.current = 0
        self.created = []

    def escape_for_body(self, body, width, height):
        self.seq += 1
        name = f"/tmac.w2.{os.getpid()}.{self.seq}"
        fd, path = shm_create(name, len(body))
        try:
            os.write(fd, body)
        finally:
            os.close(fd)
        self.created.append(path)
        return self.escape_for_name(name, width, height)

    def escape_for_ppm(self, ppm_path, width, height, header_len):
        self.seq += 1
        name = f"/tmac.w2.{os.getpid()}.{self.seq}"
        size = width * height * 3
        fd, path = shm_create(name, size)
        try:
            shm_fill_from_file(fd, ppm_path, header_len, size)
        finally:
            os.close(fd)
        self.created.append(path)
        return self.escape_for_name(name, width, height)

    def escape_for_name(self, name, width, height):
        new_id = 2 if self.current in (0, 3) else 3
        out = kitty_shm_escape(name, width, height, new_id)
        if self.current:
            out += kitty_delete(self.current)
        self.current = new_id
        return out


def probe_shm(stdin_fd, timeout=0.6):
    name = f"/tmac.w2probe.{os.getpid()}"
    fd, path = shm_create(name, 4)
    try:
        os.write(fd, b"\x00\x00\x00\xff")
    finally:
        os.close(fd)
    os.write(sys.stdout.fileno(),
             f"\x1b_Gi=31,s=1,v=1,a=q,t=s,f=32,S=4;{b64(name)}\x1b\\".encode())
    sys.stdout.flush()
    buf = b""
    end = time.time() + timeout
    while time.time() < end:
        r, _, _ = select.select([stdin_fd], [], [], max(0, end - time.time()))
        if not r:
            break
        chunk = os.read(stdin_fd, 4096)
        if not chunk:
            break
        buf += chunk
        if b"\x1b\\" in buf:
            break
    try:
        os.unlink(path)  # kitty unlinks on success; harmless if already gone
    except FileNotFoundError:
        pass
    return b"i=31;OK" in buf, buf.decode("utf-8", "backslashreplace")


def query_metrics(stdin_fd, timeout=0.5):
    """Return (text_w, text_h, cell_w, cell_h, raw reply)."""
    os.write(sys.stdout.fileno(), b"\x1b[14t\x1b[16t")
    sys.stdout.flush()
    buf = b""
    end = time.time() + timeout
    while time.time() < end:
        r, _, _ = select.select([stdin_fd], [], [], max(0, end - time.time()))
        if not r:
            break
        chunk = os.read(stdin_fd, 4096)
        if not chunk:
            break
        buf += chunk
        if b"t" in buf and buf.count(b"t") >= 2:
            break
    text = buf.decode("utf-8", "backslashreplace")
    m14 = re.search(r"\x1b\[4;(\d+);(\d+)t", text)
    m16 = re.search(r"\x1b\[6;(\d+);(\d+)t", text)
    if not (m14 and m16):
        return None
    text_h, text_w = int(m14.group(1)), int(m14.group(2))
    cell_h, cell_w = int(m16.group(1)), int(m16.group(2))
    return text_w, text_h, cell_w, cell_h, text


class Placement:
    def __init__(self, img_w, img_h, text_w, text_h, cell_w, cell_h,
                 reserve_rows=1):
        avail_h = max(cell_h, text_h - reserve_rows * cell_h)
        scale = min(text_w / img_w, avail_h / img_h)
        self.scale = scale
        self.cols = max(1, int(img_w * scale / cell_w))
        self.rows = max(1, int(img_h * scale / cell_h))
        self.cell_w, self.cell_h = cell_w, cell_h
        self.term_rows = max(1, text_h // cell_h)
        self.term_cols = max(1, text_w // cell_w)
        self.col = max(1, 1 + (self.term_cols - self.cols) // 2)
        self.row = max(1, 1 + (self.term_rows - reserve_rows - self.rows) // 2)
        # pixel rect actually covered by the image on screen
        self.px = (self.col - 1) * cell_w
        self.py = (self.row - 1) * cell_h
        self.pw = self.cols * cell_w
        self.ph = self.rows * cell_h

    def point(self, cell_col, cell_row):
        x = self.px + (cell_col - self.col + 0.5) * self.cell_w
        y = self.py + (cell_row - self.row + 0.5) * self.cell_h
        if not (self.px <= x < self.px + self.pw and self.py <= y < self.py + self.ph):
            return None
        return int(x * 1.0), int(y * 1.0)


# ---------------------------------------------------------------------------
# Input (batched QMP events)


SHIFTED = {"!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6",
           "&": "7", "*": "8", "(": "9", ")": "0", "_": "minus", "+": "equal",
           "{": "bracket_left", "}": "bracket_right", "|": "backslash",
           ":": "semicolon", '"': "apostrophe", "~": "grave_accent",
           "<": "comma", ">": "dot", "?": "slash"}
PLAIN = {"\n": "ret", "\r": "ret", "\t": "tab", " ": "spc", "-": "minus",
         "=": "equal", "[": "bracket_left", "]": "bracket_right",
         "\\": "backslash", ";": "semicolon", "'": "apostrophe",
         "`": "grave_accent", ",": "comma", ".": "dot", "/": "slash",
         "\b": "backspace", "\x7f": "backspace"}
for _i in range(1, 13):
    PLAIN[f"f{_i}"] = f"f{_i}"
for _k in "abcdefghijklmnopqrstuvwxyz0123456789":
    PLAIN[_k] = _k
MODS = {"cmd": "meta_l", "command": "meta_l", "super": "meta_l",
        "win": "meta_l", "ctrl": "ctrl", "control": "ctrl", "alt": "alt",
        "option": "alt", "shift": "shift"}


def ev(qcode, down):
    return {"type": "key", "data": {"key": {"type": "qcode", "data": qcode},
                                    "down": down}}


def chord_events(chord):
    parts = [p for p in chord.lower().split("+") if p]
    if not parts:
        raise ValueError("empty chord")
    name = parts[-1]
    qcode = PLAIN.get(name) or SHIFTED.get(name)
    if qcode is None:
        raise ValueError(f"unknown key {chord}")
    mods = [MODS[m] for m in parts[:-1]]
    events = [ev(m, True) for m in mods] + [ev(qcode, True), ev(qcode, False)]
    events += [ev(m, False) for m in reversed(mods)]
    return events


def text_events(text):
    events = []
    for ch in text:
        shifted = ch in SHIFTED or ch.isupper()
        qcode = SHIFTED.get(ch) or PLAIN.get(ch)
        if qcode is None and ch.isdigit():
            qcode = ch
        if qcode is None:
            raise ValueError(f"cannot type {ch!r}")
        if shifted:
            events.append(ev("shift", True))
        events += [ev(qcode, True), ev(qcode, False)]
        if shifted:
            events.append(ev("shift", False))
    return events


# ---------------------------------------------------------------------------
# Synthetic source


def test_frame(i, w, h):
    """Cheap 1280x800 RGB frame: solid colour + moving bar + counter stripe."""
    import numpy as np
    r = (i * 7) % 256
    g = (i * 13) % 256
    b = (i * 29) % 256
    arr = np.empty((h, w, 3), dtype=np.uint8)
    arr[:, :, 0] = r
    arr[:, :, 1] = g
    arr[:, :, 2] = b
    x = (i * 17) % max(1, w - 120)
    arr[:, x:x + 120, 0] = 255
    arr[:, x:x + 120, 1] = 255 - r
    arr[:, x:x + 120, 2] = 128
    # a block whose position encodes the frame number (visible in screenshots)
    y = (i * 11) % max(1, h - 80)
    arr[y:y + 80, 0:80, :] = 255
    return arr.tobytes()


# ---------------------------------------------------------------------------
# Viewer


class Viewer:
    def __init__(self, args):
        self.args = args
        self.home = os.environ.get("TMAC_HOME",
                                   os.path.join(ROOT, "work/main/tmac-home"))
        self.mdir = os.path.join(self.home, "machines", args.machine)
        self.qmp_path = os.path.join(self.mdir, "qmp.sock")
        self.stop = False
        self.placement = None
        self.log = open(args.log, "w", buffering=1, encoding="utf-8") if args.log else None
        self.last_digest = None
        self.frames_sent = 0
        self.frames_captured = 0
        self.rows = []
        self.t_start = None
        self.shm = ShmFrames()
        self.probe_ok = None
        self.metrics_raw = None
        self.cell = (8, 19)
        self.text_px = None
        self.q = None
        self.last_status = None
        self.image_origin = None

    def emit(self, obj):
        if self.log:
            self.log.write(json.dumps(obj) + "\n")

    def connect(self):
        self.q = QMP(self.qmp_path)

    def capture_vm(self):
        t0 = time.perf_counter()
        ppm = os.path.join(SHM, f"w2cap.{os.getpid()}.ppm")
        self.q.command("screendump", {"filename": ppm, "format": "ppm"})
        t_reply = time.perf_counter()
        w, h, off = ppm_header(ppm)
        expected = off + w * h * 3
        size, polls, wait = wait_complete(ppm, expected)
        if size != expected:
            raise RuntimeError(f"PPM short: {size} != {expected}")
        return ppm, w, h, off, t_reply - t0, wait, polls

    def capture_vm_png(self, machine):
        """Baseline path: exact tmac.screendump (new QMP connection, PNG, poll)."""
        import tmac
        os.environ.setdefault("TMAC_HOME", self.home)
        m = tmac.load_machine(self.args.machine)
        t0 = time.perf_counter()
        path = tmac.screendump(m, tmac.machine_dir(self.args.machine) / "frame.png",
                               fmt="png")
        dt = time.perf_counter() - t0
        png = path.read_bytes()
        w, h = tmac.png_size(path)
        return png, w, h, dt

    def setup_terminal(self):
        stdin_fd = sys.stdin.fileno()
        self.old_term = termios.tcgetattr(stdin_fd)
        tty.setraw(stdin_fd)
        # alternate screen, hidden cursor, no line wrap
        sys.stdout.write("\x1b[?1049h\x1b[?25l\x1b[?7l")
        sys.stdout.flush()
        m = query_metrics(stdin_fd)
        if m:
            tw, th, cw, ch, raw = m
            self.text_px = (tw, th)
            self.cell = (cw, ch)
            self.metrics_raw = raw
        if self.args.mode == "proto":
            ok, raw = probe_shm(stdin_fd)
            self.probe_ok = ok
            self.probe_raw = raw

    def restore_terminal(self):
        try:
            sys.stdout.write("\x1b_Ga=d,d=A,q=2;\x1b\\\x1b[?7h\x1b[?25h\x1b[?1049l")
            sys.stdout.flush()
        except Exception:
            pass
        for path in self.shm.created:
            try:
                os.unlink(path)
            except OSError:
                pass
        try:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self.old_term)
        except Exception:
            pass
        if self.log:
            self.log.close()

    def place_once(self, w, h):
        if self.text_px:
            tw, th = self.text_px
        else:
            sz = os.get_terminal_size()
            tw, th = sz.columns * self.cell[0], sz.lines * self.cell[1]
        self.placement = Placement(w, h, tw, th, *self.cell)
        p = self.placement
        self.image_origin = f"\x1b[{p.row};{p.col}H"
        sys.stdout.write(self.image_origin)
        sys.stdout.flush()

    def status(self, text):
        if not self.placement:
            return
        if text == self.last_status:
            return
        self.last_status = text
        p = self.placement
        rows = getattr(p, "term_rows", None) or os.get_terminal_size().lines
        cols = getattr(p, "term_cols", None) or os.get_terminal_size().columns
        line = f"\x1b[{rows};1H\x1b[2K\x1b[7m{text[:cols]}"
        # return cursor to the image origin so the next placement lands right
        origin = self.image_origin
        if origin is None:
            origin = f"\x1b[{p.row};{p.col}H"
        sys.stdout.write(line + "\x1b[0m" + origin)
        sys.stdout.flush()

    def run(self):
        self.setup_terminal()
        self.t_start = time.perf_counter()
        deadline = self.t_start + self.args.seconds if self.args.seconds else None
        if self.args.mode == "baseline" and self.args.source == "vm":
            # no persistent connection: tmac opens one per frame
            pass
        elif self.args.source == "vm":
            self.connect()
        i = 0
        try:
            while not self.stop:
                if deadline and time.perf_counter() >= deadline:
                    break
                if self.args.frames and i >= self.args.frames:
                    break
                t0 = time.perf_counter()
                cap_wait = polls = 0.0
                cap_ms = read_ms = shm_ms = 0.0
                if self.args.source == "test":
                    w = h = 0
                    body = test_frame(i, self.args.test_width, self.args.test_height)
                    w, h = self.args.test_width, self.args.test_height
                    digest = hashlib.blake2b(body, digest_size=16).digest()
                    t_read = time.perf_counter()
                    read_ms = (t_read - t0) * 1000
                    changed = digest != self.last_digest
                    if changed or self.args.force:
                        t1 = time.perf_counter()
                        if self.args.mode == "baseline":
                            import io
                            from PIL import Image
                            import tmac
                            buf = io.BytesIO()
                            Image.frombytes("RGB", (w, h), body).save(buf, "PNG")
                            png = buf.getvalue()
                            if not self.placement:
                                cols, rows = os.get_terminal_size()
                                self.baseline_placement = tmac.Placement(w, h, cols, rows)
                                self.placement = self.baseline_placement
                            p = self.baseline_placement
                            esc = ("\x1b[?25l\x1b[2J\x1b[H"
                                   + f"\x1b[{p.row};{p.col}H"
                                   + tmac.kitty_png(png, p.cols, p.rows))
                        else:
                            if not self.placement:
                                self.place_once(w, h)
                            esc = self.shm.escape_for_body(body, w, h)
                        shm_ms = (time.perf_counter() - t1) * 1000
                        sys.stdout.write(esc)
                        sys.stdout.flush()
                        self.frames_sent += 1
                        self.last_digest = digest
                elif self.args.mode == "baseline":
                    png, w, h, cap = self.capture_vm_png(self.args.machine)
                    cap_ms = cap * 1000
                    digest = hashlib.sha256(png).digest()
                    changed = digest != self.last_digest
                    if changed or self.args.force:
                        import tmac
                        if not self.placement:
                            cols, rows = os.get_terminal_size()
                            p = tmac.Placement(w, h, cols, rows)
                            self.placement = p
                            self.baseline_placement = p
                        p = self.baseline_placement
                        t1 = time.perf_counter()
                        body = tmac.kitty_png(png, p.cols, p.rows)
                        frame = ("\x1b[?25l\x1b[2J\x1b[H"
                                 + f"\x1b[{p.row};{p.col}H" + body)
                        sys.stdout.write(frame)
                        sys.stdout.flush()
                        shm_ms = (time.perf_counter() - t1) * 1000
                        self.frames_sent += 1
                        self.last_digest = digest
                else:
                    ppm, w, h, off, reply, wait, polls = self.capture_vm()
                    cap_ms = (reply + wait) * 1000
                    cap_wait = wait * 1000
                    # hash without copying the 3 MB body
                    with open(ppm, "rb") as fh:
                        mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
                        try:
                            digest = hashlib.blake2b(
                                memoryview(mm)[off:off + w * h * 3],
                                digest_size=16).digest()
                        finally:
                            mm.close()
                    t_read = time.perf_counter()
                    read_ms = (t_read - t0) * 1000 - cap_ms
                    changed = digest != self.last_digest
                    if changed or self.args.force:
                        if not self.placement:
                            self.place_once(w, h)
                        t1 = time.perf_counter()
                        esc = self.shm.escape_for_ppm(ppm, w, h, off)
                        shm_ms = (time.perf_counter() - t1) * 1000
                        sys.stdout.write(esc)
                        sys.stdout.flush()
                        self.frames_sent += 1
                        self.last_digest = digest
                    os.unlink(ppm)
                self.frames_captured += 1
                total_ms = (time.perf_counter() - t0) * 1000
                self.rows.append({"i": i, "cap_ms": round(cap_ms, 2),
                                  "read_ms": round(read_ms, 2),
                                  "shm_ms": round(shm_ms, 2),
                                  "total_ms": round(total_ms, 2),
                                  "changed": changed, "w": w, "h": h,
                                  "polls": polls, "cap_wait_ms": round(cap_wait, 2)})
                if i % 10 == 0:
                    self.status(f" {self.args.machine}  {w}x{h}  "
                                f"{self.args.mode}  frame {i}  "
                                f"{1000 / max(total_ms, 0.001):.0f} fps ")
                # input
                remaining = 0.0
                if self.args.fps:
                    remaining = max(0.0, 1.0 / self.args.fps
                                    - (time.perf_counter() - t0))
                self.poll_input(remaining)
                i += 1
        finally:
            summary = self.summary()
            self.emit(summary)
            if self.args.summary:
                with open(self.args.summary, "w", encoding="utf-8") as fh:
                    json.dump(summary, fh, indent=2)
            self.restore_terminal()
            if self.q:
                self.q.close()

    def poll_input(self, timeout):
        if timeout <= 0:
            r, _, _ = select.select([sys.stdin], [], [], 0)
        else:
            r, _, _ = select.select([sys.stdin], [], [], timeout)
        if not r:
            return
        data = os.read(sys.stdin.fileno(), 4096)
        self.handle(data)

    def handle(self, data):
        i = 0
        while i < len(data):
            b = data[i]
            if b == 0x1d:
                self.stop = True
                return
            if b == 0x1b:
                m = re.match(rb"\x1b\[<(\d+);(\d+);(\d+)([Mm])", data[i:])
                if m:
                    btn, cx, cy = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    i += m.end()
                    self.mouse(btn, cx, cy, m.group(4) == b"m")
                    continue
                keys = {b"\x1b[A": "up", b"\x1b[B": "down", b"\x1b[C": "right",
                        b"\x1b[D": "left", b"\x1b[H": "home", b"\x1b[F": "end",
                        b"\x1b[3~": "delete", b"\x1b[5~": "pgup",
                        b"\x1b[6~": "pgdn", b"\x1b[Z": "shift+tab"}
                for prefix, key in sorted(keys.items(), key=lambda kv: -len(kv[0])):
                    if data.startswith(prefix, i):
                        self.key(key)
                        i += len(prefix)
                        break
                else:
                    self.key("escape")
                    i += 1
                continue
            if b in (10, 13):
                self.key("return"); i += 1; continue
            if b == 9:
                self.key("tab"); i += 1; continue
            if b in (8, 127):
                self.key("backspace"); i += 1; continue
            if 1 <= b <= 26:
                self.key("ctrl+" + chr(b + 96)); i += 1; continue
            length = 1 if b < 0x80 else 2 if 0xc2 <= b <= 0xdf else 3 if 0xe0 <= b <= 0xef else 4 if 0xf0 <= b <= 0xf4 else 0
            if not length:
                i += 1
                continue
            if i + length > len(data):
                return
            text = data[i:i + length].decode("utf-8", "replace")
            i += length
            self.type_text(text)

    def send_events(self, events):
        if not events:
            return
        try:
            if self.args.mode == "baseline":
                q = QMP(self.qmp_path)
                try:
                    q.command("input-send-event", {"events": events})
                finally:
                    q.close()
            else:
                if self.q is None:
                    self.connect()
                self.q.command("input-send-event", {"events": events})
        except Exception as e:
            self.status(f"input error: {e}"[:60])

    def key(self, chord):
        try:
            self.send_events(chord_events(chord))
        except Exception as e:
            self.status(f"key error: {e}"[:60])

    def type_text(self, text):
        try:
            self.send_events(text_events(text))
        except Exception as e:
            self.status(f"type error: {e}"[:60])

    def mouse(self, btn, cx, cy, release):
        if not self.placement:
            return
        pt = self.placement.point(cx, cy)
        if not pt:
            return
        if btn & 64:
            return
        x, y = pt
        name = "left" if (btn & 3) == 0 else "middle" if (btn & 3) == 1 else "right"
        if release:
            return
        events = [
            {"type": "abs", "data": {"axis": "x", "value": int(x * 32767 / max(1, self.args.test_width - 1))}},
            {"type": "abs", "data": {"axis": "y", "value": int(y * 32767 / max(1, self.args.test_height - 1))}},
        ]
        events += [{"type": "btn", "data": {"down": True, "button": name}},
                   {"type": "btn", "data": {"down": False, "button": name}}]
        self.send_events(events)

    def summary(self):
        rows = self.rows
        total = time.perf_counter() - (self.t_start or time.perf_counter())
        def med(key):
            xs = sorted(r[key] for r in rows)
            return round(xs[len(xs) // 2], 2) if xs else None
        def p95(key):
            xs = sorted(r[key] for r in rows)
            return round(xs[min(len(xs) - 1, int(0.95 * len(xs)))], 2) if xs else None
        return {
            "mode": self.args.mode, "source": self.args.source,
            "machine": self.args.machine,
            "frames_captured": len(rows), "frames_sent": self.frames_sent,
            "elapsed_s": round(total, 3),
            "capture_fps": round(len(rows) / total, 2) if total else 0,
            "send_fps": round(self.frames_sent / total, 2) if total else 0,
            "median_total_ms": med("total_ms"), "p95_total_ms": p95("total_ms"),
            "median_cap_ms": med("cap_ms"), "p95_cap_ms": p95("cap_ms"),
            "median_shm_ms": med("shm_ms"), "p95_shm_ms": p95("shm_ms"),
            "probe_shm_ok": self.probe_ok,
            "metrics_raw": self.metrics_raw,
            "cell": list(self.cell), "text_px": list(self.text_px) if self.text_px else None,
            "placement": None if not self.placement else {
                "cols": self.placement.cols, "rows": self.placement.rows,
                "col": self.placement.col, "row": self.placement.row,
                "px": self.placement.px, "py": self.placement.py,
                "pw": self.placement.pw, "ph": self.placement.ph},
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["proto", "baseline"], default="proto")
    ap.add_argument("--source", choices=["vm", "test"], default="vm")
    ap.add_argument("--machine", default="gui-proof")
    ap.add_argument("--seconds", type=float, default=0.0)
    ap.add_argument("--frames", type=int, default=0)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--log", default="")
    ap.add_argument("--summary", default="")
    ap.add_argument("--test-width", type=int, default=1280)
    ap.add_argument("--test-height", type=int, default=800)
    args = ap.parse_args()
    v = Viewer(args)
    try:
        v.run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
