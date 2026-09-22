# Installing coscc

`coscc` runs as a systemd user service: install it once and it comes back on its own after
every reboot. This page is what `.cos/0011_no-install-path-on-a-clean-machine/intent.md`
measured as missing — before this unit, `README.md` and `docs/studio.md` together had **0**
occurrences of the word "install", and three things a clean machine needs were written down
nowhere.

## Linux and systemd only

`scripts/install.sh` refuses on anything else. macOS, Windows, and a Linux distribution
that does not run systemd as its init are out of scope
(`.cos/0011_no-install-path-on-a-clean-machine/intent.md:164-166`) — there is no fallback
path for them here.

## Prerequisites

Three things this machine needs, none of which `scripts/install.sh` puts there for you in
full:

- **Python 3.14.** The wheel declares `requires-python = ">=3.14"` (`pyproject.toml:14`).
  You do not need to install this yourself: `uv`, which the install script installs if it
  is missing, downloads a matching Python on its own when none is already on the machine.
- **systemd**, as this machine's init — see above. `scripts/install.sh` checks for
  `/run/systemd/system` and a `systemctl` on `PATH`, and refuses if either is absent.
- **The Claude Code CLI, signed in to an account.** Nothing in this install path checks for
  it, and its absence does not stop the page from rendering — that is deliberately as far
  as this unit's proof looks (`intent.md:168-171`). But without it, chat does not work: the
  Sessions screen's **Send** and a work unit's **Run** both start a Claude Code session
  under the hood, and both fail with no working credential if the CLI is not installed and
  logged in. Get it from Anthropic's own instructions; there is nothing coscc-specific
  about that step.

**You do not need Node, npm or bun.** The release wheel carries the frontend already
compiled, so nothing on this machine builds JavaScript. That is worth stating because it is
the one prerequisite this project got wrong: the first wheel built for this unit installed
cleanly on a machine with no Node, reported `active`, and served nothing at all — Reflex
re-runs its compile on every start unless the wheel carries the build state that lets it
skip. `.cos/0011_no-install-path-on-a-clean-machine/impl.md` records the measurement.

## Install

```sh
curl -LsSf https://github.com/baodq97/coscc/releases/latest/download/install.sh | sh
```

That one line: refuses unless this is Linux with systemd; installs `uv` if it is not
already here; downloads the release wheel for the version baked into that copy of
`install.sh` and checks its sha256 before touching anything; `uv tool install --force`s it;
writes `${XDG_CONFIG_HOME:-$HOME/.config}/coscc/env` if it does not already exist, and
never if it does; writes and enables
`${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/coscc.service`; runs
`loginctl enable-linger` for your account, so the service can start at boot before you have
logged in; starts it. No `sudo`, at any step.

When it finishes, the page is at `http://<this machine's address>:8790/` — or the host and
port you passed, see `## Options` below.

## Update

```sh
curl -LsSf https://github.com/baodq97/coscc/releases/latest/download/install.sh | sh
```

The exact same line. Re-running it fetches whatever `install.sh` the *latest* release now
publishes, which carries that release's version and checksum baked in, verifies and
reinstalls the wheel, rewrites the (generated) systemd unit, and restarts the service — all
without touching your env file.

**`uv tool upgrade coscc` typed on its own will not do this.** The release wheel's filename
carries its version number (`coscc-<version>-py3-none-any.whl`), so
`/releases/latest/download/` — the one URL that can be linked without knowing a version in
advance — cannot serve it under a stable name, and `uv tool upgrade` has nothing fixed to
ask for. This is a real trap for anyone who already knows `uv`: the command runs, prints
nothing alarming, and simply leaves the old version in place
(`.cos/0011_no-install-path-on-a-clean-machine/spec.md:189-192`). The install line above is
the only supported update path — running it again *is* the upgrade.

## Options

`scripts/install.sh` reads flags, not environment variables, and only on first install (an
update never revisits them once the env file exists). Piped into `sh`, there is no other
stdin left to read flags from, so they go after `sh -s --`:

```sh
curl -LsSf https://github.com/baodq97/coscc/releases/latest/download/install.sh | sh -s -- --port 9090 --host 127.0.0.1 --working-dir ~/projects
```

| Flag | Default | Written to |
|---|---|---|
| `--port` | `8790` | `COS_PORT` in the env file |
| `--host` | `0.0.0.0` | `COS_HOST` in the env file |
| `--working-dir` | unset | `COS_WORKING_DIR` in the env file, and the directory is created if it does not exist |

Changing any of these after the first install means editing
`${XDG_CONFIG_HOME:-$HOME/.config}/coscc/env` by hand, then
`systemctl --user restart coscc` — `install.sh` will not touch that file again on its own,
by design (see `## Update`).

## The default bind address is 0.0.0.0, and there is no authentication anywhere in this app

Say this plainly, because softening it would be the wrong kind of documentation: **this app
has no login, no token, no password, on any route.** Binding `0.0.0.0` means every machine
that can route to this one, on this port, can do everything the page can do — including the
two controls that spend real Claude account quota (sending a chat message, and running a
step of the SDLC loop). There is no in-between state; reaching the port is using the app.

This is a decision the project's originator made on 2026-09-22, not an oversight
(`.cos/0011_no-install-path-on-a-clean-machine/spec.md:158-170`). If that is not what you want,
pass `--host 127.0.0.1` at install time (see `## Options`) and reach the page over an SSH
tunnel or a VPN instead of exposing the port directly. There is no built-in access control
to fall back on if you leave it open.

## What install leaves behind, and why there is no uninstall

Installing turns on `loginctl enable-linger` for your account. That is system state kept by
`systemd-logind`, outside of anything `coscc` owns, and it **outlives** the install — a
later `uv tool uninstall coscc` does not turn it back off.

There is no `uninstall.sh` (`.cos/0011_no-install-path-on-a-clean-machine/spec.md:202-204`).
To undo an install by hand:

```sh
systemctl --user disable --now coscc
rm -f ~/.config/systemd/user/coscc.service
rm -rf ~/.config/coscc
uv tool uninstall coscc
loginctl disable-linger "$(id -un)"
```

That last line is the one step above that is not reversed by anything else here — decide
whether you still want linger on for other reasons before running it.

## Checking it

```sh
systemctl --user status coscc
curl -fsS http://127.0.0.1:8790/api/health
```

Use the port you installed with if it is not the default. `coscc --version` prints exactly
one line, `coscc <version>` — useful for confirming an update actually landed.
