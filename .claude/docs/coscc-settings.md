# Settings and the backlog

Read this before changing `/api/settings/*`, model resolution, `/api/backlog/*` or the backlog.

- Model, effort, ceilings, agent names and the autopilot are settings for whoever holds the
  password. An agent's are its row: the built-in pack's, the owner's layer laid over it
  (`<data root>/packs/local/agents/<key>.md`, only what differs), then `COS_MODEL` for a row
  with no model. Every change is one `agent-setting` row with the old and new value, and each
  run's `start` names its row (`pack`, `row_hash`, `edited`). `POST /api/agents/field` saves one
  key whole (`body`, `skill:<name>` too; `null` resets) after `pack.check` with the catalog and
  the contracts; `trigger` is saved only on a row its own trigger starts. A model id is not checked when
  saved; a wrong one fails the next step. A hand-edited owner file that breaks the row refuses
  that agent's runs (`agent-invalid`) and shows on the Agents page.
- A stage marked `novel` runs on a dearer row with higher ceilings, so one press can cost more.
  A temporary model trial can override a stage's model without showing on Settings; the step's
  `start` row names it.
- A step that hits its turn or $ ceiling pauses and keeps its session: a person raises the ceiling
  on the unit's page and it goes on where it stopped. A raise is a person's; the autopilot stops
  there and never raises one.
- An agent's name goes into every prompt, the `Author:` a session writes and the commit trailer;
  it opens and closes no gate.
- The autopilot turns on only on loopback, starts only shortlisted units (no shortlist starts
  nothing), and its daily cap counts every step end of the day, a person's too. It holds only
  the autopilot, never a press.
- A pack is on or off per project, and a project has a default process (`GET/POST /api/packs`, the
  prefs `packs.state` and `packs.process`; Settings › Each project). Off, a new unit or idea is
  refused `no-process` and units already open carry on; a unit records its process when it opens and
  never re-reads the default. A unit whose process no pack has, or lacks a state the unit
  recorded, is held `state-gone`.
- Packs live under `<data root>/packs/<name>/`, each a plugin folder; the built-in stays in the
  package. `local` is the owner's: a file there whose key another pack has is laid over that row,
  any other is a whole row of their own (`POST /api/agents/new`, `/api/agents/delete`), and its
  `process.json` holds their processes (`POST /api/packs/process`). An imported pack
  (`POST /api/packs/import`, a zip) is off until a project turns it on; off, its processes open no
  unit and its rows run on no event or schedule. A bad imported pack or `local` process is a
  problem on the page, never a crash. The loop is handed each unit's process in the snapshot
  (`processes`) and reads no pack under the data root.
- Backlog estimates, relations and the shortlist are run-log rows with `by`; `propose` opens one
  paid session. No gate reads them.
- A row no state runs may carry a `trigger` (`coscc/runner/triggers.py`): an `event` (at once, or
  `after_hours` later through `trigger_due`), a `schedule` (`hours` since its last `end`), `manual`
  (*Run now*, `POST /api/agents/run`) and `leif` (Leif's `run_agent`). Such a row holds only
  reading tools when an event, a schedule or Leif starts it. An event or a schedule runs it only
  where it is on: `default`, or the pref `agents.state` (`POST /api/agents/state`, an
  `agent-state` row `by: owner`). A run that stops at its ceiling turns it off there with an
  `agent-state` row `by: app` and a notice. A row whose input says `skip_when_empty` and finds no
  intervention ends `skipped` at $0 with no session.
- A row whose output is `proposal` puts what it proposes in `proposals`, shown on Up next; only
  the owner's press (`POST /api/proposals/{id}`, accept or dismiss with a reason) moves one.
