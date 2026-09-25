---
paths:
  - "coscc/prsync.py"
  - "coscc/prcomment.py"
---

# What the app writes to GitHub on its own

- **`POST /api/units/review-comment` writes to GitHub under this machine's `gh` login.** It
  posts a round of `review.md` to the unit's pull request, verbatim and unfiltered: a
  finding that quotes a token or a local path goes up with it, and a public repository's
  pull request is public. Whoever holds the password or a live session can press it. The
  body is only ever the round's own text, and the marker on its last line stops a second
  copy. The trace is a `pr-comment` row in Activity and the comment itself. `run_step` also
  posts on its own after writing a review round, which can hold the `done` row for two `gh`
  calls of `prcomment.TIMEOUT` (`coscc/prcomment.py:38`) each on a slow network.
- **Every `pr` step that is not stopped rewrites its pull request's title and body under
  this machine's `gh` login.** After the step, `_sync_pr` reads `cos.mjs pr-text` and, when
  `pr.md` is `accepted` and names a pull request URL, runs `gh pr view` and — unless both
  already match — `gh pr edit` on the pull request `pr.md` names, holding the `done` row for
  two calls of `prcomment.TIMEOUT` each (chosen, not measured).
  - It overwrites whatever a person changed on GitHub since, and keeps the old text nowhere
    (`.cos/0055_*/spec.md` C1). A `pr.md` edited by hand to name another repository's pull
    request is written there.
  - The trace is one `pr-sync` row in the run log per step, with `existed` (the lookup
    before the step saw the pull request; `null` when that lookup could not answer — count
    those apart, not as `false`) and `outcome` `updated`, `already`, `failed` or `skipped`;
    no screen shows it. A `pr` step at a terminal leaves none.
- A review comment is not an approval, and no gate reads it (`.claude/docs/not-built.md`).
