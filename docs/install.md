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

Four things this machine needs, none of which `scripts/install.sh` puts there for you in
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
- **`node`, for the Board screen only.** Every read of the Board runs
  `.claude/scripts/cos.mjs` as a child process (`coscc/board.py:90`), and that is
  JavaScript. Without `node` on `PATH` the Board answers
  `could not run node: [Errno 2] No such file or directory: 'node'` and the other five
  screens carry on working. `scripts/install.sh` does not install it and does not refuse
  without it. Any `node` your distribution ships will do — but see the trap below if yours
  came from `nvm`.

**If `node` came from `nvm`, the service will not find it.** `nvm` puts `node` under your
home directory and puts it on `PATH` from your shell's startup files; a systemd user
service reads neither. Measured 2026-09-22 on the machine this was written on: `node` was
at `~/.nvm/versions/node/v24.20.0/bin/node`, `command -v node` answered instantly in a
terminal, and the service's own `PATH` was the systemd default with no `nvm` anywhere in
it — so the Board failed while every check a person would think to run said node was
installed. The fix is one line in your env file
(`${XDG_CONFIG_HOME:-$HOME/.config}/coscc/env`), then
`systemctl --user restart coscc`:

```sh
PATH=/home/you/.nvm/versions/node/v24.20.0/bin:/usr/local/bin:/usr/bin:/bin
```

Use the directory `dirname "$(command -v node)"` prints. A `node` installed by your
distribution's package manager lands in `/usr/bin` and needs none of this.

**A session the app opens does not read your Claude Code settings.** Since `0088` every
session — each stage, chat, an integration, a proposal of estimates — starts with no
settings source: nothing from `~/.claude/settings.json` or `settings.local.json`, no MCP
server, no personal skill or plugin, and none of the `env` block in those files. Its
environment is the service's. So a proxy (`ANTHROPIC_BASE_URL`), or any other variable a
session needs, goes into the same env file, followed by `systemctl --user restart coscc`:

```sh
ANTHROPIC_BASE_URL=https://your-proxy.example
```

Left where it was, it is simply not used, and sessions go straight to Anthropic on this
machine's `claude` login. A machine whose login lives in that `env` block
(`ANTHROPIC_API_KEY`) or in `apiKeyHelper` has no login for the app's sessions until the
key is in the env file too. Your own `claude` at a terminal is unchanged.

**You do not need Node, npm or bun to *build* anything**, and that is a different sentence
from the bullet above. The release wheel carries the frontend already compiled, so nothing
on this machine compiles JavaScript; `node` is still what reads the Board at runtime.
Until `0012` this page said only the first half, which read as "you do not need Node" and
was wrong for anyone who opened the Board
(`.cos/0012_installed-copy-runs-no-stage/intent.md`).

The build half is worth stating because it is the one prerequisite this project got wrong:
the first wheel built for `0011` installed cleanly on a machine with no Node, reported
`active`, and served nothing at all — Reflex re-runs its compile on every start unless the
wheel carries the build state that lets it skip.
`.cos/0011_no-install-path-on-a-clean-machine/impl.md` records the measurement.

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
the only supported update path from a terminal — running it again *is* the upgrade.

### From the board

Since `0068`, an install made by `install.sh` can also update itself from the Board's
*Update* panel. The panel shows the version and commit running now and, per channel,
whether a newer build is ready:

- **release** — the app asks `https://api.github.com/repos/baodq97/coscc/releases/latest`
  once at start and every six hours, downloads the new wheel and its `SHA256SUMS` into
  `<COS_DATA_DIR>/updates/release/`, and shows it as ready only when the sha256 matches.
  The source is fixed in the code; nothing in a request or the environment changes it.
  `COS_UPDATE_CHECK=0` in the env file turns the asking and the downloading off.
- **local** — set `COS_UPDATE_LOCAL_FROM=<workspace name>` in the env file and the panel
  offers *Build from origin/main*: it fetches that workspace, checks that `origin` is
  `github.com/baodq97/coscc`, and runs `scripts/build_wheel.sh --local` from `origin/main`
  in a throwaway worktree. That runs upstream `main`'s build scripts under your user.

*Apply* waits until no step, integration, chat turn or local build of this process is
running, then applies. *Apply now…* lists what it would stop first; integrations are
never stopped, only waited for. Applying tries the new version beside the running one,
then stops the app, installs the wheel offline, checks `coscc --version`, and exits with
code 75 so `Restart=on-failure` brings it back. The page reloads itself when it answers
again. Every step is written to `<COS_DATA_DIR>/updates/logs/<time>-update.log`.

Applying keeps the running version's own wheel in `<COS_DATA_DIR>/updates/current/`, to go
back to. A release fetches it from its own GitHub release — at each check, or, with
`COS_UPDATE_CHECK=0`, when *Apply* is pressed, so that press needs the network. A local
build has no release to fetch from: once it is running, `current/` holds it only if it was
applied from the board, and otherwise the panel says `blocked` and offers no press.

**What does not come back by itself.** If the new version passes its trial but fails to
start for real, there is no board left to say so and nothing rolls it back: `systemctl
--user status coscc` shows it restarting. The log of that update ends with the command to
go back, which is:

