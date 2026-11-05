# Third-party notices and attribution

This repository does not vendor a third-party hypervisor, macOS image, or GPU-runtime source tree.
The following projects informed, support, or are modified by the work here.

## Terminal-mac

- Project: https://github.com/govijr/terminal-mac
- License: MIT
- Contribution: the MCP tool model, terminal-viewer concept, and the original Apple-silicon
  Virtualization.framework design. The Linux host code here is an independent implementation; no
  upstream Swift source is included.

## Reims vGPU and metal2vulkan

- Project: https://github.com/steelbrain/reims-vgpu
- License: GPL-2.0-or-later (see the upstream `LICENSE` and `LICENSES/` directory)
- Contribution: the x86-64 virtual GPU used by the current Tahoe guest, including `apple-gfx-pci`,
  the Reims QEMU integration, `reims-ctl`, and the terminal viewer.
- Modification: `scripts/reims-cursor.patch` changes
  `crates/reims-vgpu-window/src/sdl/shell.rs` so the host pointer remains visible when the guest
  cursor is hidden. That patch is distributed under the same GPL-2.0-or-later terms.

- Runtime: https://github.com/steelbrain/metal2vulkan — see the upstream license.

## OSX-KVM and AMD_Vanilla

- OSX-KVM: https://github.com/kholia/OSX-KVM
- AMD_Vanilla: https://github.com/AMD-OSX/AMD_Vanilla
- Contribution: the OpenCore/OVMF/QEMU boot recipe and AMD kernel-patch data used by the preserved
  Monterey workflow. See each upstream repository for its license and attribution requirements.
- The Python/OpenCore helper scripts in `scripts/` are independent implementations; they consume
  patch data and images supplied by the user or by those projects.

## QEMU, OpenCore, and OVMF

- QEMU: https://www.qemu.org/ (GPL-2.0-or-later)
- OpenCore: https://github.com/acidanthera/OpenCorePkg (see upstream license)
- OVMF: https://github.com/tianocore/edk2 (see upstream license)

No QEMU, OpenCore, or OVMF binary is committed here.

## Python dependencies

- Pillow — HPND; optional, used for JPEG screenshots and image conversion.
- WsgiDAV — MIT; optional, used by the shared-folder helper.
- Cheroot — BSD-3-Clause; optional runtime dependency of WsgiDAV.

`pip` resolves these packages from PyPI; their license files are installed with them.

## Apple

macOS, Mac, Finder, Xcode, and related marks are Apple Inc. trademarks. This project is not
affiliated with Apple. No Apple software or installer image is distributed. See [LEGAL.md](LEGAL.md).
