#!/usr/bin/env node
// Mechanical checks for the work-unit loop. Everything here is a fact about what is on
// disk: which artifacts exist, what status each carries, which gate that clears. Judgement
// — whether a spec should be skipped, whether a plan is good — stays with the skills.
import { readdirSync, readFileSync, existsSync } from 'node:fs'
import { execFileSync, spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
const COS = join(ROOT, '.cos')

const UNIT_RE = /^(\d{4})_([a-z0-9]+(?:-[a-z0-9]+)*)$/
const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/

// The nine stages, in order. This list is the single place the loop is defined: the
// artifacts a unit may hold, the statuses each may carry, what the gate demands, and what
// `status` proposes next are all read off it. Adding a stage is editing this array.
//
// `optional: true` on `idea` is the one exception, and it is deliberate. `idea` comes
// before `intent`, but `readUnit` has always insisted that every unit opens with an
// intent, and eight units on disk were opened that way. Making `idea` mandatory would
// retroactively mark all of them incomplete. So it gates nothing: it is a place to record
// a thought that preceded the intent, and its absence means only that nobody recorded one.
//
// `when: 'unmeasured'` on `spike` (`0039`) is the other: the stage is required only when
// `spec.md` marks a concern `[unmeasured] U<n>`, or `spike.md` already exists (`required`
// below). Every other unit walks the loop as if it were not there. It is not `optional`,
// which means "blocks nothing" — once required, it blocks `plan` and everything after.
//
// `lanes` names the lanes a stage belongs to; a stage with none belongs to every lane.
// `spec`, `spike` and `plan` are `full`'s alone: a fix in the `fast` lane goes from its
// intent to `impl` (`laneOf` below says which lane a unit is in).
const STAGES = [
  { name: 'idea', file: 'idea.md', optional: true, hint: 'write-idea', statuses: ['draft', 'accepted', 'rejected'] },
  { name: 'intent', file: 'intent.md', hint: 'write-intent — the unit has no intent.md', statuses: ['draft', 'accepted', 'rejected'] },
  { name: 'spec', file: 'spec.md', lanes: ['full'], hint: 'write-spec — it assesses whether to skip first', statuses: ['draft', 'accepted', 'rejected', 'skipped'] },
  { name: 'spike', file: 'spike.md', lanes: ['full'], when: 'unmeasured', hint: 'write-spike — spec.md has [unmeasured] items', statuses: ['draft', 'accepted', 'rejected'] },
  { name: 'plan', file: 'plan.md', lanes: ['full'], hint: 'write-plan', statuses: ['draft', 'accepted', 'rejected', 'done'] },
  { name: 'impl', file: 'impl.md', hint: 'write-impl — implementation starts', statuses: ['draft', 'accepted', 'rejected', 'done'] },
  // `0139` R12: `pr` and `ship` are the app's PR machine, with no session and no skill, so
  // their hint names the stage the board's button starts, never a `write-*` skill.
  { name: 'pr', file: 'pr.md', hint: 'pr', statuses: ['draft', 'accepted', 'rejected'] },
  // `changes-requested` is the one status that sends work back rather than forward or
  // out: the reviewer looked, found something, and the unit goes on. `rejected` still
  // closes the unit, as it does on every other artifact. `settled` below does not include
  // it, so the `ship` gate is closed by it without a line of its own.
  { name: 'review', file: 'review.md', hint: 'write-review', statuses: ['draft', 'changes-requested', 'accepted', 'rejected'] },
  { name: 'ship', file: 'ship.md', hint: 'ship', statuses: ['draft', 'accepted', 'rejected'] },
]

// `implement` was the stage name for the first seven units and it is still written into
// `.claude/skills/write-plan/SKILL.md`. Keeping it as an alias costs one line; breaking it
// would silently block the two units that have not been implemented yet.
const STAGE_ALIAS = { implement: 'impl' }

const stageOf = (name) => STAGES.find((s) => s.name === (STAGE_ALIAS[name] ?? name)) ?? null
export const STAGE_NAMES = STAGES.map((s) => s.name)

// `0054` R1 (a): the stages a person may run again from the board once their artifact is
// accepted (`intent.md ## Answers, câu 2`). `idea`, `impl`, `review` and `ship` are not
// among them. `cmdRerun` is the one reader.
export const RERUNNABLE = ['intent', 'spec', 'spike', 'plan', 'pr']

const ARTIFACTS = STAGES.map((s) => s.file)
const VALID = Object.fromEntries(STAGES.map((s) => [s.file, s.statuses]))

// --- reading -----------------------------------------------------------------

// The status line is prose: "Intent: intent.md. Author: X. Status: draft." Take the first
// `Status:` in the file and nothing else, so a later mention in the body cannot shadow it.
// Hyphenated words are one status (`changes-requested`); a trailing hyphen is not part of it.
const STATUS_RE = /\bStatus:\s*([A-Za-z]+(?:-[A-Za-z]+)*)/

export function parseStatus(text) {
  const m = text.match(STATUS_RE)
  return m ? m[1].toLowerCase() : null
}

export function parseSkipReason(text) {
  const m = text.match(/\bSpec:\s*skipped\s*\(([^)]*)\)/i)
  return m ? m[1].trim() : null
}

// --- questions and the answers a person gave to them ---------------------------

// The lines of one `## <title>` section: from the line that is exactly that heading to the
// next `## ` heading or the end of the file. `null` when the file has no such section, so a
// caller can tell "no section" from "a section with nothing in it".
function section(text, title) {
  const lines = text.split(/\r?\n/)
  const start = lines.findIndex((l) => l.trimEnd() === `## ${title}`)
  if (start === -1) return null
  const rest = lines.slice(start + 1)
  const end = rest.findIndex((l) => l.startsWith('## '))
  return end === -1 ? rest : rest.slice(0, end)
}

// A question is a numbered item at column 0 under `## Open questions` whose first paragraph
// — up to its first blank line — holds a `?`. Its identity is the number written, not its
// position and not its words — a later stage may reword it, and a list that skips a number
// still means the number it wrote. Lines up to the next numbered item belong to the one
// above them. `null` when the file has no `## Open questions`.
//
// Everything else there is a note, and is never listed: a numbered item with no `?`, a
// bullet, a sentence. `0109`: a line such as "7. The initiator should still reread this
// file." stopped the autopilot as if a person owed it an answer. Both marks are ones the
// writer puts down, the number and the `?`, so this compares characters and never reads
// meaning — "inferring from content is not deterministic, and the gate must be"
// (`0109` `intent.md ## Answers, câu 1`). A numbered note still ends the item above it, and
// its number is never given to another question.
//
// This reverses what was measured 2026-09-23, when a section with no numbered item was
// read as a bullet list numbered by position, because `0015` and `0002` wrote their
// questions that way. `0109` `spike.md ## U2` found that reversing it changes only units
// already finished or `dropped`; `write-intent` and `write-spec` now say the line's shape.
export function parseQuestions(text) {
  const lines = section(text, 'Open questions')
  if (lines === null) return null
  const found = []
  for (const line of lines) {
    const m = line.match(/^(\d+)\.\s+(.*)$/)
    if (m) found.push({ n: Number(m[1]), lines: [m[2]] })
    else if (found.length) found[found.length - 1].lines.push(line)
  }
  const asks = (q) => {
    const blank = q.lines.findIndex((l) => l.trim() === '')
    return (blank === -1 ? q.lines : q.lines.slice(0, blank)).join('\n').includes('?')
  }
  return found.filter(asks).map((q) => ({ n: q.n, text: q.lines.join('\n').trim() }))
}

// The header line of an answer block. The name is lazy so that a name holding a full stop
// still stops at `. Date:`.
const ANSWER_META = /^Answered by:\s*(.+?)\.\s+Date:\s*(\S+?)\.\s+Via:\s*(\S+?)\.?\s*$/

