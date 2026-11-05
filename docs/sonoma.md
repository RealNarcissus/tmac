# macOS 14 Sonoma clone (in progress)

The working Monterey VM and its disk are preserved. The Sonoma clone uses a
separate VM home (`work/sonoma/tmac-home`), a separate 35 GiB qcow2 disk, a
copy of OpenCore with the same 4-core AMD topology, VNC `127.0.0.1:18`, and
SSH host-forward `2223`.

Installer source: Apple full installer `14.8.8` (build `23J620`) through
`gibMacOS`, because `fetch-macOS-v2.py -s sonoma` returned Sequoia 15.4.1
(board `Mac-827FAC58A8FDFA22` serves the latest compatible recovery).
The full installer's `SharedSupport.dmg` supplies the Sonoma BaseSystem.

Status: installer download/extraction and boot validation are recorded here
when completed. Do not describe the clone as installed until `sw_vers` in the
clone reports Sonoma.

## Result (2026-11-05): blocked in Sonoma recovery

The exact Sonoma **14.6.1 (23G93)** recovery image boots its kernel but never
reaches the installer UI. It stalls in launchd with:

`shared_region: ... check_np(...) vm_shared_region_start_address() failed`

Observed variants: `-cpu host` (original feature list), 2-core and 4-core
topologies, `Haswell-noTSX`, no AMD patches/Intel CPU model, old and current
OpenCore kexts, and `vmware-svga`/`VGA`. Kexts beyond Lilu/VirtualSMC/MCE and
explicit AVX2 either reset the VM or triggered the same launchd failure.
Full logs and screenshots: `evidence/host/sonoma/attempt.md`,
`evidence/host/sonoma/shared-region-hang.png`.

The Monterey VM and disk were not modified; Monterey was restarted and reached
its login screen after the attempt. The Sonoma disk remains a separate clone
for a known-good AMD Sonoma EFI or a from-Monterey install trial.

### 8-vCPU/x2APIC re-test (2026-11-05)

Re-tested with the now-working Monterey topology: 8 logical CPUs (4 cores × 2
threads), `+x2apic`, AMD patches, MAT booter quirks, minimal kexts and VGA.
Sonoma 14.6.1 still fails in launchd with repeated
`vm_shared_region_start_address() failed` (`evidence/host/sonoma/8vcpu-shared-region.png`).
The topology fix that made Monterey an 8-vCPU guest does not fix the Sonoma
launchd/shared-region blocker.

### CryptexFixup retest (2026-11-05)

Added Acidanthera CryptexFixup 1.0.5 (AVX2/cryptex patch) to the Sonoma EFI.
With 8 vCPU/x2APIC it reached XHCI initialization but did not produce the installer UI;
with 4 vCPU it reset back to the OpenCore picker. CryptexFixup did not resolve the
`vm_shared_region_start_address()` failure. Evidence: `evidence/host/sonoma/cryptex-xhci-hang.png`.

### CryptexFixup + USB kexts (2026-11-05)

With 8 vCPU/x2APIC, CryptexFixup 1.0.5 and USBToolBox/UTBMap enabled, launchd
still reports `vm_shared_region_start_address() failed` for launchd and every
process (`evidence/host/sonoma/cryptex-shared-region.png`). This rules out
CryptexFixup/AVX2 as the sole cause for this recovery image.
