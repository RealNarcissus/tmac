# Final report: running macOS on Linux, terminal-first

Date: 2026-11-04. Main agent, with worker2 (OpenCode) and reviewer (agy) in Herdr.
Upstream: `govijr/terminal-mac` @ `7009117` (v0.2.0 build 6).

## Addendum 2026-11-05: macOS 26.6.2 Tahoe with Reims vGPU

The current guest is **macOS Tahoe 26.6.2 (25G83), Darwin 25.6.0 x86_64**, not the Monterey guest
described below. It runs under reims-ctl's QEMU with `reims-vgpu`; the guest reports
**Reims vGPU / Metal 2**, renders the full desktop (Dock, menu bar, widgets, Terminal, Settings),
and is reachable at host SSH port **2224** as the guest account created during Setup. The terminal-first path is
`reims-ctl view tahoe`; it drew a live half-block frame in a 30×100 pty and disconnected with
`Ctrl-]`.

Observed artifacts:

- `evidence/host/tahoe/system-profiler.txt` — macOS 26.6.2 + Reims vGPU + Metal 2.
- `evidence/host/tahoe/desktop.png` — clean 1024×768 Tahoe desktop with Dock, menu bar and widgets; the 1920×1080 and 1280×800 runs are preserved as `desktop-1920.png` and `desktop-1280.png`.
- `evidence/host/tahoe/reims-view.raw` / `viewer.txt` — terminal viewer capture.
- `evidence/host/tahoe/show.txt` / `qemu-command.txt` — VM and QEMU configuration.
- `docs/tahoe.md` — complete runbook, install notes, SSH setup and troubleshooting.
- `evidence/host/tahoe/reims-window.png` — host screenshot of the Reims window with the guest
  desktop and a visible host cursor; guest `METAL_ERROR_MODE=5` is set.

One long first session later ended in a Reims host-Vulkan `out of Device memory` failure after
heavy Safari/App Store and QMP screenshot load. The VM restarted from the same disk to the desktop;
the current state is recovered. The failure and mitigation are recorded in `docs/tahoe.md` and
`evidence/host/tahoe/reims-crash-note.txt`; Reims is not claimed crash-free under arbitrary 4 GiB
GPU load.

Monterey remains the **stable terminal-first lane** and the recommended default for headless
automation and long-running work. Tahoe is the **experimental GPU lane** for modern macOS/Metal and
GUI experiments. The earlier “software-rendered desktop” and “GPU acceleration is the path, not a
requirement” limits apply to the Monterey lane; Tahoe’s Reims scanout and terminal view both work,
but host-VRAM stability is limited.

## Bottom line (2026-11-04 Monterey milestone; superseded by the Tahoe addendum)

**Complete.** A real macOS 12.7.6 guest (Darwin 21.6.0 x86_64) was installed into QEMU/KVM on this
AMD Ryzen Linux host, booted to the Finder desktop, and controlled through a Linux terminal viewer
and MCP server. No GPU passthrough, no Apple hardware, no guest helper binary.

Verified evidence:

- `evidence/host/desktop-clean.png` — installed macOS Finder desktop, menu bar, Dock.
- `evidence/host/desktop-terminal-proof2.png` — guest Terminal:
  `ProductVersion: 12.7.6`, `BuildVersion: 21H1320`,
  `Darwin ... 21.6.0 ... x86_64`.
- `evidence/host/mcp-macos-screenshot.jpg` — MCP `screenshot` of the live 1280×800 desktop.
- `evidence/host/kitty-viewer-macos3.png` — the macOS desktop rendered inside a real Kitty window
  by `host/tmac.py view`; status bar `gui-proof 1280x800 live Ctrl+] quit`.
- `evidence/host/install-*.png`, `setup-*.png` — Recovery GUI, APFS erase, Reinstall, Setup Assistant.

## Why upstream cannot run as-is

`terminal-mac` is Apple-silicon-only:

- `Package.swift` links `Virtualization`, `AppKit`, `ScreenCaptureKit`, `ApplicationServices`,
  `ServiceManagement`, `CoreMedia`; `scripts/build.sh` hard-fails unless `uname -m == arm64`.
- The release ZIP ships `macos-arm64` only.
- VM creation requires `VZMacOSBootLoader`, `VZMacOSRestoreImage`, Apple IPSW, virtio-fs automount,
  and a guest helper built for arm64.

The portable part is the **control model**: separate guest, terminal graphics, and MCP computer-use
tools. On Linux this is implemented with QEMU/QMP instead of Virtualization.framework.
See `docs/architecture.md` and `docs/porting.md`.

## Verified milestone table

| Milestone | Evidence | Status |
|---|---|---|
| QMP screenshot/input/lifecycle | `alpine-boot.png`, `alpine-login-ok.png` | PASS |
| OpenCore picker + Apple boot | `macos-6core-picker.png`, `macos-installer-25s.png` | PASS |
| XNU boots | `monterey-2x2-core2-40s.png`, `evidence/macos/monterey6-serial.log` | PASS |
| Recovery desktop | `gui-proof-45s.png`; worker2 `w2-mont-hostcpu-recovery.png` | PASS |
| APFS erase + Reinstall + Setup Assistant | `install-progress-start.png` … `setup-18.png` | PASS |
| Installed desktop | `desktop-clean.png` | PASS |
| Guest shell proof | `desktop-terminal-proof2.png` | PASS |
| MCP against macOS | `mcp-macos-screenshot.jpg` | PASS |
| Terminal viewer visual | `kitty-viewer-macos3.png` | PASS |

## The exact working recipe

