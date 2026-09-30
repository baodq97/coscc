# coscc

A local SDLC harness. `cos.mjs` decides every gate; each stage's rules live in its skill.

## Commands

```
npm test        # lint, then every test
uv run ruff format && uv run ruff check --fix  # before commit
node .claude/scripts/cos.mjs <command>:
  status [--json] · gate <unit> <stage> [--json]   # 0 open, 1 blocked with reasons, 2 misuse
  next <unit> · new-path <slug> · new-idea <slug> · unit-branch <unit>
  pr-text <unit> · rerun <unit> [<stage>] · check-branch [name] · check-tag <tag> · check-version
```

The deciding commands (`status`, `gate`, `next`, `rerun`, `unit-branch`, `pr-text`) need the
app's snapshot: `uv run coscc state <workspace> | ... --state -`. Most take `--root <dir>`;
`gate` and `next` take `--repo <dir>`. Branch on `reasons` codes (`coscc/units/guards.py`), never
on their words.

## Verifying your work

`npm test` green before done. Never skip a test or switch a check off: fix the code.

## Conventions

- English everywhere except `.cos/`: English filenames and headings, Vietnamese prose.
- Branches and tags are `<type>/<slug>`, `vX.Y.Z`, `vX.Y.Z-rc.N`; never compose one by hand:
  `unit-branch`, `check-branch`, `check-tag`.
- One branch and one PR per change, rebased onto `main` (never merge `main` in).
- New work is a feature (`coscc/features/<name>.py` + a `FEATURES` line) using only `Ctx` and
  the kernel's extension points; a need none serves is a kernel change, planned first
  (`.claude/docs/code-and-tests.md`).
- Code little and simple; split a file only when needed.
- Unit paths come from `new-path`. Cut unsourced figures.

## Architecture

A unit is `.cos/NNNN_<slug>/` holding its artifacts; its state is in the app's `cos.db`.
`cos.mjs` is the one definition of the loop. The app runs every stage.
`Status: accepted` is the agent's judgement, not a person's approval.

## Things agents get wrong

- Re-asking a gate the prompt answered; at a terminal, ask `cos.mjs gate` and stop on non-zero.
- No code while `plan.md` is `draft` (accept it in its own commit), or in the `fast` lane while
  `gate <unit> impl` is closed; there the first commit is the failing test.
- `plan.md: done` is terminal: set it only after the proof command passed.
- A skip is a person's: `uv run coscc skip <workspace> <unit> spec [--delegated] <reason>`.
- Committing on `main`: cut the branch from `unit-branch` first.

## Docs (read when it applies)

- `.claude/docs/code-and-tests.md` — adding a feature, the checks, tests.
- `.claude/docs/branches.md` — rebasing, review rounds, the merge.
- `.claude/docs/copying.md` — copying `.claude/` elsewhere.
- `.claude/docs/not-built.md` — the trust model: before adding a route, button or grant.
- `.claude/docs/ideas.md` — an idea shared by several units.
