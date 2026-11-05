#!/usr/bin/env python3
"""tmac - a Linux host port of terminal-mac's control plane.

The upstream host uses Apple's Virtualization.framework. This port uses QEMU's
QMP socket instead: screendump for frames, input-send-event for absolute
pointer/keyboard input. No guest helper, no Accessibility permission, no
virtio-fs mailbox is required. The terminal viewer speaks the Kitty graphics
protocol (Ghostty/Kitty) and the MCP server is stdio JSON-RPC, mirroring
terminal-mac's tools.

This is intentionally a single boring file: stdlib + Pillow, easy to audit.
"""

from __future__ import annotations

import argparse
import base64
import errno
import fcntl
import hashlib
import io
import json
import math
import mmap
import os
import re
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import termios
import time
import tty
import uuid
from pathlib import Path

try:
    from PIL import Image
except Exception:  # pragma: no cover - only the MCP JPEG path needs Pillow
    Image = None

HOME = Path(os.environ.get("TMAC_HOME", Path.home() / ".local/share/tmac"))
QEMU = os.environ.get("TMAC_QEMU", "qemu-system-x86_64")
KEY_NAMES = set(json.loads((Path(__file__).with_name("qkeys.json")).read_text())) if (Path(__file__).with_name("qkeys.json")).exists() else set()


class TmacError(RuntimeError):
    pass


def log(msg: str) -> None:
    print(f"tmac: {msg}", file=sys.stderr, flush=True)


def machine_dir(name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}", name):
        raise TmacError("VM names must be 1-48 letters, numbers, underscores or hyphens")
    return HOME / "machines" / name


def load_machine(name: str) -> dict:
    path = machine_dir(name) / "machine.json"
    if not path.exists():
        raise TmacError(f"VM '{name}' does not exist ({path})")
    return json.loads(path.read_text())


def save_machine(m: dict) -> None:
    d = machine_dir(m["name"])
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "machine.json.tmp"
    tmp.write_text(json.dumps(m, indent=2, sort_keys=True) + "\n")
    tmp.replace(d / "machine.json")


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# QMP client


class QMP:
    def __init__(self, path: str | Path, timeout: float = 10.0):
        self.path = str(path)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        deadline = time.time() + timeout
        while True:
            try:
                self.sock.connect(self.path)
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if time.time() > deadline:
                    raise TmacError(f"QMP socket did not appear: {self.path}")
                time.sleep(0.1)
        self.file = self.sock.makefile("rwb", buffering=0)
        greeting = self._read()
        if "QMP" not in greeting:
            raise TmacError(f"not a QMP greeting: {greeting!r}")
        self.command("qmp_capabilities")

    def _read(self) -> dict:
        line = self.file.readline()
        if not line:
            raise TmacError("QMP connection closed")
        return json.loads(line)

    def command(self, name: str, arguments: dict | None = None) -> dict:
        message = {"execute": name}
        if arguments is not None:
            message["arguments"] = arguments
        self.file.write(json.dumps(message).encode() + b"\n")
        while True:
            obj = self._read()
            if "event" in obj:
                continue
            if "error" in obj:
                raise TmacError(f"QMP {name}: {obj['error'].get('desc', obj['error'])}")
            return obj.get("return", {})

    def close(self) -> None:
        try:
            self.file.close()
        finally:
            self.sock.close()


def is_external(m: dict) -> bool:
    """True for an already-running QEMU/Reims VM registered by its QMP socket."""
    return m.get("mode") == "external" or bool(m.get("qmp_socket"))


def qmp_for(m: dict) -> QMP:
    if m.get("qmp_socket"):
        return QMP(m["qmp_socket"])
    d = machine_dir(m["name"])
    broker = d / "broker.sock"
    return QMP(broker if broker.exists() else d / "qmp.sock")


def attach_machine(name: str, qmp_socket: str | Path, width: int = 1280, height: int = 800,
                   ssh_port: int = 0, guest_user: str = "guest", force: bool = False) -> dict:
    """Register an already-running VM (for example a Reims VM) without taking ownership."""
    machine_dir(name)  # validate the name
    path = Path(qmp_socket).expanduser()
    if not path.is_absolute():
        path = path.resolve()
    if not path.exists():
        raise TmacError(f"QMP socket not found: {path}")
    existing = machine_dir(name) / "machine.json"
    if existing.exists() and not force:
        raise TmacError(f"VM '{name}' already exists; pass --force to replace its record")
    q = QMP(str(path))
    try:
        status = q.command("query-status")
    finally:
        q.close()
    m = {
        "name": name,
        "mode": "external",
        "qmp_socket": str(path),
        "width": int(width),
        "height": int(height),
        "ssh_port": int(ssh_port),
        "guest_user": guest_user,
        "created": time.time(),
    }
    save_machine(m)
    return {"name": name, "state": "running" if status.get("running") else "paused",
            "qmp_socket": str(path), "width": m["width"], "height": m["height"]}


def broker_pid_path(m: dict) -> Path:
    return machine_dir(m["name"]) / "broker.pid"


