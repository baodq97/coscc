#!/usr/bin/env node
// Proof for .cos/0001_terminal-only-access. Exits 0 only when the transcript shows at least
// TURNS inbound prompts that the session actually answered, all in one session.
//
// It counts delivered turns, not sent ones. A notification is never acked, so a count of
// what was written to the transport would include everything that fell on the floor when a
// session was started without the channel flag — and a number that cannot come back false
// is not a measure.
import { read, summarize } from '../channel/transcript.mjs'

const TURNS = 10 // from intent.md. Change it there, not here.

const path = process.argv[2]
if (!path) {
  console.error('usage: verify-0001.mjs <transcript.jsonl>')
  process.exit(2)
}

const { records, problems } = read(path)
const s = summarize(records)
const failures = []

for (const p of problems) failures.push(p)
if (!s.bestSession) {
  failures.push(`no session has a single delivered turn (${s.inbound} inbound, none answered)`)
} else if (s.bestSession.delivered < TURNS) {
  failures.push(
    `best session ${s.bestSession.session} delivered ${s.bestSession.delivered} of ${TURNS} turns ` +
      `(${s.inbound} inbound recorded, ${s.delivered} answered across ${s.sessions} session(s))`,
  )
}

if (failures.length) {
  console.error('FAIL')
  for (const f of failures) console.error(`  - ${f}`)
  process.exit(1)
}
console.log(`PASS: session ${s.bestSession.session} delivered ${s.bestSession.delivered} turns`)
