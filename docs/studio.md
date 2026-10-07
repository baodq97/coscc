# CoS Studio

The page this app serves: a React app in `ui/`, built into `coscc/_studio/` and served at `/`
by the same process as the API (`coscc/http/studio.py`). It reads and acts only through `/api/*`
(`coscc/http/routes.py`, typed by `ui/src/api.gen.ts`) and hears changes on `/api/stream`.

```sh
npm --prefix ui ci && npm --prefix ui run build # build the page
COS_WORKING_DIR=~/projects uv run coscc         # then http://127.0.0.1:8790
npm --prefix ui run dev                         # or: vite on :5173, proxying /api to :8790
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

| Screen | Address | Can change |
|---|---|---|
| Leif's briefing | `/` | nothing |
| Needs you | `/inbox` | answers a question, holds or reruns a unit |
| A project | `/work/<project>` | pulls, relabels, removes it from the list |
| A unit | `/unit/<project>/<number>` | runs its next step, stops it, holds it |
| Up next | `/up-next` | the shortlist and its order; accepts or dismisses an agent's proposals |
| Talk to Leif | `/leif` | sends a message, which starts or resumes a chat |
| Agents | `/agents` | every part of an agent row; a new agent (a copy, a blank one, or Dagaz's draft); *Run now* on a triggered row |
| Insights | `/insights` | nothing |
| What Leif may do | `/may-do` | features, autopilot, and per project its packs, default process and the owner's processes |

Removing a project takes it off the list. The directory on disk is never deleted.

## What it costs

Every control that opens a session spends real account quota and says so before it is used:

- **Send**, on Talk to Leif. One chat turn; no tools unless the app is configured with some.
- **Run**, on a unit's page. One step of the unit's process: the agent row its state names,
  under that row's turn and $ ceilings and the grant the app issues for the run. A state that
  is an engine action (open the pull request, merge) opens no session.
- **Run now** on a triggered row, and **Describe the task** (Dagaz), on the Agents page. One
  read-only session each, under the row's ceilings.

What an agent may touch is its row's tools: a row whose artifact the app writes from the reply
holds only reading tools, so it cannot write a file. The agent's page shows its tools, model
and ceilings.

## Proofs

```sh
npm run e2e   # the board in a browser, on temporary workspaces
```

It needs a browser; it serves the app on a spare port, on a temporary data root, and spends
no quota.

Exit codes: `0` pass, `1` the page is broken, `2` the environment is not ready.
