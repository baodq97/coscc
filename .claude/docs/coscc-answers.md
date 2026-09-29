# Routes that write into a unit's artifacts

Read this before changing `POST /api/units/answer`, `/outcome`, `/hold` or `/more-rounds`, `POST /api/board/run`'s `rerun`, `coscc/units/hold.py`, `Service._append_answers` or `_append_to_answers`, `cos.mjs rerun`, or the runner's `## Answers` guard (`answers_section`, `strip_answers`, `with_answers`). Moved here whole from `.claude/rules/coscc-app.md` (`0094`); the history ("Since `00xx`") is kept at this tier.

- **`POST /api/units/answer` writes a stranger's words into a paid prompt.** Since `0016`
  it records an answer to an artifact, and the next stage's prompt carries it. Since `0135`
  it is a `unit_answers` row in `cos.db` and not a byte of the file; the prompt renders it as
  the `### Câu N` block it once appended, and `cos.mjs` reads it from `--state`. Since `0082` the
  board sends no name and the answer says `Answered by: owner`, a fixed word that is not an
  identity; a request that carries a name still has it written. Whoever holds the password
  or a live session can put text there that a stage will read as a person's decision. The
  only trace is the row and an `outputs` row with `actor = human:<name>` — `human:owner`
  from the board. Since `0106` every block also leaves an `answer`
  record in `runs`; it starts nothing, but the autopilot runs a draft again only on one
  written since the stage's last `start` (`autopilot.answered_since_start`), and counts it
  toward the two times it may (`autopilot.reruns_of`). A record lost to a busy run log is not
  counted, and an answer given before `0106` shipped left none.
  The password is what stands in front; `COS_HOST=127.0.0.1` still narrows who can try it. Since `0028` it also takes `question: "F<n>"` with
  `artifact: "review.md"` for a finding `cos.mjs` lists in `personFindings`, and that block
  does more than reach a prompt: `next` offers `review` once every such finding has one,
  and the `ship` gate counts a finding the review then marks `[answered]` as closed only
  when its answer exists. A stranger's answer plus one agent's round is part of what opens
  a merge.
  Since `0137` it also takes `delegation: "D<n>"`: while a `delegation` entered on Settings
  is in force in the workspace, whoever holds the password or a live session can write an
  answer under the name of the agent it names, ending `Theo ủy quyền: D<n>`; the row is
  recorded as `delegated`, as good as their own answer. Whether the question
  is one the delegation `covers` is not checked (that unit's spec ## Out of scope). The
  decisions are written only from the Settings screen, never over HTTP, but the screen
  cannot tell a person from an agent with a browser; the only trace is the `decisions` row.
- **Re-running a prose stage keeps `## Answers` byte for byte; a reply's own attempt at
  one is dropped, silently.** Since `0135` the section on disk holds only what was there
  before and the blocks the app still appends — `### Rerun`, `### More rounds`,
  `### Outcome`; answers and holds are rows, and a renumbered question points their `N` at
  another question just as a block's did. Since `0025` the runner (`coscc/runner/prompt.py`: `answers_section`,
  `strip_answers`, `with_answers`) reads the section already on disk right before it
  writes — not at the step's start — and writes it back after the stage's own text, on
  all five prose stages: `idea`, `intent`, `spec`, `plan` and `review`. Whatever a reply
  says under its own `## Answers` heading — copied from the artifact, forged, or a model
  answering its own question — never reaches disk, and nothing records that a reply tried.
  A window remains between the answer route's read and the runner's: the two hold no lock
  in common (the lock is `Service._answer_lock`, `coscc/service/__init__.py:82`, and `Runner` carries
  no reference to it). Byte-identical is not meaning-identical: a re-run that renumbers
  `## Open questions` leaves `### Câu N` on disk pointing at whichever question now
  carries that number, not the one a person answered
  (0025 spec C1). `impl.md` is
  still overwritten whole — a session writes it with its own tools, and this unit never
  covered it.
- **`POST /api/units/outcome` writes the ground for keeping or dropping a unit.** Since
  `0047`. On a `finished` unit it appends a `### Outcome` block (`Result:`
  `đạt` | `trượt` | `không đo được`, `Measured by:`, `Source:` or `Reason:`) under
  `intent.md ## Answers`, and the board labels the unit from the last valid one. Anyone
  holding the password can record `đạt`; the block's name is `owner` since `0082` unless the
  request carries one, `Measured by:` is `agent` or `owner` from the board since `0089` but
  still any word through the API, and `Source:` is checked
  against nothing. No gate reads
  the block. The trace is the block in the file and an `outputs` row with `source =
  outcome`. The password is what stands in front; `COS_HOST=127.0.0.1` still narrows who can try it.
