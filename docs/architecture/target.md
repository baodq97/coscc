# coscc: the target architecture

Where the app goes as the Reflex page is replaced. It is written for the owner first and for
the agents who will build it second. Figures in §2 come from `main` at dd33c2b. The way the
state machine works stays in [fsm.md](fsm.md). Idea 0006 (agents and packs) built §5 and
changed §1, §3.3, §4 and §9; each says what runs now and what was not built.

## 1. What the app is

The app is where one person works with **Leif**, their chief of staff. Leif runs a team of
agents across several repositories, decides what it may, and asks only when it must. Every
other part serves that conversation: units, boards, costs and settings.

The architecture has three concepts, and only three.

| Concept | What it is | Changes when |
|---|---|---|
| **Core** | What coscc cannot exist without: the store, units and their state machine, the runner, Leif, git/GitHub, HTTP. | Rarely, and only through a planned kernel change. |
| **Feature** | Anything optional. Each one lives in one folder, builds only on the kernel, and can be added or removed with one line. | Often. This is where new work goes. |
| **Agent** | A row of a pack: a prompt and skills, a model and effort, tools (each `allow`, `ask` or `off`), ceilings, an input and an output contract, and a trigger. A pack also holds processes, the state machines that bind states to agents. The stage agents (Nauthiz, Kenaz, …), the scanner, the outcome grader and Dagaz, which drafts agents, are rows of the built-in pack `coscc-sdlc`. | On the Agents page and in Settings, not through code. |

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
  kernel.py        the one module a feature imports: Feature, Ctx, Tool, Guard, Block, the tool catalog
  packs/coscc-sdlc/ the built-in pack: agents/<key>.md rows, skills/, process.json   (data, no code)
  store/           cos.db: connection, migrations, prefs, the run log           (data.py, runlog/journal.py)
  loop/            the state machine and its rules                             (unchanged)
  units/           units, transitions, holds, worktrees, the one board read
  runner/          runs any row: run(agent, input), the grant, attempts, triggers, cost (+ agent/: packs, policy)
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

The features are `codegraph`, `notices`, `parallel`, `release`, `scratch` and `vault`
(`coscc/features/__init__.py`). What the first plan listed and where it went:

| Part | Where it is |
|---|---|
| `vault` | A feature with the `vault` catalog tool and the `vault-leak` guard; which agents may use a secret is the secret's own list. |
| `scan` | A row of the built-in pack (Sowilo, `agents/scan.md`) on a schedule; its proposals are the core table `proposals`, shown on Up next. No feature code. |
| `backlog` | Core (`units/backlog.py`, `leif/backlog.py`); the estimate is a row with an `engine` trigger. Not moved. |
| `update` | Core (`coscc/update/`). Not moved. |
| `model-trial` | Row data: a row's `model.trial`. |
| `insights` | Core (`leif/insights.py`): cost, runs and the grader's verdicts, per agent and project. |

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
    name,
    routes=lambda ctx: [...],  # routes under /api/<name>/
    tables=(...),  # created at start; only this feature reads and writes them
    agent=lambda ctx: Parts(...),  # catalog tools, guards, prompt blocks
    default="on" | "off",
    pilot=False,  # whether `pilot` (half the units) may be chosen
    status=...,  # its line on Settings
    on_set=...,  # told when a person set a state
    summary="...",
)
```

A feature's tool is a catalog entry (`kernel.Tool`: name, effect, tier) that an agent row
names; the engine issues the grant per run, so a feature declares no grant. A paid session is
never a feature's: it is a pack row with a trigger. Not built from the first sketch:
`grants=`, `events=`, `schedule=` and a settings model.

`Ctx` holds a few typed handles, each a narrow view of a core part:

| Handle | Gives |
|---|---|
| `ctx.units` | the workspace's units, creating one, the trunk tree, open pull requests |
| `ctx.runs` | the run log |
| `ctx.store` | a connection limited to the feature's own tables |
| `ctx.bus` | publish and listen; every subject's payload is typed |
| `ctx.settings` | the feature's state per workspace |
| `ctx.asks`, `ctx.required_checks`, `ctx.refuse_updating` | slow reads asked again in the background; a PR's required checks; refusal while an update runs |

A feature that needs something none of these give means a kernel change, planned first. This
rule is unchanged from `.claude/docs/code-and-tests.md`.

## 5. Agents are data

An agent is a row of a pack, `agents/<key>.md`; its frontmatter is data and its body the
system prompt:

```
name, glyph, description   who it is and what it is for
body, skills               what it is told
model, effort, variants    what it runs on (a `novel` variant for a dearer step)
ceilings {turns, usd}      where it pauses; a person raises and it goes on
tools {name: allow|ask|off} catalog entries; the engine derives the grant per run
input, output              the envelope it is given; the typed record it hands back
trigger, default           engine | event | schedule | manual | leif; on or off by default
```

- A pack is a Claude Code plugin folder: `.claude-plugin/plugin.json`, `agents/`, `skills/`,
  `process.json`. The built-in `coscc-sdlc` ships with the app and is never written; the
  owner's `local` pack is laid over it (a key it shares holds only what differs) and holds
  their own rows and processes; imported packs sit beside it, off until a project turns them on.
- A process binds each state to a row or an engine action (`open-pr`, `merge`); its ways on
  read output fields or named guards. The loop runs any process; the core names no stage.
- The runner runs any row. The loop still decides transitions; an agent only gives evidence
  (fsm.md §7).
- The Agents page shows and edits every part, builds new agents and processes, and exports or
  imports a pack as a zip; every save passes the same load checks as a pack.
- Dagaz drafts a row or a process from a task in words; the draft is checked at `submit`, kept
  on the run's `end`, and a person saves it. It writes nothing itself.
- Not built: a skills hub that pilots a skill per agent (a row lists its skills), and a
  capability layer above the output contracts (it waits for a second provider of one output).

## 5a. Leif learns

Leif reasons from what happened. It does not obey a list of rules. The owner's words (10-04)
were: everything comes from data and is distilled into knowledge used for reasoning, never
into sentences followed to the letter. Leif supports and recommends, and does more than help
take decisions. Everything should get better over time.

```
facts       what happened, append-only: runs, costs, tool errors, answers and who gave them,
            review findings, outcomes, holds, stops, interventions, what the owner chose
