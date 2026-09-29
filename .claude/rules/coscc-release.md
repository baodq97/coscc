---
paths:
  - "coscc/github/release.py"
  - "coscc/service/release.py"
---

# Things that break here

- `POST /api/release/prepare` and `/publish` commit, push, merge and tag under this machine's
  `gh` login, for whoever holds the password (default bind `0.0.0.0`). *Prepare* commits the four
  version files on `chore/release-X-Y-Z` in `<COS_DATA_DIR>/worktrees/<slot>/release`, pushes
  without force and opens a pull request; *Merge and tag* squash-merges with
  `--match-head-commit` and pushes a lightweight `vX.Y.Z`, which builds the release. No ruleset
  protects `v*` tags, so a tag on any commit already on `main` publishes without the release
  branch's CI. Every press is one `release` row.
- It runs the released repository's own `cos.mjs check-version`, `check-tag`, `check-branch` and
  `uv lock` (which may reach the network) as this process's user; no `uv sync`, `npm ci` or build.
- `gh pr merge --delete-branch` also deletes the local branch, so the tree is detached first;
  a fake `gh` in a proof does nothing locally. A push needs a credential helper in
  `~/.gitconfig`. The in-process mark stops a second press in this process only.
- A board read of a workspace with a `vX.Y.Z` tag costs one `node cos.mjs check-tag` per
  candidate, plus `gh pr checks` or `gh release view` and `gh run list`, each up to
  `gh.TIMEOUT`; offline reads `unknown` after that wait. Only a press fetches tags, so a
  proposal can be behind.
