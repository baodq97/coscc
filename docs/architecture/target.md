# coscc: the target architecture

Where the app goes as the Reflex page is replaced. It is written for the owner first and for
the agents who will build it second. Figures come from `main` at dd33c2b. The way the
state machine works stays in [fsm.md](fsm.md).

## 1. What the app is

The app is where one person works with **Leif**, their chief of staff. Leif runs a team of
agents across several repositories, decides what it may, and asks only when it must. Every
other part serves that conversation: units, boards, costs and settings.

The architecture has three concepts, and only three.

| Concept | What it is | Changes when |
|---|---|---|
| **Core** | What coscc cannot exist without: the store, units and their state machine, the runner, Leif, git/GitHub, HTTP. | Rarely, and only through a planned kernel change. |
| **Feature** | Anything optional. Each one lives in one folder, builds only on the kernel, and can be added or removed with one line. | Often. This is where new work goes. |
| **Agent** | A configured worker: a prompt or skill, a model, an effort level, tools, a grant, a budget, a trigger, and an output. Stage agents (Nauthiz, Kenaz, …) are agents. So is a scanner, and so is Dagaz, which makes agents. | Through the Agents page, not through code. |

Nothing else is a concept: no service layer, no ports, no adapters.

## 2. Why change

| Today | Cost |
|---|---|
| The page is Reflex: `screens/` and `state/` hold 10.7k lines, `build.py`, `frontend.py` and `ui.py` are Reflex-only, and auth carries a websocket recheck only for Reflex (`auth.py:336-410`, `:513-582`). | The page cannot look like a product. Its build is heavy, and its socket gives 403 from `localhost`. |
| `service/` is 10.6k lines in 20 modules. Half of it is domain (steps, answers, attempts); half is glue for the page (`common.unit_state`, `agents.AgentPage`, `activity`, `watch`). | Every change touches it. An agent reads glue to find logic. |
| One action reads the board up to 17 times: `board_reader.read` runs a loop subprocess from answers ×6, steps ×5, backlog ×2, and from ideas, board, release and models. | The board takes 1 to 10 s. |
| Routes return untyped JSON: 896 `dict[str, Any]`. The board's shape is built in 5 layers (`loop/rules.py:783` → `units/board.py:145` → `service/board.py:254` → attach integration, release, backlog → `service/autopilot.py:797`). | The page and the agents guess the shapes. |
| `Ctx` has 13 callables with stubs (`plugin.py:95-126`). Six of them exist for `scan` alone, and `ctx_of` reaches into `service` (`plugin.py:383-474`). Core names features: `scan` appears in `bus.py`, `service/attempts.py:51`, `agent/policy.py:329`, `:369-372`, and `units/submit.py:230`. The vault's paths are in `agent/policy.py:445`. | Adding a feature means editing the core. |
| Real time is per-screen Reflex tasks: `poll_running` every 5 s, `watch_board`, and `watch_attempts`. | None of it survives Reflex. |

## 3. Shape

```
coscc/
  kernel.py        the one module a feature imports: Feature, Ctx, Tool, Guard, Block, the read models
  store/           cos.db: connection, migrations, prefs, the run log           (data.py, runlog/journal.py)
  loop/            the state machine and its rules                             (unchanged)
  units/           units, transitions, holds, worktrees, the one board read
  runner/          runs one agent session: prompt, grant, attempt, cost, events (+ agent/)
  leif/            the decision loop: autopilot, decisions, delegations, the inbox, chat with Leif
  git/  github/    worktrees, PRs, CI, integrate                                (unchanged)
  http/            app, auth, routes by area, SSE stream, the studio's files    (api.py, auth.py, studio.py)
  features/<name>/ feature.py, README.md, ui/, one folder each
ui/                the studio: shell, core screens; it loads features' ui/ by glob
```

### 3.1 Dependency rules (enforced by `tests/test_layers.py`)

- A layer imports only layers below it: `http` → `leif` → `runner` → `units` → `loop`, `git`, `github` → `store` → `config`.
- `kernel.py` sits beside `http`. It is the only core module a feature imports.
- A feature imports `coscc.kernel`, its own folder, the standard library and third-party packages. It imports no other feature.
- Core never imports a feature and never contains a feature's name. A test greps the core for the names in `FEATURES`.

### 3.2 Core parts

