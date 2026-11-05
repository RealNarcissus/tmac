# Legal and licensing notice

This is an independent interoperability and research project. It is **not affiliated with,
sponsored by, or supported by Apple Inc.**

## macOS licensing

macOS is proprietary software owned by Apple. Apple's software license generally restricts
virtualization of macOS to Apple-branded hardware. This repository does **not** include macOS,
an IPSW, an installer image, a recovery image, a preinstalled VM disk, or an Apple license.

You are responsible for complying with Apple's license terms, export controls, and local law before
creating or running a macOS guest. Do not use this project to distribute Apple software or to offer
a macOS cloud service unless you have the necessary rights. This is not legal advice; obtain
independent counsel for a commercial deployment.

The same caution applies to using this project for a commercial "macOS CI without a Mac" service.
A hosted service that provides macOS execution may require an Apple-authorized hosting arrangement.

## Third-party software

This repository contains independent Linux host code and documentation. It links to, calls, or
describes other projects that keep their own licenses:

- [`govijr/terminal-mac`](https://github.com/govijr/terminal-mac) — MIT; design inspiration, no
  vendored Swift source.
- [`steelbrain/reims-vgpu`](https://github.com/steelbrain/reims-vgpu) — GPL-2.0-or-later; the
  virtual GPU engine used for the accelerated Tahoe demonstration. The patch in
  `scripts/reims-cursor.patch` is a modification of that GPL-covered source.
- [`steelbrain/metal2vulkan`](https://github.com/steelbrain/metal2vulkan) — see upstream license.
- [OSX-KVM](https://github.com/kholia/OSX-KVM) — see upstream license.
- [AMD_Vanilla](https://github.com/AMD-OSX/AMD_Vanilla) — see upstream license.
- QEMU, OpenCore, OVMF, Pillow, WsgiDAV, and Cheroot — see their upstream licenses.

No third-party binary or source tree is vendored in this repository except the explicitly
identified cursor patch. `THIRD_PARTY_NOTICES.md` records attribution.

## Trademarks

Apple, macOS, Mac, Finder, Xcode, and related names are trademarks of Apple Inc. They are used here
only to describe interoperability and the tested environment. No Apple logo or trademark is used as
this project's branding.

## No warranty

The software is provided under the GNU General Public License, version 2 or later, without any
warranty. Virtualization, GPU translation, and macOS upgrades can change behavior without notice.
