# Practical-gain observations (2026-11-04)

| Gain | Observation | Evidence |
|---|---|---|
| RAM | macOS 12.7.6 reports 6 GB after `-m 6144`; 4 vCPU boots. | `ram-6gb.png` |
| 6 vCPU | QEMU/OpenCore 6-core topology hangs at `EXITBS:START` (4-core control boots). | host boot screenshots / session log |
| Storage | MacHDD on `virtio-blk-pci` + `cache=writeback` boots; `AppleVirtIOPCITransport` present; guest `dd` 512 MiB write = 214 MB/s. | `virtio-picker.png`, `virtio-boot.png` |
| VNC | QEMU VNC at 127.0.0.1:5917 returns `RFB 003.008`; `tmac show`/`vnc` print `vnc://127.0.0.1:5917`. | session log |
| Audio | QEMU USB audio with `multi=on`; host PulseAudio shows `application.name = "qemu"`, `media.name = "snd0"` while guest `afplay` runs. Guest labels the stream "Audio Output - Disabled" but audio reaches the host. | `audio-pulse.txt` |
| SSH | `SSH-2.0-OpenSSH_8.6` on host `127.0.0.1:2222`; key login works. | `ssh-key.txt` |
| Shared folder | Host WsgiDAV on port 8080; guest mount_webdav writes `guest.txt` into host `work/main/share`. | `webdav.txt` |
| Unicode | Guest `pbcopy` with `LC_ALL=en_US.UTF-8` + QMP Cmd+V pastes `Grüße 世界 🚀` into TextEdit. | `unicode-paste.png` |
| Broker | One broker socket serves concurrent `query-status` and `query-cpus-fast`; MCP status succeeded while viewer held broker. | worker2 broker evidence |
| Snapshots | `qemu-img` internal snapshot create/list/rollback via `tmac snapshot/snapshots/rollback`. | snapshot logs |
