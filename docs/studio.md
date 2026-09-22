# COS Studio

The page this app serves. One route, `/`, six screens, and everything on them read from
the running service.

It began as `fragmented-product-experience`'s prototype — the same layout, spacing and words — and `0006` replaced
the invented data underneath it with the real thing and deleted the page that came before.
`coscc/studio.py`, the presentation primitives, did not change in that swap, which is
the clearest statement of what `fragmented-product-experience` actually settled: the look.

```sh
uv run coscc-build                                   # compile the page
COS_WORKING_DIR=~/projects uv run coscc        # then http://127.0.0.1:8790
```

## Where the data is

Two roots, and they are not the same thing.

| Root | Holds | Set by |
|---|---|---|
| `COS_DATA_DIR`, default `~/.cos` | The app's own state: `cos.db` | environment only |
| `COS_WORKING_DIR` | The workspaces themselves — somebody's git checkouts | environment only |

Backing up one does **not** back up the other. The Settings screen prints both for that
reason.

Neither is settable over HTTP. The only reader of the environment in the app is
`coscc/config.from_env`, and there is no setter anywhere, so a request has no path to
either value.

`cos.db` holds the workspace list, the run log, and the handful of interface preferences
the Settings screen remembers. A workspace row stores a **name** — one path segment — and
the table has no column for a path, so a hand-edited database cannot point the app at
`/etc`. A `.cos-journal.jsonl` left by a version before `0006` is imported once, on first
use, and the file is left where it is. The workspace list had an import of the same shape
and `0008` removed it — see `.claude/CLAUDE.md`.

## The screens

| Screen | Reads | Can change |
|---|---|---|
| Overview | workspaces, board, run log | nothing |
| Workspaces | the workspace list | add, adopt, clone, relabel, pull, remove from the list |
| Board | `cos.mjs status` in each workspace, joined with the run log | a step's mode; running a step |
| Sessions | the SDK's session store | sends a message, which creates or resumes a session |
| Activity & usage | the run log | nothing |
| Settings | the configuration and the grant table | board density and colour mode |

Removing a workspace takes it off the list. The directory on disk is never deleted.

## What it costs

Two controls spend real account quota, and both say so before they are used:

- **Send**, on Sessions. One message, chat only, no tools.
- **Run**, in a work unit's drawer. A real step of the loop. In `autonomous` mode on `impl`
  it carries a $5 ceiling, and on `pr` it can reach every repository this machine's GitHub
  login reaches. The drawer shows the tools the step would get and that warning *before*
  the button, which is `0006 spec.md` R17.

Six of the eight stages — idea, intent, spec, plan, review, ship — get no tools in either
mode. A session with no tools cannot write a file, so the app writes the artifact from the
reply. The Settings screen says so, because otherwise it looks like the agent wrote it.

## Proofs

```sh
uv run python scripts/verify_0003.py   # the page renders, and the check can fail
uv run python scripts/verify_0006.py   # five flows on real data, and a restart
```

Both need a browser and a free `COS_PORT` — the compiled bundle hardcodes the address it
opens its WebSocket against, so neither can move to a spare port. Stop the app first, and
do not run them at the same time. `verify_0003.py` spends no quota; `verify_0006.py` sends
one short prompt.

Exit codes: `0` pass, `1` the page is broken, `2` the environment is not ready.
