# Publishing and release checklist

This document is for maintainers. It keeps a local, buildable checkout from becoming an
accidentally public Apple-software distribution.

## Before the first public push

- [ ] `LICENSE`, `SECURITY.md`, `LEGAL.md`, and `THIRD_PARTY_NOTICES.md` are present.
- [ ] No Apple installer, IPSW, recovery image, VM disk, OpenCore image, or private key is tracked:
      `git ls-files | grep -Ei '\.(img|qcow2|iso|dmg|ipsw|vmdk)$'` must print nothing.
- [ ] No passwords or private keys are tracked:
      `git grep -nE 'password|passwd|BEGIN .*PRIVATE KEY' -- ':!docs/agents'`
- [ ] The lab guest credentials were replaced with placeholders in user-facing docs.
- [ ] Screenshots in `README.md` and `docs/` contain no personal data.
- [ ] Unit tests pass: `python -m unittest discover -s tests -v`.
- [ ] Packaging installs in a clean virtual environment and `tmac --help` works.
- [ ] The external-VM MCP path is exercised against a disposable VM.
- [ ] The third-party license audit is current, including the GPL-covered Reims cursor patch.
- [ ] Read [LEGAL.md](../LEGAL.md) and obtain legal review for a commercial or hosted service.

## Repository settings

Suggested GitHub description:

> Terminal-first QEMU/Reims control plane and MCP server for self-hosted macOS VMs on Linux.

Suggested topics:

`macos`, `qemu`, `qmp`, `mcp`, `computer-use`, `gui-automation`, `virtualization`, `reims`,
`developer-tools`, `linux`

Suggested settings:

- Enable Issues and Discussions.
- Enable private vulnerability reporting.
- Require CI for `main`.
- Protect `main`; require pull-request review for outside contributors.
- Enable Dependabot alerts and security updates.
- Do not attach releases containing macOS images or Apple binaries.

## Create the remote

```sh
gh repo create tmac --private --source=. --remote=origin --push
```

After review, only if the legal and security checklist is complete:

```sh
gh repo edit --visibility public --accept-visibility-change-consequences
```

## Release

```sh
git tag -a v0.2.0 -m "tmac 0.2.0"
git push origin v0.2.0
gh release create v0.2.0 --generate-notes --title "tmac 0.2.0"
```

Release notes must state:

- This is an experimental interoperability project, not an Apple-supported macOS distribution.
- Users supply their own installer and are responsible for Apple's license terms.
- The tested host GPU/resolution and known Reims VRAM limits.
- No Apple software or installer is included.

## Do not publish

- Apple installers, IPSWs, recovery images, or preinstalled macOS disks.
- Credentials, SSH private keys, or unredacted personal screenshots.
- Claims that the project provides a free/licensed macOS cloud or a supported Mac replacement.
- Instructions that bypass Apple licensing or activation.
