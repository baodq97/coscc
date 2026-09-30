# The trust model

Read this before adding a route, a button or a grant.

- **One password, no identities.** Every route, button and grant acts for whoever holds the
  password or a live session. `owner` is a label, not an identity; a name a request carries is
  written as sent. The default bind is all interfaces over plain HTTP, so off loopback the
  password crosses the network in clear.
- **`accepted` is an agent's word.** A separate agent's review is still an agent judging an
  agent's work. The loop waits for a person only at the review-round limit and at
  `needs-person`, and that stop is an agent's claim confirmed by another agent.
- **A gate is advice unless code enforces it.** Nothing forces a session to run `cos.mjs` or
  stop on non-zero; a hook could. A grant reads words, not intent: assume any program it may
  start walks past it. What stops a merge or a force-push on `main` is the host's ruleset, not
  this harness.
- **A press starts what it names and nothing more.** A person's answer, an outcome, a review
  comment, an integration, a hold, a stop, a release, a setting, a shortlist and turning a
  feature off each write a row and start no other stage. The autopilot, when a workspace turns
  it on, starts the next stage through the same gate, never releases, and is refused beyond
  loopback.
- **Answers reach gates.** An answer is a row rendered into the next prompt. A finding answer
  and a delegation also feed a gate, so one agent's round plus an answer anyone with the
  password can write is part of what opens a merge.
- **A screenshot is an agent's look,** not a person's.
- **Say what a new route can do,** to whom, at what cost, and where the trace is. Prefer a row
  in the run log to a claim in prose.
