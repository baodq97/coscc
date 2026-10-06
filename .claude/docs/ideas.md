# One idea, several units, several repositories

Read this before opening a unit from an idea or declaring what it depends on.

An idea is one file, `.cos/ideas/NNNN_<slug>.md`, in the store of the workspace it started in;
`uv run python -m coscc.loop new-idea <slug>` prints the path and creates nothing.

```
# Idea: <title>
Author: the originator. Status: accepted.

## In their own words

<brief>
```

The app writes it once and never rewrites it. A unit is an ordinary unit in its own repository's
workspace. Opening it from the idea is a press, `POST /api/units` with `idea` and, when it
waits on another unit of the same idea, `depends_on`; the app writes the unit's `idea` and
`depends` rows (`unit_links`). Those rows are the whole link: an idea's units are the units whose
`idea` row names it, in every workspace, and no file lists them. `intent.md` carries no `Idea:`,
`Repo:` or `Depends on:` line and is not read for one.

`impl` stays shut until every dependency is merged; `uv run python -m coscc.loop next` says
why. A `depends_on` must name a unit of the same idea, else the press is refused before a number
is taken.

References: a workspace name is 1-64 letters, digits, `.`, `-`, `_`; a unit is
`<ws>/<NNNN_slug>`; an idea is `<ws>/ideas/NNNN_<slug>.md`.

The intent step is given the idea's text and the idea's other units. An `impl` may read the
checkouts of the other workspaces the idea's units are in, and must not write them.
