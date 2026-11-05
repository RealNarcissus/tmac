# Changelog

All notable changes are recorded here. The project follows a lightweight semantic versioning
scheme; the CLI/MCP surface is still experimental.

## [Unreleased]

### Added

- `tmac attach` for registering an already-running QEMU/Reims VM by its QMP socket.
- External-VM status, screenshot, input, viewer, and MCP support without taking lifecycle ownership.
- `tmac mcp NAME` against the current Tahoe/Reims guest; verified with screenshot and status tools.
- Professional repository metadata: GPL-2.0-or-later license, security and legal notices,
  contributing guide, code of conduct, CI, packaging, issue/PR templates, and publishing guidance.
- Tahoe 26.6.2 / Reims vGPU evidence, including cursor visibility and the required
  `METAL_ERROR_MODE=5` preference.

### Changed

- Removed lab credentials and machine-specific paths from the public-facing documentation.
- The host is no longer required to have a local QEMU installation for `attach`/`mcp` against an
  external VM.
- The default guest SSH username is `guest`; configure `--guest-user` explicitly.

### Known limits

- The Tahoe guest is at 1024×768 for host-VRAM stability on the tested 4 GiB GPU. 1280×800 and
  1920×1080 can exhaust the Reims Vulkan device under load.
- The MCP surface is GUI-level computer use; app build/test CI is not yet demonstrated.
- macOS is x86_64 only in this setup; Apple silicon parity is out of scope.
- Apple licensing and third-party licenses must be reviewed before public/commercial deployment.

## [0.1.0] - 2026-11-04

Initial Linux x86_64 port of the terminal-mac control model: QEMU/QMP backend, terminal viewer,
MCP server, QMP broker, OpenCore/AMD helper scripts, and macOS Monterey evidence.
