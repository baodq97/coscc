# Routes that write into a unit's artifacts

Read this before changing `POST /api/units/answer`, `/outcome` or `/hold`, `coscc/hold.py`, or the runner's `## Answers` guard (`answers_section`, `strip_answers`, `with_answers`). Moved here whole from `.claude/rules/coscc-app.md` (`0094`); the history ("Since `00xx`") is kept at this tier.

- **`POST /api/units/answer` writes a stranger's words into a paid prompt.** Since `0016`
  it appends an answer under a typed name to an artifact, and the next stage embeds that
  file. Whoever holds the password or a live session can put text there, under any name,
  that a stage will read as a person's decision. The only trace is the file and an
  `outputs` row with `actor = human:<name>` — watch for a name nobody recognises.
  The password is what stands in front; `COS_HOST=127.0.0.1` still narrows who can try it. Since `0028` it also takes `question: "F<n>"` with
  `artifact: "review.md"` for a finding `cos.mjs` lists in `personFindings`, and that block
  does more than reach a prompt: `next` offers `review` once every such finding has one,
  and the `ship` gate counts a finding the review then marks `[answered]` as closed only
  when its block exists. A stranger's answer plus one agent's round is part of what opens
  a merge.
- **Re-running a prose stage keeps `## Answers` byte for byte; a reply's own attempt at
  one is dropped, silently.** Since `0025` the runner (`coscc/runner.py`: `answers_section`,
  `strip_answers`, `with_answers`) reads the section already on disk right before it
  writes — not at the step's start — and writes it back after the stage's own text, on
  all five prose stages: `idea`, `intent`, `spec`, `plan` and `review`. Whatever a reply
  says under its own `## Answers` heading — copied from the artifact, forged, or a model
  answering its own question — never reaches disk, and nothing records that a reply tried.
  A window remains between the answer route's read and the runner's: the two hold no lock
  in common (`_answer_lock` is `Service`'s, `coscc/service.py:140`, and `Runner` carries
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
  holding the password can record `đạt` under any name; `Measured
  by:` is a word they typed too, and `Source:` is checked against nothing. No gate reads
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
