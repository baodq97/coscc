# Notices: `GET /api/notices/follow`, and listening from a terminal

Read this before changing `coscc/runlog/notices.py`, `coscc/service/notices.py`, the route, or
`_NOTICE_JS` in `coscc/screens/chrome.py` (`0113`).

## What it sends

One NDJSON stream for every listener — the page's script, a terminal, an agent's session.
It ends after `notices.LIFETIME_SECONDS` (30 s, `auth.WS_RECHECK`, the bound a socket has),
and the listener connects again with `after`: `auth.Guard` asks for a live session once per
request, so that is how a listener whose session ended is refused.

- `{"type": "head", "id": N}` first, when there is no `after` or it is past every row:
  nothing at or below `N` follows, and a listener sets its cursor to `N`. An `after` past
  every row is a cursor from a run log since deleted or replaced, whose ids start again at
  1. `after=0` replays the whole run log.
- `{"type": "notice", "id", "at", "workspace", "unit", "stage", "kind", "text", "record"}`,
  one per run-log row past `after` that `notices.notice_of` makes a notice, by `id`, none
  twice. `kind` is one of `autopilot-stop`, `questions`, `step-ended`, `ship-refused`,
  `shipped`; `record` is the row as stored. Every line opens with `type` then `id`, and the
  listener below reads `id` off that prefix.
- `{"type": "beat", "id": N}` after `notices.BEAT_SECONDS` (15, chosen) without a line.
  It moves no cursor.
- `workspace=<the workspace's directory>` narrows to one; one the app does not have is a
  400, as is an `after` that is not a whole number ≥ 0.

## Listening from a terminal

Log in once; the cookie lands in a jar file:

```bash
curl -c "$COSCC_JAR" --data-urlencode "password=…" "$COSCC_BASE/login"
```

Then run the block below with `bash`. It prints each notice line as it arrives and keeps
its cursor in `$COSCC_AFTER_FILE`, so a restart picks up where it stopped. 40 s with no line
means the connection is dead (a `beat` comes every 15 s): it kills `curl` — which would
otherwise hang for ever on a socket that stopped moving — and connects again after 1 s,
doubling to 30 s, with `after` set to the last notice it printed. It does the same each time
the server ends the stream. A refusal (an ended session, a bad `after`) stops it with a line
on stderr.

<!-- listener -->
```bash
: "${COSCC_BASE:=http://127.0.0.1:8790}" "${COSCC_JAR:=$HOME/.coscc-cookies}"
: "${COSCC_AFTER_FILE:=$HOME/.coscc-notice-after}"
wait=1
while :; do
  after=$(cat "$COSCC_AFTER_FILE" 2>/dev/null)
  exec 3< <(exec curl -sN --fail -b "$COSCC_JAR" "$COSCC_BASE/api/notices/follow${after:+?after=$after}")
  pid=$!
  while IFS= read -r -t 40 -u 3 line; do
    wait=1
    case $line in
      '{"type": "notice", "id": '*)
        printf '%s\n' "$line"
        id=${line#'{"type": "notice", "id": '}
        printf '%s\n' "${id%%,*}" > "$COSCC_AFTER_FILE" ;;
      '{"type": "head", "id": '*)
        id=${line#'{"type": "head", "id": '}
        printf '%s\n' "${id%%\}*}" > "$COSCC_AFTER_FILE" ;;
    esac
  done
  kill "$pid" 2>/dev/null
  wait "$pid" 2>/dev/null
  code=$?
  exec 3<&-
  if [ "$code" = 22 ]; then
    echo "coscc refused the listener; log in again, or check COSCC_AFTER_FILE" >&2
    break
  fi
  sleep "$wait"
  wait=$(( wait * 2 > 30 ? 30 : wait * 2 ))
done
```
<!-- /listener -->

`scripts/e2e.py` runs exactly the block between the two markers, through a proxy that cuts
the connection both ways (`close` and `stall`); it does not run the `/login` line, since its
session is seeded (`0113` plan, *Proof*).

## Hazards

- **The cookie lives 30 days from its last use** (`coscc/web/auth.py`, `SESSION_TTL`), and the
  jar holds a live session: whoever reads that file can call every route. Once the session
  ends — its 30 days, a logout, `coscc reset-password` — a listener hears nothing past the
  end of the stream it is on, at most 30 s; its next connection is refused, it stops with
  the refusal line and needs the `/login` line again. There is no machine credential
  (`0113` spec C5).
- **Each listener holds a connection, and a dead one is held longer.** A peer that vanished
  without closing (a slept laptop, dropped Wi-Fi) is not noticed by the server until a
  `beat` fails to write at the TCP level or the stream's 30 s run out; until then it keeps a
  task and a `journal.BELL` ticket. What the server does with the socket after that, and how
  many can pile up, is not measured (spec C6; the spike saw one still open 46 s after the
  cut). Nothing is lost by it.
- **A record another process writes arrives up to 15 s late**; one this process writes rings
  the bell and arrives at once. Measured in `service_notices_test.py`, not in production.
- **An autopilot stop reaches the run log only when a pass runs**, every 5 minutes or after a
  step ends (spec C1): the stream is prompt about the log, not about the stop.
- **`ship-refused` can be a merge that happened.** `why` is read off the files after the step;
  a `ship` whose branch deletion failed after the merge leaves `ship.md` `draft` and reads as
  `ship-refused` (plan Risk 4). A `shipped` for the same unit follows once `ship` runs again.
- **Hearing a notice is not an approval.** It changes no artifact, gate, stop or hold, and
  the stream writes nothing to the run log.
