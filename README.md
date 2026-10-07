# coscc

`coscc` is **Chief of Staff**, on **Claude Code** — `cos` + `cc`.

That name says where this is going, not what it is yet. **The Chief-of-Staff function is
not built.** What exists today is the second half of the name working on the first half's
behalf: a local harness that runs an AI-native SDLC loop, plus a web page that drives Claude
Code sessions across projects. `pyproject.toml` describes the thing that exists; this
paragraph is the only place the destination is written down, and nothing in the repository
implements it.

A local AI-native SDLC harness: a unit of work walks one **process**, a state machine kept
as data. Each state runs an **agent** or an engine action (open the pull request, merge), and
each way on reads a field of the last agent's typed output or a named guard. Processes and
agents come in **packs**, Claude Code plugin folders: the built-in `coscc-sdlc`
(`coscc/packs/coscc-sdlc/`) has the processes `full` (idea, intent, spec, spike, plan, impl,
pull request, review, merge, with a fast-lane branch from intent to impl) and `short`
(intent, impl, pull request, review, merge), and one row per agent (`agents/<key>.md`: model,
ceilings, tools, input, output, trigger; the body is the prompt). A project turns packs on or
off and picks its default process; a unit keeps the process it opened on.

On the page a person edits every part of an agent, builds new agents and processes (the
owner's pack, `local`), imports and exports packs as zips, and can ask **Dagaz** to draft an
agent or a process from a sentence, then read it and save it. An agent no state runs starts on
a trigger: a bus event, a schedule, a press, or Leif. Every agent runs on one runtime under a
grant the app issues for that run alone, in Claude Code's `auto` mode with a few critical calls
refused. The agent judges its own work, so a `judgement` records readiness rather than
approval; `.claude/docs/not-built.md` says what was traded away for that.

The harness is inside `.claude/`; the mechanical checks — numbering, gates, status — are the
app's `coscc.loop`:

| Path | What it is |
|---|---|
| `.claude/CLAUDE.md` | Every rule that holds in every session. Claude Code loads it automatically. Read this first. |
| `.claude/rules/` | Rules that load only when a session touches the files they name. |
| `.claude/skills/` | A person's skills: `cos-status`, `incident`. The agents and their stage skills are the app's pack, `coscc/packs/coscc-sdlc/`. |

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
