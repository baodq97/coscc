# The trust model

Read this before adding a route, a button or a grant.

- **One password, no identities.** Every route, button and grant acts for whoever holds the
  password or a live session. `owner` is a label, not an identity; a name a request carries is
  written as sent. The default bind is all interfaces over plain HTTP, so off loopback the
  password crosses the network in clear.
- **`accepted` is an agent's word.** A separate agent's review is still an agent judging an
  agent's work. The loop waits for a person only at the review-round limit and at
  `needs-person`, and that stop is an agent's claim confirmed by another agent.
- **A gate is advice unless code enforces it.** Nothing forces a session to run `coscc.loop` or
  stop on non-zero; a hook could. Every session runs auto mode: the app refuses a few critical
  calls by their words and a classifier judges the rest, so assume a program it lets run can
  walk past both. What stops a merge or a force-push on `main` is the host's ruleset, not this
  harness.
- **A grant is the engine's, per run.** No route, button or agent writes one: the engine issues
  it as a run opens, from the agent's row and where the run stands (its worktree, the unit's
  branch, the vault's list of agents per secret), and it ends with the run. No grant, no action:
  a write, a push, a helper or an MCP tool the run's grant does not hold is refused, and the
  secrets are refused whatever it holds.
- **Whoever holds the password can widen what an agent may do, inside the critical calls.** A
  row is data: the built-in pack ships with the app, and the Agents page writes every part of
  it (model, ceilings, tools, input, output, prompt, skills) into the owner's layer
  (`<data root>/packs/local/`), each change logged `by: owner` and each run naming its
  `row_hash`. A tool set to `ask` is refused `asks-a-person`: no run asks a person yet. The
  critical calls read only the grant, so no row reaches the secrets, another branch, a
  background command, a nested helper, a merge or a release. No agent holds a tool that writes
  a row.
- **Whoever holds the password can write new skills.** `POST /api/skills/new` writes
  `local/skills/<name>/SKILL.md` (a new name only, no link on the way, at most 16 KB), the same
  trust as editing a prompt: every run of a row naming it is given its text, and its `start`
  names it as `name@hash`. An owner's skill wins over a same-named skill an update adds later.
- **Whoever holds the password can add agents, processes and packs.** `POST /api/agents/new`
  (a copy, a blank reader, or a whole `row`: Dagaz's draft, which holds no tool and writes
  nothing; its `end` keeps the draft and a person saves it) and `/api/agents/delete` write a whole
  row of the owner's pack `local`; `POST /api/packs/process`
  sets or removes a process `local/<name>`; `POST /api/packs/import` puts a third party's pack in
  `<data root>/packs/<name>/`, off in every workspace, and `POST /api/packs {delete: true}` removes
  one; `GET /api/packs/{name}/export` hands a pack out as a zip. Each writes packs only (the
  owner's configuration, no decision, answer or secret), logged `agent-setting` or `pack-setting`
  `by: owner`. Every row of every pack passes the same `pack.check` with the catalog and every
  process `check_process`, so an imported prompt, skill or composition reaches only catalog tools,
  a triggered row only reading ones and a sandboxed Bash, and the critical calls hold for any
  prompt. An import is a zip of at most 1 MB and 200 entries holding only the plugin folder's files, with no absolute
  path, `..` or link; its keys, skill names and name are no other pack's, and a refusal leaves
  nothing behind. Its text still reaches a session's prompt once a workspace turns it on and a
  run starts: read it first.
- **No list of programs, and no route to widen one.** What a session may run is auto mode's
  judgement plus the few critical blocks; a person adds nothing at runtime. The one place a line
  is read strictly (no substitution at all) is the vault's `vault_exec`, since it runs with secrets
  in it.
- **A press starts what it names and nothing more.** A person's answer, an outcome, a review
  comment, an integration, a hold, a stop, a release, a setting, a shortlist and turning a
  feature off each write a row and start no other stage. The autopilot, when a workspace turns
  it on, starts the next stage through the same gate, never releases, and is refused beyond
  loopback.
- **Answers reach gates.** An answer is a row rendered into the next prompt, with `by`
  (`person` or `delegated`) written as sent: a label, not an identity check, and no gate reads it.
  A finding answer also feeds a gate, so one agent's round plus an answer anyone with the
  password can write is part of what opens a merge.
- **A trigger starts a read-only run, not a writing one.** A row an event, a schedule or Leif
  starts holds only reading tools, and Bash only as `{"sandbox": {"network": [loopback host:port]}}`
  (`pack.check`, at load and at save), so nothing unwatched writes outside its own scratch folder.
  That Bash runs in Claude Code's OS sandbox (`sessions.sandbox_settings`): no unsandboxed retry and
  no run if the sandbox cannot start, writes only in the session's data root, the app's secrets and
  Claude Code's login unreadable, the network only those loopback hosts. A loopback service it
  reaches is the owner's to trust: what that service lets a request do, the run can do.
  `POST /api/agents/state` turns a row's event or schedule on or off per workspace (an
  `agent-state` row `by: owner`); `POST /api/agents/run` and Leif's `run_agent` (a kernel tool
  only the chat's grant holds, refused `not-leif` for a row without `trigger.leif`) each open one
  paid session under the row's ceilings and the daily cap, its `start` naming who started it.
  `POST /api/proposals/{id}` is the owner's accept or dismiss of a proposal, `by: owner`; no agent
  holds a tool that reaches it. `POST /api/runs/{run}/ask` opens one paid follow-up about an ended
  run (≤ $0.50, 3 turns, the daily cap): a triggered row's session is resumed with its grant less
  `submit`, any other run gets a new reader holding Read, Grep and Glob only; it writes nothing
  and its `start` names the `parent_run` and the question. `POST /api/runs/{run}/stop` cancels one.
- **A screenshot is an agent's look,** not a person's.
- **Say what a new route can do,** to whom, at what cost, and where the trace is. Prefer a row
  in the run log to a claim in prose.