```sh
systemctl --user stop coscc
cp <COS_DATA_DIR>/updates/cos.db.bak <COS_DATA_DIR>/cos.db
rm -f <COS_DATA_DIR>/cos.db-wal <COS_DATA_DIR>/cos.db-shm
UV_OFFLINE=1 UV_TOOL_DIR=<tool dir> UV_TOOL_BIN_DIR=<bin dir> uv tool install --force <COS_DATA_DIR>/updates/current/<old wheel>
systemctl --user start coscc
```

Copying the backup back loses whatever the new version wrote since it started; skipping it
leaves a database the old version may answer with 500 on every route.

**The board never rewrites the unit file or the env file.** A release that changes what
`install.sh` generates reaches this machine only through the `curl … | sh` line above.

**Anyone holding the master password or a live session can press these buttons**, under any
name — the password names nobody (see `## Logging in`). What they cannot choose is what gets
installed.

**The first update from the board past the release that adds the login fails its trial, on
purpose.** The version you are running checks a trial copy of the new one by asking
`/api/workspaces` for `200`, and from that release on the answer without a session is `401`.
The panel reports the trial failed and nothing is installed. Install that one release with
the `curl … | sh` line above; every update after it logs in to its own trial and passes.
**Then reload every board tab that was open before it.** Such a tab still runs the old page,
which reads the new `401` as an app that never came back: it says `đang khởi động lại…` and,
two minutes later, `không kết nối lại được`, pointing at the rollback below. Nothing failed —
the tab has no session. Reloading it lands on `/setup` or `/login`; a tab loaded from that
release on goes to `/login` by itself when its session ends. A browser that cached the old
page may show the old board until that reload too.

**Upgrading past the release that adds per-stage models changes which model runs.**
`COS_MODEL` no longer decides the model of the eight stages: each now ships with a default
(`claude-opus-5-5[1m]` for idea, intent, spec, plan and review; `claude-sonnet-5[1m]` for
impl, pr and ship), and Settings → *Which model runs each stage* overrides any of them
without a restart. `COS_MODEL` answers only chat and any stage with no default. If you had
set it to pin every stage, set those stages on that screen instead.

**Since `0031_shipped-model-defaults-cap-every-stage-at-200k`, every shipped default is the
`[1m]` variant** — a 1,000,000-token context window instead of 200,000. The non-`[1m]` ids
this section named before that unit capped every stage at 200k with nobody having decided
that on purpose: `impl` on `claude-sonnet-5` was measured auto-compacting twice around
167k tokens (`.cos/0031_shipped-model-defaults-cap-every-stage-at-200k/idea.md`). An
**override already saved in Settings does not move with this default**: anyone who had
overridden a stage to an id with no `[1m]` suffix stays at 200k for that stage until they
change or remove that override themselves.

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

## Logging in

**The default bind address is still `0.0.0.0`**, and since
`.cos/0070_anyone-who-reaches-the-port-can-run-anything` one master password stands in front of
every route: the page, its socket, every `/api` route. The only paths that answer without a
session are `/api/health` (`install.sh` probes it), `/login`, and `/setup` until a password is
set. It is one password for one person: whoever holds it, or a live session cookie, can do
everything the page can — including the two controls that spend real Claude account quota —
under any name they type.

**The first visit sets the password.** With no password stored, the service prints a setup
token to its log each time it starts, and `/setup` asks for it:

```sh
journalctl --user -u coscc | grep 'setup token'
```

Run by hand (`uv run coscc`), the line is on that terminal. Only someone who can read this
machine's log can set the password — anyone in the `adm` or `systemd-journal` group included,
until you have set it. After that the token in the log is dead.

**Forgot it?** On the machine running coscc, with the service running or not:

```sh
coscc reset-password
```

It removes the password and every session from `cos.db` and prints that file's path. The
running service notices on its next request: every browser is logged out, and the next visit
goes back to `/setup` with a new token in the log. There is no way to reset it over the web.

**A session lasts 30 days from its last use**; *Log out* at the bottom of the sidebar ends it.
When a session ends under an open board — it expired, you logged out in another tab, or
someone ran `coscc reset-password` — that board goes to `/login` the next time it asks `/api/update`, which it does every 5 s.
**Five wrong passwords from one address in a minute lock that address out** for 60 s, then twice
as long after each further failure, up to an hour. The count lives in memory: restarting the
service clears it.

**Plain HTTP is readable.** coscc serves no TLS. Off loopback, the password, the session cookie
and the setup token cross the network as anyone watching it can read; the startup banner and the
login page both say so. Put it behind a reverse proxy with TLS, or on a private network (a VPN,
an SSH tunnel, or `--host 127.0.0.1` at install time, see `## Options`). A proxy in front must:

- **pass `Host` through unchanged.** A request that changes something, and every websocket
  handshake, is refused with `403` when its `Origin` does not match `Host`.
- **carry the websocket at `/_event`.** The page does nothing without it.
- be on this machine for the session cookie to carry `Secure`: `X-Forwarded-Proto: https` is read
  only from a loopback peer.

Behind a proxy every client shares the proxy's address, so one stranger's five wrong tries lock
the owner out as well. `X-Forwarded-For` is never read; that is deliberate.

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
