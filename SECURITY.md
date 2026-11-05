# Security policy

## Scope

This project controls a virtual machine. The MCP server and CLI can send arbitrary keyboard and
pointer input to the guest through the QMP socket. Treat a running VM, its QMP socket, and its SSH
forward as privileged local resources.

## Threat model

- The MCP server is a **local stdio process**. It has no network listener and no authentication
  layer of its own.
- The QMP Unix socket is equivalent to control of the VM. Anyone who can open it can stop the VM,
  capture its screen, and inject input.
- The guest is not a security boundary for the host: QEMU, Reims, and the host GPU driver are
  trusted code. Run the VM as an unprivileged user and keep the host patched.
- Do not expose the QMP socket or guest SSH port to an untrusted network. The documented Reims
  setup binds SSH to `127.0.0.1`.
- Do not place credentials, API keys, or production data in a demo guest. Evidence files and
  screenshots are easy to publish accidentally.

## Reporting

Once this repository has a public GitHub remote, report vulnerabilities privately through GitHub
Security Advisories on the repository. Until then, do not disclose a working exploit in a public
issue; contact the repository owner privately.

Include the affected commit, host/guest versions, a minimal reproducer, and whether the issue
requires access to the QMP socket, SSH, or the guest desktop.

## Hardening checklist for users

- Use a dedicated, disposable VM for agent experiments.
- Generate your own guest account and SSH key; there is no default password in this repository.
- Keep the QMP socket in a directory readable only by your user.
- Do not run the MCP server as root.
- Limit guest network egress and do not mount host credentials into the guest.
- Review each tool call when using an agent that can write files or execute commands in the guest.
