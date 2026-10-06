# Routes that write a person's decision on a unit

Read this before changing the answer, outcome, hold or more-rounds routes, or `rerun`.

- **An answer is a row, rendered into the next prompt.** It carries a required `by`, `person`
  (a person's press) or `delegated` (decided for them), and a `name`, `owner` unless the request
  names someone. Both are written as sent: anyone with the password can write text a stage will
  read as a decision already made. No gate reads `by`; a finding answer feeds a gate.
- **No file holds the answers a prompt shows:** every stage that declares `answers` gets the
  rows, each under its question; a reply is written as it comes. A renumbered question leaves an
  old answer pointing at another question.
- **An outcome, more rounds and a rerun are `unit_decisions` rows,** shown in the unit's history;
  no file is touched. An outcome is read by no gate.
- **A hold closes the pull request under the machine's `gh` login** and removes a clean
  worktree; a failed side effect is not retried from the board. A hold closes every gate and
  reaches later prompts. A move is refused while a step of the unit is preparing.
- **More rounds raises the review limit** and writes no run-log row.
- **A rerun marks every later artifact stale** while it holds the record the rerun named
  (`coscc.loop rerun` lists them); a stage that runs again leaves a new record, even with the same
  text. The note reaches the prompt verbatim; a rerun costs, so one press can run every later
  stage again.
