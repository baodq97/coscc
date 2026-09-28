---
paths:
  - "coscc/github/release.py"
  - "coscc/service/release.py"
---

# Cutting a release from the board (`0046`)

- **`POST /api/release/prepare` and `/publish` commit, push, merge and tag under this
  machine's `gh` login.** Whoever holds the password or a live session can publish a
  release; the default bind is `0.0.0.0`. *Prepare* commits the four version files on
  `chore/release-X-Y-Z` in the release worktree (`<COS_DATA_DIR>/worktrees/<slot>/release`),
  pushes it without force and opens a pull request. *Merge and tag* squash-merges it with
  `--match-head-commit` and pushes a lightweight `vX.Y.Z` onto the merge commit, which
  builds the release on GitHub. No ruleset protects `v*` tags (0046 spec ## Answers, câu 4),
  so a tag on any commit already on `main` publishes without the release branch's CI.
  Every press, refused ones included, is one `release` row in the run log.
- **It runs the released repository's own code.** `cos.mjs check-version`, `check-tag` and
  `check-branch` from that checkout, and `uv lock`, which may reach the network, under this
  process's user (0046 spec C3). No `uv sync`, `npm ci` or build.
- **What no test sees.** `gh pr merge --delete-branch` also deletes the local branch; the
  tree is detached first, and the fake `gh` of `verify_0046.py` does nothing locally
  (0046 plan Risk 3). A push needs a credential helper in `~/.gitconfig`, since
  `gitops.child_env` carries none (Risk 4). The in-process mark stops a second press in
  this process only (Risk 10).
- **A board read of a workspace with `cos.mjs` and a `vX.Y.Z` tag costs** one
  `node cos.mjs check-tag` per candidate tag, the `gh pr list` it now shares with the
  integration block, and `gh pr checks` on an open release pull request or `gh release view`
  and `gh run list` after a tag this app pushed — each up to `GH_TIMEOUT` (30 s, chosen).
  Offline, the block reads `unknown` after that wait. It reads the tags the last fetch
  brought; only a press fetches them (`fetch --tags`), so a proposal can be behind (Risk 7).
