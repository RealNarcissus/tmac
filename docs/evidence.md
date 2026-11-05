# Evidence ledger

Append-only. Format: `E<n> | UTC time | agent | type | command/file | result`. Raw artifacts under
`evidence/`. Types: OBSERVED / INFERENCE / UNKNOWN.

## 2026-11-04

E1 | 2026-11-04T17:22Z | main | OBSERVED | `pwd; ls -la` | workspace empty except `.git`; no commits.
E2 | 2026-11-04T17:23Z | main | OBSERVED | `herdr --version` | `herdr 0.8.0`; `HERDR_ENV`/pane vars absent in tool shell.
E3 | 2026-11-04T17:26Z | main | OBSERVED | `herdr workspace list` | `server_not_running` before starting server.
E4 | 2026-11-04T17:37Z | main | OBSERVED | `git clone https://github.com/govijr/terminal-mac` | cloned at `$HOME/Desktop/agentProjects/Herdr/terminal-mac`, then moved to `upstream/terminal-mac`.
E5 | 2026-11-04T17:38Z | main | OBSERVED | `lscpu`, `ls -l /dev/kvm`, `free -h`, `df -h`, `qemu-system-x86_64 --version` | AMD Ryzen 5 4600H, svm+avx2, `/dev/kvm` present, 14 GiB RAM, 46 GiB free, QEMU 11.1.2.
E6 | 2026-11-04T17:39Z | main | OBSERVED | `herdr workspace create --cwd ... --label terminalMac` | workspace `wF`, root pane `wF:p1`.
E7 | 2026-11-04T17:40Z | main | OBSERVED | `nohup herdr server` | server running, socket `$HOME/.config/herdr/herdr.sock`.
E8 | 2026-11-04T17:41Z | main | OBSERVED | upstream `Package.swift`, `build.sh`, `VirtualMachine.swift`, `Mailbox.swift`, `Guest.swift`, `Viewer.swift`, `AgentServer.swift` | Apple frameworks; arm64-only; virtio-fs JSON mailbox; guest helper guarded by `hw.model` prefix `VirtualMac`.
E9 | 2026-11-04T17:42Z | main | OBSERVED | `gh release view govijr/terminal-mac` | only assets `terminal-mac-0.2.0-6-macos-arm64.zip` (+ sha256, get.sh); no x86_64 or Linux build.
E10 | 2026-11-04T17:44Z | main | OBSERVED | OSX-KVM README (raw GitHub) | supports AMD Ryzen, macOS Ventura/Sonoma/Sequoia/Tahoe, headless boot, offline install.
E11 | 2026-11-04T17:44Z | main | OBSERVED | reims-vgpu README (raw GitHub) | x86-64 Linux KVM path + QEMU `apple-gfx-pci` + Vulkan via metal2vulkan; reims-ctl automates create/start; `REIMS_VGPU_WINDOW=0` headless.
E12 | 2026-11-04T17:44Z | main | OBSERVED | metal2vulkan README search result | project description: Metal shader/device API backed by Vulkan; used as reims-vgpu runtime.

## Main investigation — macOS boot (2026-11-04, after worker2 baseline)

