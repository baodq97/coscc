# cos-baodo

Local AI-native SDLC harness, and the template for it.
Reference: `docs/harness.md`. Each stage's rules live in its own skill.

## Commands

None — no application code yet. Add build, test and lint here when it arrives.

## The loop

One unit of work per `.cos/NNNN_<slug>/` directory, holding `intent.md`, `spec.md` and
`plan.md` and nothing else.

`write-intent` → `write-spec` → `write-plan`, each gated on the one before.
`cos-status` reports where everything stands.

## Invariants

- Never set `Status: accepted`. Write `draft` and hand it back; that edit is the human's.
- Never act on a `draft`. Read the upstream status first, and if it is not `accepted`, stop
  and say what is missing.
- No code while `plan.md` is `draft`. An unaccepted plan authorizes nothing.
- Never guess a work unit number. List `.cos/` first.
- Cut a figure that has no source. Do not soften it.
- Cite only a file committed in this repository, by path and line range.
- Inside `.cos/`: English filenames and headings, Vietnamese prose. Everywhere else,
  English.
