#!/usr/bin/env node
// Mechanical checks for the work-unit loop. Everything here is a fact about what is on
// disk: which artifacts exist, what status each carries, which gate that clears. Judgement
// — whether a spec should be skipped, whether a plan is good — stays with the skills.
import { readdirSync, readFileSync, existsSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
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
const CODE = { draft: 'd', accepted: 'A', rejected: 'x', skipped: 's', done: 'D' }
const cell = (u, f) => {
  const status = u.artifacts[f]?.status
  if (status === undefined) return dash
  return CODE[status] ?? '?'
}

function cmdStatus(json, cosDir) {
  const units = readAll(cosDir)
  const rows = units.map((u) => ({ ...u, next: nextAction(u) }))

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
  console.log(`\nA accepted · d draft · s skipped · D done · x rejected · ${dash} not started`)

  const problems = rows.flatMap((u) => u.problems.map((p) => `${u.name}: ${p}`))
  if (problems.length) {
    console.log('\nProblems (report these, do not infer past them):')
    for (const p of problems) console.log(`  - ${p}`)
  }
  return 0
}

function cmdGate(unitName, stage, cosDir) {
  if (!unitName || !stage) {
    console.error(`usage: cos.mjs gate <NNNN_slug> <${STAGE_NAMES.join('|')}>`)
    return 2
  }
  const dir = join(cosDir, unitName)
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

function cmdNewPath(slug, cosDir) {
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
  console.log(`.cos/${nextNumber(readAll(cosDir))}_${slug}`)
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
  const words = rootAt === -1 ? argv : argv.filter((_, i) => i !== rootAt && i !== rootAt + 1)
  const [cmd, ...rest] = words

  const run = {
    status: () => cmdStatus(rest.includes('--json'), cosDir),
    gate: () => cmdGate(rest[0], rest[1], cosDir),
    'new-path': () => cmdNewPath(rest[0], cosDir),
    'unit-branch': () => cmdUnitBranch(rest[0], cosDir),
    'check-branch': () => cmdCheckBranch(rest[0]),
    'check-tag': () => cmdCheckTag(rest[0]),
    'check-version': () => cmdCheckVersion(),
  }[cmd]

  if (!run) {
    console.error('usage: cos.mjs [--root <dir>] <command>')
    console.error('  reading a .cos/ (these take --root):')
    console.error('    status [--json] | gate <unit> <stage> | new-path <slug> | unit-branch <unit>')
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

  process.exit(run())
}
