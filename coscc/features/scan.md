# Scan: the run log read for work that would stop people stepping in

Read this before changing `coscc/features/scan.py`: the tables, the routes, the scan, the
schedule's tick and the Backlog script are all in it.

## What one scan does

1. Reads the workspace's interventions past its cursor (`Ctx.interventions`, from
   `coscc/service/interventions.py`): `refused`, `ci-red`, `rerun`, `review-round`,
   `impl-draft`, `integrate`. A tool a session was denied is none.
2. With none, records `skipped` and stops: no session, $0.
3. Builds one prompt of at most 12,000 characters: the instructions, the proposals already made
   (pending, accepted, dismissed with why; newest first, 2,000 characters at most, the count cut
   recorded) and at most 25 interventions, oldest first.
4. Opens one `scan` session (`Ctx.session`): the grant `scan` in `coscc/agent/policy.py`, the
   model of the Settings row `estimate`, `submit` only, 2 turns, $0.68. It is a run in the run
   log like the estimate's, so `/cost` counts it.
5. Keeps each proposal that keeps the rules (`problems_of`), at most 8, as `pending`; records why
   the rest were dropped; moves the cursor to the last intervention the prompt held. A session
   that handed back no object is `failed` and the cursor stays.

A scan that cost more than $1 sets the workspace's schedule to off, and the Backlog says why.

## Tables

- `scan_runs`: one row per scan, `done`, `skipped` or `failed`, its cost, how many
  interventions it took and proposals it cut or dropped.
- `scan_proposals`: `pending`, `accepted` (with the unit made) or `dismissed` (with the reason),
  each with the interventions it gathers.
- `scan_cursor`: per workspace, the time of the last intervention a scan took.

## Routes

- `POST /api/scan?cwd=`: one scan, by `owner`. **Opens a paid session** unless nothing is new.
- `GET /api/scan/proposals?cwd=`: `{on: false}` while off; else the proposals, the last scans,
  the schedule and the sentences the Backlog shows.
- `POST /api/scan/proposals/<id>`: `{cwd, action: accept, slug}` makes a unit through the app's
  own `create_unit` (the brief is the title, the problem and the sources); `{cwd, action:
  dismiss, reason}` needs 1 to 500 characters. Both write `owner`.

## Schedule

Off by default (`default="off"`). Turned on, `on_set` sets 24 h; Settings offers off, 12 h, 24 h
and 168 h (`features.schedule`). The core asks `tick` every 5 minutes; it scans once the chosen
hours passed since the last scan of the workspace, a skipped one included.

## What the agent sees

- One prompt, written in `INSTRUCTIONS` and `prompt_of`: no skill and no file is read.
- One tool, `submit`, against the schema `scan` of `coscc/units/submit.py`: `proposals`, each
  `type`, `slug`, `title`, `problem`, `sources`. No other tool and no command.

## Hazards

- The budget is checked only after a turn is paid for, so a scan can pass $0.68 by one turn;
  $1 at worst was measured once, by a spike, not proven.
- The autopilot never reads these tables, and accepting a proposal never touches the shortlist.
- A scan in progress is held per process (`_scanning`), and by the attempt `Ctx.session` opens
  for the workspace's unit `""`, which an estimate also takes: one waits for the other.
- A dismissed proposal past the 2,000-character lists is not in the prompt and may be proposed
  again; `scan_runs.cut` counts how many were left out.
