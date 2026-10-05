---
paths:
  - "coscc/features/release/**"
---

# Things that break here

- Prepare and publish commit, push, merge and tag under the machine's `gh` login, for whoever
  holds the password. No ruleset protects `v*` tags, so a tag on any commit already on `main`
  publishes without the release branch's CI.
- It runs the released repository's own `check-*` commands and `uv lock` (which may reach the
  network) as this process's user.
- Merging with `--delete-branch` also deletes the local branch: detach the tree first. A fake
  `gh` in a proof does nothing locally.
- Only a press fetches tags, so a proposal can be behind; a board read costs `gh` calls and
  reads `unknown` offline.
