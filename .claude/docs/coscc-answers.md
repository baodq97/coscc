# Routes that write into a unit's artifacts

Read this before changing the answer, outcome, hold or more-rounds routes, `rerun`, or the
runner's `## Answers` guard.

- **An answer is a row, rendered into the next prompt.** It carries `owner` unless the request
  names someone; anyone with the password can write text a stage will read as a person's
  decision. A finding answer also feeds a gate.
- **A prose-stage rewrite keeps `## Answers` byte for byte;** a reply's own attempt at one is
  dropped silently. The runner reads the section right before it writes. `impl.md` and `pr.md`
  are written by the session, so their section is checked afterwards and not restored. A renumbered
  question leaves an old `### Câu N` pointing at another question.
- **An outcome is recorded for the board's label** and read by no gate.
- **A hold closes the pull request under the machine's `gh` login** and removes a clean
  worktree; a failed side effect is not retried from the board. A hold closes every gate and
  reaches later prompts. A move is refused while a step of the unit is preparing.
- **More rounds appends a block** that raises the review limit and writes no run-log row.
- **A rerun marks every later artifact stale** until its stage writes itself again (`coscc.loop
  rerun` lists them). The note reaches the prompt verbatim; a rerun costs, so one press can run
  every later stage again. A rerun that writes its artifact unchanged stays stale.
