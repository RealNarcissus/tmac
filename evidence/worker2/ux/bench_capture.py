#!/usr/bin/env python3
"""Latency benchmark: QMP screendump PNG vs PPM, 20 frames each.

Also reports the bytes that each design must push into the terminal:
  * PNG path: base64(PNG) in 4096-char kitty chunks
  * PPM path: one tiny escape (shm name) per frame
Writes JSON to stdout and optionally --json PATH.
"""
import argparse
import base64
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qmpfast  # noqa: E402


def bench(q, fmt, frames, workdir):
    lat, sizes, read_ms = [], [], []
    tex = {"ppm": "ppm", "png": "png"}[fmt]
    path = os.path.join(workdir, f"bench-capture.{tex}")
    for _ in range(frames):
        t0 = time.perf_counter()
        dt, size = q.screendump(path, fmt=fmt)
        t1 = time.perf_counter()
        with open(path, "rb") as fh:
            fh.read()
        t2 = time.perf_counter()
        lat.append(dt * 1000)
        read_ms.append((t2 - t1) * 1000)
        sizes.append(size)
    return {
        "format": fmt,
        "frames": frames,
        "capture_ms_median": round(statistics.median(lat), 2),
        "capture_ms_min": round(min(lat), 2),
        "capture_ms_max": round(max(lat), 2),
        "read_ms_median": round(statistics.median(read_ms), 2),
        "file_bytes": sizes[0],
        "note": "capture_ms excludes the Python file read",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--machine-json", required=True)
    ap.add_argument("--frames", type=int, default=20)
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    q, m = qmpfast.from_machine(args.machine_json)
    workdir = os.path.dirname(args.machine_json)
    try:
        png = bench(q, "png", args.frames, workdir)
        ppm = bench(q, "ppm", args.frames, workdir)
    finally:
        q.close()

    # bytes the terminal must ingest per frame for each design
    ppm_px = 1280 * 800 * 3
    term = {
        "png_b64_chars": ((png["file_bytes"] + 2) // 3) * 4,
        "ppm_shm_escape_chars": 140,
    }
    out = {
        "machine": m.get("name"),
        "resolution": [m.get("width"), m.get("height")],
        "png": png,
        "ppm": ppm,
        "speedup_capture": round(png["capture_ms_median"] / ppm["capture_ms_median"], 1),
        "terminal_bytes": term,
        "ppm_pixels_bytes": ppm_px,
    }
    line = json.dumps(out, indent=2)
    print(line)
    if args.json:
        with open(args.json, "w") as fh:
            fh.write(line + "\n")


if __name__ == "__main__":
    main()
