# GPU acceleration status

## Verified working: macOS 26.6.2 Tahoe (2026-11-05)

The current `tahoe` VM runs on reims-vgpu with a real Reims vGPU scanout and reports Metal 2.
This is not a software-framebuffer claim: the Dock, menu bar, widgets, Terminal, System Settings,
Spotlight and the app windows all render in the Reims window, and the same desktop is visible in
QMP screenshots.

Guest report (`evidence/host/tahoe/system-profiler.txt`):

```text
macOS 26.6.2 (25G83), Darwin 25.6.0 x86_64, 8 vCPU, 8 GiB

Reims vGPU:
  Chipset Model: Reims vGPU
  Type: GPU
  Vendor: Apple (0x106b)
  Device ID: 0xeeee
  Revision ID: 0x0000
  Metal Support: Metal 2
  Displays:
    Reims vGPU:
      Resolution: 1024 x 768
      UI Looks like: 1024 x 768
      Framebuffer Depth: 24-Bit Color (ARGB8888)
      Main Display: Yes
```

`ioreg` shows `AppleParavirtGPUControl` and `AppleParavirtGPU` loaded. The VM was created with
reims-ctl's `q35-x86_64` profile, automatic GPU selection, 8 vCPU, 8 GiB RAM and a 1024×768
display. 1920×1080 and 1280×800 runs rendered the same desktop but exhausted host Vulkan memory
under load; 1024×768 is the current conservative configuration. `reims-ctl show tahoe` and the exact QEMU invocation are recorded in
`evidence/host/tahoe/show.txt` and `evidence/host/tahoe/qemu-command.txt`.

`METAL_ERROR_MODE=5` is set in `/Library/Preferences/.GlobalPreferences`. This is not an optional
tweak: reims' x86-64 Tahoe instructions require it for the icon service and WindowServer. The guest
was initially brought up without it and showed the wallpaper-only / missing-Dock symptom; after
setting it and rebooting, the full desktop rendered. Verify with:

```sh
defaults read /Library/Preferences/.GlobalPreferences METAL_ERROR_MODE   # 5
```

## Terminal view

reims-ctl includes a terminal viewer:

```sh
~/.cargo/bin/reims-ctl view tahoe --fps 15 --protocol blocks
```

It requires a real tty. In a 30×100 pty it drew the live screen as half-block cells and
exited cleanly when sent `Ctrl-]`; the raw ANSI capture is
`evidence/host/tahoe/reims-view.raw`, with the observation in
`evidence/host/tahoe/viewer.txt`. Kitty/Ghostty can use the default (`kitty`) protocol.

## Cursor fix

The missing host pointer over the Reims image was a host-window issue, not a guest Metal failure.
`crates/reims-vgpu-window/src/sdl/shell.rs` used to call `SDL_HideCursor()` whenever the guest cursor
was hidden (macOS 12's AppleParavirtGPU ABI reports binary version 0 and can leave it hidden). The
local fix removes that early hide and leaves the last guest glyph, or SDL's arrow, visible by calling
`SDL_ShowCursor()` unconditionally in `refresh_cursor`.

The patch is saved as `scripts/reims-cursor.patch`, and the installed QEMU was built after it:

```text
source patch:  2026-11-05 02:54
installed QEMU: 2026-11-05 02:58
symbol:  <reims_vgpu_window::sdl::shell::Shell>::refresh_cursor
VM:      first started 2026-11-05 10:29; after the VRAM failure it restarted from the same QEMU
```

If the host pointer still disappears over the image after a future Reims upgrade, rebuild QEMU from
the patched source and verify the `refresh_cursor` symbol before changing guest settings. Do not
apply this patch to a newer source tree blindly; re-read the cursor code first.

The host screenshot `evidence/host/tahoe/reims-window.png` now shows the Reims window with the
guest desktop and a visible host cursor arrow over the image. The installed binary was also checked
directly: `refresh_cursor` calls SDL's `SetCursor` and `ShowCursor`, and has no `SDL_HideCursor` call.
The disassembly/symbol record is `evidence/host/tahoe/refresh-cursor.txt`.

## Host VRAM limit

Repeated 1920×1080 and 1280×800 runs, including one after the Metal error mode was set, ended with a
Reims Vulkan `out of Device memory` failure after
`overrun write ... > max:4194304` messages. On this 4 GiB GTX 1650 Ti, `nvidia-smi` later showed
QEMU/Reims at 1152 MiB and Herdr at 1217 MiB. The same disk restarted to login and the desktop;
the failure is a host GPU/Reims limit (or firmware/driver interaction) under load, not a broken
macOS install. The current 1024×768 run is the working configuration but is not claimed crash-free. See `evidence/host/tahoe/reims-crash-note.txt`. Close heavy host GPU clients,
lower the guest resolution, close host GPU clients, and avoid continuous QMP screendumps if it recurs.

## Setup Assistant and first-boot glitches

- QMP mouse clicks selected radio buttons and menu entries but repeatedly did not activate the
  Setup Assistant **Continue** button. A real host Return key through `ydotool` did. Manual UI
  completion is equivalent.
- Immediately after Setup Assistant, one boot showed only the wallpaper with no menu bar or Dock.
  A VM reset produced the login screen; clicking the user avatar then reached the normal rendered
  desktop. Treat this as a transient loginwindow/WindowServer state, not a GPU setup failure.
  Reboot and log in before changing GPU configuration.

## Historical: Monterey limitation

The preserved macOS 12.7.6 `gui-proof` VM also enumerated `AppleParavirtualGPU`, but its
WindowServer did not present application windows or the Dock through the ABI; only wallpaper and
menu bar rendered. That is why the earlier documentation used the software-rendered
`host/tmac.py view` path for Monterey. macOS 26 Tahoe does present the complete desktop through the
same Reims device, so the old limitation is not a property of reims-vgpu on this host.

The macOS 12 guest remains useful as the fully software-rendered reference and for the
`host/tmac.py` MCP/terminal-viewer proof. Keep its `tmac`/`guest` credentials and
`~/.ssh/tmac_guest` workflow as documented in `docs/validation.md`; do not apply the Tahoe
`launchctl` or account details to it.

## Runbook

Complete details, credentials, SSH and evidence links are in [`tahoe.md`](tahoe.md). The short
version:

```sh
~/.cargo/bin/reims-ctl show tahoe
~/.cargo/bin/reims-ctl start tahoe
~/.cargo/bin/reims-ctl view tahoe --fps 15 --protocol blocks
ssh -i ~/.ssh/tmac_guest -p 2224 tmac@127.0.0.1
```