E13 | 2026-11-04T18:24Z | main | OBSERVED | `work/main/monterey/BaseSystem.img` + patched OpenCore + `-smp 4,cores=2,threads=2` + core-patch=2 | XNU booted: serial begins `Darwin Kernel Version 21.6.0 ... RELEASE_X86_64`; launchd/WindowServer/loginwindow start. Artifacts: `evidence/host/monterey-2x2-core2-40s.png` (sha256 4cb277…), `evidence/macos/monterey6-serial.log` (sha256 d9723…).
E14 | 2026-11-04T18:38Z | main | OBSERVED | guest RAM dump `work/main/ram.bin`, offset 108163896 | panic text: `Kernel trap at 0xffffff80059b8e79, type 0=divide error`; symbolization against `BootKernelExtensions.kc` places fault in `_cpu_thread_alloc` (XNU topology). Earlier `-smp 4,cores=2,threads=2` + core-patch=6 (or 6/6+6) reproduced it.
E15 | 2026-11-04T18:46Z | main | OBSERVED | `evidence/host/monterey6-95s.png`, `evidence/macos/monterey6-serial.log` | Kernel/userspace start, but GUI black; `shared_region: ... vm_shared_region_start_address() failed` appears for pid 1, WindowServer, loginwindow, securityd (401 lines in monterey6 serial log).
E16 | 2026-11-04T19:00Z | main | OBSERVED | `dmg2img` built from source; `catalina/BaseSystem.dmg2img.img`, `monterey/BaseSystem.dmg2img.img` | Both produce GPT images with EFI + HFS; HFS listing still has no `dyld_shared_cache*` files. `qemu-img` and `dmg2img` outputs are not the GUI fix.
E17 | 2026-11-04T19:05Z | main | OBSERVED | `script -q -c "host/tmac.py view alpine"` | viewer emitted 4 Kitty graphics escapes and alternate-screen enter; live graphical-terminal visual not verified.
E18 | 2026-11-04T19:06Z | main | OBSERVED | `printf initialize/tools-list | host/tmac.py mcp` | JSON-RPC initialize + 8 tools returned; MCP smoke passes.
E19 | 2026-11-04T19:06Z | main | UNKNOWN | `/sys/module/kvm/parameters/ignore_msrs` | value `N`; no passwordless sudo. OSX-KVM documents `1` as required; unverified host-level suspect.

E20 | 2026-11-04T19:15Z | main | OBSERVED | `evidence/host/catalina3-100s.png`, `catalina3/serial.log` | Catalina 10.15 with Penryn + 2x2 topology + AMD patches still stops at `EXITBS:START`; no Darwin banner. The documented Catalina workaround was not reproduced in this QEMU/OpenCore build.
E21 | 2026-11-04T19:12Z | main | OBSERVED | `evidence/host/monterey-gui-90s.png`, `monterey-gui-selected.png`, `monterey-gui-180s.png` | Non-verbose Monterey with the working 2x2/core-2 topology returns to the OpenCore picker after handoff; GUI not reached. `-v` is not the cause.
E22 | 2026-11-04T19:14Z | main | EXTERNAL | jprx/darwin-vm PR #1 | A QEMU macOS VM project calls `vm_shared_region_start_address()` failures **harmless boot noise** because QEMU does not emulate a real dyld shared cache. This weakens the earlier shared-region hypothesis; the GUI blocker is WindowServer/framebuffer, not the log line alone.

E23 | 2026-11-04T19:34Z | main | OBSERVED | `evidence/host/gui-proof-45s.png`; worker2 `evidence/worker2/shots/w2-mont-hostcpu-recovery.png` (sha256 db37c158…b48c99) | macOS Monterey Recovery desktop renders under QEMU: main CLI + worker2 OpenCore + `-cpu host` + `-smp 4,cores=4,threads=1` + `vmware-svga`. The earlier black screen was from masked CPU models / config, not the GUI itself.
E24 | 2026-11-04T19:52Z | main | OBSERVED | `evidence/host/install-du-4.png`, `install-erase-done.png`, `install-license.png`, `install-progress-start.png` | Erased 42.95 GB target as APFS, started Reinstall macOS Monterey; installer reports "About 2 hours and 19 minutes remaining". QMP mouse/keyboard drove Disk Utility and Install Assistant.

E25 | 2026-11-04T20:10Z | main | OBSERVED | `evidence/host/install-setup-1.png`, `setup-account-filled.png`, `setup-18.png` | Installer completed; macOS Setup Assistant ran; account `tmac` created; full desktop reached.
E26 | 2026-11-04T20:12Z | main | OBSERVED | `evidence/host/desktop-clean.png` | macOS 12.7.6 Finder desktop, menu bar and Dock render under QEMU; no GPU passthrough.
E27 | 2026-11-04T20:14Z | main | OBSERVED | `evidence/host/desktop-terminal-proof2.png` | `sw_vers` → ProductVersion 12.7.6 / Build 21H1320; `uname -a` → Darwin 21.6.0 x86_64. QMP Cmd+Space, typing and Return worked.
E28 | 2026-11-04T20:15Z | main | OBSERVED | `evidence/host/mcp-macos-screenshot.jpg` (JPEG 1280×800), `evidence/host/terminal-viewer-macos.log` | MCP `status` + `screenshot` returned the live macOS desktop; terminal viewer emitted 193 Kitty graphics escapes, alternate screen and cleanup.

