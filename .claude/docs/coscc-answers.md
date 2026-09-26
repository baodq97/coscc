# Routes that write into a unit's artifacts

Read this before changing `POST /api/units/answer`, `/precedent`, `/outcome` or `/hold`, `POST /api/board/run`'s `rerun`, `coscc/hold.py`, `coscc/precedent.py`, `Service._append_answers` or `_append_to_answers`, `cos.mjs rerun`, or the runner's `## Answers` guard (`answers_section`, `strip_answers`, `with_answers`). Moved here whole from `.claude/rules/coscc-app.md` (`0094`); the history ("Since `00xx`") is kept at this tier.

- **`POST /api/units/answer` writes a stranger's words into a paid prompt.** Since `0016`
  it appends an answer to an artifact, and the next stage embeds that file. Since `0082` the
  board sends no name and the block says `Answered by: owner`, a fixed word that is not an
  identity; a request that carries a name still has it written. Whoever holds the password
  or a live session can put text there that a stage will read as a person's decision. The
  only trace is the file and an `outputs` row with `actor = human:<name>` — `human:owner`
  from the board. Since `0106` every block, a person's or Jera's, also leaves an `answer`
  record in `runs`; it starts nothing, but the autopilot runs a draft again only on one
  written since the stage's last `start` (`autopilot.answered_since_start`), and counts it
  toward the two times it may (`autopilot.reruns_of`). A record lost to a busy run log is not
  counted, and an answer given before `0106` shipped left none.
  The password is what stands in front; `COS_HOST=127.0.0.1` still narrows who can try it. Since `0028` it also takes `question: "F<n>"` with
  `artifact: "review.md"` for a finding `cos.mjs` lists in `personFindings`, and that block
  does more than reach a prompt: `next` offers `review` once every such finding has one,
  and the `ship` gate counts a finding the review then marks `[answered]` as closed only
  when its block exists. A stranger's answer plus one agent's round is part of what opens
  a merge.
- **`POST /api/units/precedent` puts an agent's words into a paid prompt as decided.** Since
  `0044` it opens one paid session (Jera, grant `precedent`: no tools, one turn, $1.00
  chosen, not measured) and appends each answer that survives `precedent.verdicts` through
  `Service._append_answers`, the same path as a person's, headed `Answered by: Jera. …
  Via: precedent.`. Whoever holds the password or a live session can press it, as often as
  they like: `_take` stops only a second run on the same unit. What stands between Jera
  and a later stage is the app's filter, and it cannot see everything:
  - The category is Jera's word (`.cos/0044_*/spec.md` C2). A question about permissions
    that Jera files as `other` is answered and written.
  - The store is every answer in force in the workspace, Leif's included (C3), and the
    *Decision preferences* text, all sent word for word; nothing checks either for a
    company name (C4).
  - The later stage is told which blocks are Jera's (`runner._jera_answers`, R15), and the
    skills say to cite them as an inference; nothing checks that it does.
  - The board read and the write share `_answer_lock`, and a question a person answered
    while Jera ran is skipped, but the runner holds no such lock: a stage re-run that
    renumbers `## Open questions` while Jera runs leaves its `### Câu N` on the wrong
    question, and nothing says so (C6, the same window as below). `_take` keeps a step of
    the same unit out in this process only.
  - The whole store goes into one prompt, never cut; a store past the $1.00 ceiling is a
    failed `end` row with the money spent and nothing written (C7).
  - With the workspace's autopilot on and the unit on its shortlist, a Jera answer that
    clears the last open question lets the autopilot's next pass, at most
    `autopilot.POLL_SECONDS` (300 s) later, start the next stage on it unpressed — or,
    when the artifact is a `draft` of `intent`, `spec`, `spike` or `plan`, run that stage
    again (`0106`).
    `precedent` does not call `_autopilot_nudge`, which only delays that. The autopilot
    reads past Jera's own `start`/`end` rows (`autopilot.is_step`), so a Jera run neither
    lifts nor sets the stop on a failed step.
- **Re-running a prose stage keeps `## Answers` byte for byte; a reply's own attempt at
  one is dropped, silently.** Since `0025` the runner (`coscc/runner_prompt.py`: `answers_section`,
  `strip_answers`, `with_answers`) reads the section already on disk right before it
  writes — not at the step's start — and writes it back after the stage's own text, on
  all five prose stages: `idea`, `intent`, `spec`, `plan` and `review`. Whatever a reply
  says under its own `## Answers` heading — copied from the artifact, forged, or a model
  answering its own question — never reaches disk, and nothing records that a reply tried.
  A window remains between the answer route's read and the runner's: the two hold no lock
  in common (`_answer_lock` is `Service`'s, `coscc/service.py:131`, and `Runner` carries
  no reference to it). Byte-identical is not meaning-identical: a re-run that renumbers
  `## Open questions` leaves `### Câu N` on disk pointing at whichever question now
  carries that number, not the one a person answered
  (`.cos/0025_rerunning-a-stage-erases-what-was-added-to-it/spec.md` C1). `impl.md` is
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
  `0045`. `to: "dropped"` appends `### Dropped` under `intent.md ## Answers`, then runs
  `gh pr list` and `gh pr close <n>` for each open pull request whose head is the unit's
  branch (no `--delete-branch`), and `git worktree remove` without `--force` — a tree with
  uncommitted changes stays and the move reports `failed` for it. Each `gh` call waits up to
  30s (chosen). A side effect that failed cannot be retried from the board: `dropped →
  dropped` is refused, so the person closes the pull request or removes the tree by hand;
  the route's reply and the `hold` row on Activity say which. `paused` and `→ active` run
  no `git` and no `gh`. The block is read by `cos.mjs`: no stage is offered and every gate
  is closed, so anyone holding the password or a live session can stop every unit under a
  name they chose. The block also reaches every later stage's prompt as
  part of `intent.md`. An older `cos.mjs` reads the reason as the tail of the answer above
  it and offers the next stage again: downgrading past `0045` with held units is unsafe.
  `next_step` now asks `cos.mjs next` once more, files only, before opening a worktree
  (unmeasured). Since `0050` a move — and *Integrate* — is also refused while a step of
  the unit is still being prepared: the gate, the fetch (up to `FETCH_TIMEOUT` = 20s),
  `impl`'s `prepare` and `pr`'s `gh pr list`, a stretch whose length is unmeasured. A step
  in that phase is not on `/api/board/steps` and has no *Stop*, so the only thing to do is
  wait; the refusal says so and says since when.
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
  tail; nothing restores it.