```
machine : q35, KVM, OVMF, OSX-KVM OpenCore (with AMD patches)
cpu     : -cpu host,kvm=on,vendor=GenuineIntel,+invtsc,vmware-cpuid-freq=on,
          +ssse3,+sse4.2,+popcnt,+avx,+aes,+xsave,+xsaveopt
memory  : 3584–6144 MiB
smp     : 4 vCPU: either 4,cores=2,threads=2 (patch 02) or 4,cores=4,threads=1 (patch 04)
patches : AMD_Vanilla merged into OpenCore; four Force cpuid_cores_per_package values match cores
booter  : SetupVirtualMap=false, ProvideCustomSlide=true, RebuildAppleMemoryMap=false,
          SyncRuntimePermissions=false, ProvideCurrentCpuInfo=true
gpu     : -device vmware-svga (VGA,vgamem_mb=64 also works)
boot    : keepsyms=1 debug=0x100 -v serial=3
disk    : 40 GiB qcow2, erased as APFS in Disk Utility, installed from Monterey recovery
```

Root-cause history (important):

1. **Masked Intel CPU models** (`Skylake-Client`, `Haswell-noTSX`) made QEMU drop `pcid`, `erms`,
   `invpcid`; XNU stalled before console. Worker2's controlled A/B (W2-13c) isolated `-cpu host` as
   the discriminating variable.
2. **Topology/core-patch mismatch** is a separate failure: XNU traps `type 0=divide error` in
   `_cpu_thread_alloc`. It must match, but it was not the stall fix.
3. `vm_shared_region_start_address() failed` is **harmless boot noise** in this QEMU setup
   (confirmed by jprx/darwin-vm PR #1 and by the GUI rendering while the lines appear).

## Reproduce

```sh
# Host control-plane smoke test
TMAC_HOME=/tmp/tmac-smoke python3 host/tmac.py create alpine \
  --iso work/testvm/alpine.iso --memory 1024 --cpus 2 --mode linux --vnc 127.0.0.1:5
TMAC_HOME=/tmp/tmac-smoke python3 host/tmac.py start alpine
TMAC_HOME=/tmp/tmac-smoke python3 host/tmac.py action alpine screenshot --output /tmp/a.png
TMAC_HOME=/tmp/tmac-smoke python3 host/tmac.py action alpine type --text root
TMAC_HOME=/tmp/tmac-smoke python3 host/tmac.py action alpine key --key return
TMAC_HOME=/tmp/tmac-smoke python3 host/tmac.py stop alpine --force

# macOS installer VM (OSX-KVM checkout + AMD patches)
bash scripts/prepare-macos.sh --osx-kvm work/main/osx-kvm --version monterey --cores 2 --start
# OpenCore: right arrow to "macOS Base System", Return.
# Disk Utility: erase the 40 GiB QEMU HARDDISK as APFS; Reinstall macOS Monterey.
```

Live VM used for the proof:

```sh
export TMAC_HOME=$PWD/work/main/tmac-home
python3 host/tmac.py status gui-proof
python3 host/tmac.py view gui-proof
python3 host/tmac.py mcp gui-proof
```

## Agent record / audit

- **worker2 (OpenCode)** independently reproduced the boot, discovered `-cpu host` with a controlled
  A/B (W2-13c), proved `vm_shared_region` noise, and produced Recovery GUI screenshots:
  the internal development audit logs (retained in the private development repository).
- **reviewer (agy)** adversarially audited upstream source, worker2 artifacts, and the evolving
  claims; its REV-06/REV-07 corrected earlier wrong conclusions:
  the internal development audit logs (retained in the private development repository).
- Reviewer’s earlier claim that XNU “never reaches console” is superseded by direct Darwin banner,
  GUI and installed-desktop evidence. Reviewer’s **REV-08** independently re-hashed and visually
  verified `desktop-clean.png`, `desktop-terminal-proof2.png`, `mcp-macos-screenshot.jpg` and
  `kitty-viewer-macos3.png`, and marked the desktop/MCP/terminal milestones **FULLY VERIFIED**.
  REV-07’s “install still running” items are corrected in REV-08.


## UX optimization (terminal viewer)

The first viewer flashed and ran at ~2–5 fps. Root causes: PNG screendump (~300–490 ms), a 50 ms
stable-size poll, `ESC[2J` + delete-all on every changed frame, and base64 PNG over the pty.

The production viewer now uses PPM capture (**6–14 ms**), Kitty shared-memory transport (a
~140-char escape instead of ~765 KB base64), no per-frame clear, double-buffered image IDs, and
one key transition per QMP command. Measured production recording: **122 frames, 117 transitions,
0 blank frames, 28.77 effective fps**; capture cadence 20.47 fps with shm. Worker2's independent
baseline-vs-fast recording measured **25.4% blank frames vs 0%**. Details:
`docs/ux.md`, `evidence/host/ux-production-grab.json`, `evidence/worker2/ux/`.

## Historical limits (Monterey, 2026-11-04)

These apply to the preserved Monterey milestone, not the current Tahoe addendum.

- Guest is macOS 12.7.6, not the upstream-recommended 14+. The Linux control plane is OS-version
  independent; the arm64 guest helper was not used.
- Typing is US-ASCII QKeyCode; Unicode/clipboard/IME are not implemented in `host/tmac.py`.
- Software-rendered desktop; at that date `metal2vulkan`/reims-vgpu had not been built. It is now
  installed for Tahoe (`docs/tahoe.md`), with the VRAM caveat recorded there.
- Host `kvm.ignore_msrs` remained `N`; not needed for this success.
- The terminal viewer was captured under Xvfb; a physical Kitty/Ghostty window was not used.