knowledge   distilled from facts by an agent: a claim, where it holds (project, stage, kind),
            the facts for and against it, how sure, when it was last checked
reasoning   Leif, on a question, a next step or a recommendation, reads the knowledge that
            bears on it and cites what it used
recommend   options with the one Leif would pick and why; the owner's choice, and later the
            outcome, are new facts
```

- **Facts are already kept**, in the run log and in `cos.db`, and every answer carries who
  gave it (`by`: `person` or `delegated`). Choices other than answers do not yet.
- **Knowledge is distilled, never typed in.** A distiller agent (scheduled, like scan) reads
  new facts and adds, strengthens, weakens or retires claims. A claim with no evidence left,
  or contradicted by outcomes, is retired. The owner can correct a claim, and the correction
  is a fact too.
- **Nothing is pushed into every prompt.** The knowledge store removed in #161 failed that
  way: notes injected wholesale, never measured, going stale (−3.9 % on its own measure). Here
  Leif retrieves what bears on one question and the agent sees only Leif's answer and its
  reasons.
- **Value is measured, on Insights**: how often a recommendation is taken, how often its
  predicted outcome happens, $ and turns per unit, and how often the owner steps in. A claim
  that is never used or keeps being wrong decays.
- **The old decisions and delegations are gone**: the `decisions` table, its Settings form
  and `DELEGATES`. An answer given for the owner is written `by: delegated`. Letting Leif act
  in the owner's place comes back as recommendations Leif may act on when the knowledge behind
  them is strong and the owner has let it (a fact, not a rule). The distiller, the knowledge
  and the recommendations are not built.

## 6. Contract between the app and the studio

- **Typed routes.** Every route takes and returns a Pydantic model. The ceiling on `dict[str, Any]` in route signatures is 0.
- **Generated types.** `npm --prefix ui run api` writes `ui/src/api.gen.ts` from FastAPI's OpenAPI. A test fails when the file is stale, so a change to the backend breaks the page's type check, not the screen.
- **One stream.** `GET /api/stream` (SSE, same login cookie) forwards bus events as `{subject, ...payload}`, each payload the `TypedDict` its subject declares (`coscc/bus.py` `SCHEMAS`, checked at `publish`; `unit.shipped{workspace, unit, sha, at}` once a merge is read). The studio's `useResource` names the subjects that make it read again. No screen polls. Server-side polls (autopilot 300 s, integrate 2 s, CI) stay.
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
| Core names no stage or agent | `tests/test_no_stage_names.py` |
| Every pack row and process passes the load checks | `pack.check`, `check_process` at load and at save |

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
     Done: `service/` is gone and `coscc/http/` (`app.py`, `routes.py`, `auth.py`, `studio.py`, `plugin.py`) is the top.
   - Remove the `reflex` dependency.
   - Delete the `decisions` table, its form and the `delegated` path (§5a).
4. **Features to folders.** `release` and `scratch` are features. `backlog` and `update` stayed core, `model-trial` became row data, and proposals are a core table.
5. **Agents as data.** Built by idea 0006: rows in packs, processes as data, packs on or off per project, triggers, scan and the outcome grader as rows, review by rubric, the Agents page that edits and builds them, pack export and import, Dagaz, and Insights. Not built: the skills hub.
6. **Leif learns (§5a).** Provenance on every answer and choice, the distiller agent and its knowledge, Leif's recommendations with their reasons, and their measures on Insights. Talk to Leif is built on it.

Steps 1 and 3 are kernel changes and are planned as units. Steps 2, 4 and 5 are ordinary
units on the board.

## 10. Not decided here

- Whether a feature's UI may add a whole page, or only slots and a settings form. The default is both, and the vault has a page.
- Multi-user access. It is one login, and every action is `owner` (`.claude/docs/not-built.md`).
- Moving the SQLite schema to another engine. There is no reason to.
