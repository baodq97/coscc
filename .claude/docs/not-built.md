# What is deliberately not built

Read this before adding a route, a button or a grant, and before copying this harness. Moved here whole from `.claude/CLAUDE.md` (`0094`).

- **A person's approval.** Since `0015` there is a step that can say "not yet": `review`
  runs on the open pull request, before the merge, and `ship` cannot merge until a round
  passes with nothing open. But the one saying it is a separate agent session, not a
  person, so `accepted` is still an agent's word about an agent's work. The chair is in
  front of the door now, and an agent sits in it — nobody who is not an agent approves
  anything. The loop waits for a person in two places: when `COS_REVIEW_ROUNDS` rounds have
  asked for changes and findings are still open; and, since `0028`, when a review round
  ends `Verdict: needs-person` — every finding left is one `impl` listed under
  `impl.md ## Needs a person` and the review accepted as needing one. That second stop is
  an agent's claim confirmed by another agent, not proof that the finding could not be
  fixed. Anyone copying this template should make that trade on purpose rather than
  inherit it.
- **Hooks.** Every gate is advisory: nothing forces a session to run `cos.mjs`, or to stop
  when it exits non-zero. A `PreToolUse` hook blocking `Write` while `plan.md` is `draft`
  would be one file. The same holds for the merge: in the app, the `pr` grant refuses the
  merge command, `gh api …/pulls/<n>/merge` and `gh alias set` with their flags removed,
  but `node -e` spawning `gh`, or an alias defined before the step, still walks past it,
  and at a terminal nothing refuses anything. What stops a merge before review is the
  `ship` gate being asked — and only when it is asked.
- **Anything that starts the next stage — unless a workspace turns the autopilot on.** An
  accepted artifact lights no gate. Since `0043` each workspace has an autopilot switch,
  off by default (`POST /api/settings/autopilot`). While it is on, the app itself starts
  the stage `cos.mjs next` names after each step or integration ends, after an answer, and
  every 5 minutes for a unit between `pr` and `ship`, through the same `run_step` a press
  uses, so the gate is asked there. It stops at an open question, where `next` awaits a
  person, at `ship` unless its own switch is on, at a Gebo `[needs-person]`, after a step
  that did not end `done`, and where the gate refuses; and at a daily cap for the whole
  app. None of that makes a gate more than advice or a grant more than a reading of words,
  and a step the autopilot starts is not a person's approval of anything. It never
  releases. It refuses to turn on while the app listens beyond loopback, but whoever holds
  the password or a live session can turn it on, raise the cap, or let it ship to `main`
  under this machine's `gh` login. The run log's `started_by` says which starts were its.
