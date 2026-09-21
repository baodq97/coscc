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
const ARTIFACTS = ['intent.md', 'spec.md', 'plan.md']

const VALID = {
  'intent.md': ['draft', 'accepted', 'rejected'],
  'spec.md': ['draft', 'accepted', 'rejected', 'skipped'],
  'plan.md': ['draft', 'accepted', 'rejected', 'done'],
}

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
const settled = (s) => s === 'accepted' || s === 'skipped'

// A file that exists but carries no readable status is not the same as a missing file, and
// saying so is the difference between "write it" and "fix the one line at the top of it".
const missing = (u, f) => (present(u, f) ? `${f} exists but carries no Status line` : `${f} does not exist`)

// One action per unit, and it is always either a human decision or a named skill.
export function nextAction(unit) {
  const intent = statusOf(unit, 'intent.md')
  const spec = statusOf(unit, 'spec.md')
  const plan = statusOf(unit, 'plan.md')

  if (!intent) {
    return present(unit, 'intent.md')
      ? { blocked: true, action: 'fix intent.md — it carries no Status line' }
      : { blocked: true, action: 'write-intent — the unit has no intent.md' }
  }
  if (intent === 'rejected') return { blocked: false, action: 'closed — intent rejected' }
  if (intent === 'draft') return { blocked: true, action: 'human accepts intent.md' }

  if (!spec) {
    return present(unit, 'spec.md')
      ? { blocked: true, action: 'fix spec.md — it carries no Status line' }
      : { blocked: true, action: 'write-spec — it assesses whether to skip first' }
  }
  if (spec === 'rejected') return { blocked: false, action: 'closed — spec rejected' }
  if (spec === 'draft') return { blocked: true, action: 'human accepts spec.md' }

  if (!plan) {
    return present(unit, 'plan.md')
      ? { blocked: true, action: 'fix plan.md — it carries no Status line' }
      : { blocked: true, action: 'write-plan' }
  }
  if (plan === 'rejected') return { blocked: false, action: 'closed — plan rejected' }
  if (plan === 'draft') return { blocked: true, action: 'human accepts plan.md' }
  if (plan === 'accepted') return { blocked: false, action: 'implementation starts' }
  return { blocked: false, action: 'finished' }
}

// Does `stage` have everything it needs? Returns the reasons it does not.
export function checkGate(unit, stage) {
  const intent = statusOf(unit, 'intent.md')
  const spec = statusOf(unit, 'spec.md')
  const plan = statusOf(unit, 'plan.md')
  const need = []

  const wantIntent = () => {
    if (intent === null) need.push(missing(unit, 'intent.md'))
    else if (intent !== 'accepted') need.push(`intent.md is "${intent}", not accepted`)
  }
  const wantSpec = () => {
    if (spec === null) need.push(`${missing(unit, 'spec.md')} — write it, or record the skip in it`)
    else if (!settled(spec)) need.push(`spec.md is "${spec}", not accepted or skipped`)
  }

  if (stage === 'spec') wantIntent()
  else if (stage === 'plan') { wantIntent(); wantSpec() }
  else if (stage === 'implement') {
    wantIntent(); wantSpec()
    if (plan === null) need.push(missing(unit, 'plan.md'))
    else if (plan !== 'accepted') need.push(`plan.md is "${plan}", not accepted`)
  } else return { ok: false, need: [`unknown stage "${stage}" — use spec, plan or implement`] }

  return { ok: need.length === 0, need }
}

export function nextNumber(units) {
  const max = units.reduce((n, u) => (u.number > n ? u.number : n), 0)
  return String(max + 1).padStart(4, '0')
}

// --- commands ----------------------------------------------------------------

const dash = '—'
const cell = (u, f) => u.artifacts[f]?.status ?? dash

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

  console.log('| Unit | Intent | Spec | Plan | Next action |')
  console.log('|---|---|---|---|---|')
  for (const u of rows) {
    console.log(`| ${u.name} | ${cell(u, 'intent.md')} | ${cell(u, 'spec.md')} | ${cell(u, 'plan.md')} | ${u.next.action} |`)
  }

  const problems = rows.flatMap((u) => u.problems.map((p) => `${u.name}: ${p}`))
  if (problems.length) {
    console.log('\nProblems (report these, do not infer past them):')
    for (const p of problems) console.log(`  - ${p}`)
  }
  return 0
}

function cmdGate(unitName, stage) {
  if (!unitName || !stage) {
    console.error('usage: cos.mjs gate <NNNN_slug> <spec|plan|implement>')
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
