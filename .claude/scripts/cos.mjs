#!/usr/bin/env node
// Mechanical checks for the work-unit loop. Everything here is a fact about what is on
// disk: which artifacts exist, what status each carries, which gate that clears. Judgement
// — whether a spec should be skipped, whether a plan is good — stays with the skills.
import { readdirSync, readFileSync, existsSync } from 'node:fs'
import { execFileSync, spawnSync } from 'node:child_process'
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
const STAGES = [
  { name: 'idea', file: 'idea.md', optional: true, hint: 'write-idea', statuses: ['draft', 'accepted', 'rejected'] },
  { name: 'intent', file: 'intent.md', hint: 'write-intent — the unit has no intent.md', statuses: ['draft', 'accepted', 'rejected'] },
  { name: 'spec', file: 'spec.md', hint: 'write-spec — it assesses whether to skip first', statuses: ['draft', 'accepted', 'rejected', 'skipped'] },
  { name: 'spike', file: 'spike.md', when: 'unmeasured', hint: 'write-spike — spec.md has [unmeasured] items', statuses: ['draft', 'accepted', 'rejected'] },
  { name: 'plan', file: 'plan.md', hint: 'write-plan', statuses: ['draft', 'accepted', 'rejected', 'done'] },
  { name: 'impl', file: 'impl.md', hint: 'write-impl — implementation starts', statuses: ['draft', 'accepted', 'rejected', 'done'] },
  { name: 'pr', file: 'pr.md', hint: 'write-pr', statuses: ['draft', 'accepted', 'rejected'] },
  // `changes-requested` is the one status that sends work back rather than forward or
  // out: the reviewer looked, found something, and the unit goes on. `rejected` still
  // closes the unit, as it does on every other artifact. `settled` below does not include
  // it, so the `ship` gate is closed by it without a line of its own.
  { name: 'review', file: 'review.md', hint: 'write-review', statuses: ['draft', 'changes-requested', 'accepted', 'rejected'] },
  { name: 'ship', file: 'ship.md', hint: 'write-ship', statuses: ['draft', 'accepted', 'rejected'] },
]

// `implement` was the stage name for the first seven units and it is still written into
// `.claude/skills/write-plan/SKILL.md`. Keeping it as an alias costs one line; breaking it
// would silently block the two units that have not been implemented yet.
const STAGE_ALIAS = { implement: 'impl' }

const stageOf = (name) => STAGES.find((s) => s.name === (STAGE_ALIAS[name] ?? name)) ?? null
export const STAGE_NAMES = STAGES.map((s) => s.name)

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

