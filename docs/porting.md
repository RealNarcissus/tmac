# Porting notes: Apple frameworks → QEMU/QMP

## Mapping

| Upstream | Linux port | Fidelity |
|---|---|---|
| `VZMacOSBootLoader` + IPSW | OpenCore + OVMF + Apple `BaseSystem.dmg` | boot purpose equivalent; different image pipeline |
| `VZMacPlatformConfiguration` / hardware identity | OpenCore SMBIOS + NVRAM | not interchangeable |
| `VZMacOSInstaller` | macOS Recovery GUI | works; installer completed on QEMU |
| `VZVirtioBlockDeviceConfiguration` | SATA/IDE disk on `ich9-ahci` | works (OpenCore/Apple driver) |
| `VZMacGraphicsDeviceConfiguration` | `vmware-svga` / `VGA` | software-rendered GUI works; no acceleration |
| `VZUSBKeyboard` / screen-coordinate pointing | `usb-kbd` / `usb-tablet` on `qemu-xhci` | verified on Alpine and OpenCore picker |
| `VZVirtioEntropyDeviceConfiguration` | `virtio-rng-pci` (can be added) | equivalent |
| `VZNATNetworkDeviceAttachment` | `-netdev user` + `virtio-net-pci` | equivalent |
| `VZVirtioFileSystemDeviceConfiguration` automount | virtiofs/9p/manual mount | not on critical path; `Mailbox` is portable if needed |
| ScreenCaptureKit + CoreGraphics | QMP `screendump` (PNG) | no window list, no cursor metadata |
| CGEvent input | QMP `input-send-event` (abs/btn/key) + guest clipboard | ASCII/Unicode text is exact via `pbcopy`+Cmd+V when the SSH key is installed; QMP keys are the fallback |
| AppKit viewer | Python raw terminal + Kitty/iTerm2 graphics | feature subset; no native window |
| MCP stdio | Python stdio JSON-RPC | same 8 tool names/schemas |

## AMD/QEMU findings (all reproducible from this workspace)

### 1. `EXITBS:START` had two separate causes

**a. Masked CPU model (the actual stall).** `-cpu Skylake-Client`/`Haswell-noTSX` on this AMD host
made QEMU drop `pcid`, `erms`, `invpcid`; XNU stopped before console. `-cpu host` fixed it. Worker2's
controlled A/B W2-13c isolated this; main reproduced the Recovery GUI.

**b. Topology divide error (a later, different trap).** When topology and the AMD core-count patch
disagree, XNU panics with a divide error.

With AMD_Vanilla patches loaded but QEMU topology inconsistent with the patched
`cpuid_cores_per_package`, XNU traps with `type 0=divide error` at
`_cpu_thread_alloc` and halts in an infinite loop. The screen appears stuck at OpenCore's last line
`#[EB|LOG:EXITBS:START]`.

Evidence:
- `evidence/host/macos-quirks-boot-35s.png` (stuck picker handoff)
- a guest RAM dump (`work/main/ram.bin`) contains
  `panic(cpu 0 ...): Kernel trap at 0xffffff80059b8e79, type 0=divide error`
- symbolization against `BootKernelExtensions.kc` puts the fault in `_cpu_thread_alloc`
- fixing topology to `-smp 4,cores=2,threads=2` + core-count patch `02` produced normal XNU boot
  logs (`evidence/host/monterey-2x2-core2-40s.png`)

### 2. KVM `ignore_msrs`

OSX-KVM's README requires `echo 1 | sudo tee /sys/module/kvm/parameters/ignore_msrs`. The value is
`N` here and the host has no passwordless sudo. This is an **unverified host-level suspect** for
some AMD boot failures. It must be tested before blaming a guest config.

### 3. Shared-region messages are not a GUI blocker

QEMU does not emulate a real dyld shared cache, so every process can log
`vm_shared_region_start_address() failed`. This is harmless: the Recovery GUI and the installed
desktop render while the lines are present (and jprx/darwin-vm PR #1 documents the same noise). The
earlier black screens were caused by masked CPU models plus an incomplete boot/install cycle, not by
this log line.

## metal2vulkan / reims-vgpu evaluation

- `metal2vulkan` is a Metal→Vulkan shader/device runtime, not a macOS hypervisor.
- `reims-vgpu` is the macOS-guest virtual GPU built on it. Its x86-64 Linux path is
  `q35 + OpenCore + apple-gfx-pci` with Vulkan on the host and a small `ReimsVgpu.kext` in the
  guest. It ships `reims-ctl` to create/start VMs and `REIMS_VGPU_WINDOW=0` for headless.
- This host has `/dev/udmabuf` (ACL grants user rw) and a Vulkan ICD (NVIDIA 1650 Ti; AMD Renoir
  present). reims-ctl and its QEMU are now installed under `~/.cargo/bin` and
  `~/.local/share/reims-ctl`; the **current `tahoe` VM** (macOS 26.6.2) uses that path and reports
  Reims vGPU with Metal 2. See [`tahoe.md`](tahoe.md) and [`gpu.md`](gpu.md).
- Reims is no longer merely a proposed acceleration path on this host: its QMP scanout, full Tahoe
  desktop and terminal viewer are observed. The preserved Monterey guest remains the
  software-rendered `host/tmac.py` reference, and the limitations below apply to that control plane.

## Host control-plane limitations

- Typing is US-ASCII QKeyCode only. Unicode, clipboard, IME and dead keys are missing.
- `screendump` is a full-frame PNG per poll; the viewer is not 30 fps like upstream's lz4 stream.
- No drag interpolation guarantees, no accessibility tree, no per-window capture.
- No pause/resume UI beyond QMP `stop`/`cont`; no snapshot/clone.
- The viewer requires Kitty or Ghostty; other terminals can use `action screenshot`/MCP.
- `host/tmac.py` uses `-cpu host` and an Intel vendor spoof; macOS still runs AMD_Vanilla patches.
