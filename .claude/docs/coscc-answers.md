# Routes that write into a unit's artifacts

Read this before changing the answer, outcome, hold or more-rounds routes, or `rerun`.

- **An answer is a row, rendered into the next prompt.** It carries `owner` unless the request
  names someone; anyone with the password can write text a stage will read as a person's
  decision. A finding answer also feeds a gate.
- **No file holds the answers a prompt shows:** every stage that declares `answers` gets the
  rows, each under its question; a reply is written as it comes. A renumbered question leaves an
  old answer pointing at another question.
- **An outcome is recorded for the board's label** and read by no gate.
- **A hold closes the pull request under the machine's `gh` login** and removes a clean
  worktree; a failed side effect is not retried from the board. A hold closes every gate and
  reaches later prompts. A move is refused while a step of the unit is preparing.
- **More rounds appends a block** that raises the review limit and writes no run-log row.
- **A rerun marks every later artifact stale** until its stage writes itself again (`coscc.loop
  rerun` lists them). The note reaches the prompt verbatim; a rerun costs, so one press can run
  every later stage again. A rerun that writes its artifact unchanged stays stale.