// Answers are what a person wrote through the product, appended under `## Answers` as one
// `### Câu N` block each. Answering again adds a block rather than editing one, so when a
// number has several the last is the one in force. A block whose header line is missing or
// malformed is not an answer: it is not counted, because nothing says who gave it.
//
// Since `0028` a block may also be headed `### F<n>`: a person's answer to a review finding
// the review confirmed as needing one (`[needs-person]`). It carries `id: 'F<n>'` and
// `n: null`; a `Câu N` block carries `n` and `id: null`, so the two kinds never collide.
//
// Since `0047` a block may be headed `### Outcome` (`parseOutcome`). It ends the block above
// it, so its lines never become part of an answer's text, and it is not an answer itself.
//
// Since `0045` the section may also hold hold blocks (`HOLD_HEAD`). Each one ends the block
// above it, so a reason is never read as the tail of the answer before it, and none is ever
// returned as an answer.
//
// Since `0054` a `### Rerun` block (`RERUN_HEAD`) ends the block above it the same way, and
// is not an answer either.
//
// Since `0081` a `### More rounds` block (`MORE_ROUNDS_HEAD`, in `review.md`) ends the block
// above it the same way, and is not an answer either.
function answerBlocks(text) {
  const lines = section(text, 'Answers')
  if (lines === null) return []
  const blocks = []
  for (const line of lines) {
    const m = line.match(/^###\s+(?:Câu\s+(\d+)|(F\d+)|(Outcome))\s*$/)
    if (m) blocks.push({ n: m[1] !== undefined ? Number(m[1]) : null, id: m[2] ?? null, outcome: m[3] !== undefined, lines: [] })
    else if (HOLD_HEAD.test(line)) blocks.push({ hold: true, lines: [] })
    else if (RERUN_HEAD.test(line)) blocks.push({ rerun: true, lines: [] })
    else if (MORE_ROUNDS_HEAD.test(line)) blocks.push({ moreRounds: true, lines: [] })
    else if (blocks.length) blocks[blocks.length - 1].lines.push(line)
  }
  return blocks
}

export function parseAnswers(text) {
  const answers = []
  for (const b of answerBlocks(text)) {
    if (b.outcome || b.hold || b.rerun || b.moreRounds) continue
    const at = b.lines.findIndex((l) => l.trim() !== '')
    const meta = at === -1 ? null : b.lines[at].match(ANSWER_META)
    if (!meta) continue
    answers.push({
      n: b.n,
      id: b.id,
      by: meta[1].trim(),
      date: meta[2],
      via: meta[3],
      text: b.lines.slice(at + 1).join('\n').trim(),
    })
  }
  return answers
}

// --- a person's decision to pause or drop a unit --------------------------------

// `0045`. A unit may be held: `paused`, which resumes, or `dropped`, which ends it. The
// decision is appended under `intent.md ## Answers` — the one way the app writes into an
// artifact — as a `### Paused`, `### Dropped` or `### Resumed` block whose first line is
// `Decided by: <name>. Date: <YYYY-MM-DD>. Via: product.` and whose rest is the reason.
const HOLD_HEAD = /^###\s+(Paused|Dropped|Resumed)\s*$/
const HOLD_META = /^Decided by:\s*(.+?)\.\s+Date:\s*(\S+?)\.\s+Via:\s*(\S+?)\.?\s*$/
const HOLD_TO = { Paused: 'paused', Dropped: 'dropped', Resumed: 'active' }

// The five moves, and only these (`0045` spec R6). `active` is a unit with no hold. There is
// no road from `dropped` straight back to `active`: it resumes through `paused`. The app's
// route and page read `holdMoves` off a unit rather than keep a copy of this.
export const HOLD_MOVES = { active: ['paused', 'dropped'], paused: ['dropped', 'active'], dropped: ['paused'] }

const HOLD_HEAD_OF = Object.fromEntries(Object.entries(HOLD_TO).map(([head, to]) => [to, head]))

// `0135`. The hold blocks in file order, read and not judged: one row per block, `by` `null`
// when its `Decided by:` line is missing or malformed. `foldHolds` decides; the app keeps
// the well-formed rows in its database and hands them back in the same shape.
export function holdBlocks(text) {
  const lines = section(text ?? '', 'Answers')
  if (lines === null) return []
  const blocks = []
  for (const line of lines) {
    const m = line.match(HOLD_HEAD)
    if (m) blocks.push({ head: m[1], lines: [] })
    else if (/^###\s/.test(line)) blocks.push({ head: null, lines: [] })
    else if (blocks.length) blocks[blocks.length - 1].lines.push(line)
  }
  return blocks.filter((b) => b.head).map((b) => {
    const at = b.lines.findIndex((l) => l.trim() !== '')
    const meta = at === -1 ? null : b.lines[at].match(HOLD_META)
    if (!meta) return { state: HOLD_TO[b.head], reason: null, by: null, date: null, via: null }
    return { state: HOLD_TO[b.head], reason: b.lines.slice(at + 1).join('\n').trim(), by: meta[1].trim(), date: meta[2], via: meta[3] }
  })
}

// The hold in force: walk the rows in order from `active`; the last valid one decides. A row
// with no `by` is not counted, because nothing says who decided. A row describing a move
// `HOLD_MOVES` lacks is not counted either, and is reported. `hold` is `null` when the unit
// ends `active`.
export function foldHolds(rows) {
  const problems = []
  let state = 'active'
  let hold = null
  rows.forEach((r, i) => {
    if (r.by == null) return
    if (!HOLD_MOVES[state].includes(r.state)) {
      problems.push(`hold block ${i + 1} (### ${HOLD_HEAD_OF[r.state]}) is not a valid move from ${state} — it is ignored`)
      return
    }
    state = r.state
    hold = r.state === 'active' ? null : { state: r.state, reason: r.reason, by: r.by, date: r.date }
  })
  return { hold, problems }
}

export const parseHold = (text) => foldHolds(holdBlocks(text))

// --- a person's decision to allow more review rounds ----------------------------

// `0081`. A unit that used every review round with findings still open may be given more.
// The decision is appended under `review.md ## Answers`, beside the rounds it widens:
//
//   ### More rounds
//   Decided by: <name>. Date: <YYYY-MM-DD>. Via: product.
//   Rounds: <n>
//
// `granted` is the sum of `n` over every valid block. A block whose first line is not a
// well-formed `Decided by:` line, or whose next is not `Rounds: <n>` with `n` at least 1, is
// not counted, because nothing says who decided or how much, and is reported.
const MORE_ROUNDS_HEAD = /^###\s+More rounds\s*$/
const MORE_ROUNDS_N = /^Rounds:\s*(\d+)\s*$/

export function parseMoreRounds(text) {
  const lines = section(text ?? '', 'Answers')
  const problems = []
  if (lines === null) return { granted: 0, problems }
  const blocks = []
  for (const line of lines) {
    if (MORE_ROUNDS_HEAD.test(line)) blocks.push({ more: true, lines: [] })
    else if (/^###\s/.test(line)) blocks.push({ more: false, lines: [] })
    else if (blocks.length) blocks[blocks.length - 1].lines.push(line)
  }
  let granted = 0
  let k = 0
  for (const b of blocks) {
    if (!b.more) continue
    k += 1
    const [first, second] = b.lines.filter((l) => l.trim() !== '')
    if (!first?.match(HOLD_META)) {
      problems.push(`more rounds block ${k} has no valid Decided by line — it is not counted`)
      continue
    }
    const n = second?.match(MORE_ROUNDS_N)
    if (!n || Number(n[1]) < 1) {
      problems.push(`more rounds block ${k} has no valid Rounds line — it is not counted`)
      continue
    }
    granted += Number(n[1])
  }
  return { granted, problems }
}

// --- a stage run again, and the artifacts that makes stale ----------------------

// `0054`. A person may run an accepted stage again from the board. The app appends one
// `### Rerun` block under `intent.md ## Answers`, composed by `rerunBlock` below:
//
//   ### Rerun
//   Requested by: owner. Date: <YYYY-MM-DD>. Via: product.
//   Stage: <stage>.
//   Stale: <file> sha256:<64 hex>
//
// one `Stale:` line for the stage's own artifact and one for each later artifact on disk.
// An artifact is stale while the text above its `## Answers` still hashes to a value a
// block wrote for it (R4): an answer appended since does not change that, and only the
// stage writing its own text again does. `owner` is the fixed word of the UI standard's S7,
// not a name. Nothing in the block reads as approval (R3).
const RERUN_HEAD = /^###\s+Rerun\s*$/
const RERUN_META = /^Requested by:\s*(.+?)\.\s+Date:\s*(\S+?)\.\s+Via:\s*(\S+?)\.?\s*$/
const RERUN_STAGE = /^Stage:\s*([a-z]+)\.?\s*$/
const RERUN_STALE = /^Stale:\s*(\S+)\s+sha256:([0-9a-f]{64})\s*$/

// The sha256, in hex, of the text above an artifact's first `## Answers` line — the line
// `section()` finds — with its trailing whitespace dropped, so the blank line
// `runner.with_answers` or an app write puts before a new `## Answers` changes nothing.
export function aboveAnswers(text) {
  const lines = text.split(/\r?\n/)
  const at = lines.findIndex((l) => l.trimEnd() === '## Answers')
  const above = (at === -1 ? lines : lines.slice(0, at)).join('\n').trimEnd()
  return createHash('sha256').update(above, 'utf8').digest('hex')
}

// Every `### Rerun` block of an intent, in file order, as `{stage, by, date, stale: {file:
// hash}}`. A block with no well-formed `Requested by:` or `Stage:` line is not counted,
// because nothing says who asked or what for, and it is reported, as `parseHold` reports.
export function parseReruns(text) {
  const lines = section(text ?? '', 'Answers')
  const reruns = []
  const problems = []
  if (lines === null) return { reruns, problems }
  const blocks = []
  for (const line of lines) {
    if (RERUN_HEAD.test(line)) blocks.push({ rerun: true, lines: [] })
    else if (/^###\s/.test(line)) blocks.push({ rerun: false, lines: [] })
    else if (blocks.length) blocks[blocks.length - 1].lines.push(line)
  }
  let n = 0
  for (const b of blocks) {
    if (!b.rerun) continue
    n += 1
    const body = b.lines.filter((l) => l.trim() !== '')
    const meta = body[0]?.match(RERUN_META)
    const stage = body[1]?.match(RERUN_STAGE)
    if (!meta || !stage || !RERUNNABLE.includes(stage[1])) {
      problems.push(`rerun block ${n} has no well-formed Requested by: and Stage: lines — it is ignored`)
      continue
    }
    const stale = {}
    for (const l of body.slice(2)) {
      const m = l.match(RERUN_STALE)
      if (m && ARTIFACTS.includes(m[1])) stale[m[1]] = m[2]
    }
    reruns.push({ stage: stage[1], by: meta[1].trim(), date: meta[2], stale })
  }
  return { reruns, problems }
}

// Each question joined to the answer in force for it, if any.
export const answeredQuestions = (text) => joinAnswers(parseQuestions(text), parseAnswers(text))

// `0135`. The joining alone, so questions and answers the app hands back are joined the same way.
export function joinAnswers(questions, answers) {
  if (questions === null) return null
  const latest = new Map()
  for (const a of answers) if (a.n !== null) latest.set(a.n, a)
  return questions.map((q) => {
    const a = latest.get(q.n) ?? null
    return { n: q.n, text: q.text, answered: a !== null, answer: a }
  })
}

// --- the outcome a unit was measured against, after it shipped ------------------

// `0047`: the deadline of an intent's `## Proposed outcome` — the first ISO date written in
// that section, and only if it is a real calendar date. `null` when there is none. The
// first date is a guess at which one is the deadline; the board shows the one read, so a
// person can see when the guess is wrong.
export function parseDeadline(text) {
  const lines = section(text, 'Proposed outcome')
  if (lines === null) return null
  for (const m of lines.join('\n').matchAll(/\b(\d{4}-\d{2}-\d{2})\b/g)) {
    const d = new Date(`${m[1]}T00:00:00Z`)
    if (!Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === m[1]) return m[1]
  }
  return null
}

const OUTCOME_RESULTS = { 'đạt': 'met', 'trượt': 'missed', 'không đo được': 'unmeasurable' }
const OUTCOME_KEY = /^(Result|Measured by|Source|Reason):\s*(.*)$/

// `0047`: the outcome blocks under an intent's `## Answers`, each headed `### Outcome`:
//
//   Answered by: <name>. Date: <YYYY-MM-DD>. Via: product.
//
//   Result: đạt | trượt | không đo được
//   Measured by: agent | <a person's name>
//   Source: <one line>
//   Reason: <one line>
//
//   <an optional note>
//
// The key lines are read anywhere in the first paragraph after the header line; every other
// line is the note. A block is valid with a well-formed header, a known result, a
// `Measured by`, a `Source` when the result is met or missed and a `Reason` when it is
// unmeasurable. Every other block is counted in `invalid` and read no further. No gate reads
// any of this: it is information for the board, and a unit's stages are decided without it.
export function parseOutcome(text) {
  const blocks = []
  let invalid = 0
  for (const b of answerBlocks(text)) {
    if (!b.outcome) continue
    const at = b.lines.findIndex((l) => l.trim() !== '')
    const meta = at === -1 ? null : b.lines[at].match(ANSWER_META)
    if (!meta) { invalid++; continue }
    const rest = b.lines.slice(at + 1)
    let i = rest.findIndex((l) => l.trim() !== '')
    if (i === -1) i = rest.length
    const keys = {}
    const note = []
    for (; i < rest.length && rest[i].trim() !== ''; i++) {
      const k = rest[i].trim().match(OUTCOME_KEY)
      if (k) keys[k[1]] = k[2].trim()
      else note.push(rest[i])
    }
    note.push(...rest.slice(i))
    const result = OUTCOME_RESULTS[(keys.Result ?? '').normalize('NFC').toLowerCase()] ?? null
    const measuredBy = keys['Measured by'] || null
    const source = keys.Source || null
    const reason = keys.Reason || null
    const ok = result !== null && measuredBy !== null
      && (result === 'unmeasurable' ? reason !== null : source !== null)
    if (!ok) { invalid++; continue }
    blocks.push({
      result, by: meta[1].trim(), date: meta[2], via: meta[3], measuredBy, source, reason,
      note: note.join('\n').trim() || null,
    })
  }
  return { blocks, invalid }
}

// The unit-level view: the deadline, and the last valid outcome block — recording again
// adds a block rather than editing one, as answering does. Every field but `deadline` and
// `invalid` is `null` until a valid block exists.
export function unitOutcome(text) {
  const { blocks, invalid } = parseOutcome(text)
  const last = blocks[blocks.length - 1] ?? null
  return {
    deadline: parseDeadline(text),
    result: last?.result ?? null,
    by: last?.by ?? null,
    date: last?.date ?? null,
    measuredBy: last?.measuredBy ?? null,
    source: last?.source ?? null,
    reason: last?.reason ?? null,
    note: last?.note ?? null,
    invalid,
  }
}

// The unit-level view: every question in stage order, and how many are open in the
// **counted** artifact — the latest one, by stage, that has `## Open questions` at all.
// Earlier artifacts' questions are usually carried forward (`write-spec` invariant 5), so
// counting all of them would count one question several times. They stay in the list.
// This number is information, not a gate: `checkGate` does not read it.
export function unitQuestions(unit) {
  let counted = null
  for (const s of STAGES) if (unit.artifacts[s.file]?.questions) counted = s.file
  const questions = []
  for (const s of STAGES) {
    for (const q of unit.artifacts[s.file]?.questions ?? []) {
      questions.push({ artifact: s.file, n: q.n, text: q.text, answered: q.answered, counted: s.file === counted })
    }
  }
  const open = counted ? unit.artifacts[counted].questions.filter((q) => !q.answered).length : 0
  return { questions, open, counted }
}

// --- the pull request and the review rounds -----------------------------------

// `pr.md` names the pull request it opened in its header: `PR: <url>`. Without it the
// `review` gate has nothing to review and no checks to read.
export function parsePr(text) {
  const m = text.match(/\bPR:\s*(\S+?\/pull\/(\d+))/)
  return m ? { url: m[1], number: Number(m[2]) } : null
}

// What `pr.md` puts on its pull request (`0055`): the title is the `# PR:` line, the body
// is the rest of the file less that line and the header — the line holding the `Status:`
// `parseStatus` reads, so "the header" means one thing everywhere. An empty `# PR:` line
// gives no title and still leaves the body, rather than open it with an empty heading.
// Blank lines left at the top go too; every other line is kept byte for byte, `\r`
// included. The terminal and the app both read this one definition, so the two cannot
// put different words up.
export function prText(text) {
  const lines = text.split('\n')
  const titleAt = lines.findIndex((l) => /^# PR:(.*?)\r?$/.test(l))
  const title = titleAt === -1 ? null : lines[titleAt].match(/^# PR:(.*?)\r?$/)[1].trim() || null
  const m = STATUS_RE.exec(text)
  const statusAt = m ? text.slice(0, m.index).split('\n').length - 1 : -1
  const kept = lines.filter((_, i) => i !== statusAt && i !== titleAt)
  while (kept.length && /^\s*$/.test(kept[0])) kept.shift()
  return { title, body: kept.join('\n'), url: parsePr(text)?.url ?? null, scope: prScope(text) }
}

// `0049` R3. `null` when `title` is one a pull request may carry, else where it is wrong. The
// grammar is `<type>(<NNNN>): <text>`: `type` the unit's `Type:` (`null` when its intent
// declares none `BRANCH_TYPES` holds), `number` the four digits its name opens with, and
// `<text>` English that does not open with the word `wip`. This is the one copy of the
// grammar. NFC first, so a Vietnamese letter written as a base and a combining mark is
// refused like the precomposed one.
export function titleProblem(title, type, number) {
  if (!title) return 'pr.md has no title: its # PR: line is missing or empty'
  const m = title.match(/^([a-z]+)\((\d{4})\): (.+)$/)
  if (!m) return `the title "${title}" is not <type>(<NNNN>): <text>`
  if (!BRANCH_TYPES.includes(m[1])) return `the title's type "${m[1]}" is not one of ${TYPE_LIST()}`
  if (!type) return `intent.md declares no Type, so the title's type "${m[1]}" cannot be checked against it`
  if (m[1] !== type) return `the title's type is "${m[1]}", but intent.md declares Type: ${type}`
  if (m[2] !== number) return `the title names unit ${m[2]}, not ${number}`
  if (/^wip(?![a-z0-9])/i.test(m[3])) return `the title's text opens with "wip" — it names the change, not how far it got`
  const marked = title.normalize('NFC').match(/[À-ɏḀ-ỿ]/)
  if (marked) return `the title carries "${marked[0]}", a letter with a diacritic — the title is English`
  return null
}

// `0049` R5, R6: the `review` and `ship` gates' reading of `titleProblem`, `[]` or one line.
function titleNeeds(unit) {
  const artifact = unit.artifacts['pr.md']
  if (!artifact) return []
  const wrong = titleProblem(artifact.title, unit.type ?? null, unit.name.slice(0, 4))
  return wrong ? [`${wrong} — the pr stage writes the # PR: line of pr.md again`] : []
}

// `0122` R2, R3. What `pr.md ## Scope of the diff` states, in the grammar of `0122`: the first
// non-blank line `<N> files, +<A>/-<D>`, then one `` - `<path>` `` per file, then prose.
// `null` with no such heading, a first line in any other form, or a path listed twice.
// Regexes and comparisons only, so no string makes it throw; a bug here is a red test, not
// a silent `null`.
export function prScope(text) {
  const lines = text.split('\n').map((l) => l.replace(/\r$/, ''))
  const at = lines.findIndex((l) => /^## Scope of the diff\s*$/.test(l))
  if (at === -1) return null
  const next = lines.findIndex((l, i) => i > at && l.startsWith('## '))
  const part = lines.slice(at + 1, next === -1 ? lines.length : next)
  let i = part.findIndex((l) => l.trim() !== '')
  const m = i === -1 ? null : part[i].match(/^(\d+) files, \+(\d+)\/-(\d+)\s*$/)
  if (!m) return null
  for (i += 1; i < part.length && part[i].trim() === ''; i++);
  const paths = []
  for (; i < part.length; i++) {
    const p = part[i].match(/^- `([^`]+)`\s*$/)
    if (!p) break
    paths.push(p[1])
  }
  if (new Set(paths).size !== paths.length) return null
  return { files: Number(m[1]), additions: Number(m[2]), deletions: Number(m[3]), paths }
}

const ROUND_HEAD = /^## Round (\d+)\s*$/
// `needs-person` since `0028`: every finding left open is one the review confirmed a person
// must act on. The header stays `changes-requested`; the unit is not finished.
// `incomplete` since `0085`: the app's closing turn wrote it for a review that ran out of
// turns, under a `draft` header. It asks for another review, not for a fix.
const ROUND_META = /^Reviewed:\s*([0-9a-f]{7,40})\.?\s+Verdict:\s*(pass|changes-requested|needs-person|incomplete)\.?\s*$/i
// The labels a finding may carry besides `open` and `fixed <sha>` (`0028`): the review
// accepted impl's claim that a person must act (`needs-person`), rejected it
// (`claim-rejected`), or closed the finding on a person's answer in `review.md ## Answers`
// (`answered`).
const PERSON_LABELS = ['needs-person', 'claim-rejected', 'answered']
const FINDING = /^- (F\d+)\s+\[([^\]]*)\]\s*(.*)$/
// `0061` R1/R2: a finding's severity sits between two em dashes (U+2014) right after its
// location, `path:line — low — text`. Anything else — prose, a hyphen, an en dash — reads
// `null`, and `null` is never read as `low`.
const SEVERITY = /^\S+\s+—\s+(high|medium|low)\s+—\s/i
// `0083` R9: the first line of a round's `### Screens`, and one line per screenshot, the
// separators em dashes as in `SEVERITY`. Backticks around the sha, the path and the address
// are allowed and dropped. `0125` R1: so is one parenthesised note after the size,
// `1440×900 (full page)`, which `size` leaves out.
const SCREENS_HEAD = /^Taken at:\s*`?([0-9a-f]{7,40})`?\.\s+Standard:\s*`?([^`\s]+?)`?\.\s+Looked at by:\s*(.+?),\s*from screenshots\.?\s*$/i
const SCREENS_SHOT = /^- `?(\S+?\.png)`?\s+—\s+(\d+)\s*[×x]\s*(\d+)(?:\s*\([^()]*\))?\s+—\s+`?([^`\s]+)`?\s+—\s+(\S.*)$/

// `review.md` is a list of rounds, each `## Round N`, never rewritten once written: a
// re-review appends a round. Each round opens with `Reviewed: <sha>. Verdict: pass|
// changes-requested|needs-person|incomplete.` (`ROUND_META`) and lists its findings under
// `### Findings`, one per line, each labelled `[open]`, `[fixed <sha>]`, or one of
// `PERSON_LABELS` (`[needs-person]`, `[claim-rejected]`, `[answered]`, since `0028`). Any
// other label is `unreadable`, which the `ship` gate treats as not closed. Reading stops
// at `## Answers`, the app's section.
// Each round also carries `text`: its lines verbatim, from `## Round N` up to the next
// `## ` heading, trailing blank space trimmed. It is what the app posts to the pull
// request, so the app never has to find a round's edges itself.
// Since `0083` each round also carries `screens`: `null` when it has no `### Screens`, else
// `{ taken, standard, by, header, shots }` — `header` the section's first line verbatim, the
// three fields `null` when that line is not `SCREENS_HEAD`, and one `{ path, size, address,
// result }` per line that is `SCREENS_SHOT`. What the `ship` gate makes of it is
// `screensProblems`'s to say.
// Since `0027` each round also carries `dropped`, the ids an earlier round raised that this
// one does not list under `### Findings`, and `unfinished`: it asked for changes and dropped
// at least one. This is the only place either is worked out.
export function parseReview(text) {
  const lines = text.split(/\r?\n/)
  const stop = lines.findIndex((l) => l.trimEnd() === '## Answers')
  const rounds = []
  let inFindings = false
  let inScreens = false
  for (const line of stop === -1 ? lines : lines.slice(0, stop)) {
    const head = line.match(ROUND_HEAD)
    if (head) {
      rounds.push({ n: Number(head[1]), reviewed: null, verdict: null, findings: [], screens: null, seenText: false, closed: false, lines: [line] })
      inFindings = false
      inScreens = false
      continue
    }
    if (line.startsWith('## ')) {
      // A section that is not a round ends the one above it.
      if (rounds.length) rounds[rounds.length - 1].closed = true
      inFindings = false
      inScreens = false
      continue
    }
    const r = rounds[rounds.length - 1]
    if (!r || r.closed) continue
    r.lines.push(line)
    if (!r.seenText && line.trim() !== '') {
      r.seenText = true
      const meta = line.trim().match(ROUND_META)
      if (meta) {
        r.reviewed = meta[1].toLowerCase()
        r.verdict = meta[2].toLowerCase()
        continue
      }
    }
    if (line.startsWith('### ')) {
      inFindings = line.trimEnd() === '### Findings'
      inScreens = line.trimEnd() === '### Screens'
      if (inScreens && !r.screens) r.screens = { taken: null, standard: null, by: null, header: null, shots: [] }
      continue
    }
    if (inScreens) {
      if (line.trim() === '') continue
      if (r.screens.header === null) {
        r.screens.header = line.trim()
        const m = r.screens.header.match(SCREENS_HEAD)
        if (m) Object.assign(r.screens, { taken: m[1].toLowerCase(), standard: m[2], by: m[3].trim() })
        continue
      }
      const shot = line.trim().match(SCREENS_SHOT)
      if (shot) r.screens.shots.push({ path: shot[1], size: `${shot[2]}x${shot[3]}`, address: shot[4], result: shot[5].trim() })
      continue
    }
    if (!inFindings) continue
    const f = line.match(FINDING)
    if (!f) continue
    const label = f[2].trim()
    const lower = label.toLowerCase()
    const fixed = label.match(/^fixed\s+([0-9a-f]{7,40})$/i)
    const text = f[3].trim()
    r.findings.push({
      id: f[1],
      label: lower === 'open' ? 'open' : fixed ? 'fixed' : PERSON_LABELS.includes(lower) ? lower : 'unreadable',
      fixedBy: fixed ? fixed[1].toLowerCase() : null,
      text,
      severity: text.match(SEVERITY)?.[1].toLowerCase() ?? null,
    })
  }
  return withDropped(rounds.map(({ n, reviewed, verdict, findings, screens, lines }) => ({
    n, reviewed, verdict, findings, screens, text: lines.join('\n').trimEnd(),
  })))
}

// `0027` R1: every id an earlier round raised, whatever that round's verdict, in the order
// first raised. The round is only read as unfinished, never marked so in the file (R4).
function withDropped(rounds) {
  const seen = []
  return {
    rounds: rounds.map((r) => {
      const listed = new Set(r.findings.map((f) => f.id))
      const dropped = seen.filter((id) => !listed.has(id))
      for (const id of listed) if (!seen.includes(id)) seen.push(id)
      return { ...r, dropped, unfinished: r.verdict === 'changes-requested' && dropped.length > 0 }
    }),
  }
}

// `0136` R5. The rounds a review handed back to the app (`rows`, the snapshot's), each in the
// place of the round of the same number in `parsed`, `parseReview`'s reading of the file. A
// round only the file holds — one written before `0136`, or the `incomplete` round a closing
// turn writes — is still read from it. A row's finding carries its severity and `S<n>` as
// fields; its `text` is the line `write-review` writes, so every reader below sees one shape.
// `text` of the round is the file's, for a person: what the app posts to the pull request.
export function reviewFrom(rows, parsed) {
  const byN = new Map(parsed.rounds.map((r) => [r.n, r]))
  for (const row of rows) {
    const shots = row.screens?.shots ?? []
    byN.set(row.n, {
      n: row.n,
      reviewed: row.reviewed || null,
      verdict: row.verdict,
      findings: row.findings.map((f) => ({
        id: f.id,
        label: f.label,
        fixedBy: f.label === 'fixed' ? f.fixedIn : null,
        text: `${f.path ? (f.lines ? `${f.path}:${f.lines}` : f.path) : '(none)'} — ${f.severity} — ${f.rule ? `${f.rule} ` : ''}${f.text}`,
        severity: f.severity,
        rule: f.rule || null,
      })),
      screens: shots.length
        ? {
            taken: row.screens.taken ?? null, standard: row.screens.standard ?? null, by: row.screens.by ?? null,
            header: null, shots: shots.map(({ path, size, address, result }) => ({ path, size, address, result })),
          }
        : null,
      text: byN.get(row.n)?.text ?? '',
    })
  }
  return withDropped([...byN.values()].sort((a, b) => a.n - b.n).map(({ dropped, unfinished, ...r }) => r))
}

// `impl.md ## Needs a person` (`0028`): the findings impl says its stage cannot close, one
// line each, `- F<n>: <reason>`. Only the id is acted on; the reason is carried for a person
// to read and judged by nobody here. A line of any other shape is not a claim. Reading stops
// at `## Answers`, as `parseReview` does. `[]` when the section is absent — every `impl.md`
// written before `0028` — which leaves the loop exactly as it was.
export function parseNeedsPerson(text) {
  const lines = text.split(/\r?\n/)
  const stop = lines.findIndex((l) => l.trimEnd() === '## Answers')
  const own = section((stop === -1 ? lines : lines.slice(0, stop)).join('\n'), 'Needs a person')
  if (own === null) return []
  const claims = []
  for (const line of own) {
    const m = line.match(/^- (F\d+):\s*(\S.*)$/)
    if (m) claims.push({ id: m[1], reason: m[2].trim() })
  }
  return claims
}

// --- the questions a spec could not answer, and what a spike measured -----------

// `0039` R1/R2: a concern `spec.md` could not measure is an item at column 0 under
// `## Concerns` that opens `[unmeasured] U<n>` (`- `, `* ` or `N. ` first). `U<n>` is the
// question's identity across rewrites of the spec. `[unmeasured]` mid-sentence, or outside
// `## Concerns`, is prose and is not counted. An item with no readable id, or an id given
// twice, is a problem, never silently dropped: a question that cannot be named cannot be
// matched to its measurement.
export function parseUnmeasured(text) {
  const lines = section(text, 'Concerns') ?? []
  const ids = []
  const problems = []
  for (const line of lines) {
    const m = line.match(/^(?:[-*]|\d+\.)\s+\[unmeasured\](.*)$/)
    if (!m) continue
    const id = m[1].match(/^\s(U\d+)\b/)?.[1]
    if (!id) problems.push(`spec.md: an [unmeasured] item carries no U<n>: "${line.trim()}"`)
    else if (ids.includes(id)) problems.push(`spec.md: ${id} is marked [unmeasured] twice`)
    else ids.push(id)
  }
  return { ids, problems }
}

// `0039` R6: `spike.md` holds one `## U<n>` per question, each with `Verdict: holds.` or
// `Verdict: fails.` and at least one fenced block — the command run and what it printed.
// `round` is the header's `Round: N`, which `write-spike` sets; `null` when absent. Only the
// header line — the one carrying `Status:` before the first `## ` — is read for it: a
// `Round:` inside a fenced block is a command's output, not the round. Reading stops at
// `## Answers`, as `parseReview` does.
export function parseSpike(text) {
  const lines = text.split(/\r?\n/)
  const stop = lines.findIndex((l) => l.trimEnd() === '## Answers')
  const own = stop === -1 ? lines : lines.slice(0, stop)
  const first = own.findIndex((l) => l.startsWith('## '))
  const header = (first === -1 ? own : own.slice(0, first)).find((l) => /\bStatus:/.test(l))
  const round = header?.match(/\bRound:\s*(\d+)/)
  const items = {}
  let at = null
  let fence = null
  for (const line of own) {
    if (fence === null && line.startsWith('## ')) {
      const head = line.match(/^## (U\d+)\s*$/)
      at = head ? (items[head[1]] = { verdict: null, hasBlock: false, _body: false }) : null
      continue
    }
    if (!at) continue
    if (fence !== null) {
      if (line.trim().startsWith(fence)) {
        if (at._body) at.hasBlock = true
        fence = null
      } else if (line.trim() !== '') at._body = true
      continue
    }
    const open = line.trim().match(/^(`{3,}|~{3,})/)
    if (open) {
      fence = open[1]
      at._body = false
      continue
    }
    const v = line.match(/^Verdict:\s*(holds|fails)\.?\s*$/i)
    if (v) at.verdict = v[1].toLowerCase()
  }
  for (const item of Object.values(items)) delete item._body
  return { round: round ? Number(round[1]) : null, items }
}

// `0112` R5: the review round a `ship.md` was written against — `Round: <n>` on the header
// line that carries `Status:` — and, from `## What went out`, the first line opening
// `Refused:` at column 0: what `gh` said when it would not merge. Either is `null` when
// absent, which is every `ship.md` written before `0112`.
export function parseShip(text) {
  const lines = text.split(/\r?\n/)
  const stop = lines.findIndex((l) => l.trimEnd() === '## Answers')
  const own = stop === -1 ? lines : lines.slice(0, stop)
  const first = own.findIndex((l) => l.startsWith('## '))
  const header = (first === -1 ? own : own.slice(0, first)).find((l) => /\bStatus:/.test(l))
  const round = header?.match(/\bRound:\s*(\d+)/)
  const out = section(own.join('\n'), 'What went out') ?? []
  const refused = out.find((l) => l.startsWith('Refused:'))
  return {
    round: round ? Number(round[1]) : null,
    refused: refused ? refused.slice('Refused:'.length).trim() || null : null,
  }
}

// How many `spec → spike` rounds may end with a question that does not hold before the
// loop needs a person (`0039` spec, Answers, Câu 3). A choice, not a measurement.
export const SPIKE_ROUNDS = 2

// How many review rounds may end in `changes-requested` before the loop stops and needs a
// person. The originator chose 3 (`0015` intent, Answers, Câu 3); it is a choice, not a
// measurement. `COS_REVIEW_ROUNDS` overrides it, in the same `COS_*` family as the model
// setting, so that when `0004` makes settings live this number moves with the model rather
// than becoming a second place to look.
export const REVIEW_ROUNDS = 3

// The limit in force, or a thrown `RangeError` naming the variable. A bad value is a
// configuration mistake, not a fact about a unit, so the command exits 2 on it.
export function reviewRounds(env = process.env) {
  const raw = env.COS_REVIEW_ROUNDS
  if (raw === undefined || raw === '') return REVIEW_ROUNDS
  if (!/^\d+$/.test(raw.trim()) || Number(raw) < 1) {
    throw new RangeError(`COS_REVIEW_ROUNDS must be a positive integer, got "${raw}"`)
  }
  return Number(raw)
}

// `state` is the app's snapshot (`--state`), where `0040`'s references resolve too: the
// workspace `<ws>/…` names, or the unit's own for none.
//
// `0135`. A unit's metadata — each artifact's status, `Type:`, links, questions, answers and
// holds — is the app's entry for it, never its files: they are read only for what the app
// does not keep (spec C2) and for whether each one exists. Without a snapshot there is
// nothing to decide on, so it throws.
export function readUnit(dir, name, { state } = {}) {
  if (!state) throw new Error(NEEDS_STATE)
  const unit = { name, artifacts: {}, problems: [] }
  const known = entryOf(state, state.workspace, name) ?? NO_ENTRY
  let intentText = null
  // `0054`. Each artifact's text, kept for the stale check below and never attached.
  const texts = {}
  const match = name.match(UNIT_RE)
  if (!match) {
    unit.problems.push(`directory name does not match NNNN_<slug>`)
  } else {
    unit.number = Number(match[1])
    unit.slug = match[2]
  }

  for (const file of ARTIFACTS) {
    const path = join(dir, file)
    if (!existsSync(path)) continue
    const text = readFileSync(path, 'utf8')
    texts[file] = text
    if (file === 'intent.md') intentText = text
    const status = statusIn(known, file)
    if (status === null) {
      unit.problems.push(`${file} carries no Status line`)
    } else if (!VALID[file].includes(status)) {
      unit.problems.push(`${file} has status "${status}", not one of ${VALID[file].join(', ')}`)
    }
    unit.artifacts[file] = { status, skipReason: file === 'plan.md' ? parseSkipReason(text) : null }
    // `0136` R14: whose skip it was is the transition the app recorded, never the file.
    // Attached only to a skip no person decided, as `stale` below, so `status --json` of a
    // unit a person skipped is what it was.
    const by = known.artifacts?.[file]?.authority ?? null
    if (status === 'skipped' && !DECIDERS.includes(by)) unit.artifacts[file].agentSkip = { by }
    // Attached, never reported as a problem: `review.md` files written before rounds
    // existed have none, and the closed units' output must not change (`0015` spec, R7).
    if (file === 'pr.md') {
      unit.artifacts[file].pr = parsePr(text)
      // `0049`: what the `review` and `ship` gates hold the grammar to, and compare with GitHub.
      // Attached only when there is one, so `status --json` of a `pr.md` with no `# PR:` line
      // stays what it was; the gates read its absence as no title.
      const { title } = prText(text)
      if (title !== null) unit.artifacts[file].title = title
    }
    if (file === 'review.md') {
      const rows = known.artifacts?.[file]?.rounds ?? []
      unit.artifacts[file].review = rows.length ? reviewFrom(rows, parseReview(text)) : parseReview(text)
      // `0028`: the findings a person answered, by id, from `review.md ## Answers`.
      const answers = answersIn(known, file)
      unit.artifacts[file].personAnswers = [...new Set(answers.filter((a) => a.id !== null).map((a) => a.id))]
      // `0081` R2/R4: attached only when a round was granted, as `unmeasured` below, so
      // `status --json` of every unit without a `### More rounds` block stays what it was.
      const more = parseMoreRounds(text)
      if (more.granted > 0) unit.artifacts[file].roundsGranted = more.granted
      unit.problems.push(...more.problems.map((p) => `review.md: ${p}`))
    }
    // `0136` R6: the claims an impl run handed back decide; `## Needs a person` is prose, read
    // only for the reason a person sees beside each id, and for a unit whose impl ran before.
    if (file === 'impl.md') {
      const said = parseNeedsPerson(text)
      const claimed = known.artifacts?.[file]?.result?.needs_person
      unit.artifacts[file].needsPerson = Array.isArray(claimed)
        ? claimed.map((id) => ({ id, reason: said.find((c) => c.id === id)?.reason ?? null }))
        : said
    }
    // `0039`: attached only when there is something to attach, so that `status --json` of
    // every unit that never used `[unmeasured]` stays what it was, byte for byte (R4).
    // `0136` R4: a stage result the app received decides a spec's `U<n>` and a spike's
    // verdicts; the file is read only for a unit whose run handed back none.
    const result = known.artifacts?.[file]?.result ?? null
    if (file === 'spec.md' && result) {
      const ids = [...(result.unmeasured ?? [])]
      if (ids.length) unit.artifacts[file].unmeasured = { ids, problems: [] }
    } else if (file === 'spec.md') {
      const unmeasured = parseUnmeasured(text)
      if (unmeasured.ids.length || unmeasured.problems.length) {
        unit.artifacts[file].unmeasured = unmeasured
        unit.problems.push(...unmeasured.problems)
      }
    }
    if (file === 'spike.md') {
      const parsed = parseSpike(text)
      unit.artifacts[file].spike = result
        ? { round: parsed.round, items: Object.fromEntries((result.verdicts ?? []).map((v) => [v.id, { verdict: v.verdict, hasBlock: true }])) }
        : parsed
    }
    // `0112` R5: attached only when it says something, as `unmeasured` above, so that
    // `status --json` of every `ship.md` written before it stays what it was.
    if (file === 'ship.md') {
      const ship = parseShip(text)
      if (ship.round !== null || ship.refused !== null) unit.artifacts[file].ship = ship
    }
    // Read after `spec.md` and `spike.md`, which `STAGES` puts before it. Only when `spike`
    // is required: a plan may mention `spike.md` without needing one — this unit's does.
    if (file === 'plan.md' && required(unit, SPIKE)) unit.artifacts[file].citesSpike = text.includes('spike.md')
    const questions = joinAnswers(known.artifacts?.[file]?.questions ?? null, answersIn(known, file))
    if (questions !== null) unit.artifacts[file].questions = questions
  }
  for (const [file, a] of Object.entries(known.artifacts ?? {})) {
    if (!a?.status || file in unit.artifacts) continue
    // `0136` R14: a skip a person recorded (`coscc skip`) wrote no file, and needs none.
    if (a.status === 'skipped' && DECIDERS.includes(a.authority)) unit.artifacts[file] = { status: a.status, skipReason: null }
    else unit.problems.push(`the app records ${file} as ${a.status}, but the file does not exist`)
  }
  for (const u of known.unknowns ?? []) if (u.field === 'ingest') unit.problems.push(`the app could not read this unit after its last step: ${u.reason}`)

  Object.assign(unit, unitQuestions(unit))
  // The one list the board's Questions tab shows a finding from (`0028`); empty unless the
  // last review round is a well-formed wait for a person. Derived here, never on the page.
  unit.personFindings = personFindings(unit) ?? []
  // `0061` R10: what `ship.md` lists as left open on purpose, so the ship stage never
  // applies the rule itself.
  unit.nonBlocking = nonBlocking(unit)

  const stray = readdirSync(dir).filter((f) => !ARTIFACTS.includes(f))
  if (stray.length) unit.problems.push(`unexpected file(s): ${stray.join(', ')}`)

  // `phase` separates "not started" from "something is wrong". A unit holding only a valid
  // `idea.md` is one that was opened a moment ago and has not reached its intent yet; the
  // missing intent is the next step, not a defect. Anything else without an intent — an
  // empty directory, an idea with no readable status, a spec or plan with no intent under
  // it — is still reported, exactly as before. `nextAction` and `checkGate` do not read
  // this: a pre-intent unit is still blocked on `write-intent`, because it is.
  unit.phase = 'started'
  if (!unit.artifacts['intent.md']) {
    const idea = unit.artifacts['idea.md']
    const ideaValid = idea && idea.status !== null && VALID['idea.md'].includes(idea.status)
    const later = STAGES.slice(STAGES.findIndex((s) => s.name === 'intent') + 1).some((s) => present(unit, s.file))
    if (ideaValid && !later) unit.phase = 'pre-intent'
    else unit.problems.push(`no intent.md — every unit opens with one`)
  } else {
    // Same distinction `missing()` draws below: a header with no `Type:` is a different
    // repair from a header that declares one nothing accepts. Both are reported and
    // neither is blocked — `checkGate` does not read this.
    const type = known.type ?? null
    if (type === null) unit.problems.push(`intent.md declares no Type — add "Type: <${BRANCH_TYPES[0]}|…>" to its header`)
    else if (!BRANCH_TYPES.includes(type)) unit.problems.push(`intent.md has type "${type}", not one of ${BRANCH_TYPES.join(', ')}`)
    else unit.type = type
    // Derived, never typed — the `ship` gate reads the branch the review was of.
    unit.branch = branchFor(name, type).branch ?? null
  }
  Object.assign(unit, laneOf(unit, { intent: intentText, impl: texts['impl.md'] ?? null }))
  // The draft an impl leaves when it takes a fix out of the fast lane records why, and is no
  // impl of the plan written after it. Attached only when it is one, as `stale` below.
  if (unit.enteredFast && statusOf(unit, 'impl.md') === 'draft' && LANE_FULL.test(headerOf(texts['impl.md'] ?? ''))) {
    unit.artifacts['impl.md'].leftLane = true
  }
  // `0047`: read from the text already in hand, never another file. `nextAction` and
  // `checkGate` do not read it — a finished unit stays finished whatever it says.
  unit.outcome = intentText === null ? null : unitOutcome(intentText)

  // `0045`. The app's hold rows, every unit with an intent (`0135`). `plan.md: done` and a
  // rejected artifact win over a hold (spec R5): the unit stays finished or closed, and the
  // hold is reported rather than obeyed.
  const held = intentText !== null ? foldHolds(known.holds ?? []) : { hold: null, problems: [] }
  unit.problems.push(...held.problems.map((p) => `intent.md: ${p}`))
  unit.hold = held.hold
  const ended = endedOf(unit)
  if (ended && unit.hold) {
    unit.problems.push(`intent.md carries a hold block, but the unit is ${ended} — it is ignored`)
    unit.hold = null
  }
  // No intent — a pre-intent unit, or a broken one — has nowhere to write a block.
  unit.holdMoves = ended || intentText === null ? [] : HOLD_MOVES[unit.hold?.state ?? 'active']

  // `0054` R4. `stale` is attached only to an artifact that is stale, so `status --json` of a
  // unit with no `### Rerun` block is what it was, byte for byte. Only a settled artifact
  // can be: a `changes-requested` review already goes round again, and a draft is
  // unfinished either way. `spike.md` only while the spec still names a `U<n>`.
  const rerun = parseReruns(intentText)
  unit.problems.push(...rerun.problems.map((p) => `intent.md: ${p}`))
  for (const [file, text] of Object.entries(texts)) {
    if (!rerun.reruns.length || !settled(statusOf(unit, file))) continue
    if (file === 'spike.md' && !unmeasuredOf(unit).ids.length) continue
    const hash = aboveAnswers(text)
    const by = rerun.reruns.findLast((r) => r.stale[file] === hash)
    if (by) unit.artifacts[file].stale = { stage: by.stage, date: by.date }
  }

  if (intentText !== null) resolveLinks(unit, known.links ?? NO_ENTRY.links, { state })
  return unit
}

// What a deciding command says when it is not given the app's snapshot (spec R6).
export const NEEDS_STATE = 'needs the coscc app: pass --state <file|-> (uv run coscc state <workspace>)'

// `0135`. The snapshot `--state` carries, as the app builds it (`coscc/units/meta.py`):
// `{workspace, workspaces, units: {<ws>/<unit>: entry}, ideas: {<ws>: [idea]}}`, an entry
// `{artifacts: {<file>: {status, raw, questions}}, type, links, holds, answers, unknowns, merged}`.
// `raw` is a status word outside the artifact's set, kept so it is reported as it was;
// `merged` is whether the app holds the unit's merge (`0139` R5).
const NO_ENTRY = { artifacts: {}, type: null, links: { idea: null, repo: null, dependsOn: null }, holds: [], answers: [], unknowns: [], merged: false }
const entryOf = (state, ws, name) => state.units?.[`${ws}/${name}`] ?? null
const statusIn = (e, file) => e.artifacts?.[file]?.status ?? e.artifacts?.[file]?.raw ?? null
const answersIn = (e, file) => (e.answers ?? []).filter((a) => a.artifact === file).map(({ artifact, ...a }) => a)

// A unit `plan.md: done` finished, or one a rejected artifact closed; a hold does not move either.
const endedOf = (unit) => (statusOf(unit, 'plan.md') === 'done'
  ? 'finished'
  : STAGES.some((s) => statusOf(unit, s.file) === 'rejected') ? 'closed' : null)

// `0135`. What a dependency is judged on (`dependency`), from the app's entry alone: a unit
// in another workspace has no directory here to read.
function entryUnit(e) {
  const unit = { artifacts: {} }
  for (const file of ARTIFACTS) if (e.artifacts?.[file]) unit.artifacts[file] = { status: statusIn(e, file) }
  unit.hold = endedOf(unit) || !unit.artifacts['intent.md'] ? null : foldHolds(e.holds ?? []).hold
  return unit
}

// `cosDir` is a parameter because this file is also the template: the app reads another
// workspace's units by pointing **its own** copy of these rules at that workspace, rather
// than executing the copy it finds there. A workspace is a repository cloned from a URL a
// user typed, so its `.claude/scripts/cos.mjs` is someone else's code; running it would
// hand it everything this process has.
//
// `ideas/` is not a unit (`0040` R2): it holds the ideas several units share, and `readIdeas`
// reads it.
export function readAll(cosDir = COS, { state } = {}) {
  if (!existsSync(cosDir)) return []
  return readdirSync(cosDir, { withFileTypes: true })
    .filter((e) => e.isDirectory() && e.name !== IDEAS)
    .map((e) => readUnit(join(cosDir, e.name), e.name, { state }))
    .sort((a, b) => a.name.localeCompare(b.name))
}

// `0135`. A unit's metadata as its files carry it, read by the parsers above and decided on
// nowhere: `meta` prints it, and the app's import and ingest are its only readers. `status`
// is `null` unless `raw`, the word read, is one the artifact may carry. `only` narrows it to
// the artifacts an ingest saw change; `type`, `links` and `holds` come with `intent.md`.
export function unitMeta(dir, only = null) {
  const artifacts = {}
  const answers = []
  let intentText = null
  for (const file of ARTIFACTS) {
    if (only && !only.includes(file)) continue
    const path = join(dir, file)
    if (!existsSync(path)) continue
    const text = readFileSync(path, 'utf8')
    const raw = parseStatus(text)
    artifacts[file] = {
      status: raw !== null && VALID[file].includes(raw) ? raw : null,
      raw,
      sha256: createHash('sha256').update(text).digest('hex'),
      questions: parseQuestions(text),
    }
    answers.push(...parseAnswers(text).map((a) => ({ artifact: file, ...a })))
    if (file === 'intent.md') intentText = text
  }
  const meta = { artifacts, answers }
  if (intentText !== null) Object.assign(meta, { type: parseType(intentText), links: parseLinks(intentText), holds: holdBlocks(intentText) })
  return meta
}

// --- one idea, several units, several repositories (`0040`) -------------------

// A workspace name, as the app's `valid_name` has it (`coscc/service/store.py:48-69`). It
// holds no `/`, so a reference splits on it.
const WS_RE = /^[A-Za-z0-9._-]{1,64}$/
export const validWs = (s) => WS_RE.test(s ?? '') && s !== '.' && s !== '..'

const IDEAS = 'ideas'
const IDEA_FILE_RE = /^(\d{4})_([a-z0-9]+(?:-[a-z0-9]+)*)\.md$/

// `ideas/NNNN_<slug>.md` in the unit's own store, `<ws>/ideas/NNNN_<slug>.md` in a peer's,
// told apart by how many `/` the reference holds (spec, the reference grammar). `null` when
// it is neither.
export function parseIdeaRef(ref) {
  const parts = String(ref ?? '').split('/')
  const [ws, dir, file] = parts.length === 2 ? [null, ...parts] : parts.length === 3 ? parts : []
  if (dir !== IDEAS || !IDEA_FILE_RE.test(file ?? '') || (ws !== null && !validWs(ws))) return null
  return { ws, file }
}

// `NNNN_<slug>` in the unit's own store, or `<ws>/NNNN_<slug>`.
export function parseUnitRef(ref) {
  const parts = String(ref ?? '').split('/')
  const [ws, name] = parts.length === 1 ? [null, parts[0]] : parts.length === 2 ? parts : []
  if (!UNIT_RE.test(name ?? '') || (ws !== null && !validWs(ws))) return null
  return { ws, name }
}

// One field of `intent.md`'s header, above its first `## `, so the body may mention it
// freely. The value runs to whitespace, less one closing `.`; a list is joined by `, `.
function headerField(text, key) {
  const lines = text.split(/\r?\n/)
  const first = lines.findIndex((l) => l.startsWith('## '))
  const header = (first === -1 ? lines : lines.slice(0, first)).slice(1).join('\n')
  const m = header.match(new RegExp(`(?:^|\\s)${key}:[ \\t]*([^\\s,]+(?:,[ \\t]*[^\\s,]+)*)`, 'm'))
  return m ? m[1].replace(/\.$/, '') : null
}

// R4: `null` for each field the header does not carry.
export function parseLinks(text) {
  const deps = headerField(text, 'Depends on')
  return { idea: headerField(text, 'Idea'), repo: headerField(text, 'Repo'), dependsOn: deps === null ? null : deps.split(/,[ \t]*/) }
}

// The line the app appends under `## Units` for each unit opened from the idea (R3).
const UNIT_LINE = /^- (\S+?)(?:\. Depends on: (.+?))?\.?$/

// R3: an idea file, with every line under `## Units` it cannot read reported, not guessed.
export function parseIdea(text) {
  const units = []
  const problems = []
  const status = parseStatus(text)
  if (status === null) problems.push('carries no Status line')
  const lines = section(text, 'Units')
  if (lines === null) problems.push('has no ## Units section')
  for (const line of lines ?? []) {
    if (!line.trim()) continue
    const m = line.trimEnd().match(UNIT_LINE)
    const deps = m?.[2] ? m[2].split(/,\s*/) : []
    if (!m || !parseUnitRef(m[1])?.ws || deps.some((d) => !parseUnitRef(d)?.ws)) {
      problems.push(`## Units: "${line.trim()}" is not "- <ws>/NNNN_<slug>", with ". Depends on: <ws>/NNNN_<slug>" or not`)
      continue
    }
    units.push({ ref: m[1], dependsOn: deps })
  }
  return { title: text.match(/^# Idea:[ \t]*(.+)$/m)?.[1].trim() ?? null, status, units, problems }
}

// Every file under `<cosDir>/ideas/`, by name. Only called when that directory exists.
export function readIdeas(cosDir) {
  const dir = join(cosDir, IDEAS)
  return readdirSync(dir, { withFileTypes: true })
    .sort((a, b) => a.name.localeCompare(b.name))
    .map((e) => {
      if (!e.isFile() || !IDEA_FILE_RE.test(e.name)) {
        return { id: e.name, title: null, status: null, units: [], problems: ['not a file named NNNN_<slug>.md'] }
      }
      return { id: e.name.slice(0, -'.md'.length), ...parseIdea(readFileSync(join(dir, e.name), 'utf8')) }
    })
}

// R5: the workspace a reference names, as the snapshot keys it (`0135`, where `--peer` once
// named a store). The unit's own for no `<ws>`, and for its own `Repo:` when the snapshot
// names no workspace so.
function storeOf(ws, repo, { state }) {
  if (ws !== null && (state.workspaces ?? []).includes(ws)) return { ws }
  if (ws === null || ws === repo) return { ws: state.workspace }
  return { why: `the app has no workspace named ${ws}` }
}

// `readUnit` keeps what it learned about a unit's links here rather than on the unit, so
// `status --json` carries `idea`, `repo` and `dependsOn` and nothing else (R4). `needs` is
// every reason the idea shuts `impl` (R9); `waiting`, every dependency not merged (R7).
const LINKS = new WeakMap()
const linksOf = (unit) => LINKS.get(unit) ?? { needs: [], waiting: [] }

// R8: merged is the entry's `merged`, never `ship.md`'s status (`0139` R5): the app sets it from
// the PR machine's `merged` row, or from a ship recorded before the machine existed
// (`coscc/units/meta.py` `UnitMeta.snapshot`). No `gh` and no `git`: `spike.md ## U1` of
// `0040` could not measure `gh pr view` run from another repository's checkout.
function dependency(raw, unit, repo, ctx) {
  const ref = parseUnitRef(raw)
  if (!ref) return { ref: raw, merged: null, why: 'not NNNN_<slug> or <ws>/NNNN_<slug>' }
  if (ref.name === unit.name && (ref.ws === null || ref.ws === repo)) return { ref: raw, merged: null, why: 'a unit cannot depend on itself' }
  const store = storeOf(ref.ws, repo, ctx)
  if (store.why) return { ref: raw, merged: null, why: store.why }
  const e = entryOf(ctx.state, store.ws, ref.name)
  if (!e) return { ref: raw, merged: null, why: `unreadable: the app knows no unit ${store.ws}/${ref.name}` }
  const other = entryUnit(e)
  if (other.hold?.state === 'dropped') return { ref: raw, merged: false, why: 'dropped' }
  const rejected = STAGES.find((s) => statusOf(other, s.file) === 'rejected')
  if (rejected) return { ref: raw, merged: false, why: `rejected: its ${rejected.file} is rejected` }
  if (e.merged === true) return { ref: raw, merged: true, why: 'merged' }
  return { ref: raw, merged: false, why: 'not merged: the app holds no merge of it' }
}

// The unit's line under the idea's `## Units`, or why it cannot be had (R6).
function ideaLine(idea, unit, repo, ctx) {
  const ref = parseIdeaRef(idea)
  if (!ref) return { why: 'not ideas/NNNN_<slug>.md or <ws>/ideas/NNNN_<slug>.md' }
  if (repo === null) return { why: 'intent.md declares no Repo:, so its line under ## Units cannot be found' }
  const store = storeOf(ref.ws, repo, ctx)
  if (store.why) return { why: store.why }
  const read = (ctx.state.ideas?.[store.ws] ?? []).find((i) => `${i.id}.md` === ref.file)
  if (!read) return { why: `the app knows no ${store.ws}/${IDEAS}/${ref.file}` }
  const line = read.units.find((u) => u.ref === `${repo}/${unit.name}`)
  return line ? { line } : { why: `it does not list ${repo}/${unit.name} under ## Units` }
}

// R4–R9. Each field is attached only when the header carries it, so every unit that has
// none reads as it did, byte for byte. A broken link is a problem and closes nothing but
// `impl` (R6, R9).
function resolveLinks(unit, links, ctx) {
  const { idea, repo, dependsOn } = links
  if (idea === null && repo === null && dependsOn === null) return
  const needs = []
  if (idea !== null) unit.idea = idea
  if (repo !== null) {
    unit.repo = repo
    if (!validWs(repo)) unit.problems.push(`intent.md: Repo: "${repo}" is not a workspace name`)
  }
  if (dependsOn !== null) {
    unit.dependsOn = dependsOn.map((ref) => dependency(ref, unit, repo, ctx))
    for (const d of unit.dependsOn) if (d.merged === null) unit.problems.push(`intent.md: Depends on: ${d.ref} — ${d.why}`)
  }
  if (idea !== null) {
    const found = ideaLine(idea, unit, repo, ctx)
    if (found.why) {
      unit.problems.push(`intent.md: Idea: ${idea} — ${found.why}`)
      needs.push(`Idea: ${idea} cannot be read: ${found.why}`)
    } else {
      // R9: both copies agree, or `impl` stays shut. A reference with no `<ws>` is the unit's own.
      const full = (r) => (parseUnitRef(r)?.ws === null ? `${repo}/${r}` : r)
      const listed = found.line.dependsOn.map(full).sort()
      const declared = (dependsOn ?? []).map(full).sort()
      if (listed.join(', ') !== declared.join(', ')) {
        needs.push(`${idea} lists ${repo}/${unit.name} with Depends on: ${listed.join(', ') || 'nothing'}, but intent.md declares ${declared.join(', ') || 'none'} — the two must match`)
      }
    }
  }
  LINKS.set(unit, { needs, waiting: (unit.dependsOn ?? []).filter((d) => d.merged !== true) })
}

// The reasons `impl` is shut for a unit's links: R9 first, then one line per dependency (R7).
function linkNeeds(unit) {
  const { needs, waiting } = linksOf(unit)
  return [...needs, ...waiting.map((d) => `waits on ${d.ref}: ${d.why}`)]
}

// The words `next` opens its action with while `impl` waits on a dependency. Words for a
// person: the autopilot reads the code `waiting-on` beside them (`0136` R11).
export const WAITING_ON = 'waiting on '

// `0136` R11: a reason code `gate` and `next` hand out beside their words. The one table is
// `coscc/units/guards.py` `REASONS`, and `guards_test.py` reads every `code('…')` and every
// `why: '…'` here back against it. The autopilot branches on these, never on the words.
const code = (c) => c

// R7: an answer that would run `impl` while a dependency is not merged runs nothing, and
// says what it waits on. One whose idea link is broken says so (R9). Every other answer is
// returned as it came.
function waitOnDependencies(unit, answer) {
  if (answer.stage !== 'impl') return answer
  const { needs, waiting } = linksOf(unit)
  if (waiting.length) return { blocked: true, action: `${WAITING_ON}${waiting.map((d) => d.ref).join(', ')} to merge`, stage: '', why: 'dependency' }
  if (needs.length) return { blocked: true, action: `fix the idea link — ${needs[0]}`, stage: '', why: 'unreadable' }
  return answer
}

// --- deciding ----------------------------------------------------------------

const statusOf = (u, f) => u.artifacts[f]?.status ?? null
const present = (u, f) => f in u.artifacts

// What counts as "this stage is behind us". `done` is included because a plan is marked
// `done` only after its proof has passed — strictly further along than `accepted`, so a
// later stage must not be blocked by it.
const settled = (s) => s === 'accepted' || s === 'skipped' || s === 'done'

// `0136` R14: spec and plan may be skipped only on the decision of a person or their
// delegate (`coscc/units/guards.py`, `DECIDERS`). A `skipped` status an agent wrote, or one
// the app took from a file without knowing whose it was, is no skip: the unit stops on it.
const DECIDERS = ['person', 'delegated']
const agentSkip = (u, f) => statusOf(u, f) === 'skipped' && Boolean(u.artifacts[f]?.agentSkip)
const skippedBy = (u, f) => `${f} is skipped by ${u.artifacts[f]?.agentSkip?.by ?? 'no one the app knows'}, not by a person or their delegate`

// A file that exists but carries no readable status is not the same as a missing file, and
// saying so is the difference between "write it" and "fix the one line at the top of it".
const missing = (u, f) => (present(u, f) ? `${f} exists but carries no Status line` : `${f} does not exist`)

const SPIKE = STAGES.find((s) => s.when === 'unmeasured')
const unmeasuredOf = (u) => u.artifacts['spec.md']?.unmeasured ?? { ids: [], problems: [] }

// Which lane a unit walks, read off its files and written nowhere, so no agent can declare
// it: `fast` when all five marks below hold, `full` when one does not. Each mark is the
// letter `laneMissing` names it by.
//   a  the unit is `Type: fix`;
//   b  `intent.md ## Reproduction` holds a fenced block with something in it;
//   c  `intent.md ## Expected` holds one `Source: <path>` or `Source: <path>:<L1>-<L2>` line,
//      the path relative, outside `.cos/` and with no `..`, and one other line not empty;
//   d  `intent.md ## Actual` holds a line not empty;
//   e  neither `spec.md` nor `plan.md` is there, and `impl.md`'s header has no `Lane: full`.
// (e) is why leaving the lane is one-way: an impl that finds the fix is not one writes
// `Lane: full`, and the spec and plan written after it keep the unit in `full` whatever the
// header says later. `enteredFast` is (a)-(d), which leaving does not undo, so a measure of
// the lane still counts a unit that left it. `laneMissing` is only for a fix in `full`.
export function laneOf(unit, { intent = null, impl = null } = {}) {
  const lines = (title) => (intent === null ? null : section(intent, title))
  const filled = (ls) => ls.some((l) => l.trim() !== '')
  const marks = {
    a: unit.type === 'fix',
    b: fencedFilled(lines('Reproduction')),
    c: expectedCited(lines('Expected')),
    d: filled(lines('Actual') ?? []),
    e: !present(unit, 'spec.md') && !present(unit, 'plan.md') && !LANE_FULL.test(headerOf(impl ?? '')),
  }
  const lane = Object.values(marks).every(Boolean) ? 'fast' : 'full'
  const enteredFast = marks.a && marks.b && marks.c && marks.d
  const laneMissing = marks.a && lane === 'full' ? Object.keys(marks).filter((k) => !marks[k]) : []
  return { lane, enteredFast, laneMissing }
}

const LANE_FULL = /\bLane:\s*full\b/
// A unit read by no `readUnit` has no lane, and walks every stage it always walked.
const inLane = (unit, s) => !s.lanes || s.lanes.includes(unit.lane ?? 'full')

// What comes before a file's first `## ` heading: the title and the header lines under it.
const headerOf = (text) => {
  const lines = text.split(/\r?\n/)
  const end = lines.findIndex((l) => l.startsWith('## '))
  return (end === -1 ? lines : lines.slice(0, end)).join('\n')
}

// (b): a fence opened and closed with at least one line between them that is not empty.
function fencedFilled(lines) {
  let inside = null
  for (const l of lines ?? []) {
    if (/^\s*```/.test(l)) {
      if (inside?.some((x) => x.trim() !== '')) return true
      inside = inside === null ? [] : null
    } else if (inside !== null) inside.push(l)
  }
  return false
}

// (c): exactly one `Source:` line, naming a path that cannot point into the unit's own
// artifacts or outside the repository, beside the words of the expected result.
const SOURCE_LINE = /^Source:\s*(`?)([^\s`:]+)(?::(\d+)-(\d+))?\1\s*$/
function expectedCited(lines) {
  if (!lines) return false
  const sources = lines.filter((l) => /^Source:/.test(l.trim()))
  if (sources.length !== 1) return false
  const m = sources[0].trim().match(SOURCE_LINE)
  if (!m) return false
  const [, , path, from, to] = m
  if (path.startsWith('/') || path === '.cos' || path.startsWith('.cos/') || path.split('/').includes('..')) return false
  if (from !== undefined && (Number(from) < 1 || Number(to) < Number(from))) return false
  return lines.some((l) => l.trim() !== '' && !/^Source:/.test(l.trim()))
}

// Whether stage `s` has to be behind a unit before what follows it (`0039` R4, R5). Every
// stage but `idea` always does, in the lanes it belongs to. `spike` does when, and only
// when, `spec.md` names a `U<n>` (`0136` R14): a `spike.md` a spec rewritten without its
// questions left behind no longer makes it. Never when the spec was skipped.
export function required(unit, s) {
  if (!inLane(unit, s)) return false
  if (!s.when) return !s.optional
  if (statusOf(unit, 'spec.md') === 'skipped') return false
  return unmeasuredOf(unit).ids.length > 0
}

// `0039` R7: for every `U<n>` the spec names now, what `spike.md` does not yet show. An id
// the spec no longer names is not read: it was a question of an earlier spec.
function spikeFindings(unit) {
  const spike = unit.artifacts['spike.md']?.spike ?? { round: null, items: {} }
  const fails = []
  const missingIds = []
  const reasons = []
  for (const id of unmeasuredOf(unit).ids) {
    const item = spike.items[id]
    if (!item) {
      missingIds.push(id)
      reasons.push(`${id}: spike.md has no ## ${id}`)
    } else if (item.verdict === null) {
      missingIds.push(id)
      reasons.push(`${id}: spike.md ## ${id} has no readable Verdict: holds. or Verdict: fails.`)
    } else if (!item.hasBlock) {
      missingIds.push(id)
      reasons.push(`${id}: spike.md ## ${id} carries no fenced block with the command and what it printed`)
    } else if (item.verdict === 'fails') {
      fails.push(id)
      reasons.push(`${id}: spike.md measured that it does not hold — spec.md must be rewritten on it`)
    }
  }
  return { round: spike.round ?? 1, fails, missing: missingIds, reasons }
}

// The gate's reasons for everything after `spike`. R2 problems close it whether or not a
// `spike` is required — an item that cannot be named might be the one that requires it.
function spikeNeeds(unit) {
  const need = [...unmeasuredOf(unit).problems]
  if (!required(unit, SPIKE)) return need
  const status = statusOf(unit, 'spike.md')
  if (status !== 'accepted') {
    for (const id of unmeasuredOf(unit).ids) need.push(`${id}: spike.md is ${status === null ? 'missing' : `"${status}"`}, not accepted`)
    return need
  }
  return [...need, ...spikeFindings(unit).reasons]
}

// One action per unit: a named skill, or the one edit that unblocks the file.
//
// `stage` is the same decision in a form a program can act on, read off the files alone:
// the stage whose artifact is the next one missing, or `''` when the files do not settle
// which stage runs. A `changes-requested` review is the case that matters — its action
// names two stages, and only the branch can say which one is due. `nextStep` below asks it.
export function nextAction(unit, limit = REVIEW_ROUNDS) {
  const { why, rerun, ...next } = decide(unit, limit)
  return next
}

// `0106` R1: the stages whose draft can stop on open questions, and so the only ones `next`
// may name as `rerun`. `0115` R3 adds `impl`: a draft `impl.md` that left work for a person
// runs again on its own branch once each item has an answer. `status --json` carries this
// list as `afterAnswers` (R4), so the app keeps no copy of it.
const RERUN_STAGES = ['intent', 'spec', 'spike', 'plan', 'impl']

// `nextAction`, plus `why`: which rule answered, so `nextStep` refines the answer without
// reading the English of `action` back. `0040` R7: `impl` waits on the unit's dependencies.
function decide(unit, limit) {
  return waitOnDependencies(unit, decideFiles(unit, limit))
}

function decideFiles(unit, limit) {
  // `0081` R3: every comparison below, and every "N of M rounds used", reads this unit's.
  limit = reviewLimit(unit, limit)
  // `plan.md: done` closed five units under the three-stage loop, and it stays terminal.
  // Widening the loop must not reopen work that was finished and proved under the old
  // rules — `write-plan` only allows `done` once the proof command has passed.
  if (statusOf(unit, 'plan.md') === 'done') return { blocked: false, action: 'finished', stage: '', why: 'finished' }

  // `0045`: a person held the unit. No stage is offered; `paused` is still unfinished work,
  // `dropped` ended like a rejection. `readUnit` has already cleared a hold on a closed unit.
  const hold = unit.hold ?? null
  if (hold) {
    const how = hold.state === 'paused' ? ' — resume it from the board' : ''
    return { blocked: hold.state === 'paused', action: `${hold.state} — ${hold.reason} (${hold.by}, ${hold.date})${how}`, stage: '', why: hold.state }
  }

  for (const s of STAGES) {
    if (s.when) {
      // `0039` R2: before `required`, so an item with no readable id still stops the loop.
      const problems = unmeasuredOf(unit).problems
      if (problems.length) return { blocked: true, action: `fix spec.md — ${problems[0].replace(/^spec\.md: /, '')}`, stage: '', why: 'unreadable' }
    }
    if (!s.optional && !required(unit, s)) continue
    const status = statusOf(unit, s.file)
    if (s.when && status === 'accepted') {
      const found = spikeFindings(unit)
      // R8: a question measured not to hold sends the unit back to `spec`, ahead of one
      // that is merely missing — rewriting the spec may drop it.
      if (found.fails.length && found.round >= SPIKE_ROUNDS) {
        return {
          blocked: true,
          action: `needs a person — spike round ${found.round} of ${SPIKE_ROUNDS} found ${found.fails.join(', ')} does not hold`,
          stage: '',
          why: 'needs-person',
        }
      }
      if (found.fails.length) {
        return { blocked: true, action: `write-spec again — spike.md measured ${found.fails.join(', ')} does not hold`, stage: 'spec', why: 'spike-fails' }
      }
      if (found.missing.length) {
        return { blocked: true, action: `write-spike again — spike.md does not measure ${found.missing.join(', ')}`, stage: 'spike', why: 'spike-missing' }
      }
    }
    if (status === null) {
      // A file that exists but says nothing is a different problem from a missing one.
      if (present(unit, s.file)) return { blocked: true, action: `fix ${s.file} — it carries no Status line`, stage: '', why: 'unreadable' }
      if (s.optional) continue
      return { blocked: true, action: s.hint, stage: s.name, why: 'missing' }
    }
    if (status === 'rejected') return { blocked: false, action: `closed — ${s.name} rejected`, stage: '', why: 'rejected' }
    // `0136` R14: an agent's skip stops the unit for a person, who records the skip or has
    // the stage run. `stage` stays `''`: running it again would only skip again.
    if (agentSkip(unit, s.file)) {
      return { blocked: true, action: `${skippedBy(unit, s.file)} — a person records the skip (coscc skip), or runs ${s.hint.split(' ')[0]}`, stage: '', why: 'agent-cannot-skip' }
    }
    // `0054` R5: a stage run again from the board left this artifact stale; its stage runs
    // before anything after it. `nextStep` takes a stale `review` or `ship` down the road
    // a missing one takes.
    const stale = unit.artifacts[s.file]?.stale
    if (stale) {
      return { blocked: true, action: `${s.file} is stale — ${stale.stage} was rerun on ${stale.date}: ${s.hint.split(' ')[0]} again`, stage: s.name, why: 'stale' }
    }
    // `0085` R8: a review that ran out of turns left a round the app's closing turn wrote.
    // That is not a draft to finish by hand: another review goes on from it, unless the
    // rounds before it already used the limit (R9 keeps the floor for exactly that).
    if (s.file === 'review.md' && incompleteDraft(unit)) {
      const used = roundsUsed(unit)
      if (used >= limit) return { blocked: true, action: needsAPerson(used, limit), stage: '', why: 'needs-person' }
      return { blocked: true, action: `review round ${lastRound(unit).n} is incomplete — write-review again`, stage: 'review', why: 'review-incomplete' }
    }
    // Reached only once the spec and plan it asked for are behind the unit: impl runs on them.
    if (status === 'draft' && unit.artifacts[s.file].leftLane) {
      return { blocked: true, action: `${s.hint.split(' ')[0]} — impl.md records the fix leaving the fast lane: impl runs again on plan.md`, stage: s.name, why: 'missing' }
    }
    if (status === 'draft') {
      // `0112` R6 and review F1: a `ship.md` naming its `Round` records a merge that did not
      // happen, and only the branch can say what follows it — integrate, review, ship again,
      // or a stop naming the refusal (`nextStep`). So nothing read off the files alone asks
      // for it to be accepted: accepting it would read the unit as finished with its pull
      // request still open. One naming no `Round` predates `0112` and reads as any draft.
      const refused = s.file === 'ship.md' ? unit.artifacts['ship.md']?.ship?.round ?? null : null
      if (refused !== null && lastRound(unit)) {
        return { blocked: true, action: `ship after review round ${refused} did not merge — the next step says what runs now`, stage: '', why: 'ship-refused' }
      }
      // `0106` R1: every question the draft asked has an answer, so running its stage again
      // is what finishes it. `stage` stays `''` — the run button does not offer it — and only
      // the autopilot reads `rerun`, deciding for itself whether it may.
      const questions = unit.artifacts[s.file]?.questions ?? []
      const answered = RERUN_STAGES.includes(s.name) && questions.length > 0 && questions.every((q) => q.answered)
      return { blocked: true, action: `finish and accept ${s.file}`, stage: '', why: 'draft', ...(answered ? { rerun: s.name } : {}) }
    }
    // Not closed and not done: the work goes back to the branch, then to another round.
    if (status === 'changes-requested') {
      const used = roundsUsed(unit)
      if (used >= limit) return { blocked: true, action: needsAPerson(used, limit), stage: '', why: 'needs-person' }
      // `0027` R3: the last round dropped a finding an earlier one raised, so it does not
      // count, and another review goes on from it the way `0085`'s `incomplete` one does.
      // Review F1: the ids go out as `dropped`, a list, not joined into the sentence the
      // board puts on a card (S5).
      const last = lastRound(unit)
      if (last?.unfinished) {
        return {
          blocked: true,
          action: `review round ${last.n} left out findings an earlier round raised — write-review again (${used} of ${limit} rounds used)`,
          stage: 'review',
          dropped: last.dropped,
          why: 'review-incomplete',
        }
      }
      // `0028` (a) and (b): the last round is a well-formed wait for a person. Read off the
      // files alone, so it needs no `--repo`. A round that merely says `needs-person` while
      // something is still open, rejected or unreadable is not one, and falls through.
      const person = personFindings(unit)
      if (person) {
        const waiting = person.filter((p) => !p.answered)
        if (waiting.length) {
          const told = waiting.map((p) => `${p.id}: ${p.reason ?? 'impl.md gives no reason'}`).join('; ')
          return {
            blocked: true,
            action: `needs a person — ${told} — answer each on the Questions tab (POST /api/units/answer, review.md), then write-review`,
            stage: '',
            waiting: waiting.map((p) => p.id),
            why: 'awaits-person',
          }
        }
        return {
          blocked: true,
          action: `a person answered ${person.map((p) => p.id).join(', ')} in review.md — write-review again`,
          stage: '',
          why: 'person-answered',
        }
      }
      return {
        blocked: true,
        action: `fix the open findings of review round ${lastRound(unit)?.n ?? used} on the branch, then write-review again (${used} of ${limit} rounds used)`,
        stage: '',
        why: 'changes-requested',
      }
    }
  }
  return { blocked: false, action: 'finished', stage: '', why: 'finished' }
}

const reviewOf = (unit) => unit.artifacts['review.md']?.review?.rounds ?? []
const lastRound = (unit) => reviewOf(unit).at(-1) ?? null
// `0085`: the header a closing turn leaves, over the round it wrote.
const incompleteDraft = (unit) => statusOf(unit, 'review.md') === 'draft' && lastRound(unit)?.verdict === 'incomplete'

// Rounds that ended asking for changes. A `changes-requested` header whose rounds carry no
// readable verdict still counts as one, so a malformed round cannot buy another.
//
// A `needs-person` round is not counted (`0028` spec R5): it stopped for a person, it did
// not ask impl for anything. So the floor of one is waived once any round reads
// `needs-person` — otherwise a unit whose only round is that one would be charged for it.
//
// An `incomplete` round is not counted either (`0085` R9), for the same reason, and `asked`
// already leaves it out. But it turns the header to `draft`, and the header is what kept the
// floor for a round whose verdict could not be read; so a `draft` whose last round is
// `incomplete` keeps the floor while a full round before it reads no verdict.
//
// An `unfinished` round is not counted either (`0027` R2): it asked for changes but dropped
// a finding an earlier round raised. Its header is still `changes-requested`, so while it is
// last the header keeps the floor only as the `incomplete` draft does — for a round with no
// readable verdict.
export function roundsUsed(unit) {
  const rounds = reviewOf(unit)
  const asked = rounds.filter((r) => r.verdict === 'changes-requested' && !r.unfinished).length
  const waived = rounds.some((r) => r.verdict === 'needs-person')
  const last = rounds.at(-1)
  const floor = (statusOf(unit, 'review.md') === 'changes-requested' && !last?.unfinished) ||
    ((incompleteDraft(unit) || last?.unfinished) && rounds.some((r) => r.verdict === null))
  return Math.max(asked, floor && !waived ? 1 : 0)
}

// `0081` R3: the one place a unit's review limit is decided — the limit every unit shares,
// plus the rounds a person granted this one under `review.md ## Answers`. A unit built in
// memory by a test, or one with no block, was granted none.
export function reviewLimit(unit, limit = REVIEW_ROUNDS) {
  return limit + (unit.artifacts['review.md']?.roundsGranted ?? 0)
}

// The review loop has used this unit's rounds with findings still open: the condition that
// sends it to a person, which `reviewNeeds` and `moreRounds` both ask.
const outOfRounds = (unit, limit) =>
  (statusOf(unit, 'review.md') === 'changes-requested' || incompleteDraft(unit)) && roundsUsed(unit) >= reviewLimit(unit, limit)

// The ids a person answered under `review.md ## Answers`. Units built in memory by a test
// carry no such field, and read as having none.
const personAnswers = (unit) => new Set(unit.artifacts['review.md']?.personAnswers ?? [])
const needsPersonClaims = (unit) => unit.artifacts['impl.md']?.needsPerson ?? []

// `0061` R3: the one place "does not block" is decided. A finding of the last round does not
// block when it is `[open]`, reads `low`, and no earlier round rated the same id `high` or
// `medium` — a severity that could not be read does not count as higher (R12). `demoted`
// holds the `[open]` lows that last condition caught, each with the earliest round that
// rated it higher and what it said, so the `ship` gate can name it (R5). Every other label,
// and a severity that is `null`, blocks.
function severityRule(unit) {
  const rounds = reviewOf(unit)
  const last = rounds.at(-1)
  if (!last) return { nonBlocking: [], demoted: [] }
  const higher = new Map()
  for (const r of rounds.slice(0, -1)) {
    for (const f of r.findings) {
      if ((f.severity === 'high' || f.severity === 'medium') && !higher.has(f.id)) higher.set(f.id, { round: r.n, severity: f.severity })
    }
  }
  // `0083` R11 (spec C3): a finding against the UI standard — its text after the severity
  // opens with `S<n>` — is something the person sees wrong, so it blocks even when rated
  // `low`. Taken out here, before the split, so every reader of this rule sees it block.
  const low = last.findings.filter((f) => f.label === 'open' && f.severity === 'low' && !againstStandard(f.text))
  return {
    nonBlocking: low.filter((f) => !higher.has(f.id)).map((f) => ({ id: f.id, text: f.text })),
    demoted: low.filter((f) => higher.has(f.id)).map((f) => ({ id: f.id, ...higher.get(f.id) })),
  }
}

const againstStandard = (text) => /^S\d+\b/.test(text.slice(text.match(SEVERITY)?.[0].length ?? text.length))

// The findings of the last round that do not block, `[{ id, text }]`. The `ship` gate,
// `next` and `status --json` all read this; none of them applies the rule again.
export const nonBlocking = (unit) => severityRule(unit).nonBlocking
const nonBlockingIds = (unit) => new Set(nonBlocking(unit).map((f) => f.id))

// `0028` spec R6 (a)/(b): the findings the last round confirmed need a person, each with the
// reason impl gave and whether a person has answered it — or `null` when the last round is
// not a well-formed wait. Well-formed means: the header is `changes-requested`, the round's
// verdict is `needs-person`, at least one finding is `[needs-person]`, and every other
// finding is `[fixed <sha>]` or an `[answered]` a block in `review.md ## Answers` backs.
// Anything else — an `[open]`, a `[claim-rejected]`, an unreadable label, an `[answered]`
// with no answer — and the round is not a wait, so a verdict written wrong cannot open the
// way to a person.
function personFindings(unit) {
  if (statusOf(unit, 'review.md') !== 'changes-requested') return null
  const last = lastRound(unit)
  if (!last || last.verdict !== 'needs-person') return null
  const answered = personAnswers(unit)
  if (!last.findings.some((f) => f.label === 'needs-person')) return null
  // `0061` R8: a finding that does not block does not stop the wait either.
  const low = nonBlockingIds(unit)
  const closed = (f) => f.label === 'fixed' || f.label === 'needs-person' || (f.label === 'answered' && answered.has(f.id)) || low.has(f.id)
  if (!last.findings.every(closed)) return null
  const claims = needsPersonClaims(unit)
  return last.findings
    .filter((f) => f.label === 'needs-person')
    .map((f) => ({ id: f.id, reason: claims.find((c) => c.id === f.id)?.reason ?? null, answered: answered.has(f.id) }))
}

// `0028` spec R6 (c): the last round asked for changes, and every finding it left `[open]`
// is one impl claims in `## Needs a person` that no round has judged yet. That claim is
// impl's word about its own work; only a review may confirm it, so this sends the unit to
// review rather than to a person.
//
// A claim is judged once any round has labelled its finding `needs-person`,
// `claim-rejected` or `answered`. A judged finding a later round left `[open]` — a
// person's answer that did not settle it — goes back to impl like any other open finding:
// sending it to review again would show that round the same files and spend a round on
// nothing (`0028` review round 1, F1).
function everyOpenClaimed(unit) {
  const last = lastRound(unit)
  if (!last || last.verdict !== 'changes-requested') return false
  // `0061` R8: only the findings that block need a claim. A round left with nothing but
  // those that do not is still `false` — it should have passed, and goes back to impl.
  const low = nonBlockingIds(unit)
  const open = last.findings.filter((f) => f.label === 'open' && !low.has(f.id))
  if (!open.length) return false
  const judged = new Set(
    reviewOf(unit).flatMap((r) => r.findings.filter((f) => PERSON_LABELS.includes(f.label)).map((f) => f.id)),
  )
  const claimed = new Set(needsPersonClaims(unit).map((c) => c.id).filter((id) => !judged.has(id)))
  if (!open.every((f) => claimed.has(f.id))) return false
  const answered = personAnswers(unit)
  return !last.findings.some(
    (f) => f.label === 'claim-rejected' || f.label === 'unreadable' || (f.label === 'answered' && !answered.has(f.id)),
  )
}

// The first place the loop stops and waits for someone who is not an agent. Since `0081` a
// `### More rounds` block under `review.md ## Answers`, which the board writes through
// `POST /api/units/more-rounds`, lifts it for one unit (`reviewLimit`). At a terminal it
// still lifts as before: `review.md: rejected`, or a larger `COS_REVIEW_ROUNDS`.
const needsAPerson = (used, limit) =>
  `needs a person — review used ${used} of ${limit} rounds and findings are still open`

// --- the screens a unit changes (0083) ----------------------------------------

// The UI standard: its rules for a person to read, and in its front-matter `paths:` the one
// list of files that count as the app's screens (spec R2). This is the only place here that
// names it. It is read from the repository being diffed (`--repo`), not from this script's
// own checkout: the list is a list of that repository's files, and a repository that copied
// `.claude/` without this file has a `ship` gate exactly as before (spec C6).
export const UI_STANDARD = '.claude/rules/ui-standard.md'

// The globs under `paths:` in the front-matter between the first two `---` lines, quotes
// dropped. `[]` when there is no front-matter, no `paths:`, or nothing under it.
export function parseStandard(text) {
  const lines = text.split(/\r?\n/)
  if (lines[0]?.trim() !== '---') return []
  const end = lines.findIndex((l, i) => i > 0 && l.trim() === '---')
  if (end === -1) return []
  const globs = []
  let inPaths = false
  for (const line of lines.slice(1, end)) {
    if (/^paths:\s*$/.test(line)) {
      inPaths = true
      continue
    }
    const item = line.match(/^\s+-\s+(.+?)\s*$/)
    if (inPaths && item) globs.push(item[1].replace(/^(["'])(.*)\1$/, '$2'))
    else if (!/^\s*$/.test(line)) inPaths = false
  }
  return globs
}

// A glob as the rule files write one, by hand so as to add no dependency (spec R3): `**/`
// is zero or more directories, a trailing `**` is anything, `*` stays inside one directory,
// `?` is one character that is not `/`, and every other character is itself.
export function globMatch(glob, path) {
  let re = ''
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i]
    if (c === '*' && glob[i + 1] === '*') {
      if (glob[i + 2] === '/') {
        re += '(?:.*/)?'
        i += 2
      } else {
        re += '.*'
        i += 1
      }
    } else if (c === '*') re += '[^/]*'
    else if (c === '?') re += '[^/]'
    else re += c.replace(/[.+^${}()|[\]\\]/g, '\\$&')
  }
  return new RegExp(`^${re}$`).test(path)
}

// The paths among `paths` that some glob matches, in their order.
export const uiFiles = (paths, globs) => paths.filter((p) => globs.some((g) => globMatch(g, p)))

// `0083` R10: the one place the words of a passing round's `### Screens` are judged. Returns
// what is wrong with them, `[]` when nothing is. Whether `Taken at` is still current needs
// git, and is the gate's to ask (`screensNeeds`).
export function screensProblems(screens, standardPath) {
  if (!screens) return ['it has no ### Screens — review opens every screenshot in .screens/manifest.json and writes the section']
  const problems = []
  if (!screens.taken) {
    problems.push(`the first line of its ### Screens is not "Taken at: <sha>. Standard: <path>. Looked at by: <which agent session>, from screenshots."`)
  } else {
    // Constraint two of the intent: whoever looked is named as an agent, never read as a person.
    if (!/\bagent\b/i.test(screens.by)) problems.push(`its ### Screens says "Looked at by: ${screens.by}", which does not say it was an agent`)
    if (screens.standard !== standardPath) problems.push(`its ### Screens names the standard ${screens.standard}, not ${standardPath}`)
  }
  if (!screens.shots.length) problems.push('its ### Screens lists no screenshot — one line per image, "- <path>.png — <W>×<H> — <address> — <result>"')
  return problems
}

// `0083` R10/R12: asked by `shipNeeds` only once every earlier check has passed, so a unit
// stuck for another reason spends no more `git`, and its reasons read as they did. With no
// standard, or one with no globs, it asks nothing at all; for a unit that changes no screen
// it asks one `git diff`. `said.screens` sends `nextStep` to another review round — except
// when git could not say which files changed, which another round would not cure.
export function screensNeeds(unit, probe, last, said) {
  const { changed, standard, error } = uiChanged(unit, probe, said.head)
  if (error) return [error]
  if (!changed.length) return []
  const why = `this unit changes ${changed.join(', ')}, which ${standard.path} counts as screens`
  const need = screensProblems(last.screens, standard.path).map((p) => `review round ${last.n} passed, but ${p} — ${why}`)
  if (!need.length) {
    const taken = last.screens.taken
    if (probe.git('merge-base', '--is-ancestor', taken, last.reviewed).code !== 0) {
      need.push(`the screenshots of review round ${last.n} were taken at ${taken}, which is not an ancestor of the reviewed commit ${last.reviewed} — take them again on the branch, and review them`)
    } else {
      const after = probe.git('diff', '--name-only', `${taken}..${last.reviewed}`)
      if (after.code !== 0) {
        need.push(`git could not diff ${taken}..${last.reviewed}: ${after.err.trim()}`)
      } else {
        const moved = uiFiles(after.out.split('\n').map((l) => l.trim()).filter(Boolean), standard.globs)
        if (moved.length) need.push(`${moved.join(', ')} changed after the screenshots of review round ${last.n} were taken at ${taken} — take them again, and review them`)
      }
    }
  }
  if (need.length) said.screens = true
  return need
}

// The files among what `head` changed since it left the trunk that the standard counts as
// screens: `{ changed, standard }`, or `{ error }` when git could not say. `changed` is `[]`
// with no standard, or one with no globs, and then git is not asked. Shared by `screensNeeds`
// and `screensAnswer` (`0111`), so there is one comparison and not two.
function uiChanged(unit, probe, head) {
  const standard = probe.ui?.() ?? null
  if (!standard || !standard.globs.length) return { changed: [], standard }
  // Three dots: from the merge-base, in one command. The gate does not fetch (spec C7).
  let diff = probe.git('diff', '--name-only', `origin/main...${head}`)
  if (diff.code !== 0) {
    const local = probe.git('diff', '--name-only', `main...${head}`)
    if (local.code !== 0) {
      return { error: `cannot tell whether ${unit.name} changes a screen: git could not diff origin/main...${head} (${diff.err.trim()}) nor main...${head} (${local.err.trim()}) — the gate does not fetch` }
    }
    diff = local
  }
  const own = `.cos/${unit.name}/`
  return { changed: uiFiles(diff.out.split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith(own)), standard.globs), standard }
}

// `0111` R1, R2. Whether the app should take a UI unit's screenshots again before `review`:
// the screens the branch changes, what `.screens/manifest.json` says, and whether its `head`
// is still an ancestor of `HEAD`. `retake` only when the unit changes a screen, the manifest
// lists addresses, was taken on a clean tree, and its head was rewritten away (a rebase, by
// anyone). `why` names the first of those that does not hold, or is `''`. Reads only; no
// gate is opened or closed by it.
export function screensAnswer(unit, probe) {
  const { changed, error } = uiChanged(unit, probe, 'HEAD')
  const read = probe.manifest?.() ?? null
  const manifest = read && typeof read === 'object' && !Array.isArray(read)
    ? { head: read.head ?? null, dirty: read.dirty ?? null, addresses: read.addresses ?? null, hits: Array.isArray(read.hits) ? read.hits : [] }
    : null
  // A head that is no commit name is not handed to git as an argument.
  const named = typeof manifest?.head === 'string' && /^[0-9a-f]{7,40}$/.test(manifest.head)
  // Exit non-zero also when the commit is gone from the object store: rewritten all the same.
  const rewritten = named && probe.git('merge-base', '--is-ancestor', manifest.head, 'HEAD').code !== 0
  const addressed = Array.isArray(manifest?.addresses) && manifest.addresses.length > 0 && manifest.addresses.every((a) => typeof a === 'string')
  const why = error
    ?? (!changed.length ? 'this unit changes no file the UI standard counts as a screen'
      : !manifest ? 'there is no readable .screens/manifest.json in this checkout'
      : !addressed ? 'the manifest lists no addresses'
      : manifest.dirty !== false ? 'the manifest was taken on a tree with uncommitted changes'
      : !named ? 'the manifest names no commit'
      : !rewritten ? `the manifest's head ${manifest.head} is still an ancestor of HEAD`
      : '')
  return { unit: unit.name, ui: changed ?? [], manifest, rewritten, retake: why === '', why }
}

// --- what the gate asks git and gh -------------------------------------------

// Where `scripts/capture_screens.py` writes its manifest, relative to the repository.
export const SCREENS_MANIFEST = '.screens/manifest.json'

// The two questions `review` and `ship` ask outside `.cos/`. Every other gate reads files
// only. Injected so the rules can be tested without a repository or a network; the default
// runs the real commands in the repository the unit's code lives in, which is **not** the
// store `--root` points at when the app asks (`0014` moved units out of the repository).
export function makeProbe(repoDir) {
  const run = (cmd, args) => {
    const r = spawnSync(cmd, args, { cwd: repoDir, encoding: 'utf8' })
    if (r.error) return { code: -1, out: '', err: String(r.error.message ?? r.error) }
    return { code: r.status, out: r.stdout ?? '', err: r.stderr ?? '' }
  }
  // `0083`: the UI standard of that repository, read from its files rather than `git`, or
  // `null` without one. A test's probe that has no `ui` reads as having none.
  const ui = () => {
    const path = join(repoDir, UI_STANDARD)
    if (!existsSync(path)) return null
    return { path: UI_STANDARD, globs: parseStandard(readFileSync(path, 'utf8')) }
  }
  // `0111`: what `scripts/capture_screens.py` last wrote in that repository, or `null` with no
  // file there or one that is not JSON.
  const manifest = () => {
    try {
      return JSON.parse(readFileSync(join(repoDir, SCREENS_MANIFEST), 'utf8'))
    } catch {
      return null
    }
  }
  // `0103`: every workflow of that repository as `{ path, text }`, `[]` with no
  // `.github/workflows/` or one that cannot be read. A test's probe that has no `workflows`
  // reads as having none.
  const workflows = () => {
    const dir = join(repoDir, '.github', 'workflows')
    try {
      return readdirSync(dir)
        .filter((f) => /\.ya?ml$/.test(f))
        .sort()
        .map((f) => ({ path: `.github/workflows/${f}`, text: readFileSync(join(dir, f), 'utf8') }))
    } catch {
      return []
    }
  }
  return { git: (...args) => run('git', args), gh: (...args) => run('gh', args), ui, manifest, workflows }
}

// `review` may begin only on an open pull request whose required checks are green
// (`0015` spec, Answers, Câu 2). Nothing green to read is not read as green.
//
// `said.ci` records what CI said, once it was asked: `red`, `unfixable`, `pending`, `none`,
// `unreadable` or `green`. `nextStep` reads it to tell "back to impl" from "wait" and from
// "needs a person" without parsing a need. `unfixable` is a red check no rerun and no impl
// can turn green (`0103`); `none` already meant no checks at all.
function reviewNeeds(unit, probe, limit, said = {}) {
  const need = []
  const pr = unit.artifacts['pr.md']?.pr ?? null
  if (!pr) need.push('pr.md names no pull request — the pr stage opens one and writes PR: <url>')
  // `0049` R5: before `probe`, so a title outside the grammar asks no `gh`.
  need.push(...titleNeeds(unit))
  if (outOfRounds(unit, limit)) need.push(needsAPerson(roundsUsed(unit), reviewLimit(unit, limit)))
  if (need.length || !pr) return need
  if (!probe) return ['no repository given — pass --repo <dir>']
  return ciNeeds(probe, pr, said)
}

// The required checks of `pr`, read once: `[]` when green, else why not, with `said.ci` set.
// Shared by `review` and, after a clean rebase, `ship` (`0067` R3), so there is one reading.
function ciNeeds(probe, pr, said) {
  const r = probe.gh('pr', 'checks', String(pr.number), '--required', '--json', 'name,bucket')
  // `gh pr checks` exits non-zero when a check failed or is pending, and still prints the
  // JSON. So the output is read first and the exit code only when there is none.
  let checks = null
  try {
    checks = JSON.parse(r.out)
  } catch {
    checks = null
  }
  if (!Array.isArray(checks)) {
    said.ci = 'unreadable'
    const told = (r.err || r.out).trim() || `gh exited ${r.code} and said nothing`
    return [`cannot read the required checks of #${pr.number}: ${told}`]
  }
  if (!checks.length) {
    said.ci = 'none'
    return [`#${pr.number} reports no required checks — nothing green to read is not green`]
  }
  const red = checks.filter((c) => c.bucket === 'fail' || c.bucket === 'cancel').map((c) => c.name)
  // Before the checks still running: one red check no rerun can fix settles it (`0103` R5).
  if (red.length) return redNeeds(probe, pr, red, said)
  const waiting = checks.filter((c) => c.bucket !== 'pass' && c.bucket !== 'skipping').map((c) => c.name)
  if (waiting.length) {
    said.ci = 'pending'
    return [`CI has not finished on #${pr.number}: ${waiting.join(', ')} — wait, then ask again`]
  }
  said.ci = 'green'
  return []
}

// `0103`: a red check is impl's to fix, unless it is the harness's branch-name check and
// `check-branch` refuses the head GitHub reports (R2). No rerun and no commit renames a
// branch, so that one stops for a person, `said.ci` `unfixable` (R3). Every other goes back
// to impl with the line the autopilot reads as `CI_RED`, byte for byte, and a clause after it
// when whether the name is why cannot be told from here (R4). The head is asked for here and
// only here: one more `gh` on a red read, none on any other (R7).
function redNeeds(probe, pr, red, said) {
  said.ci = 'red'
  const line = `CI is red on #${pr.number}: ${red.join(', ')} — back to impl: fix on the branch and push`
  const view = probe.gh('pr', 'view', String(pr.number), '--json', 'headRefName')
  let head = null
  try {
    head = JSON.parse(view.out)?.headRefName
  } catch {
    head = null
  }
  if (typeof head !== 'string' || !head) {
    const told = (view.err || view.out).trim() || `gh exited ${view.code} and said nothing`
    return [`${line} — cannot read the branch of #${pr.number} (${told}), so whether its name is why cannot be told from here`]
  }
  const named = new Set(branchChecks(probe.workflows?.() ?? []))
  const stuck = red.filter((name) => named.has(name))
  const problem = branchProblem(head)
  if (stuck.length && problem) {
    said.ci = 'unfixable'
    return [`needs a person — CI is red on #${pr.number}: ${red.join(', ')} — ${stuck.join(', ')} checks the branch name, and no rerun or impl can fix it: ${notAWorkBranch(head, problem)}`]
  }
  if (stuck.length) {
    return [`${line} — ${stuck.join(', ')} checks the branch name, yet "${head}" passes check-branch here, so why it failed cannot be told from here`]
  }
  if (problem) {
    return [`${line} — ${notAWorkBranch(head, problem)}, but no red check runs cos.mjs check-branch in .github/workflows/, so whether that is why cannot be told from here`]
  }
  return [line]
}

// The pull request as GitHub reports it, from one `gh pr view`: `{ state, head, merged, title }`,
// `merged` being `{ commit, at }` on a `MERGED` one and `null` otherwise, `title` `null` when
// gh gave none, or `{ error }`. `0116`: the `ship` gate reads the merge commit from the same
// answer as the state, so the two cannot disagree; `0049` R6 reads the title from it too.
function prView(probe, pr) {
  const view = probe.gh('pr', 'view', String(pr.number), '--json', 'state,headRefOid,mergeCommit,mergedAt,title')
  let info = null
  try {
    info = JSON.parse(view.out)
  } catch {
    info = null
  }
  if (!info || typeof info.headRefOid !== 'string') {
    const said = (view.err || view.out).trim() || `gh exited ${view.code} and said nothing`
    return { error: `cannot read the head of #${pr.number}: ${said}` }
  }
  const merged = info.state === 'MERGED' ? { commit: info.mergeCommit?.oid ?? null, at: info.mergedAt ?? null } : null
  return { state: info.state, head: info.headRefOid, merged, title: typeof info.title === 'string' ? info.title : null }
}

// The pull request's head on GitHub, as `{ head }`, or `{ error }` saying why not. Shared by
// `shipNeeds` and `nextStep`: both ask "what is on the pull request now", and two readings
// of one `gh pr view` could disagree. `view` is one the caller already read.
function prHead(probe, pr, view = prView(probe, pr)) {
  if (view.error) return { error: view.error }
  if (view.state !== 'OPEN') return { error: `#${pr.number} is ${view.state}, not open — there is nothing to merge` }
  if (probe.git('cat-file', '-e', `${view.head}^{commit}`).code !== 0) {
    return { error: `the head of #${pr.number}, ${view.head}, is not in this repository — someone pushed from elsewhere: fetch, then ask again` }
  }
  return { head: view.head }
}

// `ship` merges. It may do so only after a pass that left nothing open, whose history is
// intact, and after which no code reached the branch (`0015` spec, R3–R5).
//
// `said.head` is set to the pull request head the gate checked, so the merge can be pinned
// to it.
//
// `0067`: a ref rewritten after the pass no longer closes the gate by itself. When the
// unit's patch there is byte for byte the reviewed one, less line numbers and `index` lines
// (`rebaseClean`), the gate reads CI on the pull request instead of asking for a round. It
// sets `said.rebased` to the two commits only when the rewritten ref is the pull request's
// head. A patch that differs, or cannot be compared, closes it as before.
function shipNeeds(unit, probe, said = {}) {
  const rounds = reviewOf(unit)
  if (!rounds.length) return ['review.md has no ## Round — nothing says what was reviewed or found']
  const last = rounds.at(-1)
  const need = []
  if (last.verdict !== 'pass') {
    need.push(`review round ${last.n} has verdict "${last.verdict ?? 'unreadable'}", not pass — its first line is Reviewed: <sha>. Verdict: pass.`)
  }
  // `0028` spec R8: `[answered]` closes a finding only when `review.md ## Answers` holds a
  // block for that id. `[needs-person]` and `[claim-rejected]` never close one.
  const answered = personAnswers(unit)
  // `0061` R4: a finding that does not block is left open on purpose; `ship.md` lists it.
  // A lowered one still blocks, but is named only on its own line below (`0078` R2).
  const { nonBlocking: low, demoted } = severityRule(unit)
  const passes = new Set([...low, ...demoted].map((f) => f.id))
  const open = last.findings.filter((f) => f.label !== 'fixed' && !(f.label === 'answered' && answered.has(f.id)) && !passes.has(f.id))
  for (const d of demoted) {
    need.push(`${d.id} is low in review round ${last.n}, but review round ${d.round} rated it ${d.severity} — lowering a severity is not a fix: fix it on the branch, or keep it open`)
  }
  if (open.length) {
    const named = (f) => (f.label === 'answered' ? `${f.id} [answered, no answer in review.md]` : `${f.id} [${f.label}]`)
    need.push(`review round ${last.n} still has findings not fixed: ${open.map(named).join(', ')}`)
  }
  // `0027` R5: `parseReview` worked out what the last round dropped.
  if (last.dropped?.length) {
    need.push(`review round ${last.n} drops findings an earlier round raised: ${last.dropped.join(', ')} — carry each one forward, fixed or open`)
  }
  const numbers = rounds.map((r) => r.n)
  if (numbers.some((n, i) => n !== i + 1)) {
    need.push(`review rounds are numbered ${numbers.join(', ')}, not 1 to ${rounds.length} — a round was removed or renumbered`)
  }
  if (!last.reviewed) need.push(`review round ${last.n} names no reviewed commit — Reviewed: <sha>`)
  // `0049` R6: as `review`'s, before `probe` and any `gh`.
  need.push(...titleNeeds(unit))
  if (need.length) return need

  if (!probe) return ['no repository given — pass --repo <dir>']
  if (!unit.branch) return ['the unit has no branch — intent.md must declare a Type']
  const pr = unit.artifacts['pr.md']?.pr ?? null
  if (!pr) return ['pr.md names no pull request — nothing says what ship would merge']
  // `0116`: the pull request is read before the branch is looked for — after
  // `--delete-branch` there may be no ref left, and a merged one needs none. Any state but
  // these two closes the gate as it always did (R3).
  const view = prView(probe, pr)
  if (view.error) return [view.error]
  if (view.state === 'MERGED') return mergedNeeds(probe, pr, view, said)
  if (view.state !== 'OPEN') return [prHead(probe, pr, view).error]
  const refs = [`refs/heads/${unit.branch}`, `refs/remotes/origin/${unit.branch}`].filter(
    (ref) => probe.git('rev-parse', '--verify', '--quiet', ref).code === 0,
  )
  if (!refs.length) return [`no branch ${unit.branch} here, local or origin — the gate does not fetch`]
  if (probe.git('cat-file', '-e', `${last.reviewed}^{commit}`).code !== 0) {
    return [`the reviewed commit ${last.reviewed} is not in this repository`]
  }

  // What the merge lands is the pull request's head on GitHub, not a ref here: a push
  // from another checkout moves it and leaves `origin/<branch>` stale, since the gate does
  // not fetch (`0015` review round 1, F2). So the head is asked for and checked like a ref,
  // and `ship` merges with `--match-head-commit` set to exactly this commit.
  const read = prHead(probe, pr, view)
  if (read.error) return [read.error]
  refs.push(read.head)
  said.head = read.head
  // `0067`: the reviewed commit's patch, taken only once a ref is found rewritten, and then
  // once for all of them — a unit never rebased asks git nothing more (R7).
  let reviewedPatch = null
  let rebased = false
  for (const ref of refs) {
    const name = ref === said.head ? `the head of #${pr.number} (${ref})` : ref
    const since = changedSince(probe, unit, last.reviewed, ref)
    if (since.error) {
      need.push(since.error)
      continue
    }
    // `said.moved`: what the pass reviewed is no longer what would merge. The cure for
    // both is another round, which is what `nextStep` offers when it sees this. A rewrite
    // that left the unit's patch as it was is not one (`0067` R2).
    if (since.rewritten) {
      reviewedPatch ??= unitPatch(probe, unit, last.reviewed)
      const compared = rebaseClean(probe, unit, reviewedPatch, ref)
      // Only the pull request's head is what merges: a local ref rewritten clean while the
      // head is still the reviewed commit leaves the gate as it was, and names no rebase.
      if (compared.clean) {
        if (ref === said.head) rebased = true
        continue
      }
      said.moved = true
      need.push(`the reviewed commit ${last.reviewed} is not on ${name} — the branch was rewritten after the pass (a rebase does this): review its new head in another round; a round that passes does not count toward the limit — ${rebaseWhy(compared)}`)
      continue
    }
    if (since.files.length) {
      said.moved = true
      need.push(`${name} changed after the reviewed commit ${last.reviewed}: ${since.files.join(', ')} — review again`)
    }
  }
  if (need.length) return need
  // `0067` R3: a clean rebase stands in for the round only once CI is green on it — CI is
  // what is left to catch a conflict with no conflicting line. Before `behind`: a head just
  // rebased is up to date, and "wait for CI" is then the true reason.
  if (rebased) {
    said.rebased = { reviewed: last.reviewed, head: said.head }
    const ci = ciNeeds(probe, pr, said)
    if (ci.length) return ci
  }
  // `0112` R3: after `moved`, so a head already rebased goes to review rather than here. A
  // pull request behind `origin/main` is one GitHub refuses to merge; the gate says so first,
  // off the ref as it is — it does not fetch, the autopilot does (R4). No `origin/main`
  // here, no opinion: GitHub still decides.
  const trunk = 'refs/remotes/origin/main'
  if (probe.git('rev-parse', '--verify', '--quiet', trunk).code === 0 && probe.git('merge-base', '--is-ancestor', trunk, said.head).code !== 0) {
    const count = probe.git('rev-list', '--count', `${said.head}..${trunk}`)
    const k = count.code === 0 ? Number(count.out.trim()) : null
    if (k !== null) said.behind = k
    const by = k !== null ? `${k} commit(s)` : `an unknown number of commits (git said: ${(count.err || count.out).trim() || `exit ${count.code}`})`
    return [`#${pr.number} is ${by} behind origin/main — integrate, then review again only if the rebase changes the unit's patch: one that leaves it unchanged opens ship once CI is green; a round that passes does not count toward the limit`]
  }
  const screens = screensNeeds(unit, probe, last, said)
  if (screens.length) return screens
  // `0049` R6, last: when this closes the gate it is the only reason, so `nextStep` may offer
  // `ship`, which the app starts by putting `pr.md` up (R7). `said.title` says so.
  const mine = unit.artifacts['pr.md'].title.trim()
  if (view.title?.trim() === mine) return []
  said.title = 'differs'
  const theirs = view.title === null ? 'no title gh could read' : `the title "${view.title}"`
  return [`#${pr.number} carries ${theirs}, not pr.md's "${mine}" — start ship from the board, which puts pr.md onto it first`]
}

// `0116`: a pull request already merged — by a `ship` whose `--delete-branch` then exited 1,
// or by hand — leaves `ship` only its record to write. The gate asks that the merge commit is
// here and on `origin/main`, and nothing else: the branch may be gone, and CI, a rebase and
// `behind` speak of a merge still to come. It sets `said.merged` and no `said.head`, so there
// is nothing to pin. A head that moved after the pass does not close it (spec C1): closing
// cannot undo the merge, and the `ship.md` the PR machine writes records the head it merged.
function mergedNeeds(probe, pr, view, said) {
  const { commit, at } = view.merged
  const again = '— fetch, then ask again'
  if (typeof commit !== 'string' || !/^[0-9a-f]{40}$/.test(commit) || typeof at !== 'string' || !at) {
    return [`cannot read the merge commit of #${pr.number}: gh gave mergeCommit ${commit ?? 'none'} and mergedAt ${at || 'none'} ${again}`]
  }
  if (probe.git('cat-file', '-e', `${commit}^{commit}`).code !== 0) {
    return [`the merge commit ${commit} of #${pr.number} is not in this repository ${again}`]
  }
  if (probe.git('merge-base', '--is-ancestor', commit, 'refs/remotes/origin/main').code !== 0) {
    return [`the merge commit ${commit} of #${pr.number} is not on origin/main here ${again}`]
  }
  said.merged = { number: pr.number, commit, at, head: view.head }
  return []
}

// What the `ship` gate's open line and `next`'s action both say of a merged pull request
// (`0116`), so the two cannot word it differently.
function mergedLine(merged) {
  return `#${merged.number} was merged as ${merged.commit} at ${merged.at}: record it in ship.md; do not merge`
}

// What reached `ref` after `reviewed`, outside the unit's own `.cos/` files:
// `{ rewritten: true }` when `reviewed` is not on it at all, `{ files }` otherwise, or
// `{ error }`. This says only that `ref` was rewritten; whether the rewrite changed the
// unit's patch is `rebaseClean`'s to say, and each caller asks it (`0067`, `0121`).
function changedSince(probe, unit, reviewed, ref) {
  if (probe.git('merge-base', '--is-ancestor', reviewed, ref).code !== 0) return { rewritten: true, files: [] }
  const diff = probe.git('diff', '--name-only', `${reviewed}..${ref}`)
  if (diff.code !== 0) return { error: `git could not diff ${reviewed}..${ref}: ${diff.err.trim()}` }
  const own = `.cos/${unit.name}/`
  return { rewritten: false, files: diff.out.split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith(own)) }
}

// `0067` R1: a patch with what a rebase alone moves taken out — each `index <blob>..<blob>`
// line, and the numbers of each `@@ -a,b +c,d @@` — and every other byte kept, the text
// after the second `@@` and the three lines of context included. Both patterns are anchored
// at column 0 on purpose: a content line opens with ` `, `+`, `-` or `\`, and a line of
// base85 in a binary patch opens with a length letter `[A-Za-z]` and holds no space, so no
// line of data can match either.
export function normalizePatch(text) {
  return text
    .split('\n')
    .filter((l) => !/^index [0-9a-f]+\.\.[0-9a-f]+(?: [0-7]{6})?$/.test(l))
    .map((l) => l.replace(/^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@/, '@@ @@'))
    .join('\n')
}

// The unit's patch at `commit`, normalized: from its merge-base with the trunk, outside
// `.cos/<unit>/`, in a format no config of the checkout can change. `{ patch }` or
// `{ error }` — a git that fails is never an empty patch, since two empty patches match.
function unitPatch(probe, unit, commit) {
  const trunk = ['refs/remotes/origin/main', 'refs/heads/main'].find((ref) => probe.git('rev-parse', '--verify', '--quiet', ref).code === 0)
  if (!trunk) return { error: 'there is no origin/main and no main here to take the merge-base from' }
  const base = probe.git('merge-base', commit, trunk)
  const sha = base.out.trim()
  if (base.code !== 0 || !/^[0-9a-f]{40}$/.test(sha)) {
    return { error: base.code !== 0 ? `git merge-base ${commit} ${trunk}: ${(base.err || base.out).trim() || `exit ${base.code}`}` : 'git merge-base printed no commit' }
  }
  const diff = probe.git(
    'diff', '--no-color', '--no-ext-diff', '--no-textconv', '--no-renames', '--unified=3', '--binary',
    '--src-prefix=a/', '--dst-prefix=b/', sha, commit, '--', ':/', `:(top,exclude).cos/${unit.name}/`,
  )
  if (diff.code !== 0) return { error: `git could not diff ${sha}..${commit}: ${(diff.err || diff.out).trim() || `exit ${diff.code}`}` }
  return { patch: normalizePatch(diff.out) }
}

// A patch cut into one block per file, keyed by its `b/` path. With renames off a header
// names one path twice, `a/P b/P`, quoted or not, so the second half is the path.
function patchFiles(patch) {
  const files = new Map()
  let path = null
  // The patch's last newline ends its last line, so a file is the same block last or not.
  for (const line of patch.replace(/\n$/, '').split('\n')) {
    if (line.startsWith('diff --git ')) {
      const rest = line.slice('diff --git '.length)
      path = rest.slice((rest.length + 1) / 2).replace(/^"?b\//, '').replace(/"$/, '')
      files.set(path, '')
    }
    if (path !== null) files.set(path, `${files.get(path)}${line}\n`)
  }
  return files
}

// `0067` R1, R4: is `ref` a clean rebase of the reviewed commit? `reviewed` is that
// commit's `unitPatch`, computed once by the caller for every ref. `{ clean: true }`,
// `{ differs: [files] }` or `{ error }`; what cannot be compared is never clean.
function rebaseClean(probe, unit, reviewed, ref) {
  if (reviewed.error) return { error: reviewed.error }
  const now = unitPatch(probe, unit, ref)
  if (now.error) return { error: now.error }
  if (now.patch === reviewed.patch) return { clean: true }
  const a = patchFiles(reviewed.patch)
  const b = patchFiles(now.patch)
  const differs = [...new Set([...a.keys(), ...b.keys()])].filter((p) => a.get(p) !== b.get(p)).sort()
  return { differs: differs.length ? differs : ['(the text before the first file)'] }
}

// Why a `rebaseClean` answer is not clean, in the words the `ship` gate and `next` both
// print (`0121` R8).
function rebaseWhy(compared) {
  return compared.differs
    ? `its patch differs from the reviewed one in ${compared.differs.join(', ')}`
    : `its patch could not be compared with the reviewed one: ${compared.error}`
}

// Does `stage` have everything it needs? Returns the reasons it does not.
//
// `probe` is how `review` and `ship` reach git and gh; `null` means no repository was
// given, and those two gates stay closed rather than guess. `limit` is the review round
// limit in force. Every other stage reads files only and ignores both.
export function checkGate(unit, stage, opts = {}) {
  const { reasons, ...answer } = gateAnswer(unit, stage, opts)
  return answer
}

// `checkGate`, plus `reasons`, the codes `gate --json` hands out (`0136` R11).
export function gateAnswer(unit, stage, { probe = null, limit = REVIEW_ROUNDS } = {}) {
  const { ok, need, said } = evaluate(unit, stage, { probe, limit })
  // `0125` R3: an open `review` after a pass `ship` stays closed on names why, for the one
  // retry to fix. Asked only when the last round passed, so every other gate asks git and gh
  // what it did (spec C2), and only adds: it never closes `review`.
  if (ok && probe && stageOf(stage)?.name === 'review' && lastRound(unit)?.verdict === 'pass') {
    const ship = evaluate(unit, 'ship', { probe, limit })
    const stuck = passLeftClosed(unit, probe, ship)
    if (stuck && !stuck.stop) return { ok, need, retry: { n: stuck.last.n, reviewed: stuck.last.reviewed, need: ship.need } }
  }
  // `0116`: `merged` only when `ship` opened on a pull request already merged.
  if (ok && said.merged) return { ok, need, merged: said.merged, reasons: [code('recording-ship')] }
  const reasons = ok ? [] : gateReasons(unit, stage, need, said)
  // `0067` R6: `rebased` only when the gate opened on a clean rebase, so every other answer
  // is what it was.
  if (!ok || !said.head) return { ok, need, reasons }
  return said.rebased ? { ok, need, head: said.head, rebased: said.rebased, reasons } : { ok, need, head: said.head, reasons }
}

// `0136` R11: the codes of a closed gate, never none. Read off what `evaluate` already
// decided — the hold, the stages before, the spike, CI, the links — in that order, so the
// first is the one a person would fix first. `gate-closed` is what closes it with no code
// of its own (a title, a screenshot, a moved head); its words say which.
function gateReasons(unit, stage, need, said) {
  const target = stageOf(stage)
  if (!target) return [code('unreadable')]
  if (unit.hold) return [unit.hold.state === 'dropped' ? code('dropped') : code('paused')]
  // First: no stage before it being done opens a stage the unit's lane does not walk.
  const codes = inLane(unit, target) ? [] : [code('not-in-lane')]
  for (const s of STAGES) {
    if (s.name === target.name) break
    if (s.optional || !required(unit, s)) continue
    const status = statusOf(unit, s.file)
    if (status === null) codes.push(code('missing'))
    else if (status === 'rejected') codes.push(code('rejected'), code('closed'))
    else if (agentSkip(unit, s.file)) codes.push(code('agent-cannot-skip'))
    else if (!settled(status)) codes.push(code('draft'))
    else if (unit.artifacts[s.file].stale) codes.push(code('stale'))
  }
  const at = (name) => STAGES.findIndex((s) => s.name === name)
  if (at(target.name) > at(SPIKE.name) && spikeNeeds(unit).length) {
    codes.push(required(unit, SPIKE) && spikeFindings(unit).fails.length ? code('spike-fails') : code('spike-missing'))
  }
  if (said.ci === 'pending') codes.push(code('ci-pending'))
  if (said.ci === 'red') codes.push(code('ci-red'))
  if (said.ci === 'unfixable') codes.push(code('ci-unfixable'), code('needs-person'))
  if (target.name === 'impl') {
    const { needs, waiting } = linksOf(unit)
    if (waiting.length) codes.push(code('waiting-on'))
    if (needs.length) codes.push(code('unreadable'))
  }
  if (!codes.length && need.length) codes.push(code('gate-closed'))
  return [...new Set(codes)]
}

// `checkGate`, plus `said`: what `review` and `ship` learned on the way — the CI verdict,
// the head, whether the branch moved after a pass. Kept out of `checkGate`'s answer so the
// gate's output is what it was; `nextStep` is the one reader.
function evaluate(unit, stage, { probe = null, limit = REVIEW_ROUNDS } = {}) {
  const target = stageOf(stage)
  if (!target) {
    return { ok: false, need: [`unknown stage "${stage}" — use one of ${STAGE_NAMES.join(', ')}`], said: {} }
  }

  // `0045` R4: a held unit runs nothing, and git and gh are not asked why.
  const hold = unit.hold ?? null
  if (hold) return { ok: false, need: [`the unit is ${hold.state}: ${hold.reason} (${hold.by}, ${hold.date})`], said: {} }

  // Every stage ahead of the requested one has to be behind us. The loop replaces the three
  // hand-written cases it grew out of; `spike`, the ninth, added only its own `when` below.
  const need = []
  if (!inLane(unit, target)) {
    need.push(`${target.name} is not a stage of the ${unit.lane} lane: intent.md carries the fix's reproduction, expected and actual result, so impl follows intent`)
  } else if (target.when && !required(unit, target)) need.push(`${target.name} is not required: spec.md has no [unmeasured] item`)
  for (const s of STAGES) {
    if (s.name === target.name) break
    if (s.optional || !required(unit, s)) continue
    const status = statusOf(unit, s.file)
    // Only a file that may legitimately be skipped gets told it has that option.
    const canSkip = s.statuses.includes('skipped')
    if (status === null) {
      need.push(canSkip ? `${missing(unit, s.file)} — write it, or a person records the skip (coscc skip)` : missing(unit, s.file))
    } else if (agentSkip(unit, s.file)) {
      need.push(skippedBy(unit, s.file))
    } else if (!settled(status)) {
      need.push(`${s.file} is "${status}", not accepted${canSkip ? ' or skipped' : ''}`)
    } else if (unit.artifacts[s.file].stale) {
      // `0054` R5. Before `reviewNeeds` and `shipNeeds`, so a gate closed by it asks no `gh`.
      const { stage: by, date } = unit.artifacts[s.file].stale
      need.push(`${s.file} is stale: ${by} was rerun on ${date} — run ${s.name} again first`)
    }
  }

  // `0039` R7 and R9, for a target after `spike`. Both add nothing for a unit that never
  // wrote `[unmeasured]`, so every gate it had reads as it did.
  const at = (name) => STAGES.findIndex((s) => s.name === name)
  if (at(target.name) > at(SPIKE.name)) {
    need.push(...spikeNeeds(unit))
    if (at(target.name) >= at('impl') && required(unit, SPIKE) && !unit.artifacts['plan.md']?.citesSpike) {
      need.push('plan.md does not cite spike.md — every step that rests on a U<n> cites spike.md ## U<n>')
    }
  }

  // Asked only once the earlier stages are behind us: a gate closed for a missing plan has
  // no business spending a network call on CI.
  const said = {}
  if (!need.length && target.name === 'review') need.push(...reviewNeeds(unit, probe, limit, said))
  if (!need.length && target.name === 'ship') need.push(...shipNeeds(unit, probe, said))
  // `0040` R7, R9: files alone, whatever else is missing, and only for `impl`.
  if (target.name === 'impl') need.push(...linkNeeds(unit))

  return { ok: need.length === 0, need, said }
}
// `stepOf` wraps `evaluate` under its own name, to note what each gate it asks read.
const evaluateGate = evaluate

// --- the next stage to run ----------------------------------------------------

// `0125` R3–R5: the one place two questions are answered — does the last pass leave `ship`
// closed for the one reason another round cures, and was the round before it a pass on the
// same head? `g` is the `ship` gate's `evaluate`. `null` when the first answer is no: only
// `said.screens` sends an unchanged head back to `review`, and `said.moved` a head the pull
// request took past the reviewed commit. A pull request whose head is an ancestor of it is
// behind by a push no round makes, so that `moved` is answered like `screens` (review F1).
// Else `{ last, need, stop }`: `stop` is `null` while the one retry is still to come, or the
// sentence the unit stops on. Same head is `changedSince`, the comparison `changes-requested`
// makes, so a commit only under `.cos/<unit>/` does not make a new one. No limit is read:
// one retry is a choice, not a setting (R7).
function passLeftClosed(unit, probe, g) {
  const rounds = reviewOf(unit)
  const last = rounds.at(-1)
  if (last?.verdict !== 'pass') return null
  const unpushed = () =>
    g.said.head && g.said.head !== last.reviewed && probe.git('merge-base', '--is-ancestor', g.said.head, last.reviewed).code === 0
  if (g.said.moved ? !unpushed() : !g.said.screens) return null
  const prev = rounds.at(-2)
  if (prev?.verdict !== 'pass' || !prev.reviewed) return { last, need: g.need, stop: null }
  const short = (r) => r.reviewed.slice(0, 7)
  // Where git cannot compare the two rounds, the sentence does not say they share a head.
  const stop = (why = '', known = true) =>
    `needs a person — ${why}review rounds ${prev.n} and ${last.n} ${known || prev.reviewed === last.reviewed ? `both passed on ${short(last)}` : `passed on ${short(prev)} and ${short(last)}, not known to be one head,`} and ship is still closed: ${g.need.join('; ')}`
  // Another round cannot bring back a commit that is not here (spec C3).
  if (probe.git('cat-file', '-e', `${prev.reviewed}^{commit}`).code !== 0) {
    return { last, need: g.need, stop: stop(`the reviewed commit ${prev.reviewed} of review round ${prev.n} is not in this repository; `, false) }
  }
  const since = changedSince(probe, unit, prev.reviewed, last.reviewed)
  if (since.error) return { last, need: g.need, stop: stop(`${since.error}; `, false) }
  if (!since.rewritten && !since.files.length) return { last, need: g.need, stop: stop() }
  return { last, need: g.need, stop: null }
}

// The one stage a run button may offer for `unit`, or `''` for none, with the sentence that
// explains it. `0024`: the app's button used to pick "the first required stage with no
// artifact" for itself — a second copy of the loop — and so after a review asked for
// changes it offered `ship`, whose gate is closed, and nothing else. This is where that
// decision lives now, beside `nextAction`, which it starts from.
//
// Files settle it everywhere but three places, and those three need git and the pull
// request, which is why this takes the same `probe` the gates take:
//   - a review asked for changes: `impl` until something outside `.cos/<unit>/` reached the
//     pull request's head after the reviewed commit, and while the head is only a clean
//     rebase of it (`0121`), then `review` once CI is green, `impl` again while it is red,
//     and nothing while it runs;
//   - `pr` is done and no review exists: `review` on green, `impl` on red, else nothing;
//   - the review passed: `ship` if its gate is open, `impl` if a clean rebase is red,
//     `review` again only if the patch moved after the pass (`0067`), or once when the pass
//     left ship closed on its own head (`0125`), else nothing.
// Wherever CI is read, a red check no impl can fix (`said.ci` `unfixable`) answers `''` with
// the gate's `needs a person` line alone, before any reason of the branch's own (`0103` R3).
// With no `probe` those three answer `''` and say `--repo` is missing, as the gates do.
//
// This names a stage; it opens nothing. A caller still asks `checkGate` before running it.
//
// `0040` R7: every road to `impl` here — a red check sends the work back to it too — waits on
// the unit's dependencies, and only a wait carries `why`.
//
export function nextStep(unit, opts = {}) {
  const { reasons, ...answer } = nextAnswer(unit, opts)
  return answer
}

// `nextStep`, plus `reasons`, the codes of what settled it (`0136` R11), which `next` prints.
export function nextAnswer(unit, opts = {}) {
  const seen = {}
  const answer = waitOnDependencies(unit, stepOf(unit, opts, seen))
  return { ...answer, reasons: nextReasons(answer, seen) }
}

// The codes of one `nextStep` answer: a wait on the links, else `decide`'s `why`, what the
// last gate it asked read of CI, and the two answers `stepOf` makes of its own — a pass
// left closed twice on one head, and a merge already made.
function nextReasons(answer, seen) {
  if (answer.why === 'dependency') return [code('dependency'), code('waiting-on')]
  if (answer.why) return [answer.why]
  const codes = seen.why ? [seen.why] : []
  if (seen.why === 'rejected') codes.push(code('closed'))
  const ci = seen.said?.ci
  if (ci === 'pending') codes.push(code('ci-pending'))
  if (ci === 'red') codes.push(code('ci-red'))
  if (ci === 'unfixable') codes.push(code('ci-unfixable'), code('needs-person'))
  if (seen.stop) codes.push(code('needs-person'))
  if (seen.recorded) codes.push(code('recording-ship'))
  return [...new Set(codes)]
}

function stepOf(unit, { probe = null, limit = REVIEW_ROUNDS } = {}, seen = {}) {
  const base = decide(unit, limit)
  const { why, ...next } = base
  seen.why = why
  // Every gate asked below goes through here, so `seen.said` is the last one's.
  const evaluate = (...args) => {
    const g = evaluateGate(...args)
    seen.said = g.said
    return g
  }
  const none = (action) => ({ blocked: true, action, stage: '' })
  const onReview = (prefix) => {
    const g = evaluate(unit, 'review', { probe, limit })
    if (g.said.ci === 'unfixable') return none(g.need.join('; '))
    const reasons = [...prefix, ...g.need].join('; ')
    // `blocked` keeps `nextAction`'s meaning — the unit is not finished — not the gate's.
    if (g.ok) return { blocked: true, action: [...prefix, 'CI is green: write-review'].join('; '), stage: 'review' }
    if (g.said.ci === 'red') return { blocked: true, action: reasons, stage: 'impl' }
    return none(reasons)
  }
  // `0125` R4: a pass left closed goes to one more round, and a second on the same head stops.
  const again = (g) => {
    const stuck = passLeftClosed(unit, probe, g)
    if (!stuck?.stop) return onReview(g.need)
    seen.stop = true
    return none(stuck.stop)
  }

  // `0045` R3: a held unit is answered from the files, before anything reaches for `probe`.
  if (why === 'paused' || why === 'dropped') return next
  if (why === 'dependency') return base

  // `0054` R5: a stale `review` or `ship` is due the way a missing one is, CI permitting.
  const due = why === 'missing' || why === 'stale'
  if (due && next.stage === 'review') return onReview(why === 'stale' ? [next.action] : [])
  // `0116` R4: a pull request already merged leaves `ship` its record to write, and no merge.
  const recorded = (g) => {
    seen.recorded = true
    return { blocked: true, action: `ship — ${mergedLine(g.said.merged)}`, stage: 'ship' }
  }

  if (due && next.stage === 'ship') {
    const g = evaluate(unit, 'ship', { probe, limit })
    if (g.ok && g.said.merged) return recorded(g)
    if (g.ok) return { ...next, action: `${next.action} — merge with --match-head-commit ${g.said.head}` }
    if (g.said.ci === 'unfixable') return none(g.need.join('; '))
    // `0083` R10: a UI unit whose pass lacks current screenshots is cured the same way.
    if (g.said.moved || g.said.screens) return again(g)
    // `0067` R5: a clean rebase CI failed on goes back to impl, never to another round.
    if (g.said.rebased && g.said.ci === 'red') return { blocked: true, action: g.need.join('; '), stage: 'impl' }
    // `0049`: a title that differs is all that closes it, and a `ship` started on the board
    // puts `pr.md` up before it asks the gate. Offering nothing would leave no one to start it.
    if (g.said.title === 'differs') return { blocked: true, action: `${next.action} — ${g.need.join('; ')}`, stage: 'ship' }
    return none(g.need.join('; '))
  }

  // `0112` R6: a `ship.md` left `draft` by a merge that did not happen. Reached only once
  // `review.md` is accepted, so the last round passed. A draft that names no `Round` was
  // written before `0112`, reads as `draft`, and stops as it always did (c). One written
  // against an older round than the last is a missing `ship.md` again (a). One written
  // against the last round (b) asks the gate: moved goes to review (R2), closed says why
  // (R3), and open means the merge was refused for something the gate cannot see, so the
  // unit stops and says what. `>` joins `=`: only a round removed makes it larger, and the
  // gate refuses that.
  if (why === 'ship-refused') {
    const ship = unit.artifacts['ship.md'].ship
    const last = lastRound(unit)
    const g = evaluate(unit, 'ship', { probe, limit })
    // `0116`: `--delete-branch` in a worktree merges, then exits 1, and leaves this draft.
    if (g.ok && g.said.merged) return recorded(g)
    if (g.said.ci === 'unfixable') return none(g.need.join('; '))
    if (g.said.moved || g.said.screens) return again(g)
    if (g.said.rebased && g.said.ci === 'red') return { blocked: true, action: g.need.join('; '), stage: 'impl' }
    if (g.said.title === 'differs') return { blocked: true, action: `ship — ${g.need.join('; ')}`, stage: 'ship' }
    if (!g.ok) return none(g.need.join('; '))
    // `0067` R5: a merge refused as not up to date, then rebased clean, adds no round to go
    // past it — the clean rebase is what cures that refusal. Any other refusal still stops.
    const cured = g.said.rebased && /not up to date/i.test(ship.refused ?? '')
    if (ship.round < last.n || cured) return { blocked: true, action: `ship — merge with --match-head-commit ${g.said.head}`, stage: 'ship' }
    return none(`ship was refused: ${ship.refused ?? 'ship.md names no refusal'} — finish and accept ship.md`)
  }

  // `0028` (a): a person is awaited. Files alone settle it, so no probe is asked.
  if (why === 'awaits-person') return next
  // `0028` (b): every finding awaiting a person has an answer; a review reads them.
  if (why === 'person-answered') return onReview([next.action])
  // `0085` R8: a review ran out of turns; the next one goes on from its round, CI permitting.
  // `0027` R3: an unfinished round goes the same way, and keeps the ids it left out.
  if (why === 'review-incomplete') return { ...onReview([next.action]), ...(next.dropped ? { dropped: next.dropped } : {}) }

  if (why === 'changes-requested') {
    // `0028` (c): every open finding is one impl claims needs a person. Only a review may
    // confirm or reject that, so it goes to review — and before the "no fix reached the
    // pull request" test below, which would otherwise send it straight back to impl.
    if (everyOpenClaimed(unit)) {
      return onReview([`every open finding of review round ${lastRound(unit).n} is claimed in impl.md ## Needs a person — review confirms or rejects each`])
    }
    if (!probe) return none(`${next.action} — no repository given to tell which: pass --repo`)
    const pr = unit.artifacts['pr.md']?.pr ?? null
    if (!pr) return none(`${next.action} — pr.md names no pull request to read the branch from`)
    const last = lastRound(unit)
    if (!last?.reviewed) return none(`${next.action} — review round ${last?.n ?? '?'} names no reviewed commit, so a fix cannot be told from none`)
    const read = prHead(probe, pr)
    if (read.error) return none(`${next.action} — ${read.error}`)
    if (probe.git('cat-file', '-e', `${last.reviewed}^{commit}`).code !== 0) {
      return none(`${next.action} — the reviewed commit ${last.reviewed} is not in this repository: fetch, then ask again`)
    }
    const since = changedSince(probe, unit, last.reviewed, read.head)
    if (since.error) return none(`${next.action} — ${since.error}`)
    // `0121`: a rewrite that left the unit's patch as it was answers no finding, so it is no
    // fix, and CI is not asked — the answer is impl whatever it says. One that changed the
    // patch, or cannot be compared, goes to review as any fix does (R2, R3).
    if (since.rewritten) {
      const compared = rebaseClean(probe, unit, unitPatch(probe, unit, last.reviewed), read.head)
      if (compared.clean) {
        return {
          blocked: true,
          action: `${next.action} — ${last.reviewed.slice(0, 7)} was rebased to ${read.head.slice(0, 7)} and the unit's patch is unchanged: the open findings of review round ${last.n} are still to be fixed on the branch, then push`,
          stage: 'impl',
        }
      }
      return onReview([`#${pr.number} was rewritten past ${last.reviewed.slice(0, 7)} — ${rebaseWhy(compared)}`])
    }
    if (!since.files.length) {
      return {
        blocked: true,
        action: `${next.action} — nothing outside .cos/${unit.name}/ has reached #${pr.number} since ${last.reviewed.slice(0, 7)}: impl, then push`,
        stage: 'impl',
      }
    }
    return onReview([`#${pr.number} moved past ${last.reviewed.slice(0, 7)}`])
  }

  return next
}

// --- running an accepted stage again -------------------------------------------

// `0054` R2: the stages after `name` in `STAGES` order, those without an artifact included,
// `idea` never, and `spike` only while it is required.
export function rerunLater(unit, name) {
  const at = STAGES.findIndex((s) => s.name === name)
  return STAGES.slice(at + 1).filter((s) => !s.optional && required(unit, s)).map((s) => s.name)
}

// R1 (b) and (c): why nothing may be run again on `unit` at all, or `null`.
function rerunClosed(unit) {
  if (statusOf(unit, 'plan.md') === 'done') return 'the unit is finished: plan.md is done'
  const rejected = STAGES.find((s) => statusOf(unit, s.file) === 'rejected')
  if (rejected) return `the unit is closed: ${rejected.file} is rejected`
  if (unit.hold) return `the unit is ${unit.hold.state}`
  return null
}

// `0054` R1: why `name` may not be run again on `unit`, or `null` when it may. Files only:
// no rerunnable stage's gate needs `--repo`.
export function rerunRefusal(unit, name, limit = REVIEW_ROUNDS) {
  if (!RERUNNABLE.includes(name)) return `${name} cannot be run again from the board — only ${RERUNNABLE.join(', ')}`
  const closed = rerunClosed(unit)
  if (closed) return closed
  const s = stageOf(name)
  const status = statusOf(unit, s.file)
  if (s.when && !unmeasuredOf(unit).ids.length) return `${name} is not required: spec.md has no [unmeasured] item`
  if (!present(unit, s.file)) return `${s.file} does not exist — ${name} has not run yet`
  if (!(status === 'accepted' || (status === 'skipped' && s.statuses.includes('skipped')))) {
    return `${s.file} is "${status}", not accepted`
  }
  if (unit.artifacts[s.file].stale) return `${s.file} is already stale — run ${name} from the next step`
  const g = evaluate(unit, name, { probe: null, limit })
  return g.ok ? null : g.need.join('; ')
}

// Every stage `rerunRefusal` lets through, each with what it makes run again.
export function rerunOffers(unit, limit = REVIEW_ROUNDS) {
  return RERUNNABLE.filter((name) => rerunRefusal(unit, name, limit) === null).map((stage) => ({ stage, later: rerunLater(unit, stage) }))
}

// R3: the `### Rerun` block the app appends to `intent.md ## Answers`, whole. `hashOf(file)`
// is `aboveAnswers` of that artifact as it is on disk now.
export function rerunBlock(unit, name, date, hashOf) {
  const files = [name, ...rerunLater(unit, name)].map((n) => stageOf(n).file).filter((f) => present(unit, f))
  return [
    '### Rerun',
    `Requested by: owner. Date: ${date}. Via: product.`,
    `Stage: ${name}.`,
    ...files.map((f) => `Stale: ${f} sha256:${hashOf(f)}`),
    '',
  ].join('\n')
}

export function nextNumber(units) {
  const max = units.reduce((n, u) => (u.number > n ? u.number : n), 0)
  return String(max + 1).padStart(4, '0')
}

// --- git conventions ---------------------------------------------------------

// Everything from here to the command section is pure: it takes strings and returns
// strings. Reading a file or asking git happens in the commands below, which is what lets
// the whole grammar be tested without a repository to test it against.

// The ten Conventional Commits types, reused rather than invented: the unit that added
// this asked for the common convention, not a local one. The set is closed because an open
// set checks nothing, and it will refuse a name somebody wants at least once.
export const BRANCH_TYPES = ['feat', 'fix', 'docs', 'refactor', 'test', 'chore', 'perf', 'build', 'ci', 'revert']

const SLUG_MAX = 60
const TYPE_LIST = () => BRANCH_TYPES.join(', ')

// The slug a branch can carry. `new-path` refuses a slug over `SLUG_MAX`, so only a unit
// made before it did (`0102`) reaches the cut: at the last hyphen that leaves `SLUG_MAX` or
// fewer, or mid-word when there is none. A unit slug has no leading or doubled hyphen, so
// either cut still matches `SLUG_RE`.
function branchSlug(slug) {
  if (slug.length <= SLUG_MAX) return slug
  const cut = slug.slice(0, SLUG_MAX + 1).lastIndexOf('-')
  return cut > 0 ? slug.slice(0, cut) : slug.slice(0, SLUG_MAX)
}

// `null` when the name is fine, otherwise the rule it broke. A boolean here would make
// every rejection say the same thing, and the point of a convention is to name what is
// wrong with the name you chose.
export function branchProblem(name) {
  if (!name) return 'no branch name'
  if (name === 'main') return 'main is the trunk, not a work branch'
  const slash = name.indexOf('/')
  if (slash === -1) return `no type prefix: expected <type>/<slug>, type one of ${TYPE_LIST()}`
  const type = name.slice(0, slash)
  const slug = name.slice(slash + 1)
  if (!BRANCH_TYPES.includes(type)) return `"${type}" is not one of ${TYPE_LIST()}`
  if (!slug) return 'the slug is empty'
  if (slug.includes('/')) return 'the slug carries a second slash'
  if (slug.length > SLUG_MAX) return `the slug is ${slug.length} characters, over the ${SLUG_MAX} allowed`
  if (!SLUG_RE.test(slug)) return 'the slug takes lowercase letters, digits and single hyphens'
  return null
}

// The line `check-branch` prints for a name `branchProblem` refused. `0103`: the `review` gate
// quotes it for a head CI refused, so the format lives here once.
export const notAWorkBranch = (name, problem) => `"${name}" is not a work branch: ${problem}`

// `0103` R2 (a): the check names of the jobs whose `run:` step calls `cos.mjs check-branch`,
// read from each workflow's text, `{ path, text }`. Not a YAML parser, and not meant to be
// one: a job it cannot read is not found, and a red check not found goes back to impl as it
// did before (spec C4). The name is the job's own `name:`, else its key; a `name:` built from
// `${{ }}` is not known here, so that job is not found either. A line that is only a comment
// counts for nothing.
export function branchChecks(files) {
  const found = []
  const calls = (text) => /\bcos\.mjs\s+check-branch\b/.test(text)
  const indentOf = (line) => line.length - line.trimStart().length
  const unquote = (v) => {
    const m = v.match(/^"([^"]*)"\s*(#.*)?$/) ?? v.match(/^'([^']*)'\s*(#.*)?$/)
    return m ? m[1] : v.replace(/\s+#.*$/, '').trim()
  }
  for (const { text } of files) {
    const lines = text.split(/\r?\n/)
    const start = lines.findIndex((l) => /^jobs:\s*(#.*)?$/.test(l))
    if (start === -1) continue
    let jobIndent = null
    let job = null
    let block = null
    const close = () => {
      if (job?.runs && typeof job.name === 'string' && job.name && !found.includes(job.name)) found.push(job.name)
    }
    for (const line of lines.slice(start + 1)) {
      const trimmed = line.trim()
      if (!trimmed || trimmed.startsWith('#')) continue
      const indent = indentOf(line)
      if (indent === 0) break
      // The lines of a `run: |` block, or of a `run:` whose value starts on the next line.
      if (block !== null && indent > block) {
        if (calls(trimmed)) job.runs = true
        continue
      }
      block = null
      jobIndent ??= indent
      if (indent < jobIndent) break
      if (indent === jobIndent) {
        close()
        const key = trimmed.match(/^("[^"]+"|'[^']+'|[^\s:#][^:]*?):\s*(#.*)?$/)
        job = key ? { name: unquote(key[1]), child: null, runs: false } : null
        continue
      }
      if (!job) continue
      job.child ??= indent
      const name = indent === job.child && trimmed.match(/^name:\s*(.*)$/)
      if (name) {
        job.name = name[1].includes('${{') || /^[|>]/.test(name[1]) ? null : unquote(name[1])
        continue
      }
      const run = trimmed.match(/^(-\s+)?run:\s*(.*)$/)
      if (!run) continue
      const value = run[2]
      if (!value || /^[|>]/.test(value)) block = indent + (run[1]?.length ?? 0)
      else if (calls(value)) job.runs = true
    }
    close()
  }
  return found
}

// `vX.Y.Z`, or `vX.Y.Z-rc.N` for a prerelease. Leading zeros are refused so that one
// release has one spelling: `v0.01.0` and `v0.1.0` would otherwise be two tags nobody
// could tell apart in a list.
const TAG_RE = /^v(\d+)\.(\d+)\.(\d+)(?:-rc\.(\d+))?$/

export function tagProblem(name) {
  if (!name) return 'no tag name'
  const m = name.match(TAG_RE)
  if (!m) return 'expected vX.Y.Z, or vX.Y.Z-rc.N for a prerelease'
  for (const part of [m[1], m[2], m[3]]) {
    if (part.length > 1 && part.startsWith('0')) return `"${part}" carries a leading zero`
  }
  if (m[4] !== undefined && (m[4].startsWith('0') || m[4] === '0')) {
    return 'the release candidate number starts at 1'
  }
  return null
}

export const isPrerelease = (name) => !tagProblem(name) && name.includes('-rc.')
export const tagVersion = (name) => (tagProblem(name) ? null : name.slice(1).split('-')[0])

// `pyproject.toml` is the source and the rest are copies. Naming a source matters more
// than the comparison: "they disagree" is not actionable until something says which one is
// right.
export const VERSION_SOURCE = 'pyproject.toml'

export function versionProblem(found) {
  const source = found[VERSION_SOURCE]
  if (!source) return `${VERSION_SOURCE} declares no version`
  const off = Object.entries(found)
    .filter(([place, value]) => place !== VERSION_SOURCE && value !== source)
    .map(([place, value]) => `${place} is ${value === null || value === undefined ? 'unreadable' : value}`)
  return off.length ? `${VERSION_SOURCE} says ${source}, but ${off.join('; ')}` : null
}

// The unit's own header, read the way `parseStatus` reads its neighbour on the same line.
export function parseType(text) {
  const m = text.match(/\bType:\s*([A-Za-z]+)/)
  return m ? m[1].toLowerCase() : null
}

// A unit's branch is derived, never typed. `0009_branch-and-release-conventions` carrying
// `Type: feat` can only be `feat/branch-and-release-conventions`, so the branch name and
// the unit name cannot drift apart. The one exception is an old unit whose slug is over
// `SLUG_MAX`: its branch carries the slug cut by `branchSlug`, still derived, so that
// `check-branch` accepts every name this prints.
export const unitBranch = (unitName, intentText) => branchFor(unitName, parseType(intentText ?? ''))

// `0135`: the same, from a `Type:` already read — the app's, given `--state`.
export function branchFor(unitName, type) {
  const match = String(unitName ?? '').match(UNIT_RE)
  if (!match) return { error: `"${unitName}" does not match NNNN_<slug>` }
  if (!type) return { error: `${unitName}/intent.md declares no Type: — one of ${TYPE_LIST()}` }
  if (!BRANCH_TYPES.includes(type)) return { error: `"${type}" is not one of ${TYPE_LIST()}` }
  return { branch: `${type}/${branchSlug(match[2])}` }
}

// --- commands ----------------------------------------------------------------

const dash = '—'
// Nine stages will not fit across a terminal spelled out, so the table carries one letter
// each and prints the key underneath. The full words stay in `status --json`, which is what
// anything other than a human reads.
const CODE = { draft: 'd', accepted: 'A', rejected: 'x', skipped: 's', done: 'D', 'changes-requested': 'c' }
const cell = (u, f) => {
  const status = u.artifacts[f]?.status
  if (status === undefined) return dash
  return CODE[status] ?? '?'
}

// `0035`: whether a unit sits between `pr` and `ship` — an accepted `pr.md` naming a pull
// request, on a unit that is neither finished nor closed. The app's integration step runs
// only there, and asks this rather than reading `pr.md` itself: this file is the one place
// the loop is defined. It reads files alone; whether the pull request is still open is the
// app's question to ask `gh`, not this one's.
export function betweenPrAndShip(unit, limit = REVIEW_ROUNDS) {
  if (statusOf(unit, 'pr.md') !== 'accepted') return false
  if (!unit.artifacts['pr.md']?.pr) return false
  const { why } = decide(unit, limit)
  // `0045` R15: nothing integrates a held unit, so the board asks `gh` nothing about it.
  return !['finished', 'rejected', 'paused', 'dropped'].includes(why)
}

// `0081` R4: the unit used its review rounds with findings still open, and is neither
// finished, closed nor held — the one case the board offers a round more. The app's route
// reads this rather than compare rounds itself.
export function moreRounds(unit, limit = REVIEW_ROUNDS) {
  if (['finished', 'rejected', 'paused', 'dropped'].includes(decide(unit, limit).why)) return false
  return outOfRounds(unit, limit)
}

// `0100` R2: the stage a unit is at, for a board that draws one column per stage. It is
// the stage `next` names; failing that, the last stage whose artifact is on disk (a draft,
// a `changes-requested` review, a closed or finished unit); failing that, the first stage
// every unit walks. Read off `STAGES`, so the board keeps no copy of the loop.
export function stageAt(unit, next) {
  if (next.stage) return next.stage
  const last = STAGES.filter((s) => present(unit, s.file)).at(-1)
  return (last ?? STAGES.find((s) => !s.optional && !s.when)).name
}

function cmdStatus(json, cosDir, limit, state) {
  const units = readAll(cosDir, { state })
  // `0040` R3: only a store that holds `ideas/` carries the key, so every other one's output
  // is what it was, byte for byte.
  const ideas = !existsSync(join(cosDir, IDEAS)) ? null : state.ideas?.[state.workspace] ?? []
  // `0100` R4: `status` carries `why` as well, so the board reads which rule answered
  // rather than the English of `action`. `next` still prints `nextAction`, without it.
  // `rerun` (`0106`) is for `next` and the autopilot only, so `status` drops it.
  const rows = units.map((u) => {
    const { rerun, ...next } = decide(u, limit)
    // `0081` R4: only when true, so every other unit's row is what it was, byte for byte.
    return { ...u, next, at: stageAt(u, next), betweenPrAndShip: betweenPrAndShip(u, limit), ...(moreRounds(u, limit) ? { moreRounds: true } : {}) }
  })

  if (json) {
    // The stage list ships with the data so a reader never has to keep its own copy of it,
    // and so do the stages an answered draft runs again (`0115` R4). Without `lanes`: each
    // unit says its own lane, and the table stays the one the app compares with its own.
    const stages = STAGES.map(({ lanes, ...s }) => s)
    console.log(JSON.stringify({ root: cosDir, stages, afterAnswers: RERUN_STAGES, units: rows, ...(ideas ? { ideas } : {}) }, null, 2))
    return 0
  }

  const ideaProblems = (ideas ?? []).flatMap((i) => i.problems.map((p) => `${IDEAS}/${i.id}: ${p}`))
  if (!units.length) {
    console.log('No work units yet. `write-intent` opens one.')
    for (const p of ideaProblems) console.log(`  - ${p}`)
    return 0
  }

  console.log(`| Unit | ${STAGE_NAMES.join(' | ')} | Next action |`)
  console.log(`|---|${STAGE_NAMES.map(() => '---').join('|')}|---|`)
  for (const u of rows) {
    const cells = STAGES.map((s) => cell(u, s.file)).join(' | ')
    console.log(`| ${u.name} | ${cells} | ${u.next.action} |`)
  }
  console.log(`\nA accepted · d draft · c changes-requested · s skipped · D done · x rejected · ${dash} not started`)

  const problems = [...rows.flatMap((u) => u.problems.map((p) => `${u.name}: ${p}`)), ...ideaProblems]
  if (problems.length) {
    console.log('\nProblems (report these, do not infer past them):')
    for (const p of problems) console.log(`  - ${p}`)
  }
  return 0
}

// `repoDir` is where the unit's code lives, which `review` and `ship` ask git and gh about.
// `null` when `--root` was given without `--repo`: the store `--root` names has no git, and
// answering from this checkout instead would name one place and read another.
// The stage a run button may offer, as one line of JSON: `{unit, stage, action, blocked}`.
// Exit 0 whatever the stage is — "nothing to run" is an answer, not a failure. Exit 2 is
// misuse: no unit named, or no such unit.
function cmdNext(unitName, cosDir, repoDir, limit, state) {
  if (!unitName) {
    console.error('usage: cos.mjs next <NNNN_slug> [--repo <dir>] --state <file|->')
    return 2
  }
  const dir = join(cosDir, unitName)
  if (!existsSync(dir)) {
    console.error(`No such work unit: ${unitName}`)
    return 2
  }
  const probe = repoDir ? makeProbe(repoDir) : null
  const unit = readUnit(dir, unitName, { state })
  const { stage, action, blocked, waiting, dropped, rerun, why, reasons } = nextAnswer(unit, { probe, limit })
  // `waiting` only when a person is awaited (`0028`), `dropped` only when the last round left
  // out an earlier finding (`0027`), `hold` only when the unit is held (`0045`), `rerun` only
  // when a draft's questions are all answered (`0106`), `why` only when `impl` waits on a
  // dependency (`0040`). `reasons` always (`0136` R11): the codes the autopilot reads. The
  // lane last, always, so a measure of the lane joins it to the runs it paid for.
  const hold = unit.hold ? { hold: unit.hold } : {}
  const { lane, enteredFast, laneMissing } = unit
  console.log(JSON.stringify({ unit: unitName, stage, action, blocked, ...(waiting?.length ? { waiting } : {}), ...(dropped?.length ? { dropped } : {}), ...hold, ...(rerun ? { rerun } : {}), ...(why === 'dependency' ? { why } : {}), reasons, lane, enteredFast, laneMissing }))
  return 0
}

// What `gate` prints when it opens. `ship` is told the one commit it may merge; any other
// head is one nobody reviewed. `0067` R6: a gate opened on a clean rebase says so on a
// second line, in short shas — the full one is the pin's alone. `0125` R3: so does a
// `review` that is the one retry after a pass `ship` stayed closed on, with the gate's reasons.
// `0116`: a `ship` opened on a pull request already merged is told to record it, not to merge.
export function openLines(stage, unitName, { head = null, rebased = null, retry = null, merged = null } = {}) {
  const pin = merged ? ` — ${mergedLine(merged)}` : head ? ` — merge with --match-head-commit ${head}` : ''
  const lines = [`open: ${stage} may proceed for ${unitName}${pin}`]
  if (rebased) {
    lines.push(`the reviewed commit ${rebased.reviewed.slice(0, 7)} was rebased to ${rebased.head.slice(0, 7)} and the unit's patch is unchanged — no review round is needed`)
  }
  if (retry) {
    lines.push(`review round ${retry.n} passed on ${retry.reviewed.slice(0, 7)} and the ship gate is still closed: ${retry.need.join('; ')} — this round is the one retry: fix what that names in this round; a second passing round on the same head that leaves ship closed stops the unit for a person`)
  }
  return lines
}

// `--json` (`0136` R11): the same lines as one JSON object on stdout, `{ok, lines, reasons}`,
// and the same exit code. The app asks this way, so it reads the codes and not the words.
// `rebased` is added only when `ship` opened on a clean rebase (`0067` R6): the app's guard
// `ship-ready` takes it in place of a round of the new head.
function cmdGate(unitName, stage, cosDir, repoDir, limit, state, json = false) {
  if (!unitName || !stage) {
    console.error(`usage: cos.mjs gate <NNNN_slug> <${STAGE_NAMES.join('|')}> [--json] [--repo <dir>] --state <file|->`)
    return 2
  }
  const dir = join(cosDir, unitName)
  if (!existsSync(dir)) {
    console.error(`No such work unit: ${unitName}`)
    return 2
  }
  const probe = repoDir ? makeProbe(repoDir) : null
  const { ok, need, head, rebased, retry, merged, reasons } = gateAnswer(readUnit(dir, unitName, { state }), stage, { probe, limit })
  const lines = ok
    ? openLines(stage, unitName, { head, rebased, retry, merged })
    : [`blocked: ${stage} cannot proceed for ${unitName}`, ...need.map((n) => `  - ${n}`)]
  if (json) console.log(JSON.stringify(rebased ? { ok, lines, reasons, rebased } : { ok, lines, reasons }))
  else for (const line of lines) (ok ? console.log : console.error)(line)
  return ok ? 0 : 1
}

// --- reading the version out of four files, two formats, no parser ------------

// No TOML parser ships with node and this repository adds no dependency for one, so the
// two lockfiles and `pyproject.toml` are read by hand. The reading is narrow on purpose:
// a single regex over a whole file finds `version` lines belonging to somebody else's
// package, which is exactly how the first draft of `scripts/verify_0009.py` came to report
// a dependency's number as this project's.
const tomlString = (text, key) => text.match(new RegExp(`^${key}\\s*=\\s*"([^"]*)"`, 'm'))?.[1] ?? null

function tomlTable(text, header) {
  const lines = text.split('\n')
  const start = lines.findIndex((l) => l.trim() === header)
  if (start === -1) return null
  const rest = lines.slice(start + 1)
  const end = rest.findIndex((l) => /^\s*\[/.test(l))
  return (end === -1 ? rest : rest.slice(0, end)).join('\n')
}

// `uv.lock` holds one `[[package]]` table per locked dependency, each with its own
// `version`. Only the one naming this project counts.
function lockedVersion(text, name) {
  for (const block of text.split(/^\[\[package\]\]\s*$/m).slice(1)) {
    if (tomlString(block, 'name') === name) return tomlString(block, 'version')
  }
  return null
}

const jsonAt = (text, path) => {
  try {
    return path.reduce((v, k) => v?.[k], JSON.parse(text))
  } catch {
    return null
  }
}

const slurp = (rel) => (existsSync(join(ROOT, rel)) ? readFileSync(join(ROOT, rel), 'utf8') : '')

// Five numbers in four files. `package-lock.json` carries two, in separate keys that can
// disagree with each other.
export function declaredVersions(readFile = slurp) {
  const pyproject = readFile('pyproject.toml')
  const project = tomlTable(pyproject, '[project]') ?? ''
  const name = tomlString(project, 'name')
  const lock = readFile('package-lock.json')
  return {
    'pyproject.toml': tomlString(project, 'version'),
    'package.json': jsonAt(readFile('package.json'), ['version']),
    'uv.lock': name ? lockedVersion(readFile('uv.lock'), name) : null,
    'package-lock.json': jsonAt(lock, ['version']),
    "package-lock.json packages['']": jsonAt(lock, ['packages', '', 'version']),
  }
}

// --- commands that describe this checkout -------------------------------------

function git(...args) {
  try {
    return execFileSync('git', args, { cwd: ROOT, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim()
  } catch {
    return null
  }
}

function cmdCheckBranch(name) {
  const subject = name ?? git('rev-parse', '--abbrev-ref', 'HEAD')
  if (subject === null) {
    console.error('not a git checkout, and no branch name was given')
    return 2
  }
  const problem = branchProblem(subject)
  if (problem) {
    console.error(notAWorkBranch(subject, problem))
    return 1
  }
  console.log(subject)
  return 0
}

function cmdCheckTag(name) {
  if (!name) {
    console.error('usage: cos.mjs check-tag <vX.Y.Z | vX.Y.Z-rc.N>')
    return 2
  }
  const problem = tagProblem(name)
  if (problem) {
    console.error(`"${name}" is not a release tag: ${problem}`)
    return 1
  }
  // The release workflow reads this line instead of comparing the string itself, so the
  // grammar has one implementation rather than one here and one in YAML.
  console.log(isPrerelease(name) ? 'prerelease' : 'release')
  return 0
}

function cmdCheckVersion() {
  const found = declaredVersions()
  const problem = versionProblem(found)
  if (problem) {
    console.error(`the version is not in step: ${problem}`)
    for (const [place, value] of Object.entries(found)) console.error(`  ${place}: ${value ?? '(unreadable)'}`)
    return 1
  }
  const version = found[VERSION_SOURCE]
  // A tag on HEAD is a fifth declaration, and it only exists sometimes.
  const tag = (git('tag', '--points-at', 'HEAD') ?? '').split('\n').find((t) => t && !tagProblem(t))
  if (tag && tagVersion(tag) !== version) {
    console.error(`the version is not in step: ${VERSION_SOURCE} says ${version}, but the tag on HEAD is ${tag}`)
    return 1
  }
  console.log(tag ? `${version} (${tag} on HEAD)` : version)
  return 0
}

function cmdUnitBranch(unitName, cosDir, state) {
  if (!unitName) {
    console.error('usage: cos.mjs unit-branch <NNNN_slug>')
    return 2
  }
  const intent = join(cosDir, unitName, 'intent.md')
  if (!existsSync(intent)) {
    console.error(`No such work unit: ${unitName}`)
    return 2
  }
  const { branch, error } = branchFor(unitName, entryOf(state, state.workspace, unitName)?.type ?? null)
  if (error) {
    console.error(error)
    return 1
  }
  console.log(branch)
  return 0
}

// Reads two files, `pr.md` and the `intent.md` whose `Type:` `titleProblem` checks the title
// against (`0049` R4), and prints; no git, no gh, no write. `titleProblem` does not change
// the exit code. The name is checked before it is joined, so `../` cannot walk out of the
// `.cos/` it was given.
function cmdPrText(unitName, cosDir, state) {
  if (!unitName) {
    console.error('usage: cos.mjs pr-text <NNNN_slug>')
    return 2
  }
  if (!UNIT_RE.test(unitName)) {
    console.error(`Invalid unit name "${unitName}": expected NNNN_slug.`)
    return 2
  }
  const dir = join(cosDir, unitName)
  if (!existsSync(dir)) {
    console.error(`No such work unit: ${unitName}`)
    return 1
  }
  const file = join(dir, 'pr.md')
  if (!existsSync(file)) {
    console.error(`${unitName} has no pr.md`)
    return 1
  }
  const text = readFileSync(file, 'utf8')
  const intent = join(dir, 'intent.md')
  const known = entryOf(state, state.workspace, unitName) ?? NO_ENTRY
  const type = !existsSync(intent) ? null : known.type ?? null
  const read = prText(text)
  const problem = titleProblem(read.title, BRANCH_TYPES.includes(type) ? type : null, unitName.slice(0, 4))
  const status = statusIn(known, 'pr.md')
  console.log(JSON.stringify({ unit: unitName, ...read, status, titleProblem: problem }))
  return 0
}

// `0054`. Which accepted stages the board may offer to run again, or — given a stage — the
// `### Rerun` block to append before running it. Reads files and prints; it writes nothing,
// the app appends the block. No `<stage>`: `{unit, offers: [{stage, later}], why}`, exit 0,
// `why` saying why when `offers` is empty. With one: `{unit, stage, later, block}` and exit
// 0, or the reason and exit 1. Exit 2 is misuse, as `gate`'s.
function cmdRerun(unitName, stage, cosDir, limit, today = localDate(), state) {
  if (!unitName) {
    console.error(`usage: cos.mjs rerun <NNNN_slug> [${RERUNNABLE.join('|')}]`)
    return 2
  }
  if (!UNIT_RE.test(unitName)) {
    console.error(`Invalid unit name "${unitName}": expected NNNN_slug.`)
    return 2
  }
  const dir = join(cosDir, unitName)
  if (!existsSync(dir)) {
    console.error(`No such work unit: ${unitName}`)
    return 2
  }
  const unit = readUnit(dir, unitName, { state })
  if (stage === undefined) {
    const offers = rerunOffers(unit, limit)
    const why = offers.length ? '' : rerunClosed(unit) ?? 'no accepted stage can be run again now'
    console.log(JSON.stringify({ unit: unitName, offers, why }))
    return 0
  }
  if (!stageOf(stage)) {
    console.error(`unknown stage "${stage}" — use one of ${STAGE_NAMES.join(', ')}`)
    return 2
  }
  const refused = rerunRefusal(unit, stage, limit)
  if (refused) {
    console.error(`${stage} cannot be run again for ${unitName}: ${refused}`)
    return 1
  }
  const block = rerunBlock(unit, stage, today, (f) => aboveAnswers(readFileSync(join(dir, f), 'utf8')))
  console.log(JSON.stringify({ unit: unitName, stage, later: rerunLater(unit, stage), block }))
  return 0
}

// `0111`. Whether the app should take a UI unit's screenshots again before `review`, as one
// line of JSON: `screensAnswer`'s. Exit 0 whatever it says; exit 2 is misuse — no unit named,
// no such unit, or `--root` with no `--repo`, since the store has no `.screens/` and no git.
// Reads git and one file; writes nothing, and opens or closes no gate.
function cmdScreens(unitName, cosDir, repoDir, state) {
  if (!unitName) {
    console.error('usage: cos.mjs screens <NNNN_slug> [--repo <dir>]')
    return 2
  }
  if (!UNIT_RE.test(unitName)) {
    console.error(`Invalid unit name "${unitName}": expected NNNN_slug.`)
    return 2
  }
  const dir = join(cosDir, unitName)
  if (!existsSync(dir)) {
    console.error(`No such work unit: ${unitName}`)
    return 2
  }
  if (!repoDir) {
    console.error('screens needs the checkout its screenshots are in — pass --repo <dir>')
    return 2
  }
  console.log(JSON.stringify(screensAnswer(readUnit(dir, unitName, { state }), makeProbe(repoDir))))
  return 0
}

// `0135`. `unitMeta` as JSON: every directory under `.cos/` but `ideas/`, whatever its name,
// and the ideas; or one unit, and only the artifacts named. Writes nothing and decides
// nothing. Exit 2 is misuse: a unit that is not a directory there, or a file no stage writes.
function cmdMeta(unitName, files, cosDir) {
  if (unitName === undefined) {
    const units = {}
    if (existsSync(cosDir)) {
      for (const e of readdirSync(cosDir, { withFileTypes: true })) {
        if (e.isDirectory() && e.name !== IDEAS) units[e.name] = unitMeta(join(cosDir, e.name))
      }
    }
    const ideas = existsSync(join(cosDir, IDEAS)) ? readIdeas(cosDir) : null
    console.log(JSON.stringify({ units, ideas }))
    return 0
  }
  const dir = join(cosDir, unitName)
  if (/[\\/]/.test(unitName) || unitName === '.' || unitName === '..' || unitName === IDEAS || !existsSync(dir)) {
    console.error(`No such work unit: ${unitName}`)
    return 2
  }
  const stray = files.filter((f) => !ARTIFACTS.includes(f))
  if (stray.length) {
    console.error(`not an artifact: ${stray.join(', ')} — use ${ARTIFACTS.join(', ')}`)
    return 2
  }
  console.log(JSON.stringify({ units: { [unitName]: unitMeta(dir, files.length ? files : null) } }))
  return 0
}

// Today on this machine's calendar, `YYYY-MM-DD` — the date the app's own blocks carry.
function localDate(d = new Date()) {
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

// The three commands above answer about this checkout, so `--root` is refused for them.
const LOCAL_ONLY = new Set(['check-branch', 'check-tag', 'check-version'])

// `reserveFrom` widens the set of numbers already taken without widening where the unit is
// written. The app keeps its units in a store of its own, beside a repository that may
// have used `.cos/` for years; counting only the store would hand out `0001` again next to
// a `0001` already in the repository. Each directory's `.cos/` is read the way the root's
// is, and a directory without one contributes nothing. The path printed stays relative to
// the root, because the root is the only place anything is created.
function cmdNewPath(slug, cosDir, reserveFrom = []) {
  if (refuseSlug(slug, 'new-path')) return 2
  // Names only (`0135`): a number is taken by a directory, whatever its files say.
  const taken = [cosDir, ...reserveFrom.map((d) => join(resolve(d), '.cos'))]
    .flatMap((d) => (existsSync(d) ? readdirSync(d, { withFileTypes: true }) : []))
    .filter((e) => e.isDirectory() && e.name !== IDEAS)
    .map((e) => ({ number: Number(e.name.match(UNIT_RE)?.[1] ?? 0) }))
  console.log(`.cos/${nextNumber(taken)}_${slug}`)
  return 0
}

// Whether `slug` was refused, having said why. `new-path` and `new-idea` hold one rule.
function refuseSlug(slug, cmd) {
  if (!slug) {
    console.error(`usage: cos.mjs ${cmd} <slug>`)
    return true
  }
  if (!SLUG_RE.test(slug)) {
    console.error(`Invalid slug "${slug}".`)
    console.error('  Lowercase letters, digits and single hyphens only; no underscore,')
    console.error('  because the underscore separates the number from the slug.')
    return true
  }
  if (slug.length > SLUG_MAX) {
    console.error(`Invalid slug "${slug}": it is ${slug.length} characters, over the ${SLUG_MAX} a branch allows.`)
    return true
  }
  return false
}

// `0040` R1: an idea's path, numbered on its own from `0001`, the highest in `ideas/` plus
// one. Like `new-path`, it prints the path and creates nothing.
function cmdNewIdea(slug, cosDir) {
  if (refuseSlug(slug, 'new-idea')) return 2
  const dir = join(cosDir, IDEAS)
  const taken = existsSync(dir) ? readdirSync(dir).map((f) => f.match(IDEA_FILE_RE)).filter(Boolean).map((m) => Number(m[1])) : []
  console.log(`.cos/${IDEAS}/${String(Math.max(0, ...taken) + 1).padStart(4, '0')}_${slug}.md`)
  return 0
}

// Only when run as a command. Importing this file for tests must not exit the process.
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const argv = process.argv.slice(2)

  // `--root <dir>` reads another repository's units with these rules. Without it, the
  // repository this script lives in.
  const rootAt = argv.indexOf('--root')
  if (rootAt !== -1 && !argv[rootAt + 1]) {
    console.error('--root needs a directory')
    process.exit(2)
  }
  const cosDir = rootAt === -1 ? COS : join(resolve(argv[rootAt + 1]), '.cos')
  // Stripped before the command is read, so `--root` may sit on either side of it.
  const afterRoot = rootAt === -1 ? argv : argv.filter((_, i) => i !== rootAt && i !== rootAt + 1)

  // `--reserve-from <dir>`, repeatable, stripped the same way and from any position.
  // `--repo <dir>`, once, the same way: the repository whose git and pull request the
  // `review` and `ship` gates read.
  // `--state <file|->` (`0135`), once, the same way: the app's snapshot of every unit's
  // metadata, JSON from a file or from stdin. It carries every workspace a link may name,
  // which is why `--peer` (`0040` R5) is gone.
  const reserveFrom = []
  let repoArg = null
  let stateArg = null
  const words = []
  for (let i = 0; i < afterRoot.length; i++) {
    const flag = afterRoot[i]
    if (flag === '--peer') {
      console.error('--peer is gone since 0135: --state carries every workspace a link may name')
      process.exit(2)
    }
    if (flag !== '--reserve-from' && flag !== '--repo' && flag !== '--state') {
      words.push(flag)
      continue
    }
    if (!afterRoot[i + 1]) {
      console.error(flag === '--state' ? '--state needs a file, or - for stdin' : `${flag} needs a directory`)
      process.exit(2)
    }
    if (flag === '--repo') repoArg = afterRoot[++i]
    else if (flag === '--state') stateArg = afterRoot[++i]
    else reserveFrom.push(afterRoot[++i])
  }
  const [cmd, ...rest] = words

  // Without `--root` the units are this checkout's, so this checkout is their repository.
  // With `--root` and no `--repo` there is none, and the two gates that need one say so.
  const repoDir = repoArg !== null ? resolve(repoArg) : rootAt === -1 ? ROOT : null

  let limit = REVIEW_ROUNDS
  try {
    limit = reviewRounds()
  } catch (e) {
    console.error(e.message)
    process.exit(2)
  }

  let state = null
  if (stateArg !== null) {
    try {
      state = JSON.parse(readFileSync(stateArg === '-' ? 0 : stateArg, 'utf8'))
    } catch (e) {
      console.error(`--state ${stateArg} is not readable JSON: ${e.message}`)
      process.exit(2)
    }
    if (typeof state?.workspace !== 'string' || typeof state.units !== 'object' || state.units === null) {
      console.error(`--state ${stateArg} is not a snapshot: it needs "workspace" and "units"`)
      process.exit(2)
    }
  }

  const run = {
    status: () => cmdStatus(rest.includes('--json'), cosDir, limit, state),
    gate: () => {
      const [unitName, stage] = rest.filter((w) => w !== '--json')
      return cmdGate(unitName, stage, cosDir, repoDir, limit, state, rest.includes('--json'))
    },
    next: () => cmdNext(rest[0], cosDir, repoDir, limit, state),
    'new-path': () => cmdNewPath(rest[0], cosDir, reserveFrom),
    'new-idea': () => cmdNewIdea(rest[0], cosDir),
    'unit-branch': () => cmdUnitBranch(rest[0], cosDir, state),
    'pr-text': () => cmdPrText(rest[0], cosDir, state),
    rerun: () => cmdRerun(rest[0], rest[1], cosDir, limit, undefined, state),
    screens: () => cmdScreens(rest[0], cosDir, repoDir, state),
    meta: () => cmdMeta(rest[0], rest.slice(1), cosDir),
    'check-branch': () => cmdCheckBranch(rest[0]),
    'check-tag': () => cmdCheckTag(rest[0]),
    'check-version': () => cmdCheckVersion(),
  }[cmd]

  if (!run) {
    console.error('usage: cos.mjs [--root <dir>] <command>')
    console.error('  reading a .cos/ (these take --root):')
    console.error('    status [--json] | gate <unit> <stage> [--repo <dir>] | next <unit> [--repo <dir>] | new-path [--reserve-from <dir>]... <slug> | new-idea <slug> | unit-branch <unit> | pr-text <unit> | rerun <unit> [<stage>] | screens <unit> [--repo <dir>] | meta [<unit> [<artifact>]...]')
    console.error('    status, gate, next, rerun, unit-branch, pr-text and screens need --state <file|->, the app\'s snapshot')
    console.error('  describing this checkout (these do not):')
    console.error('    check-branch [name] | check-tag <tag> | check-version')
    process.exit(2)
  }

  // `--root` exists so the app can read **another repository's** `.cos/` with this
  // repository's rules rather than running the copy it finds over there. A command that
  // reports on git, or on the version files beside this script, has no such meaning: given
  // `--root` it would quietly answer about this checkout while naming somebody else's, and
  // that is worse than refusing. So the flag stops at the line between "reads a `.cos/`"
  // and "describes the checkout this script lives in".
  if (rootAt !== -1 && LOCAL_ONLY.has(cmd)) {
    console.error(`--root does not apply to \`${cmd}\`: it reports on the checkout this script lives in,`)
    console.error(`  not on a .cos/ somewhere else. Run it from the repository you mean.`)
    process.exit(2)
  }

  // `--reserve-from` means "these numbers are taken too", which is a question only
  // `new-path` asks. Anywhere else it would be silently ignored, and a flag that is
  // accepted and ignored reads as a flag that worked.
  if (reserveFrom.length && cmd !== 'new-path') {
    console.error(`--reserve-from applies only to \`new-path\`, not to \`${cmd}\`.`)
    process.exit(2)
  }

  // `--repo` names where the `review` and `ship` gates ask git and gh, and `next`, which
  // asks the same questions, and where `screens` (`0111`) reads the manifest. Nothing else asks.
  if (repoArg !== null && cmd !== 'gate' && cmd !== 'next' && cmd !== 'screens') {
    console.error(`--repo applies only to \`gate\` and \`next\`, and to \`screens\`, not to \`${cmd}\`.`)
    process.exit(2)
  }

  // `--state` is the metadata these commands decide on, and they decide on nothing else
  // (`0135` R6); `meta` is what the app builds it from.
  const STATE_READERS = ['status', 'gate', 'next', 'rerun', 'unit-branch', 'pr-text', 'screens']
  if (state && !STATE_READERS.includes(cmd)) {
    console.error(`--state applies only to ${STATE_READERS.map((c) => `\`${c}\``).join(', ')}, not to \`${cmd}\`.`)
    process.exit(2)
  }
  if (!state && STATE_READERS.includes(cmd)) {
    console.error(`${cmd} ${NEEDS_STATE}`)
    process.exit(2)
  }

  // `exitCode`, not `exit()`. With stdout a pipe, `process.exit` does not wait for the
  // write to drain, and a reader gets the first 64 KiB of the output and nothing after.
  // Measured 2026-09-23: once `status --json` carried each unit's questions (`0016`), this
  // repository's output passed that size and `coscc/units/board.py` failed on truncated JSON.
  process.exitCode = run()
}
