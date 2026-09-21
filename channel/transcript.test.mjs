import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { appendIn, appendOut, read, summarize } from './transcript.mjs'

const scratch = () => join(mkdtempSync(join(tmpdir(), 'transcript-')), 'transcript.jsonl')

test('a record survives being written and read back', () => {
  const path = scratch()
  appendIn(path, { session: 's1', content: 'hello' })
  const { records, problems } = read(path)
  assert.deepEqual(problems, [])
  assert.equal(records.length, 1)
  assert.equal(records[0].content, 'hello')
  assert.equal(records[0].dir, 'in')
  assert.match(records[0].ts, /^\d{4}-\d{2}-\d{2}T/)
})

test('appending does not rewrite what is already there', () => {
  const path = scratch()
  appendIn(path, { session: 's1', content: 'first' })
  appendIn(path, { session: 's1', content: 'second' })
  assert.deepEqual(read(path).records.map((r) => r.content), ['first', 'second'])
})

test('a missing file is a problem, not an empty transcript', () => {
  const { records, problems } = read(join(tmpdir(), 'no-such-transcript.jsonl'))
  assert.deepEqual(records, [])
  assert.equal(problems.length, 1)
})

test('a corrupt line is reported rather than skipped in silence', () => {
  const path = scratch()
  appendIn(path, { session: 's1', content: 'good' })
  writeFileSync(path, read(path).records.map((r) => JSON.stringify(r)).join('\n') + '\n{oops\n')
  const { records, problems } = read(path)
  assert.equal(records.length, 1)
  assert.equal(problems.length, 1)
})

test('an inbound nobody replied to is not delivered', () => {
  const path = scratch()
  appendIn(path, { session: 's1', content: 'into the void' })
  const s = summarize(read(path).records)
  assert.equal(s.inbound, 1)
  assert.equal(s.delivered, 0)
})

test('a reply naming the inbound id is what makes it delivered', () => {
  const path = scratch()
  const sent = appendIn(path, { session: 's1', content: 'ping' })
  appendOut(path, { session: 's1', content: 'pong', inReplyTo: [sent.id] })
  const s = summarize(read(path).records)
  assert.equal(s.delivered, 1)
  assert.deepEqual(s.bestSession, { session: 's1', delivered: 1 })
})

test('delivered turns are counted per session, not across them', () => {
  const path = scratch()
  for (const session of ['s1', 's1', 's2']) {
    const sent = appendIn(path, { session, content: 'x' })
    appendOut(path, { session, content: 'y', inReplyTo: [sent.id] })
  }
  const s = summarize(read(path).records)
  assert.equal(s.delivered, 3)
  assert.equal(s.sessions, 2)
  assert.deepEqual(s.bestSession, { session: 's1', delivered: 2 })
})

test('one reply can answer several queued inbounds at once', () => {
  const path = scratch()
  const a = appendIn(path, { session: 's1', content: 'a' })
  const b = appendIn(path, { session: 's1', content: 'b' })
  appendOut(path, { session: 's1', content: 'both', inReplyTo: [a.id, b.id] })
  assert.equal(summarize(read(path).records).delivered, 2)
})
