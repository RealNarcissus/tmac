# Research log — macOS on Linux

Updated 2026-11-05. Observations and source citations only; conclusions marked INFERENCE.

**Current status:** the reims-vgpu path was subsequently installed and is running macOS 26.6.2
Tahoe; see [`tahoe.md`](tahoe.md). The earlier research questions and INFERENCEs below are kept as
the historical investigation record, not as current capability limits.

## R1. What terminal-mac actually is (OBSERVED, upstream source)

Source inspected at tag `v0.2.0-6` / commit `7009117f9e910c3dd4b4bd863e8bc331bd3c9d96`
(upstream copy removed after analysis; citations below use upstream paths at that commit).

- `Package.swift` links `Virtualization`, `AppKit`, `ScreenCaptureKit`, `ApplicationServices`,
  `ServiceManagement`, `CoreMedia`; platform `.macOS(.v14)`; build script hard-fails unless
  `uname -m == arm64`.
  Evidence: [Package.swift](https://github.com/govijr/terminal-mac/blob/7009117f9e910c3dd4b4bd863e8bc331bd3c9d96/Package.swift), [scripts/build.sh](https://github.com/govijr/terminal-mac/blob/7009117f9e910c3dd4b4bd863e8bc331bd3c9d96/scripts/build.sh).
- VM creation uses `VZMacOSBootLoader`, `VZMacPlatformConfiguration`, `VZMacHardwareModel`,
  `VZMacMachineIdentifier`, `VZMacAuxiliaryStorage`, `VZVirtioBlockDeviceConfiguration`,
  `VZMacGraphicsDeviceConfiguration`, `VZVirtioFileSystemDeviceConfiguration` with
  `VZVirtioFileSystemDeviceConfiguration.macOSGuestAutomountTag`.
  Evidence: `Sources/TerminalMac/VirtualMachine.swift:8-37`.
- Restore images come from `VZMacOSRestoreImage.latestSupported` (Apple IPSW).
  Evidence: `Sources/TerminalMac/CLI.swift` (`image` command).
- Host↔guest transport is a flat directory `exchange` shared via virtio-fs. Requests and replies
  are JSON files named `request-<uuid>.json` / `reply-<uuid>.json`; images are written as
  `image-<uuid>.png` or `.bin`. Host pins the directory fd and rejects symlinks, hardlinks, and
  oversized files (`Mailbox.swift`).
- Guest helper is a second copy of the same binary running as `TerminalMacGuest.app`. It refuses to
  run unless `hw.model` starts with `VirtualMac`, then uses ScreenCaptureKit + CGEvent for
  screenshot/input, and writes frames to the mailbox (`Guest.swift`).
- Terminal viewer uses Kitty/iTerm2 graphics protocols; host-side renderer is AppKit/CoreGraphics
  (`Viewer.swift`, `DesktopRenderer.swift`, `TerminalUI/*`).
- MCP server is a small stdio JSON-RPC implementation (no external deps); tools: screenshot, click,
  move, drag, scroll, type, key, status (`AgentServer.swift`).

INFERENCE: the guest helper and mailbox protocol are portable *into* a macOS guest. Only the host
half (Virtualization.framework, AppKit, ScreenCaptureKit host capture) is Apple-silicon-specific.
The x86-64 guest helper binary would need to be built for x86_64; the release only ships arm64.

## R2. Boot path on Linux x86_64

Sources read 2026-11-04:

- kholia/OSX-KVM README: QEMU/KVM + OpenCore; requires Intel VT-x or AMD SVM, SSE4.1, AVX2 for
  Ventura+; "modern AMD Ryzen processors work just fine (even for macOS Sonoma)"; offers
  `OpenCore-Boot.sh`, `boot-macOS-headless.sh`, `fetch-macOS-v2.py` (through Tahoe/macOS 26), and an
  offline install document. https://github.com/kholia/OSX-KVM
- reims-vgpu README: virtual GPU for macOS guests in QEMU; x86-64 Linux KVM path uses QEMU `q35` +
  OpenCore + PCI `apple-gfx-pci` + Vulkan via metal2vulkan. Claims tested x86-64 macOS 26 on Linux
  with an RTX 5080 laptop GPU. Provides `reims-ctl create mac --macos tahoe --start`, OpenCore image,
  installer download, `/dev/udmabuf`, `REIMS_VGPU_WINDOW=0` headless mode.
  https://github.com/steelbrain/reims-vgpu
- metal2vulkan README: "Translate Metal shaders to Vulkan, also a Metal Device API backed by
  Vulkan". It is the runtime used by reims-vgpu's Vulkan platform, not a standalone macOS-in-Linux
  hypervisor. https://github.com/steelbrain/metal2vulkan

INFERENCE: the base boot path is OSX-KVM (QEMU + OpenCore + Apple recovery image). GPU acceleration
is optional and orthogonal: reims-vgpu + metal2vulkan replaces Apple's paravirtual GPU host
implementation for guests that have Apple's `AppleParavirtGPU.kext`; on x86-64 it needs a small
OpenCore kext (`ReimsVgpu.kext`) and a Vulkan-capable host GPU. Software VGA remains the fallback.

## R3. Host capability check (OBSERVED)

- `lscpu`: AMD Ryzen 5 4600H, `svm`, `avx2`, `sse4_2`; KVM device exists (`/dev/kvm`, mode 666).
- QEMU 11.1.2.
- 14 GiB RAM, ~5.7 GiB available; 46 GiB free disk.
- `herdr` server started headless at 2026-11-04; workspace `wF` created.

## Open questions

- Does this exact Ryzen 4600H + QEMU 11.1.2 + OpenCore combination reach the macOS installer?
  (worker2 owns the boot experiment.)
- Which macOS release is practical within 46 GiB free and 14 GiB RAM?
- Does upstream's `Mailbox` protocol work over a QEMU virtio-fs mount in an x86_64 guest, or is the
  guest helper's `/Volumes/My Shared Files` assumption VZ-specific?
- Can reims-vgpu build and run on this AMD iGPU / RAM budget, or is software VGA the honest fallback?
- Unicode typing and clipboard semantics without the Swift guest helper.
