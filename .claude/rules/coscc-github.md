---
paths:
  - "coscc/github/prsync.py"
  - "coscc/github/prcomment.py"
  - "coscc/github/prscope.py"
  - "coscc/git/gh.py"
---

# Things that break here

- Every `gh` or `git` call on a board read or a step is bounded by a timeout, fails to
  "unknown" offline, and costs time or money: a new read says what it costs and does not repeat
  on a timer.
- A review comment is posted verbatim under the machine's `gh` login: a token or local path in a
  finding goes up with it, and a public repository's pull request is public.
- A `pr` step rewrites the pull request's title and body and overwrites human edits, keeping no
  copy.
