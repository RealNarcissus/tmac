# Support

This is an experimental project maintained on a best-effort basis.

## Where to ask

- **Usage/setup questions:** GitHub Discussions.
- **Reproducible bugs:** GitHub issue with the bug-report template.
- **Security issues:** use private GitHub Security Advisories; see [SECURITY.md](SECURITY.md).

## What to include

- Host distribution, kernel, Python, QEMU, Reims, and guest macOS versions.
- Whether the VM is managed by `host/tmac.py` or registered with `tmac attach`.
- The exact command and sanitized output.
- For display/input issues: resolution, host GPU, and whether the guest cursor/window is visible.
- For MCP issues: client name, transport (stdio), tool name, and sanitized request/response.

## What is not supported

- Distributing macOS installers or prebuilt macOS VMs.
- Circumventing Apple licensing or activation.
- Running untrusted production workloads in a demo guest.
- Guaranteed compatibility with every macOS release, GPU, or MCP client.
- Commercial "macOS CI" or hosted Mac services without an Apple-authorized arrangement.

See [LEGAL.md](LEGAL.md) and [docs/PUBLISHING.md](docs/PUBLISHING.md).
