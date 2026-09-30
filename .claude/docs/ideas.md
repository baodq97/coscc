# One idea, several units, several repositories

Read this before writing an `intent.md` that carries `Idea:`, `Repo:` or `Depends on:`, or
before changing how `cos.mjs` reads them.

## The files

An idea is one file, `.cos/ideas/NNNN_<slug>.md`, numbered on its own sequence by
`cos.mjs new-idea <slug>`, which prints the path and creates nothing. It lives in the store of
the workspace it was started in, its home. Its text is in that file and nowhere else.

```
# Idea: <title>
Author: the originator. Status: accepted.

## In their own words

<brief>

## Units

- <ws>/<NNNN_slug>
- <ws>/<NNNN_slug>. Depends on: <ws>/<NNNN_slug>.
```

The app appends one line under `## Units` for each unit opened from the idea, and rewrites
nothing above it. `readAll` passes over `ideas/`; `status --json` carries an `ideas` key only
when the store has that directory.

A unit opened from an idea is an ordinary unit in its own repository's workspace, with no
`idea.md`. Its `intent.md` declares, in the header:

```
Idea: <ws>/ideas/NNNN_<slug>.md. Repo: <ws>. Depends on: <ws>/NNNN_<slug>.
```

`Depends on:` only when the idea's line has one, and then the same list. `status --json`
attaches `idea`, `repo` and `dependsOn` to a unit only when its header carries them.

## References

- a workspace name is the app's (`valid_name`): 1–64 letters, digits, `.`, `-`, `_`; no `/`.
- a unit: `<ws>/NNNN_<slug>`, or `NNNN_<slug>` in the same store.
- an idea: `<ws>/ideas/NNNN_<slug>.md`, or `ideas/NNNN_<slug>.md` in the same store.

`<ws>` resolves through the snapshot `--state` carries: the app names every
workspace in it, leaving out a name two workspaces share. Another workspace's units are read
only to resolve a reference that names them; they are never listed, and their own links are
not followed. With no workspace of that name, the unit's own `Repo:` is its own store.

## What the gate does

A broken link — no idea file, no such workspace, a unit the idea does not list — is a problem in
`status` and closes nothing but `impl`.

`impl` stays shut while any `Depends on:` unit is not merged, and `next` answers
`stage: ""`, `why: "dependency"`, `action: "waiting on <ref> to merge"`. Merged means the
PR machine recorded that unit's `merged` (guard `merge-read`), or, for a unit the machine never
moved, that its `ship.md` was accepted by the import or a `ship` session before the merge was recorded
(`coscc/units/meta.py` `UnitMeta.snapshot`). An `accepted` `ship.md` read any other way is not
a merge. Neither `gh` nor `git` is asked: `gh pr view` run from another repository's checkout.

`impl` also stays shut when `Idea:` cannot be read, or when the idea's line for the unit and
the header disagree on `Depends on:`. The rule cannot rest on one copy an agent wrote; the
line under `## Units` is the app's.

The `impl` session may read the checkouts of the other workspaces the idea lists and of
its dependencies, and may not write there; `git -C` into one is refused. `python` could still
write there — the read boundary is not a sandbox (`.claude/rules/coscc-policy.md`).
