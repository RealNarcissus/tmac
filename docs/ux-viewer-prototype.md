# Linux viewer: measured baseline vs shared-memory prototype

Status: **historical worker2 prototype analysis** (2026-11-04). It documents the independent
baseline/prototype measurements that converged with the production implementation. The current,
verified production viewer is described in `docs/ux.md`; this file is retained as an audit trail.

## 1. The problem, stated in measured terms

`host/tmac.py`'s `Viewer` does, every frame:

1. a **new** QMP connection,
2. `screendump format=png` into the machine directory,
3. a stable-size poll that costs at least two 50 ms sleeps,
4. `ESC[?25l ESC[2J ESC[H` — a full-screen erase,
5. a PNG decode/identity hash, then base64 in 4096-char chunks,
6. the same image id every time, with no delete-after-display ordering.

Measured on `gui-proof` (1280x800, 2026-11-04T15:34:46Z, 20 frames, `/dev/shm`):

| path | median | p95 | payload |
|---|---|---|---|
| exact `tmac.screendump` PNG path (A) | **300.4 ms** | 324.0 ms | 606,798 B PNG |
| PNG, persistent QMP (B) | 244.1 ms | 257.2 ms | 606,798 B PNG |
| PNG decode to RGBA | 31.1 ms | 52.4 ms | |
| **PPM, persistent QMP (C)** | **5.9 ms** reply / **7.0 ms** complete | 11.98 ms | 3,072,016 B PPM |
| PPM read + header parse | 0.6 + 1.0 ms | | |

So the PNG encoder alone is ~35-40x the cost of writing raw pixels, and the full baseline
path is ~43x the PPM path. The flash has two independent causes: `ESC[2J` removes the image
before it is re-placed, and the ~300 ms frame interval makes each re-place visible.

## 2. Prototype design (`work/worker2/ux/viewer_ux.py`)

Mirrors upstream `DesktopRenderer.swift:138-169` / `shim.c`, not its Swift dependencies:

* **Raw pixels, not PNG.** QEMU `screendump format=ppm` (P6, 3 bytes/pixel). The PPM body is
  already kitty `f=24` RGB, so there is no colour conversion and no encode/decode.
* **POSIX shared memory, `t=s`.** `shm_open` + `ftruncate` + `copy_file_range` from the PPM
  file into the object; the escape carries only the base64 *name* plus geometry:
  `a=T,f=24,t=s,s=W,v=H,S=W*H*3,i=<2|3>,q=2,C=1,z=-1073741825;<b64 name>`.
  `copy_file_range` is a kernel-side copy on tmpfs; no 3 MB Python bytes object.
* **Double-buffered image ids.** Ids alternate 2/3. The new image is transmitted first; the
  previous id is deleted in the *same* write, so there is never a moment with no image.
* **No full-screen clear.** Alternate screen entered once, cursor hidden once, image placed
  once at the computed cell origin; `C=1` keeps the cursor there. Only the one-line status
  row is redrawn, and only when its text changes.
* **Terminal metrics.** `CSI 14 t` / `CSI 16 t` give the text-area pixel size and the cell
  size (1400x893 and 8x19 in the test window), so placement and mouse mapping use real
  pixels instead of the 8x16 guess.
* **Startup probe.** `a=q,t=s,f=32,S=4` asks kitty whether it can read shared memory; the
  reply `i=31;OK` selects `t=s`. (Fallback to direct PNG is planned, not implemented.)
* **One QMP connection for the session**, persistent, so per-frame connect cost is zero.

Measured (3 s, 30 fps cap, `--force` so every frame is sent; 2026-11-04T15:43:02Z):

```
89 frames / 3.012 s = 29.55 fps
median total 14.26 ms, p95 19.40 ms
median capture 5.99 ms, p95 11.60 ms
median shm write 2.20 ms, p95 2.86 ms
```

That leaves ~19 ms of the 33.3 ms budget unused at 30 fps. The renderer is no longer the
bottleneck; the 30 fps cap is. A 60 fps cap is plausible if the guest changes that fast.

## 3. What is still unproven

1. **Displayed fps and flicker, end to end.** The plan is an Xvfb + kitty + `ffmpeg x11grab`
   recording at 30 fps for both modes, then count unique frames and (nearly) blank frames per
   second. Blank-frame count is the objective version of "it flashes". Not run yet: the user
   paused QMP work while main measured the production viewer.
2. **Baseline A/B under identical conditions.** The baseline's `ESC[2J` may or may not be
   visible as a blank frame depending on how kitty batches writes; the recording decides.
