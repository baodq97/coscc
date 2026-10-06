# coscc

A local SDLC harness. `coscc.loop` decides every gate; each stage's rules live in its skill.

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

Lint, `tests/test_*.py` and the tests of what you changed green before done; CI runs `npm test`,
every test. Never skip a test or switch a check off: fix the code.

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
`coscc.loop` is the one definition of the loop. The app runs every stage.
A stage's `judgement` (`ready`, in `submit`) is the agent's, not a person's approval.

## Things agents get wrong

- Re-asking a gate the prompt answered; at a terminal, ask `uv run python -m coscc.loop gate` and stop on non-zero.
- No code while `gate <unit> impl` is closed; in the `fast` lane the first commit is the failing
  test.
- A skip is a person's: `uv run coscc skip <workspace> <unit> <state> <reason>`, for any state the
  process marks `skip`.
- Committing on `main`: cut the branch from `unit-branch` first.

## Docs (read when it applies)

- `.claude/docs/code-and-tests.md` — adding a feature, the checks, tests.
- `.claude/docs/branches.md` — rebasing, review rounds, the merge.
- `.claude/docs/copying.md` — copying `.claude/` elsewhere.
- `.claude/docs/not-built.md` — the trust model: before adding a route, button or grant.
- `.claude/docs/ideas.md` — an idea shared by several units.
