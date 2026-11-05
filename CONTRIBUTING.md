# Contributing

## Before you start

- Read [README.md](README.md), [SECURITY.md](SECURITY.md), and [LEGAL.md](LEGAL.md).
- For a security issue, do not open a public issue; follow [SECURITY.md](SECURITY.md).
- Do not commit Apple installers, IPSWs, VM disks, credentials, SSH private keys, or screenshots
  containing personal data.
- Keep the Linux control plane usable **without** a local macOS VM: unit tests must not require QEMU
  or Apple software.

## Development setup

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[mcp,share]'
python -m unittest discover -s tests -v
python -m compileall -q host scripts
```

The optional `mcp` dependency supplies Pillow for JPEG screenshots. The `share` extra supplies
WsgiDAV/Cheroot for the WebDAV helper. Neither is needed for the core unit tests.

## Change expectations

- Keep one behavior per commit where practical.
- Add or update a test for behavior that can regress.
- Update `docs/` when commands, architecture, or limitations change.
- Preserve the distinction between **observed**, **inferred**, and **unknown** claims in research
  documents. Do not turn a test fixture or a prose expectation into an observation.
- Do not commit generated runtime state under `work/`; it is ignored.
- External VMs are registered with `tmac attach`; do not make the CLI stop or start a VM it does not
  own.

## Pull requests

Include:

1. What changed and why.
2. The exact commands/tests run and their results.
3. Any new host/guest limitation.
4. The relevant evidence path, if this changes a validation claim.

Small, reviewable commits are preferred. By contributing, you agree that your changes are licensed
under GPL-2.0-or-later, consistent with [LICENSE](LICENSE).

## Code of conduct

Be technical, direct, and respectful. Harassment, doxxing, credential sharing, and instructions for
illegal distribution of Apple software are not acceptable. See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