- **A person's answer is not an approval, and it starts nothing either.** Since `0016` the
  app has one place where a person answers an item under `## Open questions`: the
  *Questions* tab, or `POST /api/units/answer`. It appends a block under `## Answers` and
  a row to the run log, and that is all — no gate reads it and no stage runs because of it;
  the next stage finds it in its prompt when somebody presses the button. Since `0082` the
  board asks no name: `Answered by:` is `owner`, a fixed word the app writes for whoever
  holds the password or a live session. It is not an identity and does not say who
  answered. The route still takes a name sent with the request and writes that instead, so
  a script can write any name. Since `0070` there is one master password and it names
  nobody, and the next stage will read the answer as a person's decision.
  One kind of answer is read by more than a prompt. Since `0028` a finding the last review
  round marked `[needs-person]` is answered as `F<n>` into `review.md`, as a `### F<n>`
  block: `cos.mjs next` reads it to offer `review` again once every such finding has one,
  and the `ship` gate counts a finding that review then marks `[answered]` as closed only
  when that block exists. So a block anyone holding the password or a session can write,
  followed by one agent's round, is part of what opens `ship`.
  A third kind is no answer at all. Since `0047` a finished unit's outcome is recorded as a
  `### Outcome` block under `intent.md ## Answers` (`POST /api/units/outcome`, or the
  unit's *Outcome* panel): `cos.mjs` reads it into `outcome` for the board's label, and no
  gate and no `next` reads it — a unit that is `done` stays done whatever it says.
- **A review comment is not an approval, and no gate reads it.** Since `0021` the app posts
  each round of `review.md` to the unit's pull request as one ordinary review comment —
  never `gh pr review` — under this machine's `gh` login, verbatim, first line saying an
  agent session wrote it. It is a deliberate exception to "nothing of coscc's goes into
  the repository": the pull request is where the team reads. A round the board ran is
  posted when it is written; a round written at a terminal reaches the pull request only
  when someone presses *Post to PR* on the board, or calls `POST /api/units/review-comment`.
  Whoever holds the password or a live session can make this machine's login post a
  round through that route. Neither gate changes its answer because of a comment.
- **An integration is not an approval, and nothing starts one.** Since `0035` the board
  shows whether a unit between `pr` and `ship` has fallen behind `main`, and an
  *Integrate* button (`POST /api/units/integrate`) rebases it: the app itself through
  `gh pr update-branch --rebase` when GitHub reports no conflict, or Gebo — an agent
  session under the `integrate` grant, rules in `.claude/skills/integrate/SKILL.md` — when
  it conflicts, when CI went red after an integration, or, since `0052`, when
  `gh pr update-branch --rebase` was refused and the pull request's head has not moved,
  whatever the reason: a missing permission opens a paid session too, while a lapsed login
  fails the head's read as well and opens none. Since `0052` a press fetches `origin/main` first, so a unit the
  board counted `current` against a stale ref also has the button. Gebo's grant allows one push, with a
  lease bound to the head it began at, and refuses the other roads its own commands hold
  (`gh api`, `git send-pack`, an alias made during the step) — but, like every grant here,
  it reads words: `node -e` or `python -c` pushing by itself still walks past. Gebo stops with `[needs-person]` rather than drop one side. How
  a conflict was resolved is the app's or an agent's word; the next review round is the
  only thing that reads it. No board read, timer or finished step presses the button unless
  the workspace's autopilot is on (`0043`), which integrates a unit behind, conflicting or
  red — never after a `pass` round; and whoever holds the password or a live session can make this machine's `gh` login rebase a
  unit's pull request or open a paid session. It is not a stage and `cos.mjs`
  does not know it exists beyond the `betweenPrAndShip` field `status --json` carries.
- **Stopping a step is not an approval, and anyone holding the password can do it.** Since
  `0034` the board lists the steps running in a workspace, each with a *Stop* button
  (`POST /api/board/stop`). A stopped step ends `stopped` in the run log with
  `stopped_by`: `owner` since `0082`, a fixed word and not an identity, or whatever name
  the request carried; it writes no artifact, opens and closes no gate, and starts nothing.
  What it had already committed or pushed stays. A step stopped before its first turn ran
  nothing and leaves no line in the run log at all. Whoever holds the password or a live
  session can stop anyone's step.
- **A hold is not an approval, and it starts nothing.** Since `0045` the board can pause,
  drop or resume a unit (`POST /api/units/hold`), with one line of reason; the block's name
  is `owner` since `0082` (`stopped_by` above says what that word is and is not).
  It appends a block under `intent.md ## Answers` and a `hold` row to the run log; `cos.mjs`
  then offers the unit no stage and closes every gate on it. Resuming runs nothing either.
  Dropping also closes the unit's open pull request **with this machine's `gh` login** and
  removes its worktree; the remote and local branches stay. Whoever holds the password or
  a live session can pause every unit, or drop one and close its pull request. A move is
  refused while a step or an
  integration of that unit runs — but only one this process started; a chat, a terminal or
  a second app is not seen.
- **An update is not an approval, and anyone holding the password can press it.** Since
  `0068` an install made by `install.sh` updates itself from the Board
  (`POST /api/update/apply`, `/cancel`, `/build-local`). Applying can stop every running
  step and chat turn of this process when the person chooses *Apply now*, and restarts
  the process; the local build runs upstream `main`'s build scripts under this user.
  Whoever holds the password or a live session can do all of it; the run log's `by` says
  `owner` since `0082`, not who. The only constraint is what gets installed: a wheel from
  `github.com/baodq97/coscc` checked against its release's `SHA256SUMS`, or one this
  machine built from `origin/main`, and no request carries a URL, path, version or ref.
  Nothing in the repository enforces that constraint beyond the app's own code: a
  `SHA256SUMS` from the same release catches a torn file, not a compromised release, and
  anyone who can write to `COS_DATA_DIR` can place a wheel and a manifest that agree. It
  opens and closes no gate and starts no stage.
- **A shortlist is not an approval, and it starts nothing.** Since `0074` the board holds a
  backlog: an estimate per unit (value 1–5, effort S/M/L, a basis), relations between units
  (`liên quan`, `trùng`, `thay thế`, `phụ thuộc`), a computed order, and a shortlist of at
  most seven that a person writes. All of it is rows in the app's run log, not artifacts; no
  gate, no `next` and no run button reads any of it, and `cos.mjs` does not know it exists.
  `by` is `owner` since `0082` (a fixed word, not an identity; older rows keep the name
  someone typed); an agent's estimate says `agent:<session>`, and a
  person's estimate wins over an agent's whichever came later. *Propose estimates* opens one paid session under the grant
  `estimate`. Each board step's `start` row records where its unit stood in the shortlist,
  so `verify_0074 --measure` can tell afterwards whether work was taken from it.
- **Watching a step is not an approval, and it changes nothing.** Since `0073` a board step
  records every event of its session — each message, tool call and result, thought,
  refusal, turn, the cost at the end and the outcome — and the board shows them live to any
  tab that opens the step, and afterwards from the unit's timeline (`GET /api/board/events`,
  `/follow`). Whoever holds the password or a live session reads every command, path,
  thought and tool output of every step the board ran, unfiltered. They sit in `cos.db`
  for up to 30 days and 200 MB in total, purged only when the app starts, and they travel
  with `0068`'s `updates/cos.db.bak`. Watching opens and closes no gate, starts no stage,
  writes no row and reaches no step; *Stop* is still the only thing that acts on one. A chat,
  Gebo or a step at a terminal records nothing here. A step another copy of the app runs on
  the same data root writes into the same tables, but only that copy can follow it live;
  this one reads it as `ended-unknown` until it ends.
