# coscc

A local SDLC harness. `coscc.loop` decides every gate; agents and processes are data.

## Commands

```
npm test        # lint, then every test
uv run ruff format && uv run ruff check --fix  # before commit
uv run python -m coscc.loop <command>:
  status [--json] · gate <unit> <stage> [--json]   # 0 open, 1 blocked with reasons, 2 misuse
  next <unit> · new-path <slug> · new-idea <slug> · unit-branch <unit>
  rerun <unit> [<stage>] · check-branch [name] · check-tag <tag> · check-version
```

The deciding commands (`status`, `gate`, `next`, `rerun`, `unit-branch`) need the
app's snapshot: `uv run coscc state <workspace> | ... --state -`. Most take `--root <dir>`;
`gate` and `next` take `--repo <dir>`. Branch on `reasons` codes (`coscc/units/guards.py`), never
on their words.

## Verifying your work

Before done `npm run ci` (CI's list) exits 0 on the PR head; until then lint and the tests you
changed suffice. Never skip a test or switch a check off.

## Conventions

- English everywhere except `.cos/`: English filenames and headings, Vietnamese prose.
- Branches and tags are `<type>/<slug>`, `vX.Y.Z`, `vX.Y.Z-rc.N`; never compose one by hand:
  `unit-branch`, `check-branch`, `check-tag`.
- One branch and one PR per change, rebased onto `main` (never merge `main` in).
- New work is a feature (`coscc/features/<name>/` + a `FEATURES` line) importing only
  `coscc/kernel.py`; a need none serves is a kernel change, planned first
  (`.claude/docs/code-and-tests.md`).
- Code little and simple; split a file only when needed.
- Unit paths come from `new-path`. Cite committed files as path:lines; cut unsourced figures.

## Architecture

A unit is `.cos/NNNN_<slug>/` holding its artifacts; its state is in the app's `cos.db`.
It walks one process (a pack's `process.json`): each state runs an agent row or an engine
action. `coscc.loop` decides every way on; the app runs every agent, a grant per run. Agents
are rows (`coscc/packs/coscc-sdlc/agents/`); the core names no state. A `judgement` (in
`submit`) is the agent's, not a person's.

## Things agents get wrong

- Re-asking a gate the prompt answered; at a terminal, ask `uv run python -m coscc.loop gate` and stop on non-zero.
- No code while `gate <unit> impl` is closed; on the fast-lane branch the first commit is the
  failing test.
- Coding an agent's or a state's behaviour in Python: edit its row or process.
- A skip is a person's: `uv run coscc skip <workspace> <unit> <state> <reason>`, for any state the
  process marks `skip`.
- Committing on `main`: cut the branch from `unit-branch` first.

## Docs (read when it applies)

- `.claude/docs/code-and-tests.md` — adding a feature, the checks, tests.
- `.claude/docs/branches.md` — rebasing, review rounds, the merge.
- `.claude/docs/copying.md` — copying `.claude/` elsewhere.
- `.claude/docs/not-built.md` — the trust model: before adding a route, button or grant.
- `.claude/docs/ideas.md` — an idea shared by several units.