// A question is a numbered item at column 0 under `## Open questions`. Its identity is the
// number written, not its position and not its words — a later stage may reword it, and a
// list that skips a number still means the number it wrote. Lines up to the next numbered
// item belong to the one above them. `null` when the file has no `## Open questions`.
//
// A section with no numbered item at all is read as a bullet list (`- ` or `* ` at column
// 0), numbered by position from 1. Measured 2026-09-23 on the product's own store: two of
// the artifacts there, `0015` `spec.md` and `0002` `intent.md`, wrote their questions that
// way, and a numbered-only reader showed them as having none. Position is a weaker
// identity than a written number — inserting a bullet renumbers everything below it — so
// the numbered form wins whenever a section has both.
export function parseQuestions(text) {
  const lines = section(text, 'Open questions')
  if (lines === null) return null
  const collect = (re, numberOf) => {
    const found = []
    for (const line of lines) {
      const m = line.match(re)
      if (m) found.push({ n: numberOf(m, found.length), lines: [m[m.length - 1]] })
      else if (found.length) found[found.length - 1].lines.push(line)
    }
    return found
  }
  let found = collect(/^(\d+)\.\s+(.*)$/, (m) => Number(m[1]))
  if (!found.length) found = collect(/^[-*]\s+(.*)$/, (_, i) => i + 1)
  return found.map((q) => ({ n: q.n, text: q.lines.join('\n').trim() }))
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
function answerBlocks(text) {
  const lines = section(text, 'Answers')
  if (lines === null) return []
  const blocks = []
  for (const line of lines) {
    const m = line.match(/^###\s+(?:Câu\s+(\d+)|(F\d+)|(Outcome))\s*$/)
    if (m) blocks.push({ n: m[1] !== undefined ? Number(m[1]) : null, id: m[2] ?? null, outcome: m[3] !== undefined, lines: [] })
    else if (HOLD_HEAD.test(line)) blocks.push({ hold: true, lines: [] })
    else if (blocks.length) blocks[blocks.length - 1].lines.push(line)
  }
  return blocks
}

export function parseAnswers(text) {
  const answers = []
  for (const b of answerBlocks(text)) {
    if (b.outcome || b.hold) continue
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

// The hold in force: walk the blocks in file order from `active`; the last valid one
// decides. A block with no well-formed `Decided by:` line is not counted, because nothing
// says who decided. A block describing a move `HOLD_MOVES` lacks is not counted either,
// and is reported. `hold` is `null` when the unit ends `active`.
export function parseHold(text) {
  const lines = section(text ?? '', 'Answers')
  const problems = []
  if (lines === null) return { hold: null, problems }
  const blocks = []
  for (const line of lines) {
    const m = line.match(HOLD_HEAD)
    if (m) blocks.push({ head: m[1], lines: [] })
    else if (/^###\s/.test(line)) blocks.push({ head: null, lines: [] })
    else if (blocks.length) blocks[blocks.length - 1].lines.push(line)
  }
  let state = 'active'
  let hold = null
  let n = 0
  for (const b of blocks) {
    if (!b.head) continue
    n += 1
    const at = b.lines.findIndex((l) => l.trim() !== '')
    const meta = at === -1 ? null : b.lines[at].match(HOLD_META)
    if (!meta) continue
    const to = HOLD_TO[b.head]
    if (!HOLD_MOVES[state].includes(to)) {
      problems.push(`hold block ${n} (### ${b.head}) is not a valid move from ${state} — it is ignored`)
      continue
    }
    state = to
    hold = to === 'active' ? null : { state: to, reason: b.lines.slice(at + 1).join('\n').trim(), by: meta[1].trim(), date: meta[2] }
  }
  return { hold, problems }
}

// Each question joined to the answer in force for it, if any.
export function answeredQuestions(text) {
  const questions = parseQuestions(text)
  if (questions === null) return null
  const latest = new Map()
  for (const a of parseAnswers(text)) if (a.n !== null) latest.set(a.n, a)
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
  return { title, body: kept.join('\n'), url: parsePr(text)?.url ?? null }
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
// are allowed and dropped.
const SCREENS_HEAD = /^Taken at:\s*`?([0-9a-f]{7,40})`?\.\s+Standard:\s*`?([^`\s]+?)`?\.\s+Looked at by:\s*(.+?),\s*from screenshots\.?\s*$/i
const SCREENS_SHOT = /^- `?(\S+?\.png)`?\s+—\s+(\d+)\s*[×x]\s*(\d+)\s+—\s+`?([^`\s]+)`?\s+—\s+(\S.*)$/

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
  return {
    rounds: rounds.map(({ n, reviewed, verdict, findings, screens, lines }) => ({
      n, reviewed, verdict, findings, screens, text: lines.join('\n').trimEnd(),
    })),
  }
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

export function readUnit(dir, name) {
  const unit = { name, artifacts: {}, problems: [] }
  let intentText = null
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
    if (file === 'intent.md') intentText = text
    const status = parseStatus(text)
    if (status === null) {
      unit.problems.push(`${file} carries no Status line`)
    } else if (!VALID[file].includes(status)) {
      unit.problems.push(`${file} has status "${status}", not one of ${VALID[file].join(', ')}`)
    }
    unit.artifacts[file] = { status, skipReason: file === 'plan.md' ? parseSkipReason(text) : null }
    // Attached, never reported as a problem: `review.md` files written before rounds
    // existed have none, and the closed units' output must not change (`0015` spec, R7).
    if (file === 'pr.md') unit.artifacts[file].pr = parsePr(text)
    if (file === 'review.md') {
      unit.artifacts[file].review = parseReview(text)
      // `0028`: the findings a person answered, by id, from `review.md ## Answers`.
      unit.artifacts[file].personAnswers = [...new Set(parseAnswers(text).filter((a) => a.id !== null).map((a) => a.id))]
    }
    if (file === 'impl.md') unit.artifacts[file].needsPerson = parseNeedsPerson(text)
    // `0039`: attached only when there is something to attach, so that `status --json` of
    // every unit that never used `[unmeasured]` stays what it was, byte for byte (R4).
    if (file === 'spec.md') {
      const unmeasured = parseUnmeasured(text)
      if (unmeasured.ids.length || unmeasured.problems.length) {
        unit.artifacts[file].unmeasured = unmeasured
        unit.problems.push(...unmeasured.problems)
      }
    }
    if (file === 'spike.md') unit.artifacts[file].spike = parseSpike(text)
    // Read after `spec.md` and `spike.md`, which `STAGES` puts before it. Only when `spike`
    // is required: a plan may mention `spike.md` without needing one — this unit's does.
    if (file === 'plan.md' && required(unit, SPIKE)) unit.artifacts[file].citesSpike = text.includes('spike.md')
    const questions = answeredQuestions(text)
    if (questions !== null) unit.artifacts[file].questions = questions
  }

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
    const type = parseType(intentText)
    if (type === null) unit.problems.push(`intent.md declares no Type — add "Type: <${BRANCH_TYPES[0]}|…>" to its header`)
    else if (!BRANCH_TYPES.includes(type)) unit.problems.push(`intent.md has type "${type}", not one of ${BRANCH_TYPES.join(', ')}`)
    else unit.type = type
    // Derived, never typed — the `ship` gate reads the branch the review was of.
    unit.branch = unitBranch(name, intentText).branch ?? null
  }
  // `0047`: read from the text already in hand, never another file. `nextAction` and
  // `checkGate` do not read it — a finished unit stays finished whatever it says.
  unit.outcome = intentText === null ? null : unitOutcome(intentText)

  // `0045`. Read off `intent.md ## Answers`, every unit that has one. `plan.md: done` and a
  // rejected artifact win over a hold (spec R5): the unit stays finished or closed, and the
  // block is reported rather than obeyed.
  const held = parseHold(intentText)
  unit.problems.push(...held.problems.map((p) => `intent.md: ${p}`))
  unit.hold = held.hold
  const ended = statusOf(unit, 'plan.md') === 'done'
    ? 'finished'
    : STAGES.some((s) => statusOf(unit, s.file) === 'rejected') ? 'closed' : null
  if (ended && unit.hold) {
    unit.problems.push(`intent.md carries a hold block, but the unit is ${ended} — it is ignored`)
    unit.hold = null
  }
  // No intent — a pre-intent unit, or a broken one — has nowhere to write a block.
  unit.holdMoves = ended || intentText === null ? [] : HOLD_MOVES[unit.hold?.state ?? 'active']

  return unit
}

// `cosDir` is a parameter because this file is also the template: the app reads another
// workspace's units by pointing **its own** copy of these rules at that workspace, rather
// than executing the copy it finds there. A workspace is a repository cloned from a URL a
// user typed, so its `.claude/scripts/cos.mjs` is someone else's code; running it would
// hand it everything this process has.
export function readAll(cosDir = COS) {
  if (!existsSync(cosDir)) return []
  return readdirSync(cosDir, { withFileTypes: true })
    .filter((e) => e.isDirectory())
    .map((e) => readUnit(join(cosDir, e.name), e.name))
    .sort((a, b) => a.name.localeCompare(b.name))
}

// --- deciding ----------------------------------------------------------------

const statusOf = (u, f) => u.artifacts[f]?.status ?? null
const present = (u, f) => f in u.artifacts

// What counts as "this stage is behind us". `done` is included because a plan is marked
// `done` only after its proof has passed — strictly further along than `accepted`, so a
// later stage must not be blocked by it.
const settled = (s) => s === 'accepted' || s === 'skipped' || s === 'done'

// A file that exists but carries no readable status is not the same as a missing file, and
// saying so is the difference between "write it" and "fix the one line at the top of it".
const missing = (u, f) => (present(u, f) ? `${f} exists but carries no Status line` : `${f} does not exist`)

const SPIKE = STAGES.find((s) => s.when === 'unmeasured')
const unmeasuredOf = (u) => u.artifacts['spec.md']?.unmeasured ?? { ids: [], problems: [] }

// Whether stage `s` has to be behind a unit before what follows it (`0039` R4, R5). Every
// stage but `idea` always does. `spike` does when `spec.md` names a `U<n>`, or `spike.md`
// already exists — a spec rewritten without its questions still leaves the measurement it
// was rewritten on — and never when the spec was skipped.
export function required(unit, s) {
  if (!s.when) return !s.optional
  if (statusOf(unit, 'spec.md') === 'skipped') return false
  return unmeasuredOf(unit).ids.length > 0 || present(unit, s.file)
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
  const { why, ...next } = decide(unit, limit)
  return next
}

// `nextAction`, plus `why`: which rule answered, so `nextStep` refines the answer without
// reading the English of `action` back.
function decide(unit, limit) {
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
      if (!required(unit, s)) continue
    }
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
    // `0085` R8: a review that ran out of turns left a round the app's closing turn wrote.
    // That is not a draft to finish by hand: another review goes on from it, unless the
    // rounds before it already used the limit (R9 keeps the floor for exactly that).
    if (s.file === 'review.md' && incompleteDraft(unit)) {
      const used = roundsUsed(unit)
      if (used >= limit) return { blocked: true, action: needsAPerson(used, limit), stage: '', why: 'needs-person' }
      return { blocked: true, action: `review round ${lastRound(unit).n} is incomplete — write-review again`, stage: 'review', why: 'review-incomplete' }
    }
    if (status === 'draft') return { blocked: true, action: `finish and accept ${s.file}`, stage: '', why: 'draft' }
    // Not closed and not done: the work goes back to the branch, then to another round.
    if (status === 'changes-requested') {
      const used = roundsUsed(unit)
      if (used >= limit) return { blocked: true, action: needsAPerson(used, limit), stage: '', why: 'needs-person' }
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
function roundsUsed(unit) {
  const rounds = reviewOf(unit)
  const asked = rounds.filter((r) => r.verdict === 'changes-requested').length
  const waived = rounds.some((r) => r.verdict === 'needs-person')
  const floor = statusOf(unit, 'review.md') === 'changes-requested' ||
    (incompleteDraft(unit) && rounds.some((r) => r.verdict === null))
  return Math.max(asked, floor && !waived ? 1 : 0)
}

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

// The first place the loop stops and waits for someone who is not an agent. A person
// unblocks it at a terminal: `review.md: rejected`, or a larger `COS_REVIEW_ROUNDS`.
// Nothing written through the product does — see `0015` plan, Risk 2, for why.
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
function screensNeeds(unit, probe, last, said) {
  const standard = probe.ui?.() ?? null
  if (!standard || !standard.globs.length) return []
  // Three dots: from the merge-base, in one command. The gate does not fetch (spec C7).
  let diff = probe.git('diff', '--name-only', `origin/main...${said.head}`)
  if (diff.code !== 0) {
    const local = probe.git('diff', '--name-only', `main...${said.head}`)
    if (local.code !== 0) {
      return [`cannot tell whether ${unit.name} changes a screen: git could not diff origin/main...${said.head} (${diff.err.trim()}) nor main...${said.head} (${local.err.trim()}) — the gate does not fetch`]
    }
    diff = local
  }
  const own = `.cos/${unit.name}/`
  const changed = uiFiles(diff.out.split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith(own)), standard.globs)
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

// --- what the gate asks git and gh -------------------------------------------

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
  return { git: (...args) => run('git', args), gh: (...args) => run('gh', args), ui }
}

// `review` may begin only on an open pull request whose required checks are green
// (`0015` spec, Answers, Câu 2). Nothing green to read is not read as green.
//
// `said.ci` records what CI said, once it was asked: `red`, `pending`, `none`, `unreadable`
// or `green`. `nextStep` reads it to tell "back to impl" from "wait" without parsing a need.
function reviewNeeds(unit, probe, limit, said = {}) {
  const need = []
  const pr = unit.artifacts['pr.md']?.pr ?? null
  if (!pr) need.push('pr.md names no pull request — the pr stage opens one and writes PR: <url>')
  if ((statusOf(unit, 'review.md') === 'changes-requested' || incompleteDraft(unit)) && roundsUsed(unit) >= limit) {
    need.push(needsAPerson(roundsUsed(unit), limit))
  }
  if (need.length || !pr) return need
  if (!probe) return ['no repository given — pass --repo <dir>']

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
  if (red.length) {
    said.ci = 'red'
    return [`CI is red on #${pr.number}: ${red.join(', ')} — back to impl: fix on the branch and push`]
  }
  const waiting = checks.filter((c) => c.bucket !== 'pass' && c.bucket !== 'skipping').map((c) => c.name)
  if (waiting.length) {
    said.ci = 'pending'
    return [`CI has not finished on #${pr.number}: ${waiting.join(', ')} — wait, then ask again`]
  }
  said.ci = 'green'
  return []
}

// The pull request's head on GitHub, as `{ head }`, or `{ error }` saying why not. Shared by
// `shipNeeds` and `nextStep`: both ask "what is on the pull request now", and two readings
// of one `gh pr view` could disagree.
function prHead(probe, pr) {
  const view = probe.gh('pr', 'view', String(pr.number), '--json', 'state,headRefOid')
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
  if (info.state !== 'OPEN') return { error: `#${pr.number} is ${info.state}, not open — there is nothing to merge` }
  if (probe.git('cat-file', '-e', `${info.headRefOid}^{commit}`).code !== 0) {
    return { error: `the head of #${pr.number}, ${info.headRefOid}, is not in this repository — someone pushed from elsewhere: fetch, then ask again` }
  }
  return { head: info.headRefOid }
}

// `ship` merges. It may do so only after a pass that left nothing open, whose history is
// intact, and after which no code reached the branch (`0015` spec, R3–R5).
//
// `said.head` is set to the pull request head the gate checked, so the merge can be pinned
// to it.
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
  const inLast = new Set(last.findings.map((f) => f.id))
  const dropped = [...new Set(rounds.slice(0, -1).flatMap((r) => r.findings.map((f) => f.id)))].filter((id) => !inLast.has(id))
  if (dropped.length) {
    need.push(`review round ${last.n} drops findings an earlier round raised: ${dropped.join(', ')} — carry each one forward, fixed or open`)
  }
  const numbers = rounds.map((r) => r.n)
  if (numbers.some((n, i) => n !== i + 1)) {
    need.push(`review rounds are numbered ${numbers.join(', ')}, not 1 to ${rounds.length} — a round was removed or renumbered`)
  }
  if (!last.reviewed) need.push(`review round ${last.n} names no reviewed commit — Reviewed: <sha>`)
  if (need.length) return need

  if (!probe) return ['no repository given — pass --repo <dir>']
  if (!unit.branch) return ['the unit has no branch — intent.md must declare a Type']
  const pr = unit.artifacts['pr.md']?.pr ?? null
  if (!pr) return ['pr.md names no pull request — nothing says what ship would merge']
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
  const read = prHead(probe, pr)
  if (read.error) return [read.error]
  refs.push(read.head)
  said.head = read.head
  for (const ref of refs) {
    const name = ref === said.head ? `the head of #${pr.number} (${ref})` : ref
    const since = changedSince(probe, unit, last.reviewed, ref)
    if (since.error) {
      need.push(since.error)
      continue
    }
    // `said.moved`: what the pass reviewed is no longer what would merge. The cure for
    // both is another round, which is what `nextStep` offers when it sees this.
    if (since.rewritten) {
      said.moved = true
      need.push(`the reviewed commit ${last.reviewed} is not on ${name} — the branch was rewritten after the pass (a rebase does this): review its new head in another round; a round that passes does not count toward the limit`)
      continue
    }
    if (since.files.length) {
      said.moved = true
      need.push(`${name} changed after the reviewed commit ${last.reviewed}: ${since.files.join(', ')} — review again`)
    }
  }
  if (need.length) return need
  return screensNeeds(unit, probe, last, said)
}

// What reached `ref` after `reviewed`, outside the unit's own `.cos/` files:
// `{ rewritten: true }` when `reviewed` is not on it at all, `{ files }` otherwise, or
// `{ error }`. A rewrite counts as a change on purpose — the gate cannot tell a rebase that
// changed nothing from one that changed everything (`0024` spec, Concern 4).
function changedSince(probe, unit, reviewed, ref) {
  if (probe.git('merge-base', '--is-ancestor', reviewed, ref).code !== 0) return { rewritten: true, files: [] }
  const diff = probe.git('diff', '--name-only', `${reviewed}..${ref}`)
  if (diff.code !== 0) return { error: `git could not diff ${reviewed}..${ref}: ${diff.err.trim()}` }
  const own = `.cos/${unit.name}/`
  return { rewritten: false, files: diff.out.split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith(own)) }
}

// Does `stage` have everything it needs? Returns the reasons it does not.
//
// `probe` is how `review` and `ship` reach git and gh; `null` means no repository was
// given, and those two gates stay closed rather than guess. `limit` is the review round
// limit in force. Every other stage reads files only and ignores both.
export function checkGate(unit, stage, { probe = null, limit = REVIEW_ROUNDS } = {}) {
  const { ok, need, said } = evaluate(unit, stage, { probe, limit })
  return ok && said.head ? { ok, need, head: said.head } : { ok, need }
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
  if (target.when && !required(unit, target)) need.push(`${target.name} is not required: spec.md has no [unmeasured] item`)
  for (const s of STAGES) {
    if (s.name === target.name) break
    if (s.optional) continue
    if (s.when && !required(unit, s)) continue
    const status = statusOf(unit, s.file)
    // Only a file that may legitimately be skipped gets told it has that option.
    const canSkip = s.statuses.includes('skipped')
    if (status === null) {
      need.push(canSkip ? `${missing(unit, s.file)} — write it, or record the skip in it` : missing(unit, s.file))
    } else if (!settled(status)) {
      need.push(`${s.file} is "${status}", not accepted${canSkip ? ' or skipped' : ''}`)
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

  return { ok: need.length === 0, need, said }
}

// --- the next stage to run ----------------------------------------------------

// The one stage a run button may offer for `unit`, or `''` for none, with the sentence that
// explains it. `0024`: the app's button used to pick "the first required stage with no
// artifact" for itself — a second copy of the loop — and so after a review asked for
// changes it offered `ship`, whose gate is closed, and nothing else. This is where that
// decision lives now, beside `nextAction`, which it starts from.
//
// Files settle it everywhere but three places, and those three need git and the pull
// request, which is why this takes the same `probe` the gates take:
//   - a review asked for changes: `impl` until something outside `.cos/<unit>/` reached the
//     pull request's head after the reviewed commit, then `review` once CI is green,
//     `impl` again while it is red, and nothing while it runs;
//   - `pr` is done and no review exists: `review` on green, `impl` on red, else nothing;
//   - the review passed: `ship` if its gate is open, `review` again if the branch moved
//     after the pass (a rebase does this), else nothing.
// With no `probe` those three answer `''` and say `--repo` is missing, as the gates do.
//
// This names a stage; it opens nothing. A caller still asks `checkGate` before running it.
export function nextStep(unit, { probe = null, limit = REVIEW_ROUNDS } = {}) {
  const base = decide(unit, limit)
  const { why, ...next } = base
  const none = (action) => ({ blocked: true, action, stage: '' })
  const onReview = (prefix) => {
    const g = evaluate(unit, 'review', { probe, limit })
    const reasons = [...prefix, ...g.need].join('; ')
    // `blocked` keeps `nextAction`'s meaning — the unit is not finished — not the gate's.
    if (g.ok) return { blocked: true, action: [...prefix, 'CI is green: write-review'].join('; '), stage: 'review' }
    if (g.said.ci === 'red') return { blocked: true, action: reasons, stage: 'impl' }
    return none(reasons)
  }

  // `0045` R3: a held unit is answered from the files, before anything reaches for `probe`.
  if (why === 'paused' || why === 'dropped') return next

  if (why === 'missing' && next.stage === 'review') return onReview([])

  if (why === 'missing' && next.stage === 'ship') {
    const g = evaluate(unit, 'ship', { probe, limit })
    if (g.ok) return { ...next, action: `${next.action} — merge with --match-head-commit ${g.said.head}` }
    // `0083` R10: a UI unit whose pass lacks current screenshots is cured the same way.
    if (g.said.moved || g.said.screens) return onReview(g.need)
    return none(g.need.join('; '))
  }

  // `0028` (a): a person is awaited. Files alone settle it, so no probe is asked.
  if (why === 'awaits-person') return next
  // `0028` (b): every finding awaiting a person has an answer; a review reads them.
  if (why === 'person-answered') return onReview([next.action])
  // `0085` R8: a review ran out of turns; the next one goes on from its round, CI permitting.
  if (why === 'review-incomplete') return onReview([next.action])

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
    if (!since.rewritten && !since.files.length) {
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
export function unitBranch(unitName, intentText) {
  const match = String(unitName ?? '').match(UNIT_RE)
  if (!match) return { error: `"${unitName}" does not match NNNN_<slug>` }
  const type = parseType(intentText ?? '')
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

// `0100` R2: the stage a unit is at, for a board that draws one column per stage. It is
// the stage `next` names; failing that, the last stage whose artifact is on disk (a draft,
// a `changes-requested` review, a closed or finished unit); failing that, the first stage
// every unit walks. Read off `STAGES`, so the board keeps no copy of the loop.
export function stageAt(unit, next) {
  if (next.stage) return next.stage
  const last = STAGES.filter((s) => present(unit, s.file)).at(-1)
  return (last ?? STAGES.find((s) => !s.optional && !s.when)).name
}

function cmdStatus(json, cosDir, limit) {
  const units = readAll(cosDir)
  // `0100` R4: `status` carries `why` as well, so the board reads which rule answered
  // rather than the English of `action`. `next` still prints `nextAction`, without it.
  const rows = units.map((u) => {
    const next = decide(u, limit)
    return { ...u, next, at: stageAt(u, next), betweenPrAndShip: betweenPrAndShip(u, limit) }
  })

  if (json) {
    // The stage list ships with the data so a reader never has to keep its own copy of it.
    console.log(JSON.stringify({ root: cosDir, stages: STAGES, units: rows }, null, 2))
    return 0
  }

  if (!units.length) {
    console.log('No work units yet. `write-intent` opens one.')
    return 0
  }

  console.log(`| Unit | ${STAGE_NAMES.join(' | ')} | Next action |`)
  console.log(`|---|${STAGE_NAMES.map(() => '---').join('|')}|---|`)
  for (const u of rows) {
    const cells = STAGES.map((s) => cell(u, s.file)).join(' | ')
    console.log(`| ${u.name} | ${cells} | ${u.next.action} |`)
  }
  console.log(`\nA accepted · d draft · c changes-requested · s skipped · D done · x rejected · ${dash} not started`)

  const problems = rows.flatMap((u) => u.problems.map((p) => `${u.name}: ${p}`))
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
function cmdNext(unitName, cosDir, repoDir, limit) {
  if (!unitName) {
    console.error('usage: cos.mjs next <NNNN_slug> [--repo <dir>]')
    return 2
  }
  const dir = join(cosDir, unitName)
  if (!existsSync(dir)) {
    console.error(`No such work unit: ${unitName}`)
    return 2
  }
  const probe = repoDir ? makeProbe(repoDir) : null
  const unit = readUnit(dir, unitName)
  const { stage, action, blocked, waiting } = nextStep(unit, { probe, limit })
  // `waiting` only when a person is awaited (`0028`), `hold` only when the unit is held
  // (`0045`), so every other answer is unchanged.
  const hold = unit.hold ? { hold: unit.hold } : {}
  console.log(JSON.stringify({ unit: unitName, stage, action, blocked, ...(waiting?.length ? { waiting } : {}), ...hold }))
  return 0
}

function cmdGate(unitName, stage, cosDir, repoDir, limit) {
  if (!unitName || !stage) {
    console.error(`usage: cos.mjs gate <NNNN_slug> <${STAGE_NAMES.join('|')}> [--repo <dir>]`)
    return 2
  }
  const dir = join(cosDir, unitName)
  if (!existsSync(dir)) {
    console.error(`No such work unit: ${unitName}`)
    return 2
  }
  const probe = repoDir ? makeProbe(repoDir) : null
  const { ok, need, head } = checkGate(readUnit(dir, unitName), stage, { probe, limit })
  if (ok) {
    // `ship` is told the one commit it may merge; any other head is one nobody reviewed.
    const pin = head ? ` — merge with --match-head-commit ${head}` : ''
    console.log(`open: ${stage} may proceed for ${unitName}${pin}`)
    return 0
  }
  console.error(`blocked: ${stage} cannot proceed for ${unitName}`)
  for (const n of need) console.error(`  - ${n}`)
  return 1
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
    console.error(`"${subject}" is not a work branch: ${problem}`)
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

function cmdUnitBranch(unitName, cosDir) {
  if (!unitName) {
    console.error('usage: cos.mjs unit-branch <NNNN_slug>')
    return 2
  }
  const intent = join(cosDir, unitName, 'intent.md')
  if (!existsSync(intent)) {
    console.error(`No such work unit: ${unitName}`)
    return 2
  }
  const { branch, error } = unitBranch(unitName, readFileSync(intent, 'utf8'))
  if (error) {
    console.error(error)
    return 1
  }
  console.log(branch)
  return 0
}

// Reads one file and prints; no git, no gh, no write. The name is checked before it is
// joined, so `../` cannot walk out of the `.cos/` it was given.
function cmdPrText(unitName, cosDir) {
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
  console.log(JSON.stringify({ unit: unitName, ...prText(text), status: parseStatus(text) }))
  return 0
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
  if (!slug) {
    console.error('usage: cos.mjs new-path <slug>')
    return 2
  }
  if (!SLUG_RE.test(slug)) {
    console.error(`Invalid slug "${slug}".`)
    console.error('  Lowercase letters, digits and single hyphens only; no underscore,')
    console.error('  because the underscore separates the number from the slug.')
    return 2
  }
  if (slug.length > SLUG_MAX) {
    console.error(`Invalid slug "${slug}": it is ${slug.length} characters, over the ${SLUG_MAX} a branch allows.`)
    return 2
  }
  const taken = [cosDir, ...reserveFrom.map((d) => join(resolve(d), '.cos'))].flatMap((d) => readAll(d))
  console.log(`.cos/${nextNumber(taken)}_${slug}`)
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
  const reserveFrom = []
  let repoArg = null
  const words = []
  for (let i = 0; i < afterRoot.length; i++) {
    const flag = afterRoot[i]
    if (flag !== '--reserve-from' && flag !== '--repo') {
      words.push(flag)
      continue
    }
    if (!afterRoot[i + 1]) {
      console.error(`${flag} needs a directory`)
      process.exit(2)
    }
    if (flag === '--repo') repoArg = afterRoot[++i]
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

  const run = {
    status: () => cmdStatus(rest.includes('--json'), cosDir, limit),
    gate: () => cmdGate(rest[0], rest[1], cosDir, repoDir, limit),
    next: () => cmdNext(rest[0], cosDir, repoDir, limit),
    'new-path': () => cmdNewPath(rest[0], cosDir, reserveFrom),
    'unit-branch': () => cmdUnitBranch(rest[0], cosDir),
    'pr-text': () => cmdPrText(rest[0], cosDir),
    'check-branch': () => cmdCheckBranch(rest[0]),
    'check-tag': () => cmdCheckTag(rest[0]),
    'check-version': () => cmdCheckVersion(),
  }[cmd]

  if (!run) {
    console.error('usage: cos.mjs [--root <dir>] <command>')
    console.error('  reading a .cos/ (these take --root):')
    console.error('    status [--json] | gate <unit> <stage> [--repo <dir>] | next <unit> [--repo <dir>] | new-path [--reserve-from <dir>]... <slug> | unit-branch <unit> | pr-text <unit>')
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
  // asks the same questions. Nothing else asks.
  if (repoArg !== null && cmd !== 'gate' && cmd !== 'next') {
    console.error(`--repo applies only to \`gate\` and \`next\`, not to \`${cmd}\`.`)
    process.exit(2)
  }

  // `exitCode`, not `exit()`. With stdout a pipe, `process.exit` does not wait for the
  // write to drain, and a reader gets the first 64 KiB of the output and nothing after.
  // Measured 2026-09-23: once `status --json` carried each unit's questions (`0016`), this
  // repository's output passed that size and `coscc/board.py` failed on truncated JSON.
  process.exitCode = run()
}