- **A screenshot is not a person's look.** Since `0083` a unit whose branch changes a file
  listed under `paths:` in `.claude/rules/ui-standard.md` cannot ship until its passing
  review round carries `### Screens`: `impl` takes the screenshots with
  `scripts/capture_screens.py`, and the one who looks at them is the `review` agent, reading
  the PNGs — not a person. The gate reads the words in `review.md`, never the images: a
  round can write the section without opening one, `Taken at` is impl's word passed on by
  review, and impl chose which screens to take. The originator took that trade
  (`.cos/0083_*/intent.md ## Answers, câu 1`); they still see the screens when they use the
  board, and what they dislike comes back as an idea.
- **A login that knows who you are.** Since `0070` every route — the page, its socket,
  `/api`, the static files, paths that do not exist — is refused without a live session;
  only `/api/health`, `/login`, and `/setup` until a password is set, answer.
  `coscc/auth.py` is that door, and it is one master password for one user: it proves
  someone holds the password, not who they are, so every `owner` above — and every name a
  request carries or an older row kept — is still only a word. The default bind is still `0.0.0.0` and the app serves plain HTTP, so off loopback
  the password, the cookie and the setup token cross the network readable until someone
  puts TLS in front. The setup token sits in the service's journal, readable by the `adm`
  and `systemd-journal` groups until the password is set. `coscc reset-password`, at a
  shell on the machine, is the only way back from a forgotten password.
- **CI that decides more than one thing.** Since `0015` CI decides whether `review` may
  begin: the gate reads the pull request's required checks and stays closed on red,
  pending or none. Nothing else reads it. A green check also measures a different
  interpreter than the one the proofs were measured on.
