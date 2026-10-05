# Release: cutting a release from the Work page

Read this before changing `coscc/features/release/__init__.py`: the `gh`, loop and `uv` calls, the
panel (`Release.view`), the two presses and the three routes are in it; the pure rules (states,
refusals, the version-file edits, the shape of the block, the record) are `rules.py`. The panel
and its two buttons on the Work page are `ui/index.tsx`.

## What it does

Off until a workspace turns it on (Settings, or `POST /api/features {cwd, name: release, state: on}`): a repository's release process is its own, and this one is coscc's (four version files, `chore/release-X-Y-Z`, `vX.Y.Z`). It is the one feature that writes git, through the kernel's own-tree writers, and it hands an agent no tool (`tests/features/test_features.py`).

Two presses, each a request of a person:

1. **Prepare** (`prepare`, offered in the state `ready`): fetches `main` with tags, reads the facts
   again, opens the release tree (a detached worktree of its own at `origin/main`), asks the
   loop's `check-version`, `check-tag` and `check-branch`, then cuts `chore/release-X-Y-Z`,
   changes the four version files (`pyproject.toml`, `package.json`, `package-lock.json`, `uv.lock`
   after a `uv lock`) and nothing else, commits, pushes the branch and opens the pull request.
2. **Merge and tag** (`publish`, offered in `pr-open` and `merged-untagged`): once the pull
   request's required checks are green and its head is the commit Prepare pushed, merges it with
   `--squash --delete-branch --match-head-commit`, then pushes `vX.Y.Z` onto the merge commit,
   which builds the release. From `merged-untagged` it starts at the tag.

A refusal is a 400 before anything changes. A failure after a change is a `failed` (or, once
merged, `merged`) record, and the release tree and a branch the app cut are taken away again.
One press per workspace runs at a time.

The panel's state is one of `nothing`, `ready`, `pr-open`, `merged-untagged`, `tagged`,
`published`, `unknown`, read again from git and `gh` on every read, never from memory.

## Routes

- `GET /api/release?cwd=`: the `release` block (`rules.ReleaseView`); `null` for a workspace that
  is not a git checkout or where the feature is off. A workspace the app does not have is a 400.
- `POST /api/release/prepare` and `POST /api/release/publish`: `{cwd, version}`, streamed like
  `/api/units/integrate` (NDJSON, a `done` line with the `release` record). While the feature is
  off for the workspace the answer is a 400 before any `git` or `gh` call and with no record.

## The record

Every press that gets as far as a workspace leaves one `release` record in the run log (`kind`
`release`, `unit` empty, `stage` `release`): `workspace`, `phase` (`prepare` or `publish`),
`version`, `proposed`, `last_tag`, `units` and `commits` (what the release carries and what has no
unit), `pr`, `head`, `merge_sha`, `outcome` (`opened`, `merged`, `tagged`, `refused`, `failed`)
and `detail` (the reason). The last record and the one that opened the pull request are what
decide `tagged` and whether Merge and tag may run on a head.

## What it owns

No tables. No bus events of its own: the panel reloads on `integration.ended` and `step.ended`,
as the board does. Its `gh` answers (`pr checks`, `release view`, `run list`) are held in
`Ctx.asks` and asked again in the background; the core cancels them when it goes down.

## Hazards

- **Prepare and Merge and tag commit, push, merge and tag under this machine's `gh` login**, for
  whoever holds the password or a live session. No ruleset protects `v*` tags, so a tag on any
  commit already on `main` publishes without the release branch's CI.
- **It runs the released repository's own `check-*` commands and `uv lock`** (which may reach the
  network) as this process's user.
- **Merging with `--delete-branch` also deletes the local branch**: the tree is detached first. A
  fake `gh` in a proof does nothing locally.
- **Only a press fetches tags**, so a proposal can be behind. A GET costs `git` reads, one
  `check-tag` per candidate tag (highest first, until one is a release) and, once a release tag
  is found, the board's held `gh pr list`; then `gh pr checks` on an open release pull request, or
  `gh release view` and `gh run list` after a tag this app pushed. It reads `unknown` offline.
- **The only writes are in the release tree**: the kernel's writers (`OwnTree`) refuse any other
  tree, any branch that is not `chore/release-X-Y-Z`, any tag that is not `vX.Y.Z` and any file
  that is not a version file.
