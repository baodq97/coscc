#!/usr/bin/env node
// Mechanical checks for the work-unit loop. Everything here is a fact about what is on
// disk: which artifacts exist, what status each carries, which gate that clears. Judgement
// — whether a spec should be skipped, whether a plan is good — stays with the skills.
import { readdirSync, readFileSync, existsSync } from 'node:fs'
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
  { name: 'review', file: 'review.md', hint: 'write-review', statuses: ['draft', 'accepted', 'rejected'] },
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
export function parseStatus(text) {
  const m = text.match(/\bStatus:\s*([A-Za-z]+)/)
  return m ? m[1].toLowerCase() : null
}

export function parseSkipReason(text) {
  const m = text.match(/\bSpec:\s*skipped\s*\(([^)]*)\)/i)
  return m ? m[1].trim() : null
}

export function readUnit(dir, name) {
  const unit = { name, artifacts: {}, problems: [] }
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
    const status = parseStatus(text)
    if (status === null) {
      unit.problems.push(`${file} carries no Status line`)
    } else if (!VALID[file].includes(status)) {
      unit.problems.push(`${file} has status "${status}", not one of ${VALID[file].join(', ')}`)
    }
    unit.artifacts[file] = { status, skipReason: file === 'plan.md' ? parseSkipReason(text) : null }
  }

  const stray = readdirSync(dir).filter((f) => !ARTIFACTS.includes(f))
  if (stray.length) unit.problems.push(`unexpected file(s): ${stray.join(', ')}`)
  if (!unit.artifacts['intent.md']) unit.problems.push(`no intent.md — every unit opens with one`)

  return unit
}

export function readAll() {
  if (!existsSync(COS)) return []
  return readdirSync(COS, { withFileTypes: true })
    .filter((e) => e.isDirectory())
    .map((e) => readUnit(join(COS, e.name), e.name))
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
export function nextAction(unit) {
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
  }
  return { blocked: false, action: 'finished' }
}

// Does `stage` have everything it needs? Returns the reasons it does not.
export function checkGate(unit, stage) {
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

  return { ok: need.length === 0, need }
}

export function nextNumber(units) {
  const max = units.reduce((n, u) => (u.number > n ? u.number : n), 0)
  return String(max + 1).padStart(4, '0')
}

// --- commands ----------------------------------------------------------------

const dash = '—'
// Eight stages will not fit across a terminal spelled out, so the table carries one letter
// each and prints the key underneath. The full words stay in `status --json`, which is what
// anything other than a human reads.
const CODE = { draft: 'd', accepted: 'A', rejected: 'x', skipped: 's', done: 'D' }
const cell = (u, f) => {
  const status = u.artifacts[f]?.status
  if (status === undefined) return dash
  return CODE[status] ?? '?'
}

function cmdStatus(json) {
  const units = readAll()
  const rows = units.map((u) => ({ ...u, next: nextAction(u) }))

  if (json) {
    console.log(JSON.stringify({ units: rows }, null, 2))
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
  console.log(`\nA accepted · d draft · s skipped · D done · x rejected · ${dash} not started`)

  const problems = rows.flatMap((u) => u.problems.map((p) => `${u.name}: ${p}`))
  if (problems.length) {
    console.log('\nProblems (report these, do not infer past them):')
    for (const p of problems) console.log(`  - ${p}`)
  }
  return 0
}

function cmdGate(unitName, stage) {
  if (!unitName || !stage) {
    console.error(`usage: cos.mjs gate <NNNN_slug> <${STAGE_NAMES.join('|')}>`)
    return 2
  }
  const dir = join(COS, unitName)
  if (!existsSync(dir)) {
    console.error(`No such work unit: ${unitName}`)
    return 2
  }
  const { ok, need } = checkGate(readUnit(dir, unitName), stage)
  if (ok) {
    console.log(`open: ${stage} may proceed for ${unitName}`)
    return 0
  }
  console.error(`blocked: ${stage} cannot proceed for ${unitName}`)
  for (const n of need) console.error(`  - ${n}`)
  return 1
}

function cmdNewPath(slug) {
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
  console.log(`.cos/${nextNumber(readAll())}_${slug}`)
  return 0
}

// Only when run as a command. Importing this file for tests must not exit the process.
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [cmd, ...rest] = process.argv.slice(2)
  const run = {
    status: () => cmdStatus(rest.includes('--json')),
    gate: () => cmdGate(rest[0], rest[1]),
    'new-path': () => cmdNewPath(rest[0]),
  }[cmd]

  if (!run) {
    console.error('usage: cos.mjs <status [--json] | gate <unit> <stage> | new-path <slug>>')
    process.exit(2)
  }
  process.exit(run())
}
