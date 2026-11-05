# Architecture: upstream vs Linux port

## Upstream (Apple silicon only)

```
terminal-mac CLI
 ├─ Virtualization.framework: VZMacOSBootLoader, VZMac*Platform, VZMacOSInstaller
 │    └─ IPSW restore image from Apple
 ├─ VZVirtualMachineView (native window) / TerminalViewer (Kitty/iTerm2 graphics)
 ├─ host-control mailbox (flat JSON files over virtio-fs)
 │    └─ guest helper TerminalMacGuest.app (ScreenCaptureKit + CGEvent)
 └─ MCP stdio server (screenshot/click/move/drag/scroll/type/key/status)
```

The guest helper refuses to run unless `hw.model` begins with `VirtualMac`; the mailbox is a flat
directory with request/reply JSON files and image blobs. The guest helper sees
`/Volumes/My Shared Files/control` because `VZVirtualMachineConfiguration` uses
`VZVirtioFileSystemDeviceConfiguration.macOSGuestAutomountTag`.

## Linux port (this workspace)

```
host/tmac.py
 ├─ QEMU process per VM (q35, KVM, OpenCore/OVMF for macOS)
 ├─ QMP unix socket
 │    ├─ screendump (PNG framebuffer)  -> terminal viewer frames and MCP screenshots
 │    └─ input-send-event (abs/btn/key) -> click/drag/scroll/type/key
 ├─ Terminal viewer (Kitty graphics protocol; iTerm2 inline images)
 └─ MCP stdio server (same tool names as upstream)
```

Design decisions:

1. **No guest helper is required.** Upstream uses ScreenCaptureKit and CGEvent inside the guest to
   preserve isolation and avoid the host desktop. On Linux we cannot run the arm64 guest helper in
   an x86_64 guest, and QEMU-level input is sufficient for a first functional port. The tradeoff is
   US-ASCII-only typing (QEMU QKeyCode), no per-window capture, and no guest privacy grants.
2. **QMP is the transport.** `screendump` returns a complete PNG; `input-send-event` supports
   absolute pointer, buttons, wheel and QKeyCode. This works from firmware/OpenCore through macOS,
   including the login screen, with no guest daemon.
3. **The VM remains a separate Mac.** QEMU's guest sees only its own disk, OpenCore, and the
   virtio-net NIC. The host viewer never uses host screen/input APIs.
4. **Virtio-fs mailbox is optional.** It is still possible to mount a shared folder inside a macOS
   guest and run a native helper for Unicode/accessibility, but it is not on the critical path.

## External VM mode

`tmac attach NAME --qmp-socket PATH` records an already-running QEMU or Reims VM. It stores the
socket path, screen dimensions, optional SSH port, and guest username; it does **not** record a pid
or take lifecycle ownership. `tmac start`/`stop` refuse an external record. The same QMP client is
used by `status`, `action`, `view`, and `mcp`, so the MCP server can control a Reims/Tahoe VM
without becoming its launcher.

## Wire format (upstream, reusable)

`Request { id: UUID, action: String, arguments: [String:String], deadline: Date }`
`Reply { id: UUID, ok: Bool, values: [String:String], error: String? }`

Files are `request-<uuid>.json`, `reply-<uuid>.json`, `image-<uuid>.png|.bin`. The upstream
`Mailbox` code is portable POSIX except for the `Darwin` import. A Linux helper can implement it
with `os.open(..., O_NOFOLLOW)` + `os.rename`; see [Mailbox.swift](https://github.com/govijr/terminal-mac/blob/7009117f9e910c3dd4b4bd863e8bc331bd3c9d96/Sources/TerminalMacCore/Mailbox.swift) for the exact limits.
The Linux port does **not** depend on this mailbox for QEMU control.