E29 | 2026-11-04T20:35Z | main | OBSERVED | `evidence/host/kitty-viewer-macos3.png` | Real Kitty window under Xvfb renders the live macOS desktop through the terminal viewer; status bar reads `gui-proof 1280x800 live Ctrl+] quit`, and the guest Terminal shows `uname -a`. This is the terminal-first presentation, not just a QMP screenshot.

## UX optimization (2026-11-04)

E30 | 2026-11-04T21:06Z | worker2 | OBSERVED | `evidence/worker2/ux/bench-capture.json` | PPM median 6.99 ms vs old PNG+50ms-poll 300.42 ms; 15.9–43x capture speedup.
E31 | 2026-11-04T21:10Z | worker2 | OBSERVED | `blank-baseline2.mp4` vs `grab-fast8.mp4`, `blank-baseline.json`, `blank-fast.json` | Baseline 31/122 blank frames (25.4%, longest run 10); fast path 0/122 blank.
E32 | 2026-11-04T21:34Z | main | OBSERVED | `evidence/host/ux-production-grab.json`, `ux-production-grab.mp4` | Production viewer recording: 122 frames, 117 transitions, 28.77 effective fps, **0 blank frames**, mean consecutive diff 0.18.
E33 | 2026-11-04T21:37Z | main | OBSERVED | `evidence/host/ux-production-viewer-stats.json` | `capture_fps=20.47`, `used_shm=true`, 13 changed frames, draw 56.9 ms total over 13 frames (~4.4 ms/frame).
E34 | 2026-11-04T21:34Z | main | OBSERVED | `evidence/host/ux-fast-typed-clean.png` | Viewer + shm + per-transition input: `echo hello` shows `hello` in the guest; no per-frame clear.
E35 | 2026-11-04T21:35Z | main | OBSERVED | `tmac status` while viewer running | Concurrent QMP status succeeds after switching viewer to per-call connections.
E36 | 2026-11-04T21:22Z | main | OBSERVED | `/tmp/type-hi.png` then clean boot test | Batched key transitions caused repeated/stuck characters; per-transition commands fixed new input.

## macOS 26.6.2 Tahoe + Reims vGPU (2026-11-05)