3. **Input batching.** `host/tmac.py` sends one QMP command per key event (4 commands for a
   shifted character). One `input-send-event` with a list of events should be much cheaper,
   but down+up in the same command may be coalesced by the USB HID path. The plan is to
   measure per-key vs batched latency and verify visually with Spotlight
   (`Cmd+Space`, type, screenshot, `Escape`) — harmless, transient UI only.
4. **Placement/scaling.** The guest image appeared at native 1280x800 even though the request
   was 174x46 cells (1392x874 px). Mouse mapping currently uses the cell box, so it is ~9%
   wrong at the right/bottom edge. Needs a synthetic 1-px-border test to learn kitty's rule.
5. **QMP exclusivity.** A persistent connection blocks every other QMP client (observed:
   `host/tmac.py` timed out reading the greeting). The production integration needs a broker
   or per-frame connections; otherwise MCP actions stop working while the viewer is open.
6. **Fallback when `t=s` is unsupported.** Kitty < 0.28 or a terminal that advertises kitty
   graphics without shm needs a direct-PNG path that still keeps the double-buffer/no-clear
   properties. Not implemented.

## 4. Repro (when QMP is free)

```bash
cd <repo>
TMAC_HOME=$PWD/work/main/tmac-home python3 work/worker2/ux/bench_capture.py

# Xvfb + kitty (X11 forced; initial_window_* are pixels in kitty 0.49.2)
Xvfb :97 -screen 0 1600x1000x24 -nolisten tcp &
env -u WAYLAND_DISPLAY DISPLAY=:97 LIBGL_ALWAYS_SOFTWARE=1 \
  kitty -o linux_display_server=x11 --class tmac-test \
        -o initial_window_width=1400 -o initial_window_height=900 \
        -o font_size=10 -o window_padding_width=0 -o hide_window_decorations=yes \
        python3 work/worker2/ux/viewer_ux.py --mode proto --seconds 3 --fps 30 --force \
        --log work/worker2/ux/proto.jsonl --summary work/worker2/ux/proto-summary.json
```

`--mode baseline` is a faithful copy of the current `tmac.Viewer` path (new QMP connection,
PNG, 50 ms poll, `ESC[2J`, chunked base64) for A/B.

## 5. Read-only comparison with main's in-progress `host/tmac.py`

At 2026-11-04T15:50:12Z the working tree had `host/tmac.py` modified by main
(`sha256(host/tmac.py) = 79a0a739…5e38477`, `sha256(git diff) = 99ac50e2…b43d300f`; uncommitted
and moving). The diff adopts the same design: PPM, POSIX shm, `f=24`/`t=s`, double image ids,
batched input, persistent QMP, clear only on first frame/resize.

Open risks I can test independently, without editing their file:

| risk | why it matters | status |
|---|---|---|
| `read_ppm` + `shm_write` do two 3 MB copies/frame | ~2-4 ms/frame at 30 fps; my `copy_file_range` path avoids both | to measure |
| new screendump poll is `sleep(1ms)+stat` even when complete | ~2 ms/frame; my expected-size check is 0 ms in 20/20 cases | measured, theirs untested |
| `text_events` batches a whole string into one command | down+up may coalesce in USB HID and drop characters | `bench_input.py` ready |
| `Placement` still assumes 8x16 cells and a cell box | W2-22b saw native-size display; mouse can be ~9% off | border test pending |
| persistent QMP in `Viewer` | blocks MCP/other viewers (W2-23) | observed on my code, same design |
| `_drop_old_shm` deletes all but two objects | host-side race if kitty lags | to stress test |

None of this is a claim that their change is broken; it is a list of things my prototype can
verify or falsify with the same VM and recording harness.

## 6. Evidence

| artifact | sha256 |
|---|---|
| `evidence/worker2/ux/bench-capture.json` | `44f9c3b8…e56017da` |
| `evidence/worker2/ux/probe-shm.json` | `f994030c…215ce1a8` |
| `evidence/worker2/ux/metrics.json` | `63b3a797…92c06644` |
| `evidence/worker2/ux/proto-vm-summary.json` | `a3d4d890…b0117b8c40` |
| `evidence/worker2/ux/shots/probe-shm2.png` | `5468fc9f…3fa50f65` |
| `evidence/worker2/ux/shots/proto-vm-live.png` | `d06d709b…3dd72aad` |

The worker2 append-only log has the full commands, timings and caveats in W2-21…W2-24.
