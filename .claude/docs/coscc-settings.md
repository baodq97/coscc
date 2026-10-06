# Settings and the backlog

Read this before changing `/api/settings/*`, model resolution, `/api/backlog/*` or the backlog.

- Model, effort, ceilings, agent names and the autopilot are settings for whoever holds the
  password. An agent's are its row: the built-in pack's, the owner's layer laid over it
  (`<data root>/packs/local/agents/<key>.md`, only what differs), then `COS_MODEL` for a row
  with no model. Every change is one `agent-setting` row with the old and new value, and each
  run's `start` names its row (`pack`, `row_hash`, `edited`). `POST /api/agents/field` saves one
  key whole (`body`, `skill:<name>` too; `null` resets) after `pack.check` with the catalog and
  the contracts; `trigger` is shown, not saved. A model id is not checked when
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
  never re-reads the default. A unit whose process no pack has is held `state-gone`.
- Backlog estimates, relations and the shortlist are run-log rows with `by`; `propose` opens one
  paid session. No gate reads them.
