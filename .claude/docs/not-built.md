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
- **A screenshot is an agent's look,** not a person's.
- **Say what a new route can do,** to whom, at what cost, and where the trace is. Prefer a row
  in the run log to a claim in prose.
