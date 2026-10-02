# One idea, several units, several repositories

Read this before writing an `intent.md` that carries `Idea:`, `Repo:` or `Depends on:`.

An idea is one file, `.cos/ideas/NNNN_<slug>.md`, in the store of the workspace it started in;
`uv run python -m coscc.loop new-idea <slug>` prints the path and creates nothing.

```
# Idea: <title>
Author: the originator. Status: accepted.

## In their own words

<brief>

## Units

- <ws>/<NNNN_slug>
- <ws>/<NNNN_slug>. Depends on: <ws>/<NNNN_slug>.
```

The app appends a line under `## Units` for each unit opened from the idea and rewrites nothing
above it. A unit is an ordinary unit in its own repository's workspace, and its `intent.md`
header declares:

```
Idea: <ws>/ideas/NNNN_<slug>.md. Repo: <ws>. Depends on: <ws>/NNNN_<slug>.
```

`Depends on:` appears only when the idea's line has one, and the two must agree: the line under
`## Units` is the app's copy. `impl` stays shut until every dependency is merged, and on a link
that cannot be read; `uv run python -m coscc.loop next` says why.

References: a workspace name is 1-64 letters, digits, `.`, `-`, `_`; a unit is
`<ws>/NNNN_<slug>` (`NNNN_<slug>` in the same store); an idea is `<ws>/ideas/NNNN_<slug>.md`.

An `impl` may read the checkouts of the other workspaces the idea lists, and must not write
them.
