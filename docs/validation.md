# Validation record

Scope: Linux host control plane + installed macOS guest. Each row names the artifact that proves
it. Unverified rows are labeled; nothing is claimed from prose alone.

## Current Tahoe guest (2026-11-05)

| Check | Command / artifact | Result |
|---|---|---|
| Guest identity | `evidence/host/tahoe/system-profiler.txt` | PASS — macOS 26.6.2 / 25G83 / Darwin 25.6.0 x86_64 |
| Resources | same file | PASS — 8 vCPU, 8589934592 bytes RAM |
| GPU + Metal | `system_profiler SPDisplaysDataType` | PASS — Reims vGPU, vendor 0x106b, **Metal 2**, 1024×768 |
| Full desktop rendered | `evidence/host/tahoe/desktop.png` | PASS — Dock, menu bar, widgets and wallpaper |
| QMP screenshot/input | Live QMP screendump + input events during install/setup | PASS |
| External-VM MCP | `tmac attach tahoe`; MCP `initialize`, `tools/list`, `status`, `screenshot` | PASS — 8 tools, 1024×768 JPEG |
| Host cursor over Reims image | `evidence/host/tahoe/reims-window.png` (visible arrow) | PASS |
| Reims window title | `Reims vGPU — input captured` in the same screenshot | PASS |
| SSH | `ssh -i ~/.ssh/tmac_guest -p 2224 tmac@127.0.0.1` | PASS — key auth, user `tmac`, console session |
| Terminal-first viewer | `reims-ctl view tahoe --fps 2 --protocol blocks` | PASS — 30×100 pty, half-block frame, Ctrl-] disconnect |
| VM/GPU configuration | `evidence/host/tahoe/show.txt`, `qemu-command.txt` | PASS |
| Setup Assistant | QMP mouse selected rows; real host Return activated Continue | PASS with input caveat |
| `METAL_ERROR_MODE` | `defaults read /Library/Preferences/.GlobalPreferences METAL_ERROR_MODE` | PASS — reads `5` after reboot; required by reims for x86-64 Tahoe |

## Host control plane

| Check | Command / artifact | Result |
|---|---|---|
| Syntax | `python3 -m py_compile host/tmac.py` | PASS |
| Unit tests | `python3 -m unittest discover -s tests -v` | 29 PASS (2026-11-05) |
| VM create/start/screenshot/key/stop | `alpine-boot.png`, `alpine-login-ok.png` | PASS |
| PNG dimensions | QMP screendump 1280×800 | PASS |
| Keyboard input | typed `root` + Return in Alpine | PASS |
| Lifecycle | `stop --force` → stopped | PASS |
| MCP initialize/tools/list | stdio JSON-RPC | PASS |
| Terminal viewer | `terminal-viewer-macos.log`, `kitty-viewer-macos3.png` | PASS |

## macOS guest

| Milestone | Artifact | Result |
|---|---|---|
| OpenCore picker lists installer | `macos-6core-picker.png`, `monterey-picker.png` | PASS |
| Apple boot.efi / Apple logo | `macos-installer-25s.png` | PASS |
| XNU banner | `monterey-2x2-core2-40s.png`, `evidence/macos/monterey6-serial.log` | PASS (Darwin 21.6.0) |
| Recovery desktop | `gui-proof-45s.png`; worker2 `w2-mont-hostcpu-recovery.png` | PASS |
| APFS erase | `install-erase-done.png` | PASS |
| Reinstall + Setup Assistant | `install-progress-start.png` … `setup-18.png` | PASS |
| Installed desktop | `desktop-clean.png` | PASS |
| Guest shell proof | `desktop-terminal-proof2.png` | PASS (12.7.6 / 21H1320 / x86_64) |
| MCP screenshot on macOS | `mcp-macos-screenshot.jpg` (1280×800 JPEG) | PASS |
| Terminal viewer on macOS | `kitty-viewer-macos3.png` | PASS (real Kitty under Xvfb) |
| Flash-free production viewer | `ux-production-grab.json` (0/122 blank), `ux-production-grab.mp4` | PASS |
| Production capture cadence | `ux-production-viewer-stats.json` (20.47 capture fps, shm) | PASS |
| Viewer + QMP concurrency | `status` returned while viewer ran | PASS |
| Mouse motion/drag mapping | `?1003h` + no-upscale placement; unit tests | PASS (code/unit) |

## Root cause / failure history

| Observation | Artifact | Conclusion |
|---|---|---|
| Masked Intel CPU model stalls before console | worker2 W2-13c; `w2-verbose-t140s.png` | `-cpu host` is the discriminator |
| Topology/patch mismatch traps | `evidence/macos/panic-divide-error.txt` | `type 0=divide error` in `_cpu_thread_alloc`; topology must match patch |
| `vm_shared_region_start_address() failed` | `monterey6-serial.log`; GUI screenshots | harmless QEMU boot noise; GUI still renders |
| Catalina recovery does not boot here | worker2 W2-19 | not a viable fallback in this setup |

## Resource facts

- Guest: 3584 MiB, 4 vCPU, 40 GiB qcow2; installed system used ~23 GiB.
- Host: AMD Ryzen 5 4600H, 14 GiB RAM, QEMU 11.1.2, `/dev/kvm`.
- `kvm.ignore_msrs=N`; not needed for this success.
- No GPU passthrough; desktop is software-rendered.