def stop_broker(m: dict) -> None:
    path = broker_pid_path(m)
    try:
        pid = int(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        pid = 0
    if pid and pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for p in (path, machine_dir(m["name"]) / "broker.sock"):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def stop_share(m: dict) -> None:
    path = machine_dir(m["name"]) / "share.pid"
    try:
        pid = int(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        pid = 0
    if pid and pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def start_broker(m: dict) -> bool:
    d = machine_dir(m["name"])
    sock = d / "broker.sock"
    if sock.exists():
        try:
            probe = QMP(sock)
            probe.command("query-status")
            probe.close()
            return True
        except Exception:
            try:
                sock.unlink()
            except FileNotFoundError:
                pass
    broker_py = Path(__file__).resolve().with_name("qmp_broker.py")
    log = open(d / "broker.log", "ab", buffering=0)
    proc = subprocess.Popen([sys.executable, str(broker_py), "--machine", m["name"],
                             "--home", str(Path(os.environ.get("TMAC_HOME", str(Path.home() / ".local/share/tmac"))))],
                            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True)
    broker_pid_path(m).write_text(str(proc.pid) + "\n")
    deadline = time.time() + 5
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        if sock.exists():
            return True
        time.sleep(0.05)
    return False


def screendump(m: dict, output: Path | None = None, fmt: str = "png", q: QMP | None = None) -> Path:
    """Ask QEMU for one framebuffer image. Reuse `q` for low-latency viewers."""
    d = machine_dir(m["name"])
    output = output or (d / "frame.png")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Keep the temporary file on the same filesystem as the destination so the
    # final rename is atomic and does not hit EXDEV for /tmp destinations.
    tmp = output.parent / f".screendump-{uuid.uuid4().hex}.{fmt}"
    own = q is None
    if own:
        q = qmp_for(m)
    try:
        q.command("screendump", {"filename": str(tmp), "format": fmt})
    finally:
        if own:
            q.close()
    # QEMU writes the image synchronously before replying to screendump.  The
    # old implementation waited for two 50 ms polls (a >=100 ms tax per frame)
    # even though the file was already complete.  Verify once, then rename.
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            size = tmp.stat().st_size
        except FileNotFoundError:
            size = 0
        if size > 0:
            time.sleep(0.001)
            if tmp.stat().st_size == size:
                tmp.replace(output)
                return output
        time.sleep(0.001)
    raise TmacError("screendump timed out")


def png_size(path: Path) -> tuple[int, int]:
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        raise TmacError(f"not a PNG: {path}")
    return struct.unpack(">II", head[16:24])


# ---------------------------------------------------------------------------
# Input


SHIFTED = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7",
    "*": "8", "(": "9", ")": "0", "_": "minus", "+": "equal",
    "{": "bracket_left", "}": "bracket_right", "|": "backslash",
    ":": "semicolon", '"': "apostrophe", "~": "grave_accent",
    "<": "comma", ">": "dot", "?": "slash",
}
PLAIN = {
    "\n": "ret", "\r": "ret", "\t": "tab", " ": "spc", "-": "minus",
    "=": "equal", "[": "bracket_left", "]": "bracket_right", "\\": "backslash",
    ";": "semicolon", "'": "apostrophe", "`": "grave_accent", ",": "comma",
    ".": "dot", "/": "slash", "\b": "backspace", "\x7f": "backspace",
}
KEYS = {
    "return": "ret", "enter": "ret", "escape": "esc", "esc": "esc",
    "backspace": "backspace", "tab": "tab", "space": "spc",
    "delete": "delete", "del": "delete", "insert": "insert",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "home": "home", "end": "end", "pageup": "pgup", "pagedown": "pgdn",
    "pgup": "pgup", "pgdn": "pgdn",
}
for _i in range(1, 13):
    KEYS[f"f{_i}"] = f"f{_i}"
for _k in "abcdefghijklmnopqrstuvwxyz0123456789":
    KEYS[_k] = _k
MODS = {"cmd": "meta_l", "command": "meta_l", "super": "meta_l", "win": "meta_l",
        "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "option": "alt",
        "shift": "shift"}


def _key_event(qcode: str, down: bool) -> dict:
    return {"type": "key", "data": {"key": {"type": "qcode", "data": qcode}, "down": down}}


def key_chord(q: QMP, chord: str) -> None:
    parts = [p for p in chord.lower().split("+") if p]
    if not parts:
        raise TmacError("empty key chord")
    name = parts[-1]
    qcode = KEYS.get(name)
    if qcode is None:
        if len(name) == 1 and name in PLAIN:
            qcode = PLAIN[name]
        else:
            raise TmacError(f"unknown key: {chord}")
    mods = []
    for mod in parts[:-1]:
        if mod not in MODS:
            raise TmacError(f"unknown modifier: {mod}")
        mods.append(MODS[mod])
    # Send each transition as its own command.  Batching down+up in one QMP
    # command makes macOS miss the release and auto-repeat the key.
    for mod in mods:
        q.command("input-send-event", {"events": [_key_event(mod, True)]})
    q.command("input-send-event", {"events": [_key_event(qcode, True)]})
    time.sleep(0.002)
    q.command("input-send-event", {"events": [_key_event(qcode, False)]})
    for mod in reversed(mods):
        q.command("input-send-event", {"events": [_key_event(mod, False)]})


def text_events(q: QMP, text: str) -> None:
    unsupported = sorted({c for c in text if ord(c) > 127})
    if unsupported:
        raise TmacError(
            "QMP key injection is US-ASCII only; unsupported characters: "
            + " ".join(repr(c) for c in unsupported)
            + ". Use the macOS guest helper path for Unicode."
        )
    for ch in text:
        shifted = ch in SHIFTED or ch.isupper()
        qcode = SHIFTED.get(ch) or PLAIN.get(ch) or (ch.lower() if ch.isalpha() else None)
        if qcode is None and ch.isdigit():
            qcode = ch
        if qcode is None:
            raise TmacError(f"cannot type character: {ch!r}")
        keys = [{"type": "qcode", "data": qcode}]
        if shifted:
            keys.insert(0, {"type": "qcode", "data": "shift"})
        # send-key holds the key for hold-time and releases it in QEMU; this is
        # materially more reliable than separate down/up input-send-event
        # commands, which macOS can lose when the commands arrive back-to-back.
        q.command("send-key", {"keys": keys, "hold-time": 50})
        time.sleep(0.05)
    time.sleep(0.25)


def abs_move(q: QMP, x: int, y: int, width: int, height: int) -> None:
    ax = max(0, min(32767, round(x * 32767 / max(1, width - 1))))
    ay = max(0, min(32767, round(y * 32767 / max(1, height - 1))))
    q.command("input-send-event", {"events": [
        {"type": "abs", "data": {"axis": "x", "value": ax}},
        {"type": "abs", "data": {"axis": "y", "value": ay}},
    ]})


def button(q: QMP, name: str, down: bool) -> None:
    q.command("input-send-event", {"events": [
        {"type": "btn", "data": {"button": name, "down": down}}
    ]})


def pointer_click(m: dict, x: int, y: int, count: int = 1, right: bool = False) -> None:
    w, h = screen_size(m)
    q = qmp_for(m)
    try:
        abs_move(q, x, y, w, h)
        name = "right" if right else "left"
        for _ in range(max(1, min(3, count))):
            button(q, name, True)
            button(q, name, False)
            if count > 1:
                time.sleep(0.05)
    finally:
        q.close()


def pointer_move(m: dict, x: int, y: int) -> None:
    w, h = screen_size(m)
    q = qmp_for(m)
    try:
        abs_move(q, x, y, w, h)
    finally:
        q.close()


def pointer_drag(m: dict, x: int, y: int, x2: int, y2: int) -> None:
    w, h = screen_size(m)
    q = qmp_for(m)
    try:
        abs_move(q, x, y, w, h)
        button(q, "left", True)
        steps = max(2, min(30, int(((x2 - x) ** 2 + (y2 - y) ** 2) ** 0.5 / 12)))
        for i in range(1, steps + 1):
            t = i / steps
            abs_move(q, round(x + (x2 - x) * t), round(y + (y2 - y) * t), w, h)
            time.sleep(0.008)
        button(q, "left", False)
    finally:
        q.close()


def pointer_scroll(m: dict, x: int, y: int, dy: int, dx: int = 0) -> None:
    w, h = screen_size(m)
    q = qmp_for(m)
    try:
        abs_move(q, x, y, w, h)
        name = "wheel-up" if dy > 0 else "wheel-down"
        for _ in range(min(20, max(1, abs(dy) // 100 or 1))):
            button(q, name, True)
            button(q, name, False)
        name = "wheel-right" if dx > 0 else "wheel-left"
        for _ in range(min(20, max(0, abs(dx) // 100))):
            button(q, name, True)
            button(q, name, False)
    finally:
        q.close()


def screen_size(m: dict) -> tuple[int, int]:
    d = machine_dir(m["name"])
    frame = d / "frame.png"
    if frame.exists():
        try:
            return png_size(frame)
        except Exception:
            pass
    return int(m.get("width", 1280)), int(m.get("height", 800))


# ---------------------------------------------------------------------------
# QEMU lifecycle


def base_args(m: dict) -> list[str]:
    if m.get("args_mode") == "full":
        return [QEMU] + list(m.get("qemu_args", []))
    args = [
        QEMU, "-machine", "q35,accel=kvm", "-cpu", "host", "-m", str(m.get("memory", 4096)),
        "-smp", str(m.get("cpus", 4)), "-device", "qemu-xhci,id=xhci",
        "-device", "usb-kbd,bus=xhci.0", "-device", "usb-tablet,bus=xhci.0",
        "-device", "vmware-svga",
    ]
    return args + list(m.get("qemu_args", []))


def gpu_qemu_path() -> str:
    env = os.environ.get("TMAC_REIMS_QEMU")
    if env:
        return env
    current = Path.home() / ".local/share/reims-ctl/qemu/current/bin/qemu-system-x86_64"
    if current.exists():
        return str(current)
    raise TmacError("reims QEMU not found; set TMAC_REIMS_QEMU or install reims-ctl")


def gpu_args(m: dict) -> list[str]:
    args = base_args(m)
    args[0] = gpu_qemu_path()
    if "-machine" in args and args[args.index("-machine") + 1] == "q35":
        args[args.index("-machine") + 1] = "q35,memory-backend=mem"
    else:
        raise TmacError("GPU mode needs the q35 machine")
    mem = int(m.get("memory", 4096))
    args[1:1] = ["-object", f"memory-backend-memfd,share=on,id=mem,size={mem}M"]
    try:
        i = next(i for i in range(len(args) - 1) if args[i] == "-device" and args[i + 1] == "vmware-svga")
    except StopIteration:
        raise TmacError("GPU mode needs the vmware-svga placeholder")
    args[i + 1] = "apple-gfx-pci,id=gpu0"
    args[i + 2:i + 2] = ["-device", "apple-gfx-iosurface-pci"]
    return args


def start_machine(name: str, gpu: bool = False) -> dict:
    m = load_machine(name)
    if is_external(m):
        raise TmacError(
            f"VM '{name}' is external; start it with its owner "
            "(for example: reims-ctl start tahoe)"
        )
    if shutil.which(QEMU) is None:
        raise TmacError(f"{QEMU} not found; install QEMU or set TMAC_QEMU")
    d = machine_dir(name)
    if pid_alive(int(m.get("pid", 0) or 0)):
        return {"name": name, "state": "already-running"}
    stop_broker(m)
    for p in (d / "qmp.sock", d / "frame.png"):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    log_path = d / "runtime.log"
    log = open(log_path, "ab", buffering=0)
    args = (gpu_args(m) if gpu else base_args(m)) + [
        "-display", "none",
        "-qmp", f"unix:{d / 'qmp.sock'},server=on,wait=off",
        "-vnc", m.get("vnc", "127.0.0.1:1"),
    ]
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True)
    m["pid"] = proc.pid
    save_machine(m)
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            raise TmacError(f"QEMU exited during startup; see {log_path}")
        try:
            q = qmp_for(m)
            status = q.command("query-status")
            q.close()
            broker = start_broker(m)
            return {"name": name, "state": "running", "pid": proc.pid, "qemu": status, "broker": broker}
        except Exception:
            time.sleep(0.2)
    raise TmacError(f"QEMU did not open QMP; see {log_path}")


def stop_machine(name: str, force: bool = False) -> dict:
    m = load_machine(name)
    if is_external(m):
        raise TmacError(
            f"VM '{name}' is external; stop it with its owner "
            "(for example: reims-ctl stop tahoe)"
        )
    stop_broker(m)
    stop_share(m)
    pid = int(m.get("pid", 0) or 0)
    if not pid_alive(pid):
        return {"name": name, "state": "stopped"}
    try:
        q = qmp_for(m)
        if force:
            q.command("quit")
        else:
            q.command("system_powerdown")
        q.close()
    except Exception:
        if force:
            os.kill(pid, signal.SIGTERM)
    deadline = time.time() + (20 if not force else 5)
    while time.time() < deadline:
        if not pid_alive(pid):
            return {"name": name, "state": "stopped"}
        time.sleep(0.2)
    if force:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return {"name": name, "state": "stopping" if not force else "stopped"}


def machine_status(name: str) -> dict:
    m = load_machine(name)
    if is_external(m):
        try:
            q = qmp_for(m)
            status = q.command("query-status")
            q.close()
            w, h = screen_size(m)
            return {"name": name, "state": "running" if status.get("running") else "paused",
                    "external": True, "qmp_socket": m["qmp_socket"],
                    "width": w, "height": h}
        except Exception as e:
            socket_present = Path(m.get("qmp_socket", "")).exists()
            return {"name": name, "state": "unresponsive" if socket_present else "stopped",
                    "external": True, "qmp_socket": m.get("qmp_socket"),
                    "error": str(e)}
    pid = int(m.get("pid", 0) or 0)
    if not pid_alive(pid):
        return {"name": name, "state": "stopped"}
    try:
        q = qmp_for(m)
        status = q.command("query-status")
        q.close()
        w, h = screen_size(m)
        return {"name": name, "state": "running" if status.get("running") else "paused",
                "pid": pid, "width": w, "height": h}
    except Exception as e:
        return {"name": name, "state": "unresponsive", "pid": pid, "error": str(e)}


# ---------------------------------------------------------------------------
# Terminal graphics


def kitty_png(png: bytes, cols: int, rows: int, image_id: int = 1) -> str:
    """Kitty graphics protocol: PNG payload, placed at the current cursor."""
    data = base64.b64encode(png).decode("ascii")
    chunks = [data[i:i + 4096] for i in range(0, len(data), 4096)]
    # The first chunk carries the keys; every non-final chunk, including the
    # first, must set m=1 or kitty treats the image as complete too early.
    more = 1 if len(chunks) > 1 else 0
    first = f"\x1b_Ga=T,f=100,i={image_id},c={cols},r={rows},q=2,C=1,m={more};{chunks[0]}\x1b\\"
    rest = "".join(
        f"\x1b_Gm={0 if i == len(chunks) - 1 else 1};{c}\x1b\\"
        for i, c in enumerate(chunks[1:], start=1)
    )
    return first + rest


def iterm_png(png: bytes, cols: int, rows: int) -> str:
    data = base64.b64encode(png).decode("ascii")
    return f"\x1b]1337;File=inline=1;width={cols};height={rows};preserveAspectRatio=0:{data}\x07"


def read_ppm(path: Path) -> tuple[int, int, bytes]:
    """Decode QEMU's binary PPM (P6) without re-encoding it as PNG."""
    data = path.read_bytes()
    if not data.startswith(b"P6"):
        raise TmacError(f"not a P6 PPM: {path}")
    pos = 2
    values: list[int] = []
    while len(values) < 3:
        while pos < len(data) and data[pos] in b" \t\r\n":
            pos += 1
        if pos >= len(data):
            raise TmacError("truncated PPM header")
        if data[pos] == ord("#"):
            pos = data.find(b"\n", pos)
            if pos < 0:
                raise TmacError("truncated PPM comment")
            continue
        start = pos
        while pos < len(data) and data[pos] not in b" \t\r\n":
            pos += 1
        try:
            values.append(int(data[start:pos]))
        except ValueError:
            raise TmacError("invalid PPM header")
    width, height, maxval = values
    if maxval != 255 or width <= 0 or height <= 0:
        raise TmacError("unsupported PPM format")
    pos += 1  # exactly one whitespace byte before the raster
    expected = width * height * 3
    if len(data) - pos < expected:
        raise TmacError("truncated PPM raster")
    return width, height, data[pos:pos + expected]


def shm_write(name: str, payload: bytes) -> None:
    """Create a POSIX shared-memory object holding one raw frame."""
    path = "/dev/shm/" + name.lstrip("/")
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    try:
        os.ftruncate(fd, len(payload))
        with mmap.mmap(fd, len(payload)) as mm:
            mm[:] = payload
    finally:
        os.close(fd)


def kitty_shm(rgb: bytes, width: int, height: int, cols: int, rows: int,
              image_id: int, old_id: int = 0) -> tuple[str, str]:
    """Kitty `t=s` transport: no base64, no pty copy of 3 MB per frame."""
    name = f"/tmac.{os.getpid()}.{uuid.uuid4().hex}"
    shm_write(name, rgb)
    encoded_name = base64.b64encode(name.encode()).decode("ascii")
    head = (f"\x1b_Ga=T,f=24,s={width},v={height},t=s,S={len(rgb)},"
            f"i={image_id},c={cols},r={rows},q=2,C=1;{encoded_name}\x1b\\")
    delete = f"\x1b_Ga=d,d=I,i={old_id},q=2;\x1b\\" if old_id else ""
    return head + delete, "/dev/shm/" + name.lstrip("/")


def kitty_rgb(rgb: bytes, width: int, height: int, cols: int, rows: int,
              image_id: int, old_id: int = 0) -> str:
    """Direct Kitty transport (raw RGB) for terminals without shared memory."""
    data = base64.b64encode(rgb).decode("ascii")
    chunks = [data[i:i + 4096] for i in range(0, len(data), 4096)]
    more = 1 if len(chunks) > 1 else 0
    first = (f"\x1b_Ga=T,f=24,s={width},v={height},i={image_id},c={cols},r={rows},"
             f"q=2,C=1,m={more};{chunks[0]}\x1b\\")
    rest = "".join(
        f"\x1b_Gm={0 if i == len(chunks) - 1 else 1};{c}\x1b\\"
        for i, c in enumerate(chunks[1:], start=1)
    )
    delete = f"\x1b_Ga=d,d=I,i={old_id},q=2;\x1b\\" if old_id else ""
    return first + rest + delete


class Placement:
    def __init__(self, width: int, height: int, term_cols: int, term_rows: int,
                 cell_w: float = 8.0, cell_h: float = 16.0):
        self.width, self.height = width, height
        self.cell_w, self.cell_h = cell_w, cell_h
        avail_cols = max(1, term_cols)
        avail_rows = max(1, term_rows - 1)
        # Kitty downscales to c/r but does not upscale.  Never request a cell box
        # larger than the image's native pixel rectangle, or mouse mapping drifts.
        self.scale = min(1.0, avail_cols * cell_w / width, avail_rows * cell_h / height)
        self.pixel_width = width * self.scale
        self.pixel_height = height * self.scale
        self.cols = max(1, min(avail_cols, math.ceil(self.pixel_width / cell_w)))
        self.rows = max(1, min(avail_rows, math.ceil(self.pixel_height / cell_h)))
        self.col = max(1, 1 + (avail_cols - self.cols) // 2)
        self.row = max(1, 1 + (avail_rows - self.rows) // 2)

    def point(self, cell_col: int, cell_row: int, clamp: bool = False) -> tuple[int, int] | None:
        x = (cell_col - self.col + 0.5) * self.cell_w / self.scale
        y = (cell_row - self.row + 0.5) * self.cell_h / self.scale
        if not (0 <= x < self.width and 0 <= y < self.height):
            if not clamp:
                return None
        return int(max(0, min(self.width - 1, x))), int(max(0, min(self.height - 1, y)))


def query_terminal_metrics() -> tuple[float, float]:
    """Ask the terminal for its cell size; fall back to 8x16."""
    try:
        sys.stdout.write("\x1b[16t\x1b[14t")
        sys.stdout.flush()
    except Exception:
        return 8.0, 16.0
    deadline = time.time() + 0.25
    data = b""
    cell = (8.0, 16.0)
    while time.time() < deadline:
        r, _, _ = select.select([sys.stdin], [], [], 0.03)
        if not r:
            continue
        data += os.read(sys.stdin.fileno(), 4096)
        m = re.search(rb"\x1b\[6;(\d+);(\d+)t", data)
        if m:
            h, w = int(m.group(1)), int(m.group(2))
            if 4 <= w <= 64 and 6 <= h <= 96:
                cell = (float(w), float(h))
                break
    return cell

def term_size() -> tuple[int, int]:
    try:
        size = os.get_terminal_size(sys.stdout.fileno())
        return size.columns, size.lines
    except OSError:
        return 80, 24


class Viewer:
    def __init__(self, name: str, fps: float = 10.0, protocol: str = "kitty",
                 transport: str = "auto"):
        self.name = name
        self.machine = load_machine(name)
        self.fps = max(0.5, min(30.0, fps))
        self.protocol = protocol
        self.transport = transport
        self.frame_ppm = machine_dir(name) / "frame.ppm"
        self.frame_png = machine_dir(name) / "frame.png"
        self.placement: Placement | None = None
        self.stop = False
        self.last_hash = ""
        self.last_size = (0, 0)
        self.status = "connecting"
        self.use_shm = False
        self.image_id = 0
        self.first = True
        self.shm_paths: list[str] = []
        self.last_pointer: tuple[int, int] | None = None
        self.pressed: str | None = None
        self.cell_w, self.cell_h = 8.0, 16.0
        self.stats_enabled = os.environ.get("TMAC_VIEWER_STATS") == "1"
        self.stats = {"frames": 0, "captures": 0, "capture_ms": 0.0, "draw_ms": 0.0,
                      "started": time.time(), "transport": transport, "used_shm": False}
        self.qmp: QMP | None = None

    def probe_shared_memory(self) -> bool:
        name = f"/tmac.{os.getpid()}.probe.{uuid.uuid4().hex}"
        path = "/dev/shm/" + name.lstrip("/")
        try:
            shm_write(name, b"\x00\x00\x00\x00")
        except OSError:
            return False
        encoded = base64.b64encode(name.encode()).decode("ascii")
        sys.stdout.write(f"\x1b_Gi=31,s=1,v=1,a=q,t=s,f=32,S=4;{encoded}\x1b\\")
        sys.stdout.flush()
        deadline = time.time() + 0.4
        data = b""
        ok = False
        while time.time() < deadline:
            r, _, _ = select.select([sys.stdin], [], [], 0.05)
            if not r:
                continue
            data += os.read(sys.stdin.fileno(), 4096)
            if b"i=31" in data and b"OK" in data:
                ok = True
                break
            if b"i=31" in data:
                break
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return ok

    def _drop_old_shm(self) -> None:
        # Keep the newest two objects: the terminal may still be reading the last.
        while len(self.shm_paths) > 2:
            path = self.shm_paths.pop(0)
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def draw(self, rgb: bytes, w: int, h: int) -> None:
        cols, rows = term_size()
        p = Placement(w, h, cols, rows, self.cell_w, self.cell_h)
        self.placement = p
        out: list[str] = []
        if self.first or (cols, rows) != self.last_size:
            out += ["\x1b[2J\x1b[H", "\x1b_Ga=d,d=A\x1b\\"]
            self.first = False
            old_id = 0
        else:
            old_id = self.image_id
        self.last_size = (cols, rows)
        self.image_id = 1 if self.image_id >= 100000 else self.image_id + 1
        out.append(f"\x1b[{p.row};{p.col}H")
        if self.protocol == "kitty":
            if self.use_shm:
                body, path = kitty_shm(rgb, w, h, p.cols, p.rows, self.image_id, old_id)
                self.shm_paths.append(path)
                self._drop_old_shm()
            else:
                body = kitty_rgb(rgb, w, h, p.cols, p.rows, self.image_id, old_id)
        else:
            if Image is None:
                raise TmacError("iTerm2 transport needs Pillow")
            buf = io.BytesIO()
            Image.frombytes("RGB", (w, h), rgb).save(buf, "PNG")
            body = iterm_png(buf.getvalue(), p.cols, p.rows)
        out.append(body)
        label = f" {self.name}  {w}x{h}  {self.status}  Ctrl+] quit "
        out.append(f"\x1b[{rows};1H\x1b[2K\x1b[7m{label[:cols].ljust(cols)}\x1b[0m")
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    def run(self) -> None:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise TmacError("view needs an interactive terminal")
        old = termios.tcgetattr(sys.stdin.fileno())
        previous_term = signal.getsignal(signal.SIGTERM)

        def stop_on_signal(signum, frame):
            self.stop = True

        signal.signal(signal.SIGTERM, stop_on_signal)
        try:
            tty.setraw(sys.stdin.fileno())
            self.cell_w, self.cell_h = query_terminal_metrics()
            sys.stdout.write("\x1b[?1049h\x1b[?25l\x1b[?1003h\x1b[?1006h")
            sys.stdout.flush()
            if self.protocol == "kitty":
                if self.transport in ("shm", "auto"):
                    self.use_shm = self.probe_shared_memory() if self.transport == "auto" else True
                self.stats["used_shm"] = self.use_shm
            while not self.stop:
                loop_start = time.perf_counter()
                try:
                    capture_start = time.perf_counter()
                    if self.protocol == "iterm":
                        screendump(self.machine, self.frame_png, fmt="png")
                        png = self.frame_png.read_bytes()
                        w, h = png_size(self.frame_png)
                        digest = hashlib.sha256(png).hexdigest()
                        self.stats["captures"] += 1
                        self.stats["capture_ms"] += (time.perf_counter() - capture_start) * 1000
                        if digest != self.last_hash:
                            self.last_hash = digest
                            self.status = "live"
                            # Reuse draw by decoding to RGB only when changed.
                            if Image is None:
                                raise TmacError("iTerm2 transport needs Pillow")
                            rgb = Image.open(io.BytesIO(png)).convert("RGB").tobytes()
                            draw_start = time.perf_counter()
                            self.draw(rgb, w, h)
                            self.stats["draw_ms"] += (time.perf_counter() - draw_start) * 1000
                            self.stats["frames"] += 1
                    else:
                        screendump(self.machine, self.frame_ppm, fmt="ppm")
                        w, h, rgb = read_ppm(self.frame_ppm)
                        digest = hashlib.sha256(rgb).hexdigest()
                        cols, rows = term_size()
                        self.stats["captures"] += 1
                        self.stats["capture_ms"] += (time.perf_counter() - capture_start) * 1000
                        if digest != self.last_hash or (cols, rows) != self.last_size or self.first:
                            self.last_hash = digest
                            self.status = "live"
                            draw_start = time.perf_counter()
                            self.draw(rgb, w, h)
                            self.stats["draw_ms"] += (time.perf_counter() - draw_start) * 1000
                            self.stats["frames"] += 1
                        else:
                            self.status = "idle"
                except Exception as e:
                    self.status = f"waiting: {e}"[:60]
                    time.sleep(0.5)
                # Keep the requested cadence; capture time counts against the frame budget.
                remaining = (1.0 / self.fps) - (time.perf_counter() - loop_start)
                self.poll_input(max(0.001, remaining))
        finally:
            try:
                sys.stdout.write("\x1b[?1006l\x1b[?1003l\x1b_Ga=d,d=A\x1b\\\x1b[?25h\x1b[?1049l")
                sys.stdout.flush()
            except Exception:
                pass
            for path in self.shm_paths:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
            signal.signal(signal.SIGTERM, previous_term)
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old)
            if self.stats_enabled:
                elapsed = max(1e-6, time.time() - self.stats["started"])
                self.stats["seconds"] = round(elapsed, 3)
                self.stats["fps"] = round(self.stats["frames"] / elapsed, 2)
                self.stats["capture_fps"] = round(self.stats["captures"] / elapsed, 2)
                (machine_dir(self.name) / "viewer-stats.json").write_text(
                    json.dumps(self.stats, indent=2, sort_keys=True) + "\n")

    def poll_input(self, timeout: float) -> None:
        r, _, _ = select.select([sys.stdin], [], [], timeout)
        if not r:
            return
        data = os.read(sys.stdin.fileno(), 4096)
        self.handle(data)

    def handle(self, data: bytes) -> None:
        i = 0
        text: list[str] = []

        def flush() -> None:
            if text:
                self.type_text("".join(text))
                text.clear()

        while i < len(data):
            b = data[i]
            if b == 0x1d:  # Ctrl+]
                flush()
                self.stop = True
                return
            if b == 0x1b:
                m = re.match(rb"\x1b\[<(\d+);(\d+);(\d+)([Mm])", data[i:])
                if m:
                    flush()
                    btn, cx, cy = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    release = m.group(4) == b"m"
                    i += m.end()
                    self.mouse(btn, cx, cy, release)
                    continue
                keys = {b"\x1b[A": "up", b"\x1b[B": "down", b"\x1b[C": "right", b"\x1b[D": "left",
                        b"\x1b[H": "home", b"\x1b[F": "end", b"\x1b[3~": "delete",
                        b"\x1b[5~": "pgup", b"\x1b[6~": "pgdn", b"\x1b[Z": "shift+tab"}
                for prefix, key in sorted(keys.items(), key=lambda kv: -len(kv[0])):
                    if data.startswith(prefix, i):
                        flush()
                        self.key(key)
                        i += len(prefix)
                        break
                else:
                    flush()
                    self.key("escape")
                    i += 1
                continue
            if b in (10, 13):
                flush(); self.key("return"); i += 1; continue
            if b == 9:
                flush(); self.key("tab"); i += 1; continue
            if b in (8, 127):
                flush(); self.key("backspace"); i += 1; continue
            if 1 <= b <= 26:
                flush(); self.key("ctrl+" + chr(b + 96)); i += 1; continue
            length = 1 if b < 0x80 else 2 if 0xc2 <= b <= 0xdf else 3 if 0xe0 <= b <= 0xef else 4 if 0xf0 <= b <= 0xf4 else 0
            if not length:
                i += 1
                continue
            if i + length > len(data):
                break
            text.append(data[i:i + length].decode("utf-8", "replace"))
            i += length
        flush()

    def key(self, chord: str) -> None:
        try:
            q = qmp_for(self.machine)
            try:
                key_chord(q, chord)
            finally:
                q.close()
        except Exception as e:
            self.status = f"key error: {e}"[:60]

    def type_text(self, text: str) -> None:
        try:
            q = qmp_for(self.machine)
            try:
                text_events(q, text)
            finally:
                q.close()
        except Exception as e:
            self.status = f"type error: {e}"[:60]

    def mouse(self, btn: int, cx: int, cy: int, release: bool) -> None:
        if not self.placement:
            return
        pt = self.placement.point(cx, cy, clamp=True)
        if not pt:
            return
        x, y = pt
        if btn & 64:
            self.scroll(btn, x, y)
            return
        index = btn & 3
        name = "left" if index == 0 else "middle" if index == 1 else "right"
        motion = bool(btn & 32)
        try:
            q = qmp_for(self.machine)
            try:
                w, h = screen_size(self.machine)
                if motion:
                    if self.last_pointer != (x, y):
                        abs_move(q, x, y, w, h)
                        self.last_pointer = (x, y)
                    return
                if release:
                    button(q, name, False)
                    self.pressed = None
                else:
                    abs_move(q, x, y, w, h)
                    button(q, name, True)
                    self.pressed = name
                self.last_pointer = (x, y)
            finally:
                q.close()
        except Exception as e:
            self.status = f"mouse error: {e}"[:60]
    def scroll(self, btn: int, x: int, y: int) -> None:
        try:
            pointer_scroll(self.machine, x, y, 1 if (btn & 1) == 0 else -1)
        except Exception as e:
            self.status = f"scroll error: {e}"[:60]


# ---------------------------------------------------------------------------
# MCP server (stdio JSON-RPC)


TOOLS = [
    {"name": "screenshot", "description": "Capture the virtual Mac's screen. Coordinates for other tools are pixels in this image.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "annotations": {"readOnlyHint": True}},
    {"name": "click", "description": "Click at a point. clicks: 2 double-clicks, 3 triple-clicks.",
     "inputSchema": {"type": "object", "properties": {
         "x": {"type": "integer", "minimum": 0}, "y": {"type": "integer", "minimum": 0},
         "button": {"type": "string", "enum": ["left", "right"]},
         "clicks": {"type": "integer", "minimum": 1, "maximum": 3},
         "screenshot": {"type": "boolean"}}, "required": ["x", "y"], "additionalProperties": False}},
    {"name": "move", "description": "Move the pointer.",
     "inputSchema": {"type": "object", "properties": {
         "x": {"type": "integer", "minimum": 0}, "y": {"type": "integer", "minimum": 0},
         "screenshot": {"type": "boolean"}}, "required": ["x", "y"], "additionalProperties": False}},
    {"name": "drag", "description": "Press at (x, y), drag to (toX, toY), release.",
     "inputSchema": {"type": "object", "properties": {
         "x": {"type": "integer", "minimum": 0}, "y": {"type": "integer", "minimum": 0},
         "toX": {"type": "integer", "minimum": 0}, "toY": {"type": "integer", "minimum": 0},
         "screenshot": {"type": "boolean"}}, "required": ["x", "y", "toX", "toY"], "additionalProperties": False}},
    {"name": "scroll", "description": "Scroll at a point. Negative dy scrolls content down.",
     "inputSchema": {"type": "object", "properties": {
         "x": {"type": "integer", "minimum": 0}, "y": {"type": "integer", "minimum": 0},
         "dy": {"type": "integer", "minimum": -1000, "maximum": 1000},
         "dx": {"type": "integer", "minimum": -1000, "maximum": 1000},
         "screenshot": {"type": "boolean"}}, "required": ["x", "y", "dy"], "additionalProperties": False}},
    {"name": "type", "description": "Type text into the focused field. ASCII uses QMP keys; Unicode uses the guest clipboard and Cmd+V (requires SSH key).",
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string"}, "screenshot": {"type": "boolean"}},
         "required": ["text"], "additionalProperties": False}},
    {"name": "key", "description": "Press a key or chord: return, tab, escape, up, cmd+space, cmd+c, cmd+shift+...",
     "inputSchema": {"type": "object", "properties": {
         "key": {"type": "string"}, "screenshot": {"type": "boolean"}},
         "required": ["key"], "additionalProperties": False}},
    {"name": "status", "description": "Report whether the VM is running and its screen size.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "annotations": {"readOnlyHint": True}},
]


class MCPServer:
    def __init__(self, name: str):
        self.name = name

    def send(self, message: dict) -> None:
        data = json.dumps(message, separators=(",", ":")).encode() + b"\n"
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()

    def screenshot(self) -> list[dict]:
        m = load_machine(self.name)
        frame = screendump(m)
        png = frame.read_bytes()
        w, h = png_size(frame)
        if Image is not None:
            im = Image.open(io.BytesIO(png)).convert("RGB")
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=85)
            data, mime = buf.getvalue(), "image/jpeg"
        else:
            data, mime = png, "image/png"
        return [{"type": "image", "data": base64.b64encode(data).decode(), "mimeType": mime},
                {"type": "text", "text": f"Screen: {w}×{h}. Use these pixel coordinates."}]

    def call(self, tool: str, a: dict) -> list[dict]:
        m = load_machine(self.name)
        if machine_status(self.name)["state"] not in ("running", "paused"):
            raise TmacError(f"VM '{self.name}' is stopped. Start it with: tmac start {self.name}")
        if tool == "status":
            s = machine_status(self.name)
            return [{"type": "text", "text": "\n".join(f"{k}: {v}" for k, v in sorted(s.items()))}]
        if tool == "screenshot":
            return self.screenshot()
        if tool == "click":
            pointer_click(m, int(a["x"]), int(a["y"]), int(a.get("clicks", 1)), a.get("button") == "right")
        elif tool == "move":
            pointer_move(m, int(a["x"]), int(a["y"]))
        elif tool == "drag":
            pointer_drag(m, int(a["x"]), int(a["y"]), int(a["toX"]), int(a["toY"]))
        elif tool == "scroll":
            pointer_scroll(m, int(a["x"]), int(a["y"]), int(a["dy"]), int(a.get("dx", 0)))
        elif tool == "type":
            type_or_paste(m, a["text"])
        elif tool == "key":
            q = qmp_for(m)
            try:
                key_chord(q, a["key"])
            finally:
                q.close()
        else:
            raise TmacError(f"unknown tool: {tool}")
        content = [{"type": "text", "text": f"Done: {tool}."}]
        if a.get("screenshot"):
            time.sleep(0.4)
            content += self.screenshot()
        return content

    def run(self) -> None:
        for line in sys.stdin.buffer:
            try:
                msg = json.loads(line)
            except Exception:
                self.send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
                continue
            if "id" not in msg:
                continue
            mid = msg["id"]
            method = msg.get("method")
            params = msg.get("params") or {}
            try:
                if method == "initialize":
                    self.send({"jsonrpc": "2.0", "id": mid, "result": {
                        "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "tmac", "version": "0.2.0"},
                        "instructions": "Controls a separate virtual Mac through QEMU. Call screenshot first; coordinates are pixels in that screenshot."}})
                elif method == "ping":
                    self.send({"jsonrpc": "2.0", "id": mid, "result": {}})
                elif method == "tools/list":
                    self.send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
                elif method == "tools/call":
                    try:
                        content = self.call(params.get("name", ""), params.get("arguments") or {})
                        self.send({"jsonrpc": "2.0", "id": mid, "result": {"content": content}})
                    except Exception as e:
                        self.send({"jsonrpc": "2.0", "id": mid, "result": {
                            "content": [{"type": "text", "text": str(e)}], "isError": True}})
                else:
                    self.send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found"}})
            except Exception as e:
                self.send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}})


# ---------------------------------------------------------------------------
# CLI


def cmd_create(args: argparse.Namespace) -> None:
    d = machine_dir(args.name)
    d.mkdir(parents=True, exist_ok=True)
    qemu_args: list[str] = []
    if args.iso:
        qemu_args += ["-cdrom", str(Path(args.iso).resolve()), "-boot", "d"]
    if args.disk:
        disk = Path(args.disk).resolve()
        if not disk.exists():
            subprocess.run(["qemu-img", "create", "-f", "qcow2", str(disk), f"{args.disk_size}G"], check=True)
        qemu_args += ["-drive", f"id=MacHDD,if=none,file={disk},format=qcow2",
                      "-device", "ide-hd,bus=sata.4,drive=MacHDD"]
    if args.osx_kvm:
        # Reproduce OSX-KVM's recommended x86-64 OpenCore command. The caller
        # supplies OpenCore.qcow2 + BaseSystem.img in the OSX-KVM checkout and a
        # writable disk. This is the exact command verified by worker2.
        root = Path(args.osx_kvm).resolve()
        disk = Path(args.disk).resolve() if args.disk else root / "mac_hdd_ng.img"
        if not disk.exists():
            subprocess.run(["qemu-img", "create", "-f", "qcow2", str(disk), f"{args.disk_size}G"], check=True)
        osk = os.environ.get("TMAC_OSK", "")
        if not osk:
            raise TmacError("TMAC_OSK is required for a managed macOS create; supply your own Apple SMC OSK")
        qemu_args = [
            "-enable-kvm", "-m", str(args.memory),
            "-cpu", "host,kvm=on,vendor=GenuineIntel,+invtsc,vmware-cpuid-freq=on,+ssse3,+sse4.2,+popcnt,+avx,+aes,+xsave,+xsaveopt",
            "-machine", "q35", "-global", "ICH9-LPC.acpi-pci-hotplug-with-bridge-support=off", "-device", "qemu-xhci,id=xhci",
            "-device", "usb-kbd,bus=xhci.0", "-device", "usb-tablet,bus=xhci.0",
            "-smp", args.smp,
            "-device", f"isa-applesmc,osk={osk}",
            "-drive", f"if=pflash,format=raw,readonly=on,file={root / 'OVMF_CODE_4M.fd'}",
            "-drive", f"if=pflash,format=raw,file={root / 'OVMF_VARS-1920x1080.fd'}",
            "-smbios", "type=2", "-device", "ich9-intel-hda", "-device", "hda-duplex",
            "-device", "ich9-ahci,id=sata",
            "-drive", f"id=OpenCoreBoot,if=none,snapshot=on,format=qcow2,file={root / 'OpenCore/OpenCore.qcow2'}",
            "-device", "ide-hd,bus=sata.2,drive=OpenCoreBoot",
            "-drive", f"id=InstallMedia,if=none,file={root / 'BaseSystem.img'},format=raw",
            "-device", "ide-hd,bus=sata.3,drive=InstallMedia",
            "-drive", f"id=MacHDD,if=none,file={disk},format=qcow2",
            "-device", "ide-hd,bus=sata.4,drive=MacHDD",
            "-netdev", "user,id=net0,hostfwd=tcp::2222-:22",
            "-device", "virtio-net-pci,netdev=net0,id=net0,mac=52:54:00:c9:18:27",
            "-device", "vmware-svga",
            "-rtc", "base=localtime,clock=host,driftfix=slew",
            "-serial", f"file:{d / 'serial.log'}",
        ]
    if args.qemu_args_file:
        qemu_args = json.loads(Path(args.qemu_args_file).read_text())
    m = {
        "name": args.name, "memory": args.memory, "cpus": args.cpus,
        "width": args.width, "height": args.height, "vnc": args.vnc,
        "qemu_args": qemu_args, "created": time.time(), "mode": args.mode,
        "smp": args.smp,
        "args_mode": "full" if (args.osx_kvm or args.qemu_args_file) else "base",
    }
    save_machine(m)
    print(json.dumps({"name": args.name, "directory": str(d), "mode": args.mode,
                      "memory": args.memory, "cpus": args.cpus, "qemu_args": len(qemu_args)}, indent=2))


def machine_disk(m: dict) -> Path:
    for arg in m.get("qemu_args", []):
        if arg.startswith("id=MacHDD,"):
            for part in arg.split(","):
                if part.startswith("file="):
                    return Path(part[5:])
    raise TmacError("This VM has no MacHDD disk in machine.json")


def vnc_url(m: dict) -> str:
    spec = m.get("vnc", "127.0.0.1:1")
    host, _, display = spec.rpartition(":")
    host = host or "127.0.0.1"
    if display.isdigit():
        port = 5900 + int(display) if int(display) < 100 else int(display)
    else:
        port = 5900
    return f"vnc://{host}:{port}"


def stop_for_disk(name: str) -> dict:
    status = machine_status(name)
    if status["state"] != "stopped":
        raise TmacError(f"Stop the VM first: tmac stop {name}")
    return status


def cmd_config(args: argparse.Namespace) -> None:
    m = load_machine(args.name)
    qargs = list(m.get("qemu_args", []))
    if args.memory is not None:
        if "-m" not in qargs:
            raise TmacError("This VM has no -m argument")
        qargs[qargs.index("-m") + 1] = str(args.memory)
        m["memory"] = args.memory
    if args.smp is not None:
        if "-smp" not in qargs:
            raise TmacError("This VM has no -smp argument")
        qargs[qargs.index("-smp") + 1] = args.smp
        m["smp"] = args.smp
    if args.mac_hdd_device is not None:
        for i, arg in enumerate(qargs):
            if arg == "ide-hd,bus=sata.4,drive=MacHDD":
                qargs[i] = args.mac_hdd_device
                break
        else:
            raise TmacError("MacHDD device argument not found")
    if args.audio:
        out = []
        skip = False
        for a in qargs:
            if skip:
                skip = False
                continue
            if a == "-audiodev":
                skip = True
                continue
            out.append(a)
        qargs = out
        if args.audio == "pa":
            try:
                hda = next(i for i, a in enumerate(qargs) if a.startswith("hda-duplex"))
                qargs[hda] = "hda-duplex,audiodev=snd0"
            except StopIteration:
                raise TmacError("This VM has no hda-duplex device")
            hda_bus = qargs.index("ich9-intel-hda")
            if hda_bus and qargs[hda_bus - 1] == "-device":
                hda_bus -= 1
            qargs[hda_bus:hda_bus] = ["-audiodev", "pa,id=snd0"]
        else:  # USB Audio Class: native macOS driver, no AppleALC layout needed
            try:
                xhci = next(i for i, a in enumerate(qargs) if a.startswith("qemu-xhci"))
            except StopIteration:
                raise TmacError("This VM has no qemu-xhci controller")
            if xhci and qargs[xhci - 1] == "-device":
                xhci -= 1
            qargs[xhci:xhci] = ["-audiodev", "pa,id=snd0"]
            for i, a in enumerate(qargs):
                if a.startswith("usb-audio"):
                    qargs[i] = "usb-audio,bus=xhci.0,audiodev=snd0,multi=on"
                    break
            else:
                qargs.extend(["-device", "usb-audio,bus=xhci.0,audiodev=snd0,multi=on"])
        m["audio"] = args.audio
    if args.disk_cache is not None:
        for i, arg in enumerate(qargs):
            if arg.startswith("id=MacHDD,"):
                parts = [p for p in arg.split(",") if not p.startswith("cache=")]
                parts.append(f"cache={args.disk_cache}")
                qargs[i] = ",".join(parts)
                break
    m["qemu_args"] = qargs
    save_machine(m)
    print(json.dumps({"name": args.name, "memory": m.get("memory"), "smp": m.get("smp")}, indent=2))


def cmd_snapshot(args: argparse.Namespace) -> None:
    m = load_machine(args.name)
    stop_for_disk(args.name)
    disk = machine_disk(m)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.snapshot):
        raise TmacError("snapshot names are 1-64 letters, numbers, underscores or hyphens")
    subprocess.run(["qemu-img", "snapshot", "-c", args.snapshot, str(disk)], check=True)
    print(json.dumps({"name": args.name, "snapshot": args.snapshot, "disk": str(disk)}, indent=2))


def cmd_snapshots(args: argparse.Namespace) -> None:
    m = load_machine(args.name)
    disk = machine_disk(m)
    out = subprocess.run(["qemu-img", "snapshot", "-l", "-U", str(disk)],
                         check=True, capture_output=True, text=True).stdout
    print(out, end="")


def cmd_rollback(args: argparse.Namespace) -> None:
    m = load_machine(args.name)
    stop_for_disk(args.name)
    disk = machine_disk(m)
    subprocess.run(["qemu-img", "snapshot", "-a", args.snapshot, str(disk)], check=True)
    print(json.dumps({"name": args.name, "snapshot": args.snapshot, "rolled_back": True}, indent=2))


def cmd_show(args: argparse.Namespace) -> None:
    m = load_machine(args.name)
    url = vnc_url(m)
    print(json.dumps({"name": args.name, "vnc": url}, indent=2))
    if args.no_open:
        return
    for command in (["vncviewer", url], ["gvncviewer", url], ["remmina", "-c", url], ["xdg-open", url]):
        if shutil.which(command[0]):
            subprocess.Popen(command)
            return


def cmd_ls(args: argparse.Namespace) -> None:
    root = HOME / "machines"
    out = []
    for d in sorted(root.iterdir() if root.exists() else []):
        if (d / "machine.json").exists():
            out.append(machine_status(d.name))
    print(json.dumps(out, indent=2))


def guest_port(m: dict) -> int:
    if int(m.get("ssh_port", 0) or 0):
        return int(m["ssh_port"])
    for arg in m.get("qemu_args", []):
        hit = re.search(r"hostfwd=tcp::(\d+)-:22", arg)
        if hit:
            return int(hit.group(1))
    return 2222


def guest_ssh(m: dict, command: str, data: bytes | None = None, timeout: int = 15) -> subprocess.CompletedProcess:
    key = Path(os.environ.get("TMAC_SSH_KEY", Path.home() / ".ssh" / "tmac_guest"))
    if not key.exists():
        raise TmacError(f"guest SSH key not found: {key}; install it in ~/.ssh/authorized_keys")
    known = machine_dir(m["name"]) / "ssh_known_hosts"
    return subprocess.run([
        "ssh", "-i", str(key), "-p", str(guest_port(m)),
        "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={known}",
        "-o", "BatchMode=yes", "-o", "LogLevel=ERROR",
        f"{m.get('guest_user', 'guest')}@127.0.0.1", command,
    ], input=data, capture_output=True, timeout=timeout)


def paste_text(m: dict, text: str) -> None:
    payload = base64.b64encode(text.encode()).decode()
    proc = guest_ssh(m, f"printf %s {payload} | base64 -D | LC_ALL=en_US.UTF-8 pbcopy")
    if proc.returncode:
        raise TmacError("guest clipboard failed: " + proc.stderr.decode(errors="replace")[:300])
    q = qmp_for(m)
    try:
        key_chord(q, "cmd+v")
    finally:
        q.close()


def type_or_paste(m: dict, text: str) -> None:
    # QMP key injection is lossy for long text on macOS (dropped key-ups cause
    # repeats, dropped key-downs cause missing characters). The guest clipboard
    # is exact and preserves Unicode, so prefer it whenever the SSH key exists.
    key = Path(os.environ.get("TMAC_SSH_KEY", Path.home() / ".ssh" / "tmac_guest"))
    if key.exists():
        try:
            paste_text(m, text)
            return
        except TmacError:
            if any(ord(c) > 127 for c in text):
                raise
    if any(ord(c) > 127 for c in text):
        raise TmacError("Unicode typing needs the guest SSH key for clipboard paste")
    q = qmp_for(m)
    try:
        text_events(q, text)
    finally:
        q.close()


def share_pid_path(m: dict) -> Path:
    return machine_dir(m["name"]) / "share.pid"


def cmd_share(args: argparse.Namespace) -> None:
    m = load_machine(args.name)
    pidfile = share_pid_path(m)
    if args.stop:
        try:
            pid = int(pidfile.read_text().strip())
            if pid_alive(pid):
                os.kill(pid, signal.SIGTERM)
        except (FileNotFoundError, ValueError, ProcessLookupError):
            pass
        try:
            pidfile.unlink()
        except FileNotFoundError:
            pass
        print(json.dumps({"name": args.name, "share": "stopped"}, indent=2))
        return
    if not args.path:
        raise TmacError("share requires --path (unless --stop)")
    root = Path(args.path).expanduser().resolve()
    if not root.is_dir():
        raise TmacError(f"share path is not a directory: {root}")
    exe = Path(os.environ.get("TMAC_WSGIDAV", "wsgidav"))
    if not exe.exists() and not shutil.which(str(exe)):
        raise TmacError("wsgidav not found; install it in a venv and set TMAC_WSGIDAV")
    port = args.port
    log = open(machine_dir(m["name"]) / "share.log", "ab", buffering=0)
    proc = subprocess.Popen([str(exe), f"--host=0.0.0.0", f"--port={port}",
                             f"--root={root}", "--auth=anonymous"],
                            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True)
    pidfile.write_text(str(proc.pid) + "\n")
    time.sleep(0.6)
    if proc.poll() is not None:
        raise TmacError("wsgidav exited; see share.log")
    print(json.dumps({"name": args.name, "share": str(root), "port": port,
                      "guest_url": f"http://10.0.2.2:{port}/"}, indent=2))


def cmd_action(args: argparse.Namespace) -> None:
    m = load_machine(args.name)
    if args.verb in ("screenshot", "snapshot"):
        frame = screendump(m)
        out = Path(args.output or f"tmac-{args.name}.png").expanduser()
        shutil.copyfile(frame, out)
        w, h = png_size(out)
        print(json.dumps({"image": str(out), "width": w, "height": h}, indent=2)); return
    if args.verb == "status":
        print(json.dumps(machine_status(args.name), indent=2)); return
    if args.verb == "click":
        pointer_click(m, args.x, args.y, args.clicks, args.right)
    elif args.verb == "move":
        pointer_move(m, args.x, args.y)
    elif args.verb == "drag":
        pointer_drag(m, args.x, args.y, args.x2, args.y2)
    elif args.verb == "scroll":
        pointer_scroll(m, args.x, args.y, args.dy, args.dx)
    elif args.verb == "type":
        type_or_paste(m, args.text)
    elif args.verb == "key":
        q = qmp_for(m)
        try:
            key_chord(q, args.key)
        finally:
            q.close()
    else:
        raise TmacError(f"unknown action: {args.verb}")
    print(json.dumps({"ok": True, "action": args.verb}, indent=2))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tmac", description="Linux QEMU port of terminal-mac's control plane")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="create a VM record")
    c.add_argument("name")
    c.add_argument("--memory", type=int, default=4096)
    c.add_argument("--cpus", type=int, default=4)
    c.add_argument("--width", type=int, default=1280)
    c.add_argument("--height", type=int, default=800)
    c.add_argument("--disk", help="qcow2 disk path (created sparse if absent)")
    c.add_argument("--disk-size", type=int, default=40, help="GiB for a new disk")
    c.add_argument("--iso", help="Linux ISO for smoke tests")
    c.add_argument("--osx-kvm", help="OSX-KVM checkout providing OpenCore/OVMF/BaseSystem.img")
    c.add_argument("--qemu-args-file", help="JSON array of extra QEMU arguments")
    c.add_argument("--vnc", default="127.0.0.1:1")
    c.add_argument("--smp", default="4,cores=2,threads=2,sockets=1",
                   help="QEMU -smp value; must match the AMD core-count patch")
    c.add_argument("--mode", default="manual", choices=["manual", "macos", "linux"])
    c.set_defaults(func=cmd_create)

    a = sub.add_parser("attach", help="register an already-running VM by its QMP socket")
    a.add_argument("name")
    a.add_argument("--qmp-socket", required=True, help="Unix socket of the running QEMU/Reims VM")
    a.add_argument("--width", type=int, default=1280)
    a.add_argument("--height", type=int, default=800)
    a.add_argument("--ssh-port", type=int, default=0, help="host-forwarded guest SSH port")
    a.add_argument("--guest-user", default="guest", help="guest SSH username for Unicode paste")
    a.add_argument("--force", action="store_true", help="replace an existing machine record")
    a.set_defaults(func=lambda a: print(json.dumps(attach_machine(
        a.name, a.qmp_socket, a.width, a.height, a.ssh_port, a.guest_user, a.force), indent=2)))

    s = sub.add_parser("start"); s.add_argument("name"); s.add_argument("--gpu", action="store_true")
    s.set_defaults(func=lambda a: print(json.dumps(start_machine(a.name, a.gpu), indent=2)))
    s = sub.add_parser("stop"); s.add_argument("name"); s.add_argument("--force", action="store_true"); s.set_defaults(func=lambda a: print(json.dumps(stop_machine(a.name, a.force), indent=2)))
    s = sub.add_parser("status"); s.add_argument("name"); s.set_defaults(func=lambda a: print(json.dumps(machine_status(a.name), indent=2)))
    s = sub.add_parser("ls"); s.set_defaults(func=cmd_ls)

    s = sub.add_parser("action", help="guest action (screenshot/click/move/drag/scroll/type/key/status)")
    s.add_argument("name"); s.add_argument("verb")
    s.add_argument("x", nargs="?", type=int); s.add_argument("y", nargs="?", type=int)
    s.add_argument("x2", nargs="?", type=int); s.add_argument("y2", nargs="?", type=int)
    s.add_argument("--right", action="store_true"); s.add_argument("--clicks", type=int, default=1)
    s.add_argument("--dy", type=int, default=0); s.add_argument("--dx", type=int, default=0)
    s.add_argument("--text", default=""); s.add_argument("--key", default="")
    s.add_argument("--output")
    s.set_defaults(func=cmd_action)

    s = sub.add_parser("config"); s.add_argument("name")
    s.add_argument("--memory", type=int)
    s.add_argument("--smp")
    s.add_argument("--mac-hdd-device")
    s.add_argument("--disk-cache", choices=["writeback", "writethrough", "unsafe", "none", "directsync"])
    s.add_argument("--audio", choices=["pa", "usb"])
    s.set_defaults(func=cmd_config)
    s = sub.add_parser("share"); s.add_argument("name"); s.add_argument("--path")
    s.add_argument("--port", type=int, default=8080); s.add_argument("--stop", action="store_true")
    s.set_defaults(func=cmd_share)
    s = sub.add_parser("snapshot"); s.add_argument("name"); s.add_argument("snapshot"); s.set_defaults(func=cmd_snapshot)
    s = sub.add_parser("snapshots"); s.add_argument("name"); s.set_defaults(func=cmd_snapshots)
    s = sub.add_parser("rollback"); s.add_argument("name"); s.add_argument("snapshot"); s.set_defaults(func=cmd_rollback)
    s = sub.add_parser("show"); s.add_argument("name"); s.add_argument("--no-open", action="store_true"); s.set_defaults(func=cmd_show)
    s = sub.add_parser("vnc"); s.add_argument("name"); s.set_defaults(func=cmd_show, no_open=True)
    v = sub.add_parser("view"); v.add_argument("name"); v.add_argument("--fps", type=float, default=10.0)
    v.add_argument("--protocol", choices=["kitty", "iterm"], default="kitty")
    v.add_argument("--transport", choices=["auto", "shm", "direct"], default="auto")
    v.set_defaults(func=lambda a: Viewer(a.name, a.fps, a.protocol, a.transport).run())

    m = sub.add_parser("mcp"); m.add_argument("name", nargs="?", default="default"); m.set_defaults(func=lambda a: MCPServer(a.name).run())
    return p


def main() -> None:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except TmacError as e:
        print(f"tmac: {e}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
