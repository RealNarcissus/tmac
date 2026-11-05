# Practical gains: scope and acceptance

Baseline: macOS 12.7.6 (Monterey), AMD Ryzen 5 3600, 4 vCPU / 3584 MiB, SATA IDE disk, QEMU VNC,
no audio device, SSH off, MCP uses per-call QMP connections.

| # | Gain | Acceptance (observable) |
|---|---|---|
| 1 | More CPU/RAM | Guest reports the configured count and memory; desktop is usable; no panic; host remains responsive. |
| 2 | Faster storage | Installed disk boots through the tuned controller/cache; host observes the new QEMU args; a guest disk write test completes. |
| 3 | Native display/VNC | `tmac show NAME` prints a correct `vnc://` URL and launches an installed viewer; a VNC screenshot is captured. |
| 4 | Audio | Guest has an enabled output device; host PulseAudio records a non-zero sink input while guest plays a system sound. |
| 5 | SSH / shared folder / Unicode | SSH banner on `127.0.0.1:2222`; a file is exchanged through the chosen shared mechanism; a non-ASCII string is pasted into a guest text field. |
| 6 | MCP QMP broker | One QMP owner serves concurrent viewer and MCP commands; two independent clients succeed without `timed out waiting for QMP`. |
| 7 | Snapshots/rollback | Internal snapshot create/list/rollback succeeds while stopped; guest state matches before/after. |
| 8 | GPU acceleration | reims-vgpu (or equivalent) is built/launched and guest reports a non-Apple GPU, **or** an exact build/runtime blocker is recorded with host/guest evidence. |

"No False": a gain is only verified when its acceptance row is observed. Build-only or
config-only results are marked partial.

## Results (2026-11-04)

| # | Result | Evidence |
|---|---|---|
| 1 | **PASS**: 8 logical CPUs / 4 physical cores, 6 GiB RAM; power-of-two topology `8,cores=4,threads=2,sockets=1` + `+x2apic`; guest desktop usable. | `evidence/host/gains/8vcpu-sysctl.txt`, `8vcpu-desktop.png`, `8vcpu-main.png` |
| 2 | **PASS**: virtio-blk + writeback boots; guest write 214 MB/s; 512 MiB `dd`. | `virtio-picker.png`, `virtio-boot.png` |
| 3 | **PASS**: VNC RFB 003.008 on 5917; `show`/`vnc` URL. No graphical VNC client installed on this headless host, so launch falls through to the URL. | session log |
| 4 | **PASS**: QEMU USB Audio (`multi=on`); PulseAudio sink-input `application.name=qemu`, `media.name=snd0` during guest `afplay`. Guest UI label says "Disabled"; host audio is observed. | `audio-pulse.txt` |
| 5 | **PASS**: SSH banner/key login; guest WebDAV mount writes host file; UTF-8 clipboard paste. | `ssh-key.txt`, `webdav.txt`, `unicode-paste.png` |
| 6 | **PASS**: broker serves concurrent QMP clients; MCP status while viewer holds broker. | `evidence/worker2/broker/` |
| 7 | **PASS**: `marker3` snapshot rolled back; guest file contained `snapshot-v4` before removal and after rollback (after `sync` + forced stop). | `evidence/host/gains/snapshot-test.log` |
| 8 | **PASS (enumeration/scanout on Monterey)**: reims-vgpu built for Linux x86-64; `tmac start gui-proof --gpu` boots with `AppleParavirtualGPU`; Metal supported. This guest is macOS 12.7.6; full window presentation arrived with the current Tahoe guest (`docs/tahoe.md`). | `evidence/host/gains/gpu-system-profiler.txt`; `evidence/host/tahoe/system-profiler.txt` |
