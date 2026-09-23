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
export function parseAnswers(text) {
  const lines = section(text, 'Answers')
  if (lines === null) return []
  const blocks = []
  for (const line of lines) {
    const m = line.match(/^###\s+Câu\s+(\d+)\s*$/)
    if (m) blocks.push({ n: Number(m[1]), lines: [] })
    else if (blocks.length) blocks[blocks.length - 1].lines.push(line)
  }
  const answers = []
  for (const b of blocks) {
    const at = b.lines.findIndex((l) => l.trim() !== '')
    const meta = at === -1 ? null : b.lines[at].match(ANSWER_META)
    if (!meta) continue
    answers.push({
      n: b.n,
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
  for (const a of parseAnswers(text)) latest.set(a.n, a)
  return questions.map((q) => {
    const a = latest.get(q.n) ?? null
    return { n: q.n, text: q.text, answered: a !== null, answer: a }
  })
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
const ROUND_META = /^Reviewed:\s*([0-9a-f]{7,40})\.?\s+Verdict:\s*(pass|changes-requested)\.?\s*$/i
const FINDING = /^- (F\d+)\s+\[([^\]]*)\]\s*(.*)$/

// `review.md` is a list of rounds, each `## Round N`, never rewritten once written: a
// re-review appends a round. Each round opens with `Reviewed: <sha>. Verdict: pass|
// changes-requested.` and lists its findings under `### Findings`, one per line, each
// labelled `[open]` or `[fixed <sha>]`. A label that is neither is `unreadable`, which the
// `ship` gate treats as not fixed. Reading stops at `## Answers`, the app's section.
export function parseReview(text) {
  const lines = text.split(/\r?\n/)
  const stop = lines.findIndex((l) => l.trimEnd() === '## Answers')
  const rounds = []
  let inFindings = false
  for (const line of stop === -1 ? lines : lines.slice(0, stop)) {
    const head = line.match(ROUND_HEAD)
    if (head) {
      rounds.push({ n: Number(head[1]), reviewed: null, verdict: null, findings: [], seenText: false, closed: false })
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
    const fixed = label.match(/^fixed\s+([0-9a-f]{7,40})$/i)
    r.findings.push({
      id: f[1],
      label: label.toLowerCase() === 'open' ? 'open' : fixed ? 'fixed' : 'unreadable',
      fixedBy: fixed ? fixed[1].toLowerCase() : null,
      text: f[3].trim(),
    })
  }
  return { rounds: rounds.map(({ n, reviewed, verdict, findings }) => ({ n, reviewed, verdict, findings })) }
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
    if (file === 'review.md') unit.artifacts[file].review = parseReview(text)
    const questions = answeredQuestions(text)
    if (questions !== null) unit.artifacts[file].questions = questions
  }

  Object.assign(unit, unitQuestions(unit))

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

// One action per unit: a named skill, or the one edit that unblocks the file.
export function nextAction(unit, limit = REVIEW_ROUNDS) {
  // `plan.md: done` closed five units under the three-stage loop, and it stays terminal.
  // Widening the loop must not reopen work that was finished and proved under the old
  // rules — `write-plan` only allows `done` once the proof command has passed.
  if (statusOf(unit, 'plan.md') === 'done') return { blocked: false, action: 'finished' }

  for (const s of STAGES) {
    const status = statusOf(unit, s.file)
    if (status === null) {
      // A file that exists but says nothing is a different problem from a missing one.
      if (present(unit, s.file)) return { blocked: true, action: `fix ${s.file} — it carries no Status line` }
      if (s.optional) continue
      return { blocked: true, action: s.hint }
    }
    if (status === 'rejected') return { blocked: false, action: `closed — ${s.name} rejected` }
    if (status === 'draft') return { blocked: true, action: `finish and accept ${s.file}` }
    // Not closed and not done: the work goes back to the branch, then to another round.
    if (status === 'changes-requested') {
      const used = roundsUsed(unit)
      if (used >= limit) return { blocked: true, action: needsAPerson(used, limit) }
      return {
        blocked: true,
        action: `fix the open findings of review round ${lastRound(unit)?.n ?? used} on the branch, then write-review again (${used} of ${limit} rounds used)`,
      }
    }
  }
  return { blocked: false, action: 'finished' }
}

const reviewOf = (unit) => unit.artifacts['review.md']?.review?.rounds ?? []
const lastRound = (unit) => reviewOf(unit).at(-1) ?? null

// Rounds that ended asking for changes. A `changes-requested` header whose rounds carry no
// readable verdict still counts as one, so a malformed round cannot buy another.
function roundsUsed(unit) {
  const asked = reviewOf(unit).filter((r) => r.verdict === 'changes-requested').length
  return Math.max(asked, statusOf(unit, 'review.md') === 'changes-requested' ? 1 : 0)
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
function reviewNeeds(unit, probe, limit) {
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
    const said = (r.err || r.out).trim() || `gh exited ${r.code} and said nothing`
    return [`cannot read the required checks of #${pr.number}: ${said}`]
  }
  if (!checks.length) return [`#${pr.number} reports no required checks — nothing green to read is not green`]
  const red = checks.filter((c) => c.bucket === 'fail' || c.bucket === 'cancel').map((c) => c.name)
  if (red.length) return [`CI is red on #${pr.number}: ${red.join(', ')} — back to impl: fix on the branch and push`]
  const waiting = checks.filter((c) => c.bucket !== 'pass' && c.bucket !== 'skipping').map((c) => c.name)
  if (waiting.length) return [`CI has not finished on #${pr.number}: ${waiting.join(', ')} — wait, then ask again`]
  return []
}

// `ship` merges. It may do so only after a pass that left nothing open, whose history is
// intact, and after which no code reached the branch (`0015` spec, R3–R5).
function shipNeeds(unit, probe) {
  const rounds = reviewOf(unit)
  if (!rounds.length) return ['review.md has no ## Round — nothing says what was reviewed or found']
  const last = rounds.at(-1)
  const need = []
  if (last.verdict !== 'pass') {
    need.push(`review round ${last.n} has verdict "${last.verdict ?? 'unreadable'}", not pass — its first line is Reviewed: <sha>. Verdict: pass.`)
  }
  const open = last.findings.filter((f) => f.label !== 'fixed')
  if (open.length) {
    need.push(`review round ${last.n} still has findings not fixed: ${open.map((f) => `${f.id} [${f.label}]`).join(', ')}`)
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
  const refs = [`refs/heads/${unit.branch}`, `refs/remotes/origin/${unit.branch}`].filter(
    (ref) => probe.git('rev-parse', '--verify', '--quiet', ref).code === 0,
  )
  if (!refs.length) return [`no branch ${unit.branch} here, local or origin — the gate does not fetch`]
  if (probe.git('cat-file', '-e', `${last.reviewed}^{commit}`).code !== 0) {
    return [`the reviewed commit ${last.reviewed} is not in this repository`]
  }
  const own = `.cos/${unit.name}/`
  for (const ref of refs) {
    if (probe.git('merge-base', '--is-ancestor', last.reviewed, ref).code !== 0) {
      need.push(`the reviewed commit ${last.reviewed} is not on ${ref} — the branch was rewritten after the pass`)
      continue
    }
    const diff = probe.git('diff', '--name-only', `${last.reviewed}..${ref}`)
    if (diff.code !== 0) {
      need.push(`git could not diff ${last.reviewed}..${ref}: ${diff.err.trim()}`)
      continue
    }
    const after = diff.out.split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith(own))
    if (after.length) {
      need.push(`${ref} changed after the reviewed commit ${last.reviewed}: ${after.join(', ')} — review again`)
    }
  }
  return need
}

// Does `stage` have everything it needs? Returns the reasons it does not.
//
// `probe` is how `review` and `ship` reach git and gh; `null` means no repository was
// given, and those two gates stay closed rather than guess. `limit` is the review round
// limit in force. Every other stage reads files only and ignores both.
export function checkGate(unit, stage, { probe = null, limit = REVIEW_ROUNDS } = {}) {
  const target = stageOf(stage)
  if (!target) {
    return { ok: false, need: [`unknown stage "${stage}" — use one of ${STAGE_NAMES.join(', ')}`] }
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
  if (!need.length && target.name === 'review') need.push(...reviewNeeds(unit, probe, limit))
  if (!need.length && target.name === 'ship') need.push(...shipNeeds(unit, probe))

  return { ok: need.length === 0, need }
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

function cmdStatus(json, cosDir, limit) {
  const units = readAll(cosDir)
  const rows = units.map((u) => ({ ...u, next: nextAction(u, limit) }))

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
  const { ok, need } = checkGate(readUnit(dir, unitName), stage, { probe, limit })
  if (ok) {
    console.log(`open: ${stage} may proceed for ${unitName}`)
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
    'new-path': () => cmdNewPath(rest[0], cosDir, reserveFrom),
    'unit-branch': () => cmdUnitBranch(rest[0], cosDir),
    'check-branch': () => cmdCheckBranch(rest[0]),
    'check-tag': () => cmdCheckTag(rest[0]),
    'check-version': () => cmdCheckVersion(),
  }[cmd]

  if (!run) {
    console.error('usage: cos.mjs [--root <dir>] <command>')
    console.error('  reading a .cos/ (these take --root):')
    console.error('    status [--json] | gate <unit> <stage> [--repo <dir>] | new-path [--reserve-from <dir>]... <slug> | unit-branch <unit>')
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

  // `--repo` names where the `review` and `ship` gates ask git and gh. Nothing else asks.
  if (repoArg !== null && cmd !== 'gate') {
    console.error(`--repo applies only to \`gate\`, not to \`${cmd}\`.`)
    process.exit(2)
  }

  // `exitCode`, not `exit()`. With stdout a pipe, `process.exit` does not wait for the
  // write to drain, and a reader gets the first 64 KiB of the output and nothing after.
  // Measured 2026-09-23: once `status --json` carried each unit's questions (`0016`), this
  // repository's output passed that size and `coscc/board.py` failed on truncated JSON.
  process.exitCode = run()
}
