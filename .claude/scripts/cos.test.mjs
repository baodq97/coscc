import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, readdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { spawn, spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'
import {
  parseStatus, parseSkipReason, checkGate, nextAction, nextNumber, readUnit, STAGE_NAMES,
  BRANCH_TYPES, branchProblem, tagProblem, isPrerelease, tagVersion, versionProblem, parseType,
  unitBranch, VERSION_SOURCE, parseQuestions, parseAnswers, parsePr, parseReview, REVIEW_ROUNDS,
  reviewRounds,
} from './cos.mjs'

const unit = (artifacts) => ({ name: '0001_x', artifacts, problems: [] })
const art = (status) => ({ status, skipReason: null })

// A probe that answers the way git and gh would, without either. `checks` is what
// `gh pr checks --json name,bucket` prints; `git` maps an argument string to an answer.
const ok = (out = '') => ({ code: 0, out, err: '' })
// `view` is what `gh pr view --json state,headRefOid` prints; `null` means the head is the
// reviewed commit (`SHA`, declared further down), so the diff to it is empty.
const greenProbe = (checks = [{ name: 'tests', bucket: 'pass' }], git = {}, view = null) => ({
  gh: (...args) =>
    args[1] === 'view'
      ? ok(JSON.stringify(view ?? { state: 'OPEN', headRefOid: SHA }))
      : { code: 0, out: JSON.stringify(checks), err: '' },
  git: (...args) => git[args.join(' ')] ?? ok(),
})

test('parseStatus takes the first Status line, whatever prose surrounds it', () => {
  assert.equal(parseStatus('Author: X. Status: draft.'), 'draft')
  assert.equal(parseStatus('Intent: intent.md. Spec: skipped (tiny). Status: accepted.'), 'accepted')
  assert.equal(parseStatus('Status: Accepted.'), 'accepted')
})

test('a later Status mention in the body cannot shadow the real one', () => {
  assert.equal(parseStatus('Status: draft.\n\n## Notes\nStatus: accepted is what we want.'), 'draft')
})

test('parseStatus returns null rather than guessing', () => {
  assert.equal(parseStatus('# Intent: x\nNo status here.'), null)
})

test('parseSkipReason reads the reason out of the plan header', () => {
  assert.equal(parseSkipReason('Spec: skipped (one file, no schema change).'), 'one file, no schema change')
  assert.equal(parseSkipReason('Spec: spec.md.'), null)
})

test('spec gate needs an accepted intent', () => {
  assert.equal(checkGate(unit({ 'intent.md': art('accepted') }), 'spec').ok, true)
  assert.equal(checkGate(unit({ 'intent.md': art('draft') }), 'spec').ok, false)
  assert.equal(checkGate(unit({}), 'spec').ok, false)
})

test('plan gate accepts a skipped spec but not a missing one', () => {
  const base = { 'intent.md': art('accepted') }
  assert.equal(checkGate(unit({ ...base, 'spec.md': art('skipped') }), 'plan').ok, true)
  assert.equal(checkGate(unit({ ...base, 'spec.md': art('accepted') }), 'plan').ok, true)
  assert.equal(checkGate(unit({ ...base, 'spec.md': art('draft') }), 'plan').ok, false)
  assert.equal(checkGate(unit(base), 'plan').ok, false)
})

test('implement gate needs the whole chain accepted', () => {
  const full = { 'intent.md': art('accepted'), 'spec.md': art('accepted'), 'plan.md': art('accepted') }
  assert.equal(checkGate(unit(full), 'implement').ok, true)
  assert.equal(checkGate(unit({ ...full, 'plan.md': art('draft') }), 'implement').ok, false)
  assert.equal(checkGate(unit({ ...full, 'intent.md': art('draft') }), 'implement').ok, false)
})

test('a blocked gate says what is missing', () => {
  const { need } = checkGate(unit({ 'intent.md': art('draft') }), 'plan')
  assert.equal(need.length, 2)
  assert.match(need[0], /intent\.md is "draft"/)
  assert.match(need[1], /spec\.md does not exist/)
})

test('an unknown stage is refused, not treated as open', () => {
  assert.equal(checkGate(unit({ 'intent.md': art('accepted') }), 'deploy').ok, false)
})

test('nextAction names one action per state', () => {
  assert.match(nextAction(unit({ 'intent.md': art('draft') })).action, /accept intent/)
  assert.match(nextAction(unit({ 'intent.md': art('accepted') })).action, /write-spec/)
  assert.match(
    nextAction(unit({ 'intent.md': art('accepted'), 'spec.md': art('skipped') })).action,
    /write-plan/,
  )
  assert.match(
    nextAction(unit({ 'intent.md': art('accepted'), 'spec.md': art('accepted'), 'plan.md': art('accepted') })).action,
    /implementation starts/,
  )
})

test('a rejected artifact closes the unit instead of blocking it', () => {
  const r = nextAction(unit({ 'intent.md': art('rejected') }))
  assert.equal(r.blocked, false)
  assert.match(r.action, /closed/)
})

test('a done plan is finished, not awaiting anything', () => {
  const full = { 'intent.md': art('accepted'), 'spec.md': art('accepted'), 'plan.md': art('done') }
  assert.equal(nextAction(unit(full)).action, 'finished')
})

test('nextNumber counts from the highest, not from the count', () => {
  assert.equal(nextNumber([]), '0001')
  assert.equal(nextNumber([{ number: 1 }, { number: 2 }]), '0003')
  assert.equal(nextNumber([{ number: 7 }]), '0008')
  assert.equal(nextNumber([{ number: 3 }, { number: 1 }]), '0004')
})

test('nextNumber survives a directory that failed to parse', () => {
  assert.equal(nextNumber([{ number: 2 }, { number: undefined }]), '0003')
})

test('an unreadable artifact is distinguished from a missing one', () => {
  const broken = unit({ 'intent.md': { status: null, skipReason: null } })
  assert.match(checkGate(broken, 'spec').need[0], /exists but carries no Status line/)
  assert.match(nextAction(broken).action, /fix intent\.md/)

  const absent = unit({})
  assert.match(checkGate(absent, 'spec').need[0], /does not exist/)
  assert.match(nextAction(absent).action, /write-intent/)
})

// --- eight stages ------------------------------------------------------------

test('every stage name opens a gate, and a ninth does not', () => {
  assert.deepEqual(STAGE_NAMES, ['idea', 'intent', 'spec', 'plan', 'impl', 'pr', 'review', 'ship'])
  for (const name of STAGE_NAMES) {
    assert.doesNotMatch(checkGate(unit({}), name).need.join(' '), /unknown stage/, `${name} should be a known stage`)
  }
  assert.match(checkGate(unit({}), 'deploy').need[0], /unknown stage/)
})

test('implement still names the impl stage, because a skill still says it', () => {
  const full = { 'intent.md': art('accepted'), 'spec.md': art('accepted'), 'plan.md': art('accepted') }
  assert.equal(checkGate(unit(full), 'implement').ok, true)
  assert.deepEqual(checkGate(unit(full), 'implement'), checkGate(unit(full), 'impl'))
})

test('idea gates nothing — every unit on disk was opened without one', () => {
  assert.equal(checkGate(unit({}), 'idea').ok, true)
  assert.equal(checkGate(unit({}), 'intent').ok, true)
  // and it never appears as the next action, because a missing idea is not a gap
  assert.match(nextAction(unit({})).action, /write-intent/)
})

test('a later stage needs the earlier ones behind it', () => {
  const upToPlan = { 'intent.md': art('accepted'), 'spec.md': art('accepted'), 'plan.md': art('accepted') }
  assert.equal(checkGate(unit(upToPlan), 'pr').ok, false)
  assert.match(checkGate(unit(upToPlan), 'pr').need[0], /impl\.md does not exist/)

  const withImpl = { ...upToPlan, 'impl.md': art('accepted') }
  assert.equal(checkGate(unit(withImpl), 'pr').ok, true)
  // Since `0015` the review gate also needs an open pull request with green checks, so
  // the chain alone is no longer enough; the chain is still necessary.
  const pr = { ...art('accepted'), pr: { url: 'https://github.com/o/r/pull/7', number: 7 } }
  assert.equal(checkGate(unit({ ...withImpl, 'pr.md': pr }), 'review', { probe: greenProbe() }).ok, true)
  assert.equal(checkGate(unit({ ...upToPlan, 'pr.md': pr }), 'review', { probe: greenProbe() }).ok, false)
})

test('a done artifact is behind us, not in the way', () => {
  // `done` is strictly further along than `accepted`, so it must not block what follows.
  const done = { 'intent.md': art('accepted'), 'spec.md': art('accepted'), 'plan.md': art('done') }
  assert.equal(checkGate(unit(done), 'impl').ok, true)
})

test('nextAction walks past an accepted plan into the new stages', () => {
  const base = { 'intent.md': art('accepted'), 'spec.md': art('accepted'), 'plan.md': art('accepted') }
  assert.match(nextAction(unit({ ...base, 'impl.md': art('accepted') })).action, /write-pr/)
  assert.match(
    nextAction(unit({ ...base, 'impl.md': art('accepted'), 'pr.md': art('accepted') })).action,
    /write-review/,
  )
  const shipped = { ...base, 'impl.md': art('accepted'), 'pr.md': art('accepted'), 'review.md': art('accepted'), 'ship.md': art('accepted') }
  assert.equal(nextAction(unit(shipped)).action, 'finished')
})

test('a rejection in a late stage closes the unit, same as an early one', () => {
  const base = { 'intent.md': art('accepted'), 'spec.md': art('accepted'), 'plan.md': art('accepted') }
  const r = nextAction(unit({ ...base, 'impl.md': art('rejected') }))
  assert.equal(r.blocked, false)
  assert.match(r.action, /closed — impl rejected/)
})

test('the five new artifacts are read, not reported as unexpected files', () => {
  const dir = mkdtempSync(join(tmpdir(), 'cos-stages-'))
  for (const f of ['idea.md', 'intent.md', 'spec.md', 'plan.md', 'impl.md', 'pr.md', 'review.md', 'ship.md']) {
    writeFileSync(join(dir, f), f === 'intent.md' ? 'Type: feat. Status: accepted.\n' : 'Status: accepted.\n')
  }
  const u = readUnit(dir, '0009_widened')
  assert.deepEqual(u.problems, [])
  assert.equal(Object.keys(u.artifacts).length, 8)

  writeFileSync(join(dir, 'notes.md'), 'x')
  assert.match(readUnit(dir, '0009_widened').problems[0], /unexpected file\(s\): notes\.md/)
})

// --- the Type header ---------------------------------------------------------

// `Type:` decides the branch name, so a unit that never declares one has no branch the
// convention accepts. Before this, both shapes below read as a clean unit.
const withIntent = (header) => {
  const dir = mkdtempSync(join(tmpdir(), 'cos-type-'))
  writeFileSync(join(dir, 'intent.md'), `# Intent: x\n${header}\n`)
  return readUnit(dir, '0011_typed')
}

test('a unit whose intent declares no Type is a problem, and the message says which', () => {
  const u = withIntent('Author: Bao Do. Status: accepted.')
  assert.equal(u.problems.length, 1)
  assert.match(u.problems[0], /intent\.md declares no Type/)
  assert.equal(u.type, undefined)
})

test('a Type outside the ten is a different problem from a missing one', () => {
  const u = withIntent('Author: Bao Do. Type: nonsense. Status: accepted.')
  assert.equal(u.problems.length, 1)
  assert.match(u.problems[0], /has type "nonsense", not one of/)
  for (const t of BRANCH_TYPES) assert.ok(u.problems[0].includes(t), `message omits ${t}`)
})

test('a Type inside the ten is recorded on the unit and reports nothing', () => {
  for (const t of BRANCH_TYPES) {
    const u = withIntent(`Author: Bao Do. Type: ${t}. Status: accepted.`)
    assert.deepEqual(u.problems, [], `${t} was reported`)
    assert.equal(u.type, t)
  }
})

test('a missing intent.md reports that, and not a missing Type on top of it', () => {
  const dir = mkdtempSync(join(tmpdir(), 'cos-type-'))
  const u = readUnit(dir, '0011_typed')
  assert.deepEqual(u.problems, ['no intent.md — every unit opens with one'])
})

// --- pre-intent: not started is not the same as broken -----------------------

const unitDir = (files) => {
  const dir = mkdtempSync(join(tmpdir(), 'cos-phase-'))
  for (const [name, text] of Object.entries(files)) writeFileSync(join(dir, name), text)
  return dir
}

test('a unit holding only a valid idea is pre-intent and reports nothing', () => {
  const u = readUnit(unitDir({ 'idea.md': '# Idea: x\nStatus: accepted.\n' }), '0015_fresh')
  assert.equal(u.phase, 'pre-intent')
  assert.deepEqual(u.problems, [])
  // Still blocked on its intent, because it is: the phase changes the lane, not the gate.
  assert.deepEqual(nextAction(u), { blocked: true, action: 'write-intent — the unit has no intent.md' })
  assert.equal(checkGate(u, 'spec').ok, false)
})

test('an idea with a later artifact and no intent is still a problem', () => {
  const u = readUnit(unitDir({ 'idea.md': 'Status: accepted.\n', 'spec.md': 'Status: draft.\n' }), '0015_x')
  assert.equal(u.phase, 'started')
  assert.ok(u.problems.includes('no intent.md — every unit opens with one'))
})

test('an idea with no Status line is not pre-intent', () => {
  const u = readUnit(unitDir({ 'idea.md': '# Idea: x\n' }), '0015_x')
  assert.equal(u.phase, 'started')
  assert.ok(u.problems.includes('no intent.md — every unit opens with one'))
  assert.ok(u.problems.includes('idea.md carries no Status line'))
})

test('a unit with an intent is started', () => {
  assert.equal(readUnit(unitDir({ 'intent.md': 'Type: fix. Status: draft.\n' }), '0015_x').phase, 'started')
})

test('reporting a Type problem does not close any gate', () => {
  // Deliberate, and the reason the eight older units could be backfilled at leisure rather
  // than under a red board: `checkGate` never reads `Type:`. `.cos/0010_.../spec.md` R9.
  const u = { ...unit({ 'intent.md': art('accepted') }), problems: ['intent.md declares no Type'] }
  assert.deepEqual(checkGate(u, 'spec'), { ok: true, need: [] })
})

// --- the branch grammar ------------------------------------------------------

// The eight rows of `.cos/0009_branch-and-release-conventions/spec.md` R1, copied one for
// one. Six of them are rejections, because a grammar that only ever sees valid input is a
// function that returns true.
const BRANCH_ROWS = [
  ['feat/branch-conventions', null],
  ['fix/version-drift', null],
  ['main', /trunk/],
  ['feature/foo', /not one of/],
  ['feat/Foo', /lowercase/],
  ['feat/', /empty/],
  ['feat/a--b', /single hyphens/],
  ['feat/foo/bar', /second slash/],
]

for (const [name, want] of BRANCH_ROWS) {
  test(`branchProblem: ${name || '(empty)'}`, () => {
    const got = branchProblem(name)
    if (want === null) assert.equal(got, null, `expected ${name} to be accepted, got: ${got}`)
    else assert.match(got ?? '', want)
  })
}

test('the type set is the ten of Conventional Commits, and it is closed', () => {
  assert.deepEqual(BRANCH_TYPES,
    ['feat', 'fix', 'docs', 'refactor', 'test', 'chore', 'perf', 'build', 'ci', 'revert'])
  for (const t of BRANCH_TYPES) assert.equal(branchProblem(`${t}/a-slug`), null)
  assert.match(branchProblem('feature/x') ?? '', /"feature" is not one of/)
})

test('a slug over sixty characters is refused, and the message says how long it was', () => {
  assert.equal(branchProblem(`feat/${'a'.repeat(60)}`), null)
  assert.match(branchProblem(`feat/${'a'.repeat(61)}`) ?? '', /61 characters, over the 60/)
})

test('a name with no slash is told what shape to take', () => {
  assert.match(branchProblem('justaname') ?? '', /expected <type>\/<slug>/)
  assert.match(branchProblem('') ?? '', /no branch name/)
})

// --- the tag grammar ---------------------------------------------------------

const TAG_ROWS = [
  ['v0.1.0', null],
  ['v1.20.3', null],
  ['v0.1.0-rc.1', null],
  ['v0.1.0-rc.12', null],
  ['0.1.0', /expected vX\.Y\.Z/],
  ['v0.1', /expected vX\.Y\.Z/],
  ['v0.1.0-rc', /expected vX\.Y\.Z/],
  ['v0.1.0-rc.0', /starts at 1/],
]

for (const [name, want] of TAG_ROWS) {
  test(`tagProblem: ${name}`, () => {
    const got = tagProblem(name)
    if (want === null) assert.equal(got, null, `expected ${name} to be accepted, got: ${got}`)
    else assert.match(got ?? '', want)
  })
}

test('a leading zero is refused so one release has one spelling', () => {
  assert.match(tagProblem('v0.01.0') ?? '', /leading zero/)
  assert.match(tagProblem('v0.1.0-rc.01') ?? '', /starts at 1/)
})

test('prerelease is decided by the same grammar that validates the tag', () => {
  assert.equal(isPrerelease('v0.1.0-rc.1'), true)
  assert.equal(isPrerelease('v0.1.0'), false)
  // Not a tag at all, so not a prerelease either — one implementation, one answer.
  assert.equal(isPrerelease('release-rc.1'), false)
})

test('tagVersion strips the v and the candidate suffix, or refuses', () => {
  assert.equal(tagVersion('v0.1.0-rc.3'), '0.1.0')
  assert.equal(tagVersion('v1.20.3'), '1.20.3')
  assert.equal(tagVersion('v0.1'), null)
})

// --- version drift -----------------------------------------------------------

test('pyproject.toml is the source, and the message says which one is right', () => {
  assert.equal(VERSION_SOURCE, 'pyproject.toml')
  const same = { 'pyproject.toml': '0.1.0', 'package.json': '0.1.0' }
  assert.equal(versionProblem(same), null)
  const off = versionProblem({ 'pyproject.toml': '0.1.0', 'package.json': '0.0.1' })
  assert.match(off ?? '', /pyproject\.toml says 0\.1\.0, but package\.json is 0\.0\.1/)
})

test('a place that could not be read is named, not skipped', () => {
  assert.match(versionProblem({ 'pyproject.toml': '0.1.0', 'uv.lock': null }) ?? '', /uv\.lock is unreadable/)
  assert.match(versionProblem({ 'pyproject.toml': null }) ?? '', /declares no version/)
})

// --- a unit knows its type ---------------------------------------------------

test('parseType reads the header field beside Author and Status', () => {
  assert.equal(parseType('Author: X. Type: feat. Status: accepted.'), 'feat')
  assert.equal(parseType('Author: X. Type: Feat. Status: accepted.'), 'feat')
  assert.equal(parseType('Author: X. Status: accepted.'), null)
})

test('the branch name is derived from the unit and its type', () => {
  const header = 'Author: X. Type: feat. Status: accepted.'
  assert.deepEqual(unitBranch('0009_branch-and-release-conventions', header),
    { branch: 'feat/branch-and-release-conventions' })
  assert.equal(branchProblem(unitBranch('0009_branch-and-release-conventions', header).branch), null)
})

test('a derived name that no grammar would accept cannot be produced', () => {
  // The slug comes out of UNIT_RE, which is already lowercase-and-single-hyphens, so the
  // only way to get an invalid branch is an invalid type — and that is refused here.
  assert.match(unitBranch('0009_x', 'Type: feature.').error, /"feature" is not one of/)
  assert.match(unitBranch('0009_x', 'Author: X.').error, /declares no Type:/)
  assert.match(unitBranch('9_x', 'Type: feat.').error, /does not match NNNN_<slug>/)
  assert.match(unitBranch(undefined, 'Type: feat.').error, /does not match NNNN_<slug>/)
})

// --- the --root boundary, exercised through the CLI --------------------------

// Every other test in this file imports a pure function. These four have to spawn the
// script, because what they check is the dispatcher: `--root` is stripped from argv before
// the command is read (see the bottom of `cos.mjs`), so "the command never touches cosDir"
// is not the same claim as "the flag was refused". The risk register of
// `.cos/0009_branch-and-release-conventions/plan.md` says a guard with no test is a
// sentence in a document, and this is the guard.
const cli = (...args) =>
  spawnSync(process.execPath, [fileURLToPath(new URL('./cos.mjs', import.meta.url)), ...args], { encoding: 'utf8' })

for (const args of [['check-branch', 'feat/x'], ['check-tag', 'v0.1.0'], ['check-version']]) {
  test(`--root is refused by ${args[0]}`, () => {
    const out = cli('--root', tmpdir(), ...args)
    assert.equal(out.status, 2)
    assert.match(out.stderr, /--root does not apply/)
    // Without the flag the same call is fine, so the refusal is about --root and not
    // about the command being broken.
    assert.notEqual(cli(...args).status, 2)
  })
}

test('--root still reaches the command that reads a .cos/', () => {
  const out = cli('--root', process.cwd(), 'unit-branch', '0009_branch-and-release-conventions')
  assert.equal(out.status, 0)
  assert.equal(out.stdout.trim(), 'feat/branch-and-release-conventions')
})

test('an unknown command prints both halves of the boundary', () => {
  const out = cli('nonsense')
  assert.equal(out.status, 2)
  assert.match(out.stderr, /these take --root/)
  assert.match(out.stderr, /these do not/)
})

// --- new-path --reserve-from --------------------------------------------------

// A directory with a `.cos/` holding the named units.
const cosTree = (...names) => {
  const dir = mkdtempSync(join(tmpdir(), 'cos-reserve-'))
  mkdirSync(join(dir, '.cos'))
  for (const n of names) mkdirSync(join(dir, '.cos', n))
  return dir
}

test('new-path counts the numbers taken in a reserved directory', () => {
  const root = mkdtempSync(join(tmpdir(), 'cos-root-'))
  const host = cosTree('0001_a', '0014_n')
  const out = cli('--root', root, '--reserve-from', host, 'new-path', 'x')
  assert.equal(out.status, 0, out.stderr)
  assert.equal(out.stdout.trim(), '.cos/0015_x')
})

test('new-path takes the highest of root and reserve, and still writes nothing', () => {
  const root = cosTree('0003_c')
  const host = cosTree('0014_n')
  const out = cli('--root', root, 'new-path', 'x', '--reserve-from', host)
  assert.equal(out.stdout.trim(), '.cos/0015_x')
  assert.deepEqual(readdirSync(join(root, '.cos')), ['0003_c'])
  assert.deepEqual(readdirSync(join(host, '.cos')), ['0014_n'])
})

test('a reserved directory with no .cos/ contributes nothing', () => {
  const root = cosTree('0003_c')
  const out = cli('--root', root, '--reserve-from', mkdtempSync(join(tmpdir(), 'cos-bare-')), 'new-path', 'x')
  assert.equal(out.stdout.trim(), '.cos/0004_x')
})

test('--reserve-from is repeatable', () => {
  const out = cli('--root', cosTree(), '--reserve-from', cosTree('0002_b'), '--reserve-from', cosTree('0009_i'), 'new-path', 'x')
  assert.equal(out.stdout.trim(), '.cos/0010_x')
})

for (const args of [['status'], ['gate', '0001_a', 'spec'], ['unit-branch', '0001_a']]) {
  test(`--reserve-from is refused by ${args[0]}`, () => {
    const out = cli('--root', cosTree(), '--reserve-from', cosTree('0001_a'), ...args)
    assert.equal(out.status, 2)
    assert.match(out.stderr, /--reserve-from applies only to/)
  })
}

test('--reserve-from with no directory is misuse', () => {
  const out = cli('new-path', 'x', '--reserve-from')
  assert.equal(out.status, 2)
  assert.match(out.stderr, /needs a directory/)
})

// --- 0016: the numbered items under `## Open questions`, and answers to them ---------

const QUESTIONS = [
  '# Intent: q',
  'Author: t. Type: feat. Status: accepted.',
  '',
  '## Problem',
  '',
  '1. Not a question: this list is under Problem.',
  '',
  '## Open questions',
  '',
  '1. **First?** Asked here',
  '   and continued on this line.',
  '2. Second?',
  '3. Third?',
  '',
].join('\n')

const answerBlock = (n, by, text) =>
  `\n### Câu ${n}\nAnswered by: ${by}. Date: 2026-09-23. Via: product.\n\n${text}\n`
const withAnswers = (...blocks) => `${QUESTIONS}\n## Answers\n${blocks.join('')}`

function questionTree(files) {
  const root = mkdtempSync(join(tmpdir(), 'cos-questions-'))
  const dir = join(root, '.cos', '0001_q')
  mkdirSync(dir, { recursive: true })
  for (const [name, text] of Object.entries(files)) writeFileSync(join(dir, name), text)
  return { root, u: readUnit(dir, '0001_q') }
}

test('questions are the numbered items under Open questions and nowhere else', () => {
  const qs = parseQuestions(QUESTIONS)
  assert.deepEqual(qs.map((q) => q.n), [1, 2, 3])
  assert.match(qs[0].text, /continued on this line/)
})

test('a bullet list is numbered by position, but only when nothing is numbered', () => {
  const bullets = '## Open questions\n\n- **One?** first\n  still one\n- Two?\n\n## Next\n- not a question\n'
  assert.deepEqual(parseQuestions(bullets).map((q) => q.n), [1, 2])
  assert.match(parseQuestions(bullets)[0].text, /still one/)
  const mixed = '## Open questions\n\n1. Numbered.\n- a sub-point of it\n2. Also numbered.\n'
  assert.deepEqual(parseQuestions(mixed).map((q) => q.n), [1, 2])
  assert.match(parseQuestions(mixed)[0].text, /sub-point/)
})

test('no Open questions section is not the same as an empty one', () => {
  assert.equal(parseQuestions('# x\nStatus: draft.\n'), null)
  assert.deepEqual(parseQuestions('# x\n## Open questions\n\nNone.\n'), [])
})

test('three questions and no answers is three open', () => {
  const { u } = questionTree({ 'intent.md': QUESTIONS })
  assert.equal(u.open, 3)
  assert.equal(u.questions.length, 3)
  assert.deepEqual(u.problems, [])
})

test('one answer leaves two open, and carries who gave it', () => {
  const { u } = questionTree({ 'intent.md': withAnswers(answerBlock(2, 'Phong Pham', 'Tách ra.')) })
  assert.equal(u.open, 2)
  const q2 = u.artifacts['intent.md'].questions.find((q) => q.n === 2)
  assert.equal(q2.answered, true)
  assert.equal(q2.answer.by, 'Phong Pham')
  assert.equal(q2.answer.via, 'product')
  assert.equal(q2.answer.text, 'Tách ra.')
})

test('two blocks for one number: the last is the one in force', () => {
  const text = withAnswers(answerBlock(1, 'A', 'cũ'), answerBlock(1, 'B. C', 'mới'))
  assert.equal(parseAnswers(text).length, 2)
  const { u } = questionTree({ 'intent.md': text })
  const q1 = u.artifacts['intent.md'].questions.find((q) => q.n === 1)
  assert.equal(q1.answer.text, 'mới')
  assert.equal(q1.answer.by, 'B. C')
  assert.equal(u.open, 2)
})

test('a block with no header line is not an answer', () => {
  assert.deepEqual(parseAnswers(`${QUESTIONS}\n## Answers\n\n### Câu 1\nno header here\n`), [])
})

test('Answers is never read as questions, and the Status line survives it', () => {
  const text = withAnswers(answerBlock(3, 'A', '1. looks like a question'))
  assert.deepEqual(parseQuestions(text).map((q) => q.n), [1, 2, 3])
  assert.equal(parseStatus(text), 'accepted')
  assert.equal(parseStatus(QUESTIONS), parseStatus(text))
})

test('only the latest artifact with questions is counted, but every question is listed', () => {
  const spec = '# Spec\nIntent: intent.md. Author: t. Status: accepted.\n\n## Open questions\n\n1. Only one.\n'
  const { u } = questionTree({ 'intent.md': QUESTIONS, 'spec.md': spec })
  assert.equal(u.open, 1)
  assert.equal(u.counted, 'spec.md')
  assert.deepEqual(u.questions.map((q) => q.artifact), ['intent.md', 'intent.md', 'intent.md', 'spec.md'])
  assert.deepEqual(u.questions.map((q) => q.counted), [false, false, false, true])
})

test('a unit with no Open questions anywhere has nothing open', () => {
  const { u } = questionTree({ 'intent.md': '# I\nAuthor: t. Type: feat. Status: accepted.\n' })
  assert.equal(u.open, 0)
  assert.deepEqual(u.questions, [])
})

test('an open question does not close a gate', () => {
  const { u } = questionTree({ 'intent.md': QUESTIONS })
  assert.equal(u.open, 3)
  assert.equal(checkGate(u, 'spec').ok, true)
})

// Read asynchronously, the way `coscc/board.py` reads it. `spawnSync` drains the pipe as
// fast as it fills and never saw the truncation this guards against.
test('status --json over 64 KiB reaches a pipe whole', async () => {
  const long = `${QUESTIONS}4. ${'x'.repeat(200 * 1024)}\n`
  const { root } = questionTree({ 'intent.md': long })
  const script = fileURLToPath(new URL('./cos.mjs', import.meta.url))
  const child = spawn(process.execPath, [script, '--root', root, 'status', '--json'])
  let text = ''
  child.stdout.setEncoding('utf8')
  for await (const chunk of child.stdout) {
    text += chunk
    await new Promise((r) => setTimeout(r, 5))
  }
  assert.ok(text.length > 200 * 1024, `got ${text.length} characters`)
  assert.equal(JSON.parse(text).units[0].open, 4)
})

test('status --json carries questions and open for each unit', () => {
  const { root } = questionTree({ 'intent.md': withAnswers(answerBlock(1, 'A', 'x')) })
  const out = cli('--root', root, 'status', '--json')
  assert.equal(out.status, 0, out.stderr)
  const got = JSON.parse(out.stdout).units[0]
  assert.equal(got.open, 2)
  assert.equal(got.questions.length, 3)
})

// --- review before merge (0015) -----------------------------------------------

const SHA = 'a'.repeat(40)
const FIX = 'b'.repeat(40)
const PR = { ...art('accepted'), pr: { url: 'https://github.com/o/r/pull/7', number: 7 } }
const CHAIN = {
  'intent.md': art('accepted'), 'spec.md': art('accepted'), 'plan.md': art('accepted'),
  'impl.md': art('accepted'), 'pr.md': PR,
}
const reviewArt = (status, text) => ({ ...art(status), review: parseReview(text) })
const round = (n, verdict, findings = []) =>
  `## Round ${n}\n\nReviewed: ${SHA}. Verdict: ${verdict}.\n\n### Findings\n\n${findings.join('\n')}\n\n### What was not reviewed\n\nnothing\n`
const branched = (artifacts) => ({ ...unit(artifacts), name: '0001_x', branch: 'feat/x' })

test('parseStatus reads a hyphenated status whole', () => {
  assert.equal(parseStatus('Author: X. Status: changes-requested.'), 'changes-requested')
  assert.equal(parseStatus('Status: accepted-.'), 'accepted')
})

test('parsePr reads the pull request from the header, or returns null', () => {
  assert.deepEqual(parsePr('# PR\nPR: https://github.com/o/r/pull/42. Status: accepted.'), {
    url: 'https://github.com/o/r/pull/42', number: 42,
  })
  assert.equal(parsePr('# PR\nStatus: accepted.'), null)
})

test('parseReview reads rounds, verdicts and labelled findings, and stops at Answers', () => {
  const text = `# Review\nStatus: accepted.\n\n${round(1, 'changes-requested', ['- F1 [open] a', '- F2 [open] b'])}\n${round(2, 'pass', [`- F1 [fixed ${FIX}] a`, '- F2 [maybe] b'])}\n## Answers\n\n## Round 3\n`
  const { rounds } = parseReview(text)
  assert.deepEqual(rounds.map((r) => [r.n, r.verdict, r.reviewed]), [[1, 'changes-requested', SHA], [2, 'pass', SHA]])
  assert.deepEqual(rounds[1].findings.map((f) => [f.id, f.label, f.fixedBy]), [['F1', 'fixed', FIX], ['F2', 'unreadable', null]])
  assert.deepEqual(parseReview('# Review written before rounds\nStatus: accepted.\n').rounds, [])
})

test('R1: the review gate is closed while pr.md names no pull request', () => {
  const g = checkGate(unit({ ...CHAIN, 'pr.md': art('accepted') }), 'review', { probe: greenProbe() })
  assert.equal(g.ok, false)
  assert.match(g.need[0], /pr\.md names no pull request/)
})

test('R2: changes-requested and rejected lead to different places', () => {
  const asked = nextAction(unit({ ...CHAIN, 'review.md': reviewArt('changes-requested', round(1, 'changes-requested', ['- F1 [open] x'])) }))
  assert.equal(asked.blocked, true)
  assert.match(asked.action, /fix the open findings of review round 1 .* \(1 of 3 rounds used\)/)
  const rejected = nextAction(unit({ ...CHAIN, 'review.md': art('rejected') }))
  assert.equal(rejected.blocked, false)
  assert.match(rejected.action, /closed — review rejected/)
})

test('changes-requested closes the ship gate but leaves the review gate open', () => {
  const u = unit({ ...CHAIN, 'review.md': reviewArt('changes-requested', round(1, 'changes-requested', ['- F1 [open] x'])) })
  assert.equal(checkGate(u, 'review', { probe: greenProbe() }).ok, true)
  assert.match(checkGate(u, 'ship', { probe: greenProbe() }).need[0], /review\.md is "changes-requested"/)
})

test('CI decides whether review may begin', () => {
  const u = unit(CHAIN)
  const red = checkGate(u, 'review', { probe: greenProbe([{ name: 'tests', bucket: 'fail' }, { name: 'branch-name', bucket: 'pass' }]) })
  assert.equal(red.ok, false)
  assert.match(red.need[0], /CI is red on #7: tests — back to impl/)
  assert.match(checkGate(u, 'review', { probe: greenProbe([{ name: 'tests', bucket: 'pending' }]) }).need[0], /has not finished/)
  assert.match(checkGate(u, 'review', { probe: greenProbe([]) }).need[0], /no required checks/)
  const broken = { gh: () => ({ code: 1, out: '', err: 'HTTP 401' }), git: () => ok() }
  assert.match(checkGate(u, 'review', { probe: broken }).need[0], /HTTP 401/)
  assert.match(checkGate(u, 'review').need[0], /no repository given/)
  assert.equal(checkGate(u, 'review', { probe: greenProbe([{ name: 'a', bucket: 'pass' }, { name: 'b', bucket: 'skipping' }]) }).ok, true)
})

test('the round limit stops the loop and says it needs a person', () => {
  const text = [1, 2, 3].map((n) => round(n, 'changes-requested', ['- F1 [open] x'])).join('\n')
  const u = unit({ ...CHAIN, 'review.md': reviewArt('changes-requested', text) })
  assert.equal(REVIEW_ROUNDS, 3)
  assert.match(checkGate(u, 'review', { probe: greenProbe() }).need[0], /needs a person — review used 3 of 3 rounds/)
  assert.match(nextAction(u).action, /needs a person/)
  assert.equal(checkGate(u, 'review', { probe: greenProbe(), limit: 4 }).ok, true)
  assert.match(nextAction(u, 4).action, /3 of 4 rounds used/)
})

test('reviewRounds reads COS_REVIEW_ROUNDS and refuses what is not a positive integer', () => {
  assert.equal(reviewRounds({}), 3)
  assert.equal(reviewRounds({ COS_REVIEW_ROUNDS: '5' }), 5)
  for (const bad of ['0', '-1', 'two', '1.5']) {
    assert.throws(() => reviewRounds({ COS_REVIEW_ROUNDS: bad }), /COS_REVIEW_ROUNDS/)
  }
})

test('R3: an accepted review with a finding still open cannot ship, and names it', () => {
  const text = round(1, 'pass', ['- F1 [open] the thing'])
  const g = checkGate(branched({ ...CHAIN, 'review.md': reviewArt('accepted', text) }), 'ship', { probe: greenProbe() })
  assert.equal(g.ok, false)
  assert.match(g.need.join('\n'), /F1 \[open\]/)
})

test('R4: an earlier round survives, a dropped finding or a renumbered round is caught', () => {
  const good = `${round(1, 'changes-requested', ['- F1 [open] x'])}\n${round(2, 'pass', [`- F1 [fixed ${FIX}] x`])}`
  assert.equal(checkGate(branched({ ...CHAIN, 'review.md': reviewArt('accepted', good) }), 'ship', { probe: greenProbe() }).ok, true)

  const dropped = `${round(1, 'changes-requested', ['- F1 [open] x'])}\n${round(2, 'pass')}`
  assert.match(checkGate(branched({ ...CHAIN, 'review.md': reviewArt('accepted', dropped) }), 'ship', { probe: greenProbe() }).need.join('\n'), /drops findings .*F1/)

  const gap = `${round(1, 'changes-requested', ['- F1 [open] x'])}\n${round(3, 'pass', [`- F1 [fixed ${FIX}] x`])}`
  assert.match(checkGate(branched({ ...CHAIN, 'review.md': reviewArt('accepted', gap) }), 'ship', { probe: greenProbe() }).need.join('\n'), /numbered 1, 3/)
})

test('R5: code after the reviewed commit closes the ship gate; the unit\'s own files do not', () => {
  const u = branched({ ...CHAIN, 'review.md': reviewArt('accepted', round(1, 'pass')) })
  const diff = (files) => greenProbe(undefined, { [`diff --name-only ${SHA}..refs/heads/feat/x`]: ok(files.join('\n')), [`diff --name-only ${SHA}..refs/remotes/origin/feat/x`]: ok(files.join('\n')) })
  assert.equal(checkGate(u, 'ship', { probe: diff(['.cos/0001_x/review.md']) }).ok, true)
  const g = checkGate(u, 'ship', { probe: diff(['.cos/0001_x/review.md', 'src/a.py']) })
  assert.equal(g.ok, false)
  assert.match(g.need[0], /changed after the reviewed commit .*src\/a\.py/)
  // A branch rewritten so the reviewed commit is gone from it.
  const rewritten = greenProbe(undefined, { [`merge-base --is-ancestor ${SHA} refs/heads/feat/x`]: { code: 1, out: '', err: '' } })
  assert.match(checkGate(u, 'ship', { probe: rewritten }).need[0], /not on refs\/heads\/feat\/x/)
  assert.match(checkGate(u, 'ship').need[0], /no repository given/)
})

test('F2: ship reads the pull request head, not only the refs here, and pins the merge to it', () => {
  const u = branched({ ...CHAIN, 'review.md': reviewArt('accepted', round(1, 'pass')) })
  const HEAD = 'c'.repeat(40)
  const open = { state: 'OPEN', headRefOid: HEAD }
  // Local and origin refs still say the reviewed commit; GitHub's head moved past it.
  const pushedElsewhere = greenProbe(undefined, { [`diff --name-only ${SHA}..${HEAD}`]: ok('src/a.py') }, open)
  const g = checkGate(u, 'ship', { probe: pushedElsewhere })
  assert.equal(g.ok, false)
  assert.match(g.need[0], new RegExp(`the head of #7 \\(${HEAD}\\) changed after the reviewed commit .*src/a\\.py`))

  // The head moved only by the unit's own files: open, and the gate names the head to merge.
  const good = checkGate(u, 'ship', { probe: greenProbe(undefined, { [`diff --name-only ${SHA}..${HEAD}`]: ok('.cos/0001_x/review.md') }, open) })
  assert.equal(good.ok, true)
  assert.equal(good.head, HEAD)

  const missing = greenProbe(undefined, { [`cat-file -e ${HEAD}^{commit}`]: { code: 1, out: '', err: '' } }, open)
  assert.match(checkGate(u, 'ship', { probe: missing }).need[0], /not in this repository — someone pushed from elsewhere: fetch/)
  assert.match(checkGate(u, 'ship', { probe: greenProbe(undefined, {}, { state: 'MERGED', headRefOid: SHA }) }).need[0], /#7 is MERGED, not open/)
  const offline = { gh: () => ({ code: 1, out: '', err: 'error connecting to api.github.com' }), git: () => ok() }
  assert.match(checkGate(u, 'ship', { probe: offline }).need[0], /cannot read the head of #7: error connecting/)
})

test('a review.md with no rounds cannot ship', () => {
  const u = branched({ ...CHAIN, 'review.md': reviewArt('accepted', '# Review\nStatus: accepted.\n') })
  assert.match(checkGate(u, 'ship', { probe: greenProbe() }).need[0], /no ## Round/)
})

test('--repo belongs to gate and nothing else', () => {
  const out = cli('--repo', tmpdir(), 'status')
  assert.equal(out.status, 2)
  assert.match(out.stderr, /--repo applies only to `gate`/)
})

test('a bad COS_REVIEW_ROUNDS is misuse, and names the variable', () => {
  const out = spawnSync(process.execPath, [fileURLToPath(new URL('./cos.mjs', import.meta.url)), 'status'], {
    encoding: 'utf8', env: { ...process.env, COS_REVIEW_ROUNDS: 'three' },
  })
  assert.equal(out.status, 2)
  assert.match(out.stderr, /COS_REVIEW_ROUNDS/)
})

test('with --root and no --repo, review says there is no repository instead of reading this one', () => {
  const { root } = questionTree({
    'intent.md': '# I\nAuthor: t. Type: feat. Status: accepted.\n',
    'spec.md': 'Status: accepted.\n', 'plan.md': 'Status: accepted.\n', 'impl.md': 'Status: accepted.\n',
    'pr.md': 'PR: https://github.com/o/r/pull/1. Status: accepted.\n',
  })
  const out = cli('--root', root, 'gate', '0001_q', 'review')
  assert.equal(out.status, 1)
  assert.match(out.stderr, /no repository given — pass --repo/)
})