| Part | Owns | From today |
|---|---|---|
| **store** | `cos.db`, migrations, table ownership, prefs, the run log, and the `BELL` ring | `data.py`, `runlog/journal.py`, `service/store.py` |
| **units** | A unit's facts and its one read: `units.read(ws) -> Board`. The read is cached per workspace, and a bus event invalidates it. Card state (`service/common.py:398-460`) moves next to the loop's `why`. | `units/`, `service/board.py`, `service/ideas.py`, `service/workspaces.py` |
| **runner** | Starts, resumes and stops one session for any Agent. It also owns the grant, the attempt machine, the step events and the cost. | `runner/`, `agent/`, `service/steps.py` (the step half), `service/attempts.py`, `service/resume.py`, `runlog/events.py`, `runlog/recovery.py` |
| **leif** | The pass that decides what runs next. Decisions and delegations with provenance (`service/answers.py:47-80`, `:826-938`). Answers and holds. The inbox (what needs the person). The daily cap. Chat with Leif. | `service/autopilot.py`, `units/autopilot.py`, `units/guide.py`, `service/answers.py`, `runlog/spend.py`, `service/sessions.py` |
| **git, github** | Worktrees, the PR machine, CI, integrate (Gebo) | `git/`, `github/` without `release.py` |
| **http** | The FastAPI app, the login guard, one routes file per area, the `/api/stream` SSE, the studio's files, and startup (the lifespan tasks now in `coscc.py:23-59`) | `api.py`, `auth.py` without the websocket parts, `studio.py`, `run.py` |

`service/` is dissolved. Each domain half moves to the part that owns it. Page glue is
deleted: the studio formats on its side.

### 3.3 Optional parts become features

| Feature | Today |
|---|---|
| `vault` | `features/vault.py` + `vault/`. Its paths leave `agent/policy.py:445` and are declared by the feature. |
| `notices`, `codegraph`, `parallel`, `scratch` | `features/*`. `scratch` is spread through runner, agent and service today, and becomes one folder. |
| `scan` | Becomes an **Agent** (run log in, proposals out) plus a small `proposals` feature that owns the Backlog proposals table. Its names leave core. |
| `backlog` | Estimates, relations, the shortlist: `units/backlog.py` + `service/backlog.py`. Leif reads the shortlist through the kernel. |
| `release` | `github/release.py` + `service/release.py` |
| `update` | `update/updater.py`. Core keeps only "suspend and resume sessions" (`service/__init__.py:246-407`). |
| `model-trial` | `agent/modeltrial.py` |
| `insights` | New: cost, runs and failing tool calls per agent and per project, read from the run log. |

## 4. The feature contract

```
coscc/features/<name>/
  feature.py    FEATURE = Feature(...)
  README.md     what it does, its routes, the events it sends and hears, its tables. An agent reads this, not the core.
  ui/index.tsx  its screens and slots, if any
tests/features/<name>/
```

`coscc/features/__init__.py` keeps its one line per feature.

```python
Feature(
    name, summary, default="on" | "off" | "pilot",
    routes=lambda ctx: [...],      # typed routes under /api/<name>/
    tables=(...),                  # created at start; only this feature reads and writes them
    agent=lambda ctx: Parts(...),  # tools, guards, prompt blocks (hooks.py today)
    grants=Grants(paths=..., protected=..., commands=...),  # what core policy now hard-codes
    events=("<name>.<verb>", ...), # subjects it may publish; listeners via ctx.bus
    schedule=Schedule(...),        # optional
    settings=Model,                # a Pydantic model; the studio draws its form
)
```

`Ctx` stops being a bag of callables. It holds a few typed handles, each a narrow view of a
core part:

| Handle | Gives |
|---|---|
| `ctx.units` | read a board or a unit; create a unit or an idea; move it only through the FSM |
| `ctx.runs` | read the run log, step events and interventions |
| `ctx.agents` | run an Agent with a grant and a budget, and get its submitted result |
| `ctx.store` | a connection limited to the feature's own tables |
| `ctx.bus` | publish its declared events; listen to any |
| `ctx.settings` | read its settings, per workspace |

A feature that needs something none of these give means a kernel change, planned first. This
rule is unchanged from `.claude/docs/code-and-tests.md`.

## 5. Agents are data

An Agent is a row, not code:

```
name, rune, role         who it is and what it is for
skill / prompt           what it is told
model, effort, turns, $  how much it may spend
tools, grant             what it may touch
trigger                  a unit stage, a schedule, a bus event, or Leif asking
output                   an artifact, proposals, a decision, a report
```