- **`POST /api/units/hold` closes a pull request under this machine's `gh` login.** Since
  `0045`. `to: "dropped"` records a `dropped` hold — since `0135` a `unit_holds` row
  written in one transaction with its `hold` run-log row, no byte of `intent.md` — then runs
  `gh pr list` and `gh pr close <n>` for each open pull request whose head is the unit's
  branch (no `--delete-branch`), and `git worktree remove` without `--force` — a tree with
  uncommitted changes stays and the move reports `failed` for it. Each `gh` call waits up to
  30s (chosen). A side effect that failed cannot be retried from the board: `dropped →
  dropped` is refused, so the person closes the pull request or removes the tree by hand;
  the route's reply and the `hold` row on Activity say which. `paused` and `→ active` run
  no `git` and no `gh`. The hold is read by `cos.mjs` from `--state`: no stage is offered and
  every gate is closed, so anyone holding the password or a live session can stop every unit
  under a name they chose. It also reaches every later stage's prompt, rendered as a block of
  `intent.md`. An older `cos.mjs` reads the reason as the tail of the answer above
  it and offers the next stage again: downgrading past `0045` with held units is unsafe.
  `next_step` now asks `cos.mjs next` once more, files only, before opening a worktree
  (unmeasured). Since `0050` a move — and *Integrate* — is also refused while a step of
  the unit is still being prepared: the gate, the fetch (up to `FETCH_TIMEOUT` = 20s),
  `impl`'s `prepare` and `pr`'s `gh pr list`, a stretch whose length is unmeasured. A step
  in that phase is not on `/api/board/steps` and has no *Stop*, so the only thing to do is
  wait; the refusal says so and says since when.
- **`POST /api/units/more-rounds` opens a paid review round.** Since `0081`. On a unit
  `cos.mjs` marks `moreRounds`, it appends one `### More rounds` block (`Decided by: owner.
  Date: …. Via: product.`, `Rounds: 1`) under `review.md ## Answers` through
  `_append_to_answers`, and `cos.mjs` adds its rounds to that unit's review limit. It writes
  no run-log row, runs no step and does not wake the autopilot; the only trace is the block.
  A review the app runs keeps it, as it keeps every `## Answers` (the runner's guard above,
  `with_answers`); a review at a terminal or in a second app that
  rewrites `review.md` without it loses it, and the unit reads as out of rounds again.
- **`POST /api/board/run` with `rerun: true` makes every later artifact stale.** Since
  `0054`. For a stage `cos.mjs rerun <unit>` offers — `intent`, `spec`, `spike`, `plan` or
  `pr`, accepted, on a unit neither finished, held nor closed, its gate open —
  `Service.run_step` appends the `### Rerun` block `cos.mjs rerun <unit> <stage>` composed
  under `intent.md ## Answers` through `_append_to_answers` (the path `hold` writes by),
  after the gate and before the session. The block is `Requested by: owner. Date: …. Via:
  product.`, `Stage: <s>.`, and one `Stale: <file> sha256:<hex>` for the stage's artifact
  and each later one on disk: the hash of the text above that file's `## Answers`, trailing
  whitespace dropped. `cos.mjs` reads an artifact as stale while it still hashes to that
  value: `next` offers its stage before any later one, and every later gate is closed
  naming it — rerun `pr`, and `ship` stays closed until a new review round. An answer
  appended since changes nothing; only the stage writing its own text again does.
  `review.md` counts only while `accepted`, `spike.md` only while the spec still names a
  `U<n>`. The block names nobody: `owner` is S7's fixed word, and anyone with the password
  or a live session can make a unit's every later stage run, and cost, again with one
  press. The note is never in the block; it reaches the stage's prompt verbatim under
  *Why this stage runs again* and the `start` row as `rerun_note` (up to 4000 characters,
  chosen; empty allowed, `spec.md ## Answers, câu 2`). The autopilot is refused a rerun,
  but not what follows one: on a shortlisted unit it runs the stale stages after it as it
  runs any `next`. A block appended while the session then fails leaves the stage stale
  with no run, and `next` offers it again. A rerun that writes its artifact byte for byte
  as before leaves it stale, and `next` offers it again, unmeasured (spec C2). An older
  `cos.mjs` — a repository that copied `.claude/` before `0054` — reads no block, sees
  every artifact accepted, and may open `ship` at a terminal (spec C1). A `pr` rerun
  writes `pr.md` itself, so the app reads its `## Answers` first and says so in the step's
  `done` and in `end` (`answers_kept: false`) when that section is no longer the file's
  tail; nothing restores it. Since `0115` every `impl` step writes `impl.md` itself too, so
  the app reads its `## Answers` before the step and says `answers_lost` in the same way
  when that section is no longer the file's tail; nothing restores it either. That compare
  cannot see a block appended while the step ran, so `_append_one` refuses an answer to an
  artifact while a step of the stage that writes it, any but the five prose stages, holds
  the unit in this process; the Questions tab still offers the box and shows the refusal.
