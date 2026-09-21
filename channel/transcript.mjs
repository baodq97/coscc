#!/usr/bin/env node
// The transcript is the evidence for .cos/0001_terminal-only-access/intent.md, so it is a
// component rather than a side effect. Append-only, one JSON object per line, written by
// the channel process because that is the only place that sees both directions.
//
// Delivery is derived, not stored. A notification is never acked (spec.md C4), so the only
// proof the session received an inbound message is that Claude replied to it — and the only
// way Claude can reply is the reply tool, which writes an `out` record naming the inbound
// ids it answers. An inbound with no such reply stays "written" and does not count.
import { appendFileSync, readFileSync, existsSync, mkdirSync } from 'node:fs'
import { dirname } from 'node:path'
import { randomUUID } from 'node:crypto'

export const newId = () => randomUUID().slice(0, 8)

function append(path, record) {
  mkdirSync(dirname(path), { recursive: true })
  appendFileSync(path, JSON.stringify(record) + '\n', 'utf8')
  return record
}

export function appendIn(path, { session, content, id = newId(), at = new Date() }) {
  return append(path, { id, ts: at.toISOString(), dir: 'in', session, content })
}

export function appendOut(path, { session, content, inReplyTo = [], id = newId(), at = new Date() }) {
  return append(path, { id, ts: at.toISOString(), dir: 'out', session, content, in_reply_to: inReplyTo })
}

// A malformed line is reported, never skipped silently: a transcript that quietly drops
// records is a transcript that can be made to say anything by corrupting one line.
export function read(path) {
  if (!existsSync(path)) return { records: [], problems: [`${path} does not exist`] }
  const records = []
  const problems = []
  readFileSync(path, 'utf8').split('\n').forEach((line, i) => {
    if (line.trim() === '') return
    try {
      records.push(JSON.parse(line))
    } catch {
      problems.push(`line ${i + 1} is not valid JSON`)
    }
  })
  return { records, problems }
}

export function summarize(records) {
  const answered = new Set()
  for (const r of records) {
    if (r.dir === 'out') for (const id of r.in_reply_to ?? []) answered.add(id)
  }
  const inbound = records.filter((r) => r.dir === 'in')
  const delivered = inbound.filter((r) => answered.has(r.id))
  const bySession = new Map()
  for (const r of delivered) bySession.set(r.session, (bySession.get(r.session) ?? 0) + 1)
  const best = [...bySession.entries()].sort((a, b) => b[1] - a[1])[0] ?? null
  return {
    inbound: inbound.length,
    delivered: delivered.length,
    sessions: bySession.size,
    bestSession: best ? { session: best[0], delivered: best[1] } : null,
  }
}
