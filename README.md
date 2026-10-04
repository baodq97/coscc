# coscc

`coscc` is **Chief of Staff**, on **Claude Code** — `cos` + `cc`.

That name says where this is going, not what it is yet. **The Chief-of-Staff function is
not built.** What exists today is the second half of the name working on the first half's
behalf: a local harness that runs an AI-native SDLC loop, plus a web page that drives Claude
Code sessions across projects. `pyproject.toml` describes the thing that exists; this
paragraph is the only place the destination is written down, and nothing in the repository
implements it.

A local AI-native SDLC harness: a unit of work moves through eight stages — `idea.md`,
`intent.md`, `spec.md`, `plan.md`, `impl.md`, `pr.md`, `review.md`, `ship.md` — each
artifact accepted and committed before the next begins. `idea.md` is optional and gates
nothing; the other seven are gated on the one before. The agent writes those artifacts and
accepts its own, so `Status: accepted` records readiness rather than approval;
`.claude/CLAUDE.md`, under `## What is deliberately not built`, says what was traded
away for that and what is left.

The harness is inside `.claude/`; the mechanical checks — numbering, gates, status — are the
app's `coscc.loop`:

| Path | What it is |
|---|---|
| `.claude/CLAUDE.md` | Every rule that holds in every session. Claude Code loads it automatically. Read this first. |
| `.claude/rules/` | Rules that load only when a session touches the files they name. |
| `.claude/skills/` | One skill per stage that runs a session — `write-idea` through `write-review` — plus `cos-status`. `pr` and `ship` are the app's own and have none. |

Work units live in `.cos/NNNN_<slug>/`. `docs/` holds the playbook this is built from.

```
uv run python -m coscc.loop status   # where everything stands
npm test                              # lint (ruff, ty), then the app
uv run ruff format && uv run ruff check --fix   # before a commit
```

How code and tests are checked, laid out and named: `.claude/docs/code-and-tests.md`.

Copying it into another repository means copying `.claude/`. Nothing else is needed, and
nothing lands in that repository's own tree.

## The page

**CoS Studio** is the app's page, at `/`: a React app in `ui/`, built into the package and
served by the same FastAPI process as the API it reads. Leif's briefing, what needs you, the
projects and their units, up next, agents, insights and settings.

To install it on a machine rather than work on it, see
[installing coscc](docs/install.md) — one line, a systemd user service, and it comes back
after a reboot. The two commands below are the *checkout* path, for working on the code:

```sh
npm --prefix ui ci && npm --prefix ui run build # build the page
COS_WORKING_DIR=~/projects uv run coscc         # then http://0.0.0.0:8790
```

**It binds every interface by default, and one master password stands in front of every
route** — the first visit sets it with a token printed to the service's log, and whoever
holds it, or a live session, can use all of it, including the two controls that spend real
quota. Over plain HTTP the password crosses the network readable; `docs/install.md`
`## Logging in` says what to put in front. `0.0.0.0` was a decision on 2026-09-22, not an
accident; `COS_HOST=127.0.0.1` puts it back on loopback, and the startup banner says which
one you are running. Its chat
sessions have **no tools** by default. The app keeps
its own state — the workspace list, the run log, interface preferences — in one SQLite
database under `COS_DATA_DIR`, which defaults to `~/.cos`. Workspaces themselves stay
under `COS_WORKING_DIR`; backing up one does not back up the other. Neither is settable
over HTTP.

Two controls spend real account quota and both say so before they are used: sending a chat
message, and running a step of the loop. See [the page guide](docs/studio.md).

```sh
npm run e2e   # the board in a browser, on temporary workspaces
```

It needs a browser and a free `COS_PORT`; stop the app first.