E37 | 2026-11-05T05:10Z | main | OBSERVED | reims-ctl QEMU `tahoe` VM installed from `macOS-Tahoe-26.6.2-25G83-offline-installer.img`; Setup Assistant account `tmac` | macOS 26.6.2 booted to the Tahoe desktop after setup and one reset. QMP mouse selected Setup Assistant rows; a real host Return (`ydotool key 28`) activated Continue. Artifacts: `evidence/host/tahoe/desktop.png`, `docs/tahoe.md`.
E38 | 2026-11-05T06:02Z | main | OBSERVED | `ssh -i ~/.ssh/tmac_guest -p 2224 tmac@127.0.0.1 'sw_vers; uname -a; sysctl -n hw.ncpu hw.memsize; system_profiler SPDisplaysDataType'` | ProductVersion 26.6.2 / Build 25G83; Darwin 25.6.0 x86_64; 8 CPUs / 8 GiB; **Reims vGPU**, vendor 0x106b, device 0xeeee, **Metal Support: Metal 2**, 1920×1080 main display. Artifact: `evidence/host/tahoe/system-profiler-1920.txt`, `guest-1920.txt`.
E39 | 2026-11-05T06:03Z | main | OBSERVED | `~/.cargo/bin/reims-ctl show tahoe`; `reims-ctl command tahoe`; `launchctl` / SSH checks | VM running as pid 1602688, 8 vCPU, 8G, GPU automatic, SSH 127.0.0.1:2224. SSH key installed; `systemsetup -setremotelogin on` failed (Full Disk Access), `sudo /bin/launchctl load -w /System/Library/LaunchDaemons/ssh.plist` enabled the listener. Artifacts: `evidence/host/tahoe/show.txt`, `qemu-command.txt`.
E40 | 2026-11-05T06:05Z | main | OBSERVED | `reims-ctl view tahoe --fps 2 --protocol blocks` in a 30×100 pty, then `Ctrl-]` | Viewer exited 0; captured 19,796 bytes and 2,800 U+2580 half-block cells of the live 1920×1080 desktop (pre-lower-resolution). No `host/tmac.py` QMP bridge involved. Historical capture text; the raw file at `evidence/host/tahoe/reims-view.raw` now holds the later 1280×800 capture (E47).
E41 | 2026-11-05T06:12Z | main | OBSERVED | `git diff -- crates/reims-vgpu-window/src/sdl/shell.rs`; `stat` installed QEMU; `nm -C` on QEMU | Cursor path patched in `work/reims/reims-vgpu`: removed the `SDL_HideCursor` early return and left `SDL_ShowCursor()` unconditional in `refresh_cursor`. Patch saved as `scripts/reims-cursor.patch`; installed QEMU is dated 02:58 after source patch 02:54 and contains the `refresh_cursor` symbol; current Tahoe VM started 10:29 on that binary. This is a code/build verification, not a fresh host-screen capture.
E42 | 2026-11-05T06:20Z | main | OBSERVED | xdg-desktop-portal Screenshot; host screenshot `<host-capture>` | Reims window title `Reims vGPU — input captured (press Ctrl+Alt+Super to release)`; guest shows Tahoe wallpaper, menu bar, widgets, Safari window and Dock. The portal screenshot omits the pointer, so cursor visibility is not asserted from this image. Historical host capture: `<host-capture>`; E45 supersedes it with the cursor-visible evidence.
E43 | 2026-11-05T06:14Z | main | OBSERVED | tail of `logs/qemu.log` during the first Tahoe run | `overrun write ... + size:940 > max:4194304`, then `reims-vgpu: fatal: ... drawIndexedPrimitives ... out of Device memory`; QEMU exited. Restarted with `reims-ctl start tahoe`; login and full desktop recovered. Artifact: `evidence/host/tahoe/reims-crash-note.txt`.
E44 | 2026-11-05T06:23Z | main | OBSERVED | `nm -C -S` + `objdump -d` on installed QEMU 02:58 binary | `refresh_cursor` at `0x113d350`, size `0x221`, calls GOT `0x4488628` (SDL_SetCursor) and `0x4489660` (SDL_ShowCursor); no call to `SDL_HideCursor` at `0x117bb90`. This confirms the cursor patch was compiled into the running binary. Artifact: `evidence/host/tahoe/refresh-cursor.txt`.
E45 | 2026-11-05T06:30Z | main | OBSERVED | `defaults read /Library/Preferences/.GlobalPreferences METAL_ERROR_MODE`; host portal screenshot after reboot | Preference reads **5**. Reims window title is `Reims vGPU — input captured`; the host screenshot shows the Tahoe menu bar, widgets, Dock and a **visible cursor arrow** over the captured image. Historical host capture: `<host-capture>`; E45 supersedes it with the cursor-visible evidence.
E46 | 2026-11-05T06:33Z | main | OBSERVED | `reims-ctl set tahoe --resolution 1280x800`; restart and login; `system_profiler`; host portal screenshot | Current 1280×800 Tahoe desktop renders with menu bar, widgets and Dock; `METAL_ERROR_MODE` reads 5; system-profiler reports Reims vGPU / Metal 2 at 1280×800. The host screenshot shows the Reims window **with a visible cursor arrow**. QEMU/Reims VRAM at idle 640 MiB, leaving host headroom after the 1920×1080 out-of-memory run. Artifacts: `system-profiler.txt`, `guest.txt`, `desktop.png`, `reims-window.png`, `show.txt`, `qemu-command.txt`.
E47 | 2026-11-05T06:36Z | main | OBSERVED | `reims-ctl view tahoe --fps 2 --protocol blocks` in a 30×100 pty at 1280×800, then `Ctrl-]` | Viewer exited 0; 17,916 bytes, 2,520 U+2580 half-block cells of the live 1280×800 desktop. Current raw: `evidence/host/tahoe/reims-view.raw`; text: `viewer.txt`.

E48 | 2026-11-05T08:05Z | main | OBSERVED | `tmac attach tahoe --qmp-socket ... --width 1024 --height 768 --ssh-port 2224`; stdio MCP `initialize`, `tools/list`, `status`, `screenshot` | External VM registered without a pid; all 8 tools listed; status reported `external: true`, 1024×768; screenshot returned a JPEG of the Tahoe desktop. Artifacts: `evidence/host/tahoe/mcp/tahoe-mcp-smoke.json`, `tahoe-mcp-screenshot.jpg`; docs: `tahoe.md`.
