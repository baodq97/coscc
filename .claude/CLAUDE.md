# coscc

A local AI-native SDLC harness. `cos.mjs` decides every gate; each stage's rules live in its skill.

## Commands

```
npm test                                  # every test, both runtimes
uv sync                                   # after a fresh clone
node .claude/scripts/cos.mjs <command>:
  status [--json] · gate <unit> <stage> [--json]   # 0 open, 1 blocked with reasons, 2 misuse
  next <unit> · new-path <slug> · new-idea <slug> · unit-branch <unit>
  pr-text <unit> · rerun <unit> [<stage>] · check-branch [name] · check-tag <tag> · check-version
```

`status`, `gate`, `next`, `rerun`, `unit-branch`, `pr-text` need `--state -` from
`uv run coscc state <workspace> |`. Most take `--root <dir>`; `gate` and `next` take `--repo <dir>`.
Branch on `reasons` codes (`coscc/units/guards.py`), never on their words.

## Verifying your work

Tests green before done. Never skip or delete a failing test: fix the code. No linter.

## Conventions

- English everywhere except `.cos/`: English filenames and headings, Vietnamese prose.
- Branches and tags: `<type>/<slug>` (feat fix docs refactor test chore perf build ci revert),
  `vX.Y.Z`, `vX.Y.Z-rc.N`. Never compose one by hand: `unit-branch`, `check-branch`, `check-tag`.
- One branch and one PR per change, squashed, rebased onto `main` (never merge `main` in).
  `main` is not a work branch.
- No unit numbers or requirement ids (`0139`, `R16`) in code, comments, docstrings or rules.
- Code little and simple; split a file only when truly needed.
- Take unit paths from `new-path`. Cite only committed files, by path and line range. Cut a
  figure that has no source.

## Architecture

A unit is `.cos/NNNN_<slug>/` holding its artifacts. Its status, `Type:`, links, questions,
answers and holds live in the app's `cos.db`. `.claude/scripts/cos.mjs` is the one definition
of the loop. The app runs the stages and does `pr` and `ship` itself. `spike` runs only when
`spec` has an `[unmeasured] U<n>` concern. `Status: accepted` means the agent judged the
artifact ready; nothing here is a person's approval.

## Things agents get wrong

- Re-asking a gate the prompt already answered. From the board it was asked; at a terminal, ask
  `cos.mjs gate` and stop on non-zero.
- No code while `plan.md` is `draft`; accept the plan in its own commit.
- `plan.md: done` is terminal: set it only after the proof command passed.
- A skip is a person's: `uv run coscc skip <workspace> <unit> spec [--delegated] <reason>`.
- Committing on `main`: cut the branch from `unit-branch` first.

## Docs (read when the line applies)

- `.claude/docs/branches.md` — review rounds, rebasing, the branch order.
- `.claude/docs/copying.md` — before copying `.claude/` into another repository.
- `.claude/docs/not-built.md` — before adding a route, button or grant.
- `.claude/docs/ideas.md` — an idea shared by several units.
- `.claude/docs/old-units.md` — reading an artifact of a unit below `0010` (with `.cos/RENAMES.md`).
