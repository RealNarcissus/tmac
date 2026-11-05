# Terminal UX: flash-free, interactive viewer

## Problem

The first viewer implementation was slow and flashed:

- QMP `screendump` used **PNG**, which encoded a 1280×800 frame in **~300–490 ms**.
- It polled the output file for two stable sizes, adding **≥100 ms** per frame.
- Every changed frame sent `ESC[2J` and deleted all Kitty images, then re-sent a **600–765 KB
  base64 PNG** through the pty.
- Input opened a new QMP connection per key event.

Net effect: roughly 2–5 displayed frames/s, a blank terminal between clear and image, and
noticeable input lag.

## What changed in `host/tmac.py`

| Layer | Old | New |
|---|---|---|
| Capture | PNG, 50 ms stable-size polling | **PPM**, trust QMP reply, 0–1 ms completion check |
| Transport | base64 PNG through pty | **Kitty `t=s` POSIX shared memory**, 3 MB frame in shm; escape is ~140 chars |
| Redraw | `ESC[2J` + delete all images every frame | Clear/delete-all only on first frame or terminal resize; new image id, delete old id **after** display |
| Input | one QMP connection/event; batched all keys (corrupt) | one QMP connection per action; **one transition per command**, 2 ms down/up gap |
| Coordinates | fixed 8×16 cells, always fit/upscale | query `CSI 16t` cell size; **never upscale**; native-size mapping or downscale; clamp edge clicks |
| Mouse | button-only tracking (`?1000h`) | **all-motion tracking (`?1003h`)**, drag state, pointer dedupe |
| Concurrency | persistent QMP blocked MCP/actions | per-call QMP connections; status/MCP works while the viewer runs |

## Measured results

### Capture (same VM, 1280×800)

| Path | Median | Source |
|---|---|---|
| Old PNG + 50 ms poll | ~300 ms (490 ms worst seen) | worker2 `bench-capture.json`; main timing |
| New PPM + fast completion | **6–14 ms** | worker2 median 6.99 ms; main 9–14 ms |

### Display

| Metric | Result | Evidence |
|---|---|---|
| Production viewer capture cadence | **20.3–20.5 fps** at `--fps 20` | `evidence/host/ux-production-viewer-stats.json` |
| Production video | 122 frames, 117 transitions, **0 blank frames**, 28.77 effective fps | `evidence/host/ux-production-grab.json` |
| Worker2 prototype | 30 fps cap, 14.26 ms median total frame | `evidence/worker2/ux/proto-vm-summary.json` |
| Worker2 baseline vs fast | baseline **25.4% blank frames** (31/122), fast **0%** | `blank-baseline.json`, `blank-fast.json` |
| Terminal bytes/frame | 765,572 base64 PNG chars → **~140-char shm escape** | `bench-capture.json` |
| Draw cost | ~4.4 ms/frame (production shm) | `viewer-stats.json` |

### Input

- Per-transition commands type correctly; batching a whole string caused reordered/stuck keys.
- Worker2 timing: legacy per-event 12.38 ms, per-char 8.24 ms, batch-all 4.78 ms for 8 chars
  (`bench-input.json`). The batch-all path was fastest but corrupted guest text, so production uses
  one transition per QMP command for correctness.
- Viewer and `tmac status` were exercised concurrently (status returned while viewer ran).

## Use

```sh
export TMAC_HOME=$PWD/work/main/tmac-home
python3 host/tmac.py view gui-proof --fps 20
python3 host/tmac.py view gui-proof --fps 20 --transport shm   # force shm
python3 host/tmac.py view gui-proof --fps 20 --transport direct # fallback (slower)
TMAC_VIEWER_STATS=1 python3 host/tmac.py view gui-proof --fps 20
# stats -> work/main/tmac-home/machines/gui-proof/viewer-stats.json
```

Mouse: move over the image to move the guest pointer, click/drag normally; `Ctrl+]` quits.
Kitty and Ghostty are the tested terminals. `--transport auto` probes shared memory and falls back
to raw-RGB base64.

## Evidence

- `evidence/host/ux-production-grab.mp4` / `.json` — production viewer recording, 0 blank frames.
- `evidence/host/ux-production-viewer.png` — production viewer showing a typed command.
- `evidence/host/ux-fast-shm.png`, `ux-fast-shm-terminal.png` — live shm-rendered macOS.
- `evidence/host/ux-production-viewer-stats.json` — capture/draw cadence.
- `evidence/worker2/ux/` — independent worker2 benchmarks, baseline/fast videos, shm probe,
  terminal metrics.
- `docs/agents/worker2.md` W2-21…W2-26 — independent measurements and integration risks.

## Limits

- PPM is 3 MB/frame; QMP still copies it once. At 30 fps this is fine on this host, but it is not a
  zero-copy VNC/RFB path.
- `--transport direct` still pushes ~4 MB of base64 per frame and is much slower; it is only a
  compatibility fallback.
- Software-rendered macOS remains the guest-side bottleneck for heavy UI.
- QMP is single-client at a time; per-call connections keep MCP working but a command can briefly
  wait behind a capture. Long-running capture loops still contend.
- `TMAC_VIEWER_STATS` writes only on clean exit (Ctrl+] or SIGTERM).
