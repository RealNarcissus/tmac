# Sonoma clone attempt — 2026-11-05

- Installer: Sonoma 14.6.1 BaseSystem via valid MBA8,1 MLB; build 23G93 verified from
  `SystemVersion.plist`; raw `BaseSystem.img` 3.0 GiB.
- VM: separate `work/sonoma/tmac-home`, 35 GiB disk, OpenCore copy, VNC 18, SSH 2223.
- Monterey disk and VM were stopped, not modified.

## Observed

1. `-cpu host` original feature list, 4 vCPU, latest recovery, `-v -no_compat_check`:
   kernel reaches launchd, then stalls at:
   `shared_region: ... check_np(...) vm_shared_region_start_address() failed`
   and `Darwin Ignition Sequence`; no installer UI.
2. `-cpu Haswell` / `Haswell-noTSX` (OSX-KVM Sonoma note): stalls before XNU at
   `EXITBS:START`, or resets to OpenCore picker.
3. 2-core/1-thread topology with matching core-count patch: resets to picker.
4. Disabling AMD patches and using Intel CPU model: resets.
5. Adding explicit `+avx2,+fma,+bmi1,+bmi2`: resets; QEMU `host` already advertises
   these, so the failure is not simply missing AVX2 in the QEMU model.
6. Old OpenCore kexts with `WhateverGreen` enabled reset; disabling optional kexts
   reaches launchd but still hits the shared-region failure.

## Conclusion

The Sonoma **recovery kernel** is not usable under this AMD/QEMU/OpenCore combination.
This is a blocker in the Sonoma boot path, not the storage, VNC, or clone isolation.
Monterey remains the proven VM. Next reasonable path is a known-good Sonoma AMD
EFI/patched kernel or installing Sonoma from the Monterey guest and testing the
installed system (not attempted here).