- The eight stage agents are seeded rows bound to FSM stages. Their settings move from code to the row. 0161 started this for model, effort and limits.
- The runner runs any row. The FSM still decides transitions; an agent only gives evidence (fsm.md §7).
- A skills hub turns skills on, off or pilot, per agent.
- Dagaz is an agent whose output is a new agent row, proposed to Leif.

## 6. Contract between the app and the studio

- **Typed routes.** Every route takes and returns a Pydantic model. The ceiling on `dict[str, Any]` in route signatures is 0.
- **Generated types.** `npm --prefix ui run api` writes `ui/src/api.gen.ts` from FastAPI's OpenAPI. A test fails when the file is stale, so a change to the backend breaks the page's type check, not the screen.
- **One stream.** `GET /api/stream` (SSE, same login cookie) forwards bus events as `{subject, workspace, unit}`. The studio's `useResource` names the subjects that make it read again. No screen polls. Server-side polls (autopilot 300 s, integrate 2 s, CI) stay.
- **Routes by area:** `workspaces`, `board`, `units`, `steps`, `leif`, `agents`, `settings`, `update`, and each feature under `/api/<name>/`. Routes that only the Reflex page has today are added with their screen: watch/events, rerun offers, the idea page, chat, decisions, activity and cost, editing and removing a workspace, and backlog history.

## 7. The studio

- React, TypeScript and Vite in `ui/`, built into `coscc/_studio/`. No router or state library.
- Core screens live in `ui/src/screens/`. A feature's screens live in `coscc/features/<name>/ui/` and are found by `import.meta.glob`. The sidebar and command bar list them from that glob and from `GET /api/features` (on or off).
- The design is direction A (Linear-like): briefing, inbox, Leif, decisions, work, unit, agents, insights, and "what Leif may do".

## 8. Rules an agent can trust

Each rule is a test, not a sentence:

| Rule | Test |
|---|---|
| Layers and no cycles | `tests/test_layers.py` (exists; layers updated) |
| A feature imports only the kernel | `tests/test_boundaries.py` (extended) |
| Core names no feature | new, in `test_boundaries.py` |
| No `dict[str, Any]` in a route | new ratchet, ceiling 0 |
| `api.gen.ts` is fresh | new, in `npm test` |
| A file over 800 lines is marked "still to split" and its count only falls | new ratchet. Today: `service/steps.py` 2691, `runner/step.py` 2375, `loop/model.py` 1586, and others. |
| Each feature's tests run alone | `pytest tests/features/<name>` |

An agent working on a feature should need its folder, `kernel.py` and this file, about 5
files. Today it needs `plugin.py`, `hooks.py`, `service/*` and the policy.

## 9. Order of work

Each step is one PR, and the app runs after each one.

1. **Kernel and contract.**
   - `kernel.py` (from `plugin.py` + `hooks.py`), typed `Ctx` handles, and `Feature.grants` and `events`.
   - Typed models on today's routes, `api.gen.ts`, and `/api/stream`.
   - Move `scan`'s and the vault's names out of core.
   - Proof: vault and scan run on it unchanged from the outside.
2. **The daily flows in the studio.** Inbox and answer, unit (run, stop, rerun, events), Leif's decisions, settings and autopilot, update. Each screen comes with the routes it needs. After each merge, the screen is checked by looking at it.
3. **The cut.**
   - `/` serves the studio.
   - Delete `screens/`, `state/`, `coscc.py`, `build.py`, `frontend.py`, `ui.py`, `rxconfig.py`, Reflex's auth parts, and their tests: about 11k lines of code and 5.5k of tests.
   - Move the lifespan tasks into `http`.
   - Dissolve `service/` into its owners. The board becomes one cached read.
   - Remove the `reflex` dependency.
4. **Features to folders.** `backlog`, `release`, `update`, `scratch` and `model-trial` move under `features/`, and the `proposals` feature comes out of scan.
5. **Agents as data.** Agent rows, the Agents page that edits them, the skills hub, scan as an agent, Dagaz, and Insights.

Steps 1 and 3 are kernel changes and are planned as units. Steps 2, 4 and 5 are ordinary
units on the board.

## 10. Not decided here

- Whether a feature's UI may add a whole page, or only slots and a settings form. The default is both, and the vault already has a page.
- Multi-user access. It is one login, and every action is `owner` (`.claude/docs/not-built.md`).
- Moving the SQLite schema to another engine. There is no reason to.
