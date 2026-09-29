---
paths:
  - "coscc/github/prsync.py"
  - "coscc/github/prcomment.py"
  - "coscc/github/prscope.py"
---

# Things that break here

- `POST /api/units/review-comment` posts a round of `review.md` to the pull request verbatim,
  under this machine's `gh` login: a token or local path in a finding goes up with it, and a
  public repository's pull request is public. `run_step` also posts after writing a round,
  holding the `done` row for two `gh` calls of `prcomment.TIMEOUT` (`coscc/github/prcomment.py:38`).
- Every `pr` step rewrites its pull request's title and body: `_sync_pr` reads `cos.mjs pr-text`
  and, when `pr.md` is `accepted` and names a pull request URL, runs `gh pr view`, `gh pr edit`
  and one `gh pr view --json changedFiles,additions,deletions,files` (`coscc/github/prscope.py`),
  up to three `prcomment.TIMEOUT` calls on the `done` row.
  - It overwrites what a person changed on GitHub and keeps no copy. A `pr.md` hand-edited to
    name another repository's pull request is written there.
  - The `pr-sync` run-log row has `existed` (`null` when the lookup could not answer: count it
    apart from `false`), `outcome` (`updated`, `already`, `failed`, `skipped`) and `scope`
    `verdict` (`match`, `mismatch`, `unread`). No gate reads it.
  - `_sync_pr` also runs before every `ship` step, before the gate: at most two `gh` calls, no
    scope read. The `ship` gate closes on a title that differs from `pr.md`'s.
