# Settings and the backlog

Read this before changing `/api/settings/*`, model resolution, `/api/backlog/*` or the backlog.

- Model, effort, agent names and the autopilot are settings for whoever holds the password. They
  resolve override, then file, then environment; every change is one `setting` row with the old
  and new value. A model id is not checked when saved; a wrong one fails the next step.
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
- Backlog estimates, relations and the shortlist are run-log rows with `by`; `propose` opens one
  paid session. No gate reads them.
