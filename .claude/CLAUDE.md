# coscc

A local AI-native SDLC harness. `cos.mjs` decides every gate; each stage's rules live in its skill.

## Commands

```
npm test        # lint, then every test
uv run ruff format && uv run ruff check --fix  # before commit
uv sync         # after a fresh clone
node .claude/scripts/cos.mjs <command>:
  status [--json] · gate <unit> <stage> [--json]   # 0 open, 1 blocked with reasons, 2 misuse
  next <unit> · new-path <slug> · new-idea <slug> · unit-branch <unit>
  pr-text <unit> · rerun <unit> [<stage>] · check-branch [name] · check-tag <tag> · check-version
```

`status`, `gate`, `next`, `rerun`, `unit-branch`, `pr-text` need `--state -` from
`uv run coscc state <workspace> |`. Most take `--root <dir>`; `gate` and `next` take `--repo <dir>`.
Branch on `reasons` codes (`coscc/units/guards.py`), never on their words.

## Verifying your work

`npm test` green before done. Never skip a failing test or switch a check off: fix the code.

## Conventions

- English everywhere except `.cos/`: English filenames and headings, Vietnamese prose.
- Branches and tags: `<type>/<slug>` (feat fix docs refactor test chore perf build ci revert),
  `vX.Y.Z`, `vX.Y.Z-rc.N`. Never compose one by hand: `unit-branch`, `check-branch`, `check-tag`.
- One branch and one PR per change, squashed, rebased onto `main` (never merge `main` in).
- No unit or requirement ids in comments, docstrings, names or rules (`tests/test_comments.py`).
- Import downwards, from the defining module; `tests/` mirrors `coscc/`. A feature is
  `coscc/features/<name>.py` + a line in `FEATURES`, using the app only via `Ctx`
  (`.claude/docs/code-and-tests.md`).
- Code little and simple; split a file only when needed.
- Take unit paths from `new-path`. Cite committed files by path and lines. Cut unsourced
  figures.

## Architecture

A unit is `.cos/NNNN_<slug>/` holding its artifacts; its state lives in the app's `cos.db`.
`cos.mjs` is the one definition of the loop. The app runs every stage, `pr` and `ship` too.
`Status: accepted` is the agent's judgement, never a person's approval.

## Things agents get wrong

- Re-asking a gate the prompt answered; at a terminal, ask `cos.mjs gate` and stop on non-zero.
- No code while `plan.md` is `draft` (accept it in its own commit), or in the `fast` lane while
  `gate <unit> impl` is closed; there the first commit is the failing test.
- `plan.md: done` is terminal: set it only after the proof command passed.
- A skip is a person's: `uv run coscc skip <workspace> <unit> spec [--delegated] <reason>`.
- Committing on `main`: cut the branch from `unit-branch` first.

## Docs (read when the line applies)

- `.claude/docs/branches.md` — review rounds, rebasing, the branch order.
- `.claude/docs/copying.md` — copying `.claude/` elsewhere.
- `.claude/docs/not-built.md` — before adding a route, button or grant.
- `.claude/docs/ideas.md` — an idea shared by several units.
- `.claude/docs/old-units.md` — an artifact of a unit below `0010`.
