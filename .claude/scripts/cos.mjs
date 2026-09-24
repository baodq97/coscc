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

// The eight stages, in order. This list is the single place the loop is defined: the
// artifacts a unit may hold, the statuses each may carry, what the gate demands, and what
// `status` proposes next are all read off it. Adding a stage is editing this array.
//
// `optional: true` on `idea` is the one exception, and it is deliberate. `idea` comes
// before `intent`, but `readUnit` has always insisted that every unit opens with an
// intent, and eight units on disk were opened that way. Making `idea` mandatory would
// retroactively mark all of them incomplete. So it gates nothing: it is a place to record
// a thought that preceded the intent, and its absence means only that nobody recorded one.
const STAGES = [
  { name: 'idea', file: 'idea.md', optional: true, hint: 'write-idea', statuses: ['draft', 'accepted', 'rejected'] },
  { name: 'intent', file: 'intent.md', hint: 'write-intent — the unit has no intent.md', statuses: ['draft', 'accepted', 'rejected'] },
  { name: 'spec', file: 'spec.md', hint: 'write-spec — it assesses whether to skip first', statuses: ['draft', 'accepted', 'rejected', 'skipped'] },
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
export function parseStatus(text) {
  const m = text.match(/\bStatus:\s*([A-Za-z]+(?:-[A-Za-z]+)*)/)
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
function answerBlocks(text) {
  const lines = section(text, 'Answers')
  if (lines === null) return []
  const blocks = []
  for (const line of lines) {
    const m = line.match(/^###\s+(?:Câu\s+(\d+)|(F\d+)|(Outcome))\s*$/)
    if (m) blocks.push({ n: m[1] !== undefined ? Number(m[1]) : null, id: m[2] ?? null, outcome: m[3] !== undefined, lines: [] })
    else if (blocks.length) blocks[blocks.length - 1].lines.push(line)
  }
  return blocks
}

export function parseAnswers(text) {
  const answers = []
  for (const b of answerBlocks(text)) {
    if (b.outcome) continue
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

const ROUND_HEAD = /^## Round (\d+)\s*$/
// `needs-person` since `0028`: every finding left open is one the review confirmed a person
// must act on. The header stays `changes-requested`; the unit is not finished.
const ROUND_META = /^Reviewed:\s*([0-9a-f]{7,40})\.?\s+Verdict:\s*(pass|changes-requested|needs-person)\.?\s*$/i
// The labels a finding may carry besides `open` and `fixed <sha>` (`0028`): the review
// accepted impl's claim that a person must act (`needs-person`), rejected it
// (`claim-rejected`), or closed the finding on a person's answer in `review.md ## Answers`
// (`answered`).
const PERSON_LABELS = ['needs-person', 'claim-rejected', 'answered']
const FINDING = /^- (F\d+)\s+\[([^\]]*)\]\s*(.*)$/

// `review.md` is a list of rounds, each `## Round N`, never rewritten once written: a
// re-review appends a round. Each round opens with `Reviewed: <sha>. Verdict: pass|
// changes-requested|needs-person.` (`ROUND_META`) and lists its findings under
// `### Findings`, one per line, each labelled `[open]`, `[fixed <sha>]`, or one of
// `PERSON_LABELS` (`[needs-person]`, `[claim-rejected]`, `[answered]`, since `0028`). Any
// other label is `unreadable`, which the `ship` gate treats as not closed. Reading stops
// at `## Answers`, the app's section.
// Each round also carries `text`: its lines verbatim, from `## Round N` up to the next
// `## ` heading, trailing blank space trimmed. It is what the app posts to the pull
// request, so the app never has to find a round's edges itself.
export function parseReview(text) {
  const lines = text.split(/\r?\n/)
  const stop = lines.findIndex((l) => l.trimEnd() === '## Answers')
  const rounds = []
  let inFindings = false
  for (const line of stop === -1 ? lines : lines.slice(0, stop)) {
    const head = line.match(ROUND_HEAD)
    if (head) {
      rounds.push({ n: Number(head[1]), reviewed: null, verdict: null, findings: [], seenText: false, closed: false, lines: [line] })
      inFindings = false
      continue
    }
    if (line.startsWith('## ')) {
      // A section that is not a round ends the one above it.
      if (rounds.length) rounds[rounds.length - 1].closed = true
      inFindings = false
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
      continue
    }
    if (!inFindings) continue
    const f = line.match(FINDING)
    if (!f) continue
    const label = f[2].trim()
    const lower = label.toLowerCase()
    const fixed = label.match(/^fixed\s+([0-9a-f]{7,40})$/i)
    r.findings.push({
      id: f[1],
      label: lower === 'open' ? 'open' : fixed ? 'fixed' : PERSON_LABELS.includes(lower) ? lower : 'unreadable',
      fixedBy: fixed ? fixed[1].toLowerCase() : null,
      text: f[3].trim(),
    })
  }
  return {
    rounds: rounds.map(({ n, reviewed, verdict, findings, lines }) => ({
      n, reviewed, verdict, findings, text: lines.join('\n').trimEnd(),
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
    const questions = answeredQuestions(text)
    if (questions !== null) unit.artifacts[file].questions = questions
  }

  Object.assign(unit, unitQuestions(unit))
  // The one list the board's Questions tab shows a finding from (`0028`); empty unless the
  // last review round is a well-formed wait for a person. Derived here, never on the page.
  unit.personFindings = personFindings(unit) ?? []

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
    // `ideas/` is not a unit (`0003_one-idea-is-trapped-inside-one-unit` R6); `readIdeas`
    // reads it.
    .filter((e) => e.isDirectory() && e.name !== IDEAS_DIR)
    .map((e) => readUnit(join(cosDir, e.name), e.name))
    .sort((a, b) => a.name.localeCompare(b.name))
}

// --- ideas -------------------------------------------------------------------

// `0003_one-idea-is-trapped-inside-one-unit`. An idea is one file, `.cos/ideas/NNNN_<slug>.md`,
// outside every unit, so that several units can come from it without a copy of its words
// in each. It is a source, not a stage: no gate reads it and `STAGES` does not list it.
// Its numbers are a sequence of their own, and a name is always written `ideas/<name>.md`
// so `0001` of an idea is never read as `0001` of a unit.
const IDEAS_DIR = 'ideas'
const IDEA_STATUSES = ['draft', 'accepted', 'rejected']

export function readIdeas(cosDir = COS) {
  const dir = join(cosDir, IDEAS_DIR)
  if (!existsSync(dir)) return []
  return readdirSync(dir, { withFileTypes: true })
    .map((e) => {
      const name = e.name.endsWith('.md') ? e.name.slice(0, -3) : e.name
      const idea = { name, file: `${IDEAS_DIR}/${e.name}`, status: null, units: [], listed: [], problems: [] }
      const match = name.match(UNIT_RE)
      if (!e.isFile() || !e.name.endsWith('.md') || !match) {
        idea.problems.push(`file name does not match NNNN_<slug>.md`)
        return idea
      }
      idea.number = Number(match[1])
      idea.slug = match[2]
      const text = readFileSync(join(dir, e.name), 'utf8')
      idea.status = parseStatus(text)
      if (idea.status === null) idea.problems.push(`carries no Status line`)
      else if (!IDEA_STATUSES.includes(idea.status)) {
        idea.problems.push(`has status "${idea.status}", not one of ${IDEA_STATUSES.join(', ')}`)
      }
      idea.listed = parseIdeaUnits(text)
      return idea
    })
    .sort((a, b) => a.name.localeCompare(b.name))
}

// Every line `- <name>` under every `## Units` heading, in order. The app only ever appends
// that section (`coscc/units.py`), so a second one may follow `## Answers`; all are read.
export function parseIdeaUnits(text) {
  const listed = []
  let inUnits = false
  for (const line of text.split(/\r?\n/)) {
    if (line.startsWith('## ')) {
      inUnits = line.trimEnd() === '## Units'
      continue
    }
    if (!inUnits) continue
    const m = line.match(/^- (\S+)\s*$/)
    if (m && !listed.includes(m[1])) listed.push(m[1])
  }
  return listed
}

// An idea's answer to "what next". It has no gate and no stage of its own, so the only
// thing it can propose is the first unit (`intent.md ## Answers, câu 1`); once it has one,
// it lists them and proposes nothing (`spec.md ## Answers, câu 3`).
export function ideaNext(idea) {
  if (!idea.units.length) return { blocked: true, action: 'write-intent — open a unit from this idea', stage: 'intent' }
  return { blocked: false, action: `units: ${idea.units.join(', ')}`, stage: '' }
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

  for (const s of STAGES) {
    const status = statusOf(unit, s.file)
    if (status === null) {
      // A file that exists but says nothing is a different problem from a missing one.
      if (present(unit, s.file)) return { blocked: true, action: `fix ${s.file} — it carries no Status line`, stage: '', why: 'unreadable' }
      if (s.optional) continue
      return { blocked: true, action: s.hint, stage: s.name, why: 'missing' }
    }
    if (status === 'rejected') return { blocked: false, action: `closed — ${s.name} rejected`, stage: '', why: 'rejected' }
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

// Rounds that ended asking for changes. A `changes-requested` header whose rounds carry no
// readable verdict still counts as one, so a malformed round cannot buy another.
//
// A `needs-person` round is not counted (`0028` spec R5): it stopped for a person, it did
// not ask impl for anything. So the floor of one is waived once any round reads
// `needs-person` — otherwise a unit whose only round is that one would be charged for it.
function roundsUsed(unit) {
  const rounds = reviewOf(unit)
  const asked = rounds.filter((r) => r.verdict === 'changes-requested').length
  const waived = rounds.some((r) => r.verdict === 'needs-person')
  return Math.max(asked, statusOf(unit, 'review.md') === 'changes-requested' && !waived ? 1 : 0)
}

// The ids a person answered under `review.md ## Answers`. Units built in memory by a test
// carry no such field, and read as having none.
const personAnswers = (unit) => new Set(unit.artifacts['review.md']?.personAnswers ?? [])
const needsPersonClaims = (unit) => unit.artifacts['impl.md']?.needsPerson ?? []

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
  const closed = (f) => f.label === 'fixed' || f.label === 'needs-person' || (f.label === 'answered' && answered.has(f.id))
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
  const open = last.findings.filter((f) => f.label === 'open')
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
  return { git: (...args) => run('git', args), gh: (...args) => run('gh', args) }
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
  if (statusOf(unit, 'review.md') === 'changes-requested' && roundsUsed(unit) >= limit) {
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
  const open = last.findings.filter((f) => f.label !== 'fixed' && !(f.label === 'answered' && answered.has(f.id)))
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
  return need
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

  // Every stage ahead of the requested one has to be behind us. The loop replaces the three
  // hand-written cases it grew out of, so a ninth stage needs no edit here.
  const need = []
  for (const s of STAGES) {
    if (s.name === target.name) break
    if (s.optional) continue
    const status = statusOf(unit, s.file)
    // Only a file that may legitimately be skipped gets told it has that option.
    const canSkip = s.statuses.includes('skipped')
    if (status === null) {
      need.push(canSkip ? `${missing(unit, s.file)} — write it, or record the skip in it` : missing(unit, s.file))
    } else if (!settled(status)) {
      need.push(`${s.file} is "${status}", not accepted${canSkip ? ' or skipped' : ''}`)
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

  if (why === 'missing' && next.stage === 'review') return onReview([])

  if (why === 'missing' && next.stage === 'ship') {
    const g = evaluate(unit, 'ship', { probe, limit })
    if (g.ok) return { ...next, action: `${next.action} — merge with --match-head-commit ${g.said.head}` }
    if (g.said.moved) return onReview(g.need)
    return none(g.need.join('; '))
  }

  // `0028` (a): a person is awaited. Files alone settle it, so no probe is asked.
  if (why === 'awaits-person') return next
  // `0028` (b): every finding awaiting a person has an answer; a review reads them.
  if (why === 'person-answered') return onReview([next.action])

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
// the unit name cannot drift apart.
export function unitBranch(unitName, intentText) {
  const match = String(unitName ?? '').match(UNIT_RE)
  if (!match) return { error: `"${unitName}" does not match NNNN_<slug>` }
  const type = parseType(intentText ?? '')
  if (!type) return { error: `${unitName}/intent.md declares no Type: — one of ${TYPE_LIST()}` }
  if (!BRANCH_TYPES.includes(type)) return { error: `"${type}" is not one of ${TYPE_LIST()}` }
  return { branch: `${type}/${match[2]}` }
}

// --- commands ----------------------------------------------------------------

const dash = '—'
// Eight stages will not fit across a terminal spelled out, so the table carries one letter
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
  return why !== 'finished' && why !== 'rejected'
}

function cmdStatus(json, cosDir, limit) {
  const units = readAll(cosDir)
  const rows = units.map((u) => ({ ...u, next: nextAction(u, limit), betweenPrAndShip: betweenPrAndShip(u, limit) }))
  const ideas = readIdeas(cosDir).map(({ listed, ...i }) => ({ ...i, next: ideaNext(i) }))

  if (json) {
    // The stage list ships with the data so a reader never has to keep its own copy of it.
    console.log(JSON.stringify({ root: cosDir, stages: STAGES, units: rows, ideas }, null, 2))
    return 0
  }

  if (!units.length && !ideas.length) {
    console.log('No work units yet. `write-intent` opens one.')
    return 0
  }

  if (units.length) {
    console.log(`| Unit | ${STAGE_NAMES.join(' | ')} | Next action |`)
    console.log(`|---|${STAGE_NAMES.map(() => '---').join('|')}|---|`)
    for (const u of rows) {
      const cells = STAGES.map((s) => cell(u, s.file)).join(' | ')
      console.log(`| ${u.name} | ${cells} | ${u.next.action} |`)
    }
    console.log(`\nA accepted · d draft · c changes-requested · s skipped · D done · x rejected · ${dash} not started`)
  }

  // Printed only when there is an idea, so a store without `ideas/` prints what it always did.
  if (ideas.length) {
    console.log(`${units.length ? '\n' : ''}| Idea | status | Next action |`)
    console.log('|---|---|---|')
    for (const i of ideas) console.log(`| ${i.file} | ${i.status ?? dash} | ${i.next.action} |`)
  }

  const problems = [
    ...rows.flatMap((u) => u.problems.map((p) => `${u.name}: ${p}`)),
    ...ideas.flatMap((i) => i.problems.map((p) => `${i.file}: ${p}`)),
  ]
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
  const { stage, action, blocked, waiting } = nextStep(readUnit(dir, unitName), { probe, limit })
  // `waiting` only when a person is awaited (`0028`), so every other answer is unchanged.
  console.log(JSON.stringify({ unit: unitName, stage, action, blocked, ...(waiting?.length ? { waiting } : {}) }))
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
    'check-branch': () => cmdCheckBranch(rest[0]),
    'check-tag': () => cmdCheckTag(rest[0]),
    'check-version': () => cmdCheckVersion(),
  }[cmd]

  if (!run) {
    console.error('usage: cos.mjs [--root <dir>] <command>')
    console.error('  reading a .cos/ (these take --root):')
    console.error('    status [--json] | gate <unit> <stage> [--repo <dir>] | next <unit> [--repo <dir>] | new-path [--reserve-from <dir>]... <slug> | unit-branch <unit>')
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
