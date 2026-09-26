import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { spawn, spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'
import {
  parseStatus, parseSkipReason, checkGate, nextAction, nextNumber, readUnit, STAGE_NAMES,
  BRANCH_TYPES, branchProblem, tagProblem, isPrerelease, tagVersion, versionProblem, parseType,
  unitBranch, VERSION_SOURCE, parseQuestions, parseAnswers, parsePr, parseReview, REVIEW_ROUNDS,
  reviewRounds, nextStep, parseNeedsPerson, betweenPrAndShip, parseDeadline, parseOutcome, unitOutcome,
  parseUnmeasured, parseSpike, SPIKE_ROUNDS, parseHold, HOLD_MOVES, nonBlocking, prText,
  UI_STANDARD, parseStandard, globMatch, uiFiles, screensProblems, makeProbe, stageAt,
  aboveAnswers, parseReruns, RERUNNABLE, screensAnswer, screensNeeds,
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

// --- nine stages -------------------------------------------------------------

test('every stage name opens a gate, and a tenth does not', () => {
  assert.deepEqual(STAGE_NAMES, ['idea', 'intent', 'spec', 'spike', 'plan', 'impl', 'pr', 'review', 'ship'])
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

test('the six new artifacts are read, not reported as unexpected files', () => {
  const dir = mkdtempSync(join(tmpdir(), 'cos-stages-'))
  for (const f of ['idea.md', 'intent.md', 'spec.md', 'spike.md', 'plan.md', 'impl.md', 'pr.md', 'review.md', 'ship.md']) {
    writeFileSync(join(dir, f), f === 'intent.md' ? 'Type: feat. Status: accepted.\n' : 'Status: accepted.\n')
  }
  const u = readUnit(dir, '0009_widened')
  assert.deepEqual(u.problems, [])
  assert.equal(Object.keys(u.artifacts).length, 9)

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
  // `stage` since `0024`: the same answer, in the form the run button acts on.
  assert.deepEqual(nextAction(u), { blocked: true, action: 'write-intent — the unit has no intent.md', stage: 'intent' })
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

// --- 0102: a slug longer than the branch allows -------------------------------

// A slug of exactly `n` characters, made of short words the way a real one is.
const slugOf = (n) => 'abcd-'.repeat(Math.ceil(n / 5)).slice(0, n).replace(/-$/, 'e')
const OLD_0044 = '0044_open-questions-wait-for-the-originator-even-when-precedent-answers-them'

test('every name unit-branch gives passes check-branch, whatever the slug length', () => {
  const slugs = [1, 59, 60, 61, 71].map(slugOf)
  // One word too long to cut at a hyphen, and a hyphen exactly where the cut falls.
  slugs.push('a'.repeat(71), 'a'.repeat(60) + '-b')
  for (const t of BRANCH_TYPES) {
    for (const slug of slugs) {
      const { branch } = unitBranch(`0001_${slug}`, `Type: ${t}.`)
      assert.equal(branchProblem(branch), null, `${t} with a ${slug.length}-character slug: ${branch}`)
    }
  }
})

test('a slug over the limit is cut at its last hyphen, and one within it is left alone', () => {
  assert.equal(unitBranch(OLD_0044, 'Type: feat.').branch,
    'feat/open-questions-wait-for-the-originator-even-when-precedent')
  assert.equal(slugOf(60).length, 60)
  assert.equal(unitBranch(`0001_${slugOf(60)}`, 'Type: feat.').branch, `feat/${slugOf(60)}`)
  assert.equal(unitBranch(`0001_${'a'.repeat(71)}`, 'Type: fix.').branch, `fix/${'a'.repeat(60)}`)
  assert.equal(unitBranch(`0001_${'a'.repeat(60)}-b`, 'Type: fix.').branch, `fix/${'a'.repeat(60)}`)
  assert.equal(unitBranch(OLD_0044, 'Type: feat.').branch, unitBranch(OLD_0044, 'Type: feat.').branch)
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

test('new-path refuses a slug longer than a branch allows, and makes nothing', () => {
  const root = cosTree('0003_c')
  const fits = cli('--root', root, 'new-path', 'a'.repeat(60))
  assert.equal(fits.status, 0, fits.stderr)
  assert.equal(fits.stdout.trim(), `.cos/0004_${'a'.repeat(60)}`)
  const over = cli('--root', root, 'new-path', 'a'.repeat(61))
  assert.equal(over.status, 2)
  assert.equal(over.stdout, '')
  assert.match(over.stderr, /61/)
  assert.match(over.stderr, /60/)
  assert.deepEqual(readdirSync(join(root, '.cos')), ['0003_c'])
})

test('status and unit-branch name the same shortened branch', () => {
  const name = `0001_${slugOf(71)}`
  const dir = cosTree(name)
  writeFileSync(join(dir, '.cos', name, 'intent.md'), '# Intent: x\nAuthor: t. Type: feat. Status: accepted.\n')
  const said = cli('--root', dir, 'unit-branch', name)
  assert.equal(said.status, 0, said.stderr)
  const status = JSON.parse(cli('--root', dir, 'status', '--json').stdout)
  assert.equal(status.units[0].branch, said.stdout.trim())
  assert.equal(branchProblem(said.stdout.trim()), null)
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

// --- 0109: a question carries a `?`, and everything else under the heading is a note ---
test('a bullet is never a question, even when nothing is numbered', () => {
  const bullets = '## Open questions\n\nKhông còn câu hỏi mở.\n\n- **One?** first\n- Two?\n'
  assert.deepEqual(parseQuestions(bullets), [])
  const mixed = '## Open questions\n\n1. Numbered?\n- a sub-point of it\n2. Also numbered?\n'
  assert.deepEqual(parseQuestions(mixed).map((q) => q.n), [1, 2])
  assert.match(parseQuestions(mixed)[0].text, /sub-point/)
})

test('a numbered item is a question only when it asks one', () => {
  const text = '## Open questions\n\n1. Ai quyết định?\n7. Người khởi xướng vẫn nên đọc lại file này.\n'
  assert.deepEqual(parseQuestions(text).map((q) => q.n), [1])
})

test('the ? must be in the first paragraph of the item', () => {
  assert.deepEqual(parseQuestions('## Open questions\n\n1. Chọn A\n   hay B?\n').map((q) => q.n), [1])
  assert.deepEqual(parseQuestions('## Open questions\n\n1. Ghi chú.\n\n   Còn gì nữa?\n'), [])
})

test('a numbered note still ends the question above it and keeps its number', () => {
  const qs = parseQuestions('## Open questions\n\n1. A?\n2. Ghi chú.\n3. B?\n')
  assert.deepEqual(qs.map((q) => q.n), [1, 3])
  assert.doesNotMatch(qs[0].text, /Ghi chú/)
})

test('a note keeps no answer, a question keeps its own, and a heading with none is counted', () => {
  const intent = [
    '# Intent: q', 'Author: t. Type: fix. Status: accepted.', '', '## Open questions', '',
    '1. A?', '2. Ghi chú.', '', '## Answers',
    answerBlock(1, 'A', 'có'), answerBlock(2, 'A', 'đã đọc'),
  ].join('\n')
  const spec = '# Spec\nIntent: intent.md. Author: t. Status: accepted.\n\n## Open questions\n\nKhông còn câu hỏi mở.\n'
  const { u } = questionTree({ 'intent.md': intent, 'spec.md': spec })
  const qs = u.artifacts['intent.md'].questions
  assert.deepEqual(qs.map((q) => [q.n, q.answered]), [[1, true]])
  assert.equal(parseAnswers(intent).length, 2)
  assert.equal(u.counted, 'spec.md')
  assert.equal(u.open, 0)
})

// The shape of the two sections `0109` was opened for (`spike.md ## U1`), written here so
// the test reads nothing outside the repository.
test('the notes of 0107 and 0054 are not questions', () => {
  const s0107 = [
    '## Open questions', '',
    'Không còn câu hỏi mở. Câu 1 đến câu 5 của intent.md ## Answers đã được trả lời, và spec này dùng chúng như sau:',
    '',
    '- Câu 1 là hạn 2026-10-02. Spec không đổi hạn này.',
    '- Câu 2 cho phép R4.',
    '- Câu 3 cho phép R5.',
    '- Câu 4 là cách đo, và thành phép thử ngoài spec.',
    '- Câu 5 là lý do có Out of scope về nén tất định.',
    '',
    'Cả năm câu do Leif (CoS) trả lời thay người khởi xướng.', '',
  ].join('\n')
  assert.deepEqual(parseQuestions(s0107), [])
  const s0054 = [
    '## Open questions', '',
    '1. Hạn thật cho outcome: đã trả lời, xem `intent.md ## Answers, câu 1`.',
    '6. Nút tách riêng và dòng xác nhận là yêu cầu hay gợi ý: đã trả lời, xem `intent.md ## Answers, câu 6`.',
    '7. Các câu 1–6 do Leif (CoS) trả lời thay người khởi xướng. Người khởi xướng vẫn nên đọc lại file này.',
    '',
  ].join('\n')
  assert.equal(parseQuestions(s0054).some((q) => q.n === 7), false)
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
  const spec = '# Spec\nIntent: intent.md. Author: t. Status: accepted.\n\n## Open questions\n\n1. Only one?\n'
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
  const long = `${QUESTIONS}4. ${'x'.repeat(200 * 1024)}?\n`
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

test('parseReview gives each round its text verbatim, bounded by the next ## heading', () => {
  const one = round(1, 'changes-requested', ['- F1 [open] a `quoted` thing', '- F2 [open] b'])
  const two = round(2, 'pass', [`- F1 [fixed ${FIX}] a`])
  const text = `# Review\nStatus: accepted.\n\n${one}\n${two}\n## Answers\n\n### Câu 1\nnot a round\n`
  const { rounds } = parseReview(text)
  assert.equal(rounds[0].text, one.trimEnd())
  assert.ok(rounds[0].text.startsWith('## Round 1\n'))
  for (const f of ['- F1 [open] a `quoted` thing', '- F2 [open] b']) assert.ok(rounds[0].text.includes(f))
  assert.equal(rounds[1].text, two.trimEnd())
  assert.ok(!rounds[1].text.includes('Answers'))
  assert.ok(!rounds[1].text.includes('not a round'))
  const followed = parseReview(`${one}\n## Something else\n\nnot this\n`).rounds
  assert.equal(followed[0].text, one.trimEnd())
  assert.ok(!followed[0].text.includes('Something'))
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

test('F4: the three closed cases, in the shapes gh pr checks --required --json name,bucket gives them', () => {
  // `gh` prints the JSON array on stdout whatever it exits with; red is exit 1 and running is
  // exit 8 on the plain output, and the gate must not depend on which. With nothing
  // required it prints no JSON at all, only an error on stderr, exit 1.
  const u = unit(CHAIN)
  const gh = (code, out, err = '') => ({ gh: () => ({ code, out, err }), git: () => ok() })
  const red = '[{"bucket":"fail","name":"tests"},{"bucket":"pass","name":"branch-name"}]\n'
  const running = '[{"bucket":"pending","name":"tests"},{"bucket":"pass","name":"branch-name"}]\n'
  for (const code of [0, 1]) {
    const g = checkGate(u, 'review', { probe: gh(code, red) })
    assert.deepEqual([g.ok, g.need], [false, ['CI is red on #7: tests — back to impl: fix on the branch and push']])
  }
  for (const code of [0, 8]) {
    const g = checkGate(u, 'review', { probe: gh(code, running) })
    assert.deepEqual([g.ok, g.need], [false, ['CI has not finished on #7: tests — wait, then ask again']])
  }
  const none = checkGate(u, 'review', { probe: gh(1, '', "no required checks reported on the 'feat/x' branch\n") })
  assert.deepEqual([none.ok, none.need], [false, ["cannot read the required checks of #7: no required checks reported on the 'feat/x' branch"]])
  // An empty array is the same answer by another road.
  assert.match(checkGate(u, 'review', { probe: gh(0, '[]\n') }).need[0], /reports no required checks/)
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

test('F3: pass, then rebase, closes ship; another passing round opens it and costs no round', () => {
  const REB = 'd'.repeat(40)
  const rebased = { state: 'OPEN', headRefOid: REB }
  const notAncestor = { code: 1, out: '', err: '' }
  // After `gh pr update-branch --rebase` the reviewed commit is on none of the three.
  const afterRebase = greenProbe(undefined, {
    [`merge-base --is-ancestor ${SHA} refs/heads/feat/x`]: notAncestor,
    [`merge-base --is-ancestor ${SHA} refs/remotes/origin/feat/x`]: notAncestor,
    [`merge-base --is-ancestor ${SHA} ${REB}`]: notAncestor,
  }, rebased)
  const passed = branched({ ...CHAIN, 'review.md': reviewArt('accepted', `${round(1, 'changes-requested', ['- F1 [open] x'])}\n${round(2, 'pass', [`- F1 [fixed ${FIX}] x`])}`) })
  const g = checkGate(passed, 'ship', { probe: afterRebase })
  assert.equal(g.ok, false)
  assert.match(g.need[0], /rewritten after the pass \(a rebase does this\): review its new head in another round/)

  // Round 3 reviews the rebased head and passes: ship opens on that head.
  const again = `${round(1, 'changes-requested', ['- F1 [open] x'])}\n${round(2, 'pass', [`- F1 [fixed ${FIX}] x`])}\n${round(3, 'pass', [`- F1 [fixed ${FIX}] x`]).replace(SHA, REB)}`
  const reReviewed = branched({ ...CHAIN, 'review.md': reviewArt('accepted', again) })
  const open = checkGate(reReviewed, 'ship', { probe: greenProbe(undefined, {}, rebased) })
  assert.equal(open.ok, true)
  assert.equal(open.head, REB)

  // Passing rounds are not counted: cr, pass, cr is two of three used, not three.
  const text = `${round(1, 'changes-requested', ['- F1 [open] x'])}\n${round(2, 'pass', [`- F1 [fixed ${FIX}] x`])}\n${round(3, 'changes-requested', [`- F1 [fixed ${FIX}] x`, '- F2 [open] y'])}`
  const u = unit({ ...CHAIN, 'review.md': reviewArt('changes-requested', text) })
  assert.match(nextAction(u).action, /2 of 3 rounds used/)
  assert.equal(checkGate(u, 'review', { probe: greenProbe() }).ok, true)
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

// --- `0024`: the stage a run button offers -------------------------------------

test('nextAction.stage names the missing stage, and nothing where the files do not settle it', () => {
  assert.equal(nextAction(unit({ 'intent.md': art('accepted') })).stage, 'spec')
  assert.equal(nextAction(unit(CHAIN)).stage, 'review')
  assert.equal(nextAction(unit({ 'intent.md': art('draft') })).stage, '')
  assert.equal(nextAction(unit({ 'intent.md': art('rejected') })).stage, '')
  assert.equal(nextAction(unit({ 'intent.md': art(null) })).stage, '')
  assert.equal(nextAction(unit({ ...CHAIN, 'review.md': reviewArt('changes-requested', round(1, 'changes-requested', ['- F1 [open] x'])) })).stage, '')
})

const HEAD2 = 'e'.repeat(40)
const asked = (text = round(1, 'changes-requested', ['- F1 [open] x'])) =>
  branched({ ...CHAIN, 'review.md': reviewArt('changes-requested', text) })
const movedTo = (files, checks) =>
  greenProbe(checks, { [`diff --name-only ${SHA}..${HEAD2}`]: ok(files.join('\n')) }, { state: 'OPEN', headRefOid: HEAD2 })

test('0024 a: a missing stage that needs no git is offered as it was', () => {
  const { 'pr.md': _, ...noPr } = CHAIN
  for (const [artifacts, stage] of [
    [{ 'intent.md': art('accepted') }, 'spec'],
    [{ 'intent.md': art('accepted'), 'spec.md': art('skipped') }, 'plan'],
    [{ 'intent.md': art('accepted'), 'spec.md': art('accepted'), 'plan.md': art('accepted') }, 'impl'],
    [noPr, 'pr'],
  ]) {
    const u = unit(artifacts)
    assert.equal(nextStep(u).stage, stage)
    assert.equal(nextStep(u, { probe: greenProbe() }).stage, stage)
    assert.equal(nextStep(u).action, nextAction(u).action)
  }
})

test('0024 b: changes asked, nothing new on the pull request: impl, though impl.md exists', () => {
  const n = nextStep(asked(), { probe: greenProbe() })
  assert.equal(n.stage, 'impl')
  assert.match(n.action, /fix the open findings of review round 1 .*nothing outside \.cos\/0001_x\/ has reached #7/)
  // Only the unit's own files moved: still no fix.
  assert.equal(nextStep(asked(), { probe: movedTo(['.cos/0001_x/review.md']) }).stage, 'impl')
})

test('0024 c: changes asked, a fix is on the head and CI is green: review, though review.md exists', () => {
  assert.equal(nextStep(asked(), { probe: movedTo(['src/a.py']) }).stage, 'review')
  // A rebase counts as moved (spec Concern 4), by the ship gate's own rule.
  const rebased = greenProbe(undefined, { [`merge-base --is-ancestor ${SHA} ${HEAD2}`]: { code: 1, out: '', err: '' } }, { state: 'OPEN', headRefOid: HEAD2 })
  assert.equal(nextStep(asked(), { probe: rebased }).stage, 'review')
})

test('0024 d: the fix is on the head but CI runs, or failed', () => {
  const pending = nextStep(asked(), { probe: movedTo(['src/a.py'], [{ name: 'tests', bucket: 'pending' }]) })
  assert.equal(pending.stage, '')
  assert.match(pending.action, /CI has not finished on #7/)
  const red = nextStep(asked(), { probe: movedTo(['src/a.py'], [{ name: 'tests', bucket: 'fail' }]) })
  assert.equal(red.stage, 'impl')
  assert.match(red.action, /CI is red on #7/)
  const unreadable = {
    gh: (...a) => (a[1] === 'view' ? ok(JSON.stringify({ state: 'OPEN', headRefOid: HEAD2 })) : { code: 1, out: '', err: 'HTTP 401' }),
    git: (...a) => (a[0] === 'diff' ? ok('src/a.py') : ok()),
  }
  assert.equal(nextStep(asked(), { probe: unreadable }).stage, '')
})

test('0024 e: pr is open, no review yet: review on green, impl on red, nothing while it runs', () => {
  const u = branched(CHAIN)
  assert.equal(nextStep(u, { probe: greenProbe() }).stage, 'review')
  assert.equal(nextStep(u, { probe: greenProbe([{ name: 'tests', bucket: 'fail' }]) }).stage, 'impl')
  assert.equal(nextStep(u, { probe: greenProbe([{ name: 'tests', bucket: 'pending' }]) }).stage, '')
  assert.equal(nextStep(u, { probe: greenProbe([]) }).stage, '')
})

test('0024 f: the round limit used up offers nothing and says needs a person', () => {
  const text = [1, 2, 3].map((n) => round(n, 'changes-requested', ['- F1 [open] x'])).join('\n')
  const n = nextStep(asked(text), { probe: movedTo(['src/a.py']) })
  assert.equal(n.stage, '')
  assert.match(n.action, /needs a person — review used 3 of 3 rounds/)
  assert.equal(nextStep(asked(text), { probe: movedTo(['src/a.py']), limit: 4 }).stage, 'review')
})

test('0024 g: the last round passed and the ship gate is open: ship, pinned', () => {
  const u = branched({ ...CHAIN, 'review.md': reviewArt('accepted', round(1, 'pass')) })
  const n = nextStep(u, { probe: greenProbe() })
  assert.equal(n.stage, 'ship')
  assert.match(n.action, new RegExp(`write-ship — merge with --match-head-commit ${SHA}`))
})

test('0024 h: a done plan offers nothing, whatever the later files say', () => {
  const u = unit({ ...CHAIN, 'plan.md': art('done'), 'review.md': reviewArt('changes-requested', round(1, 'changes-requested', ['- F1 [open] x'])) })
  assert.deepEqual(nextStep(u, { probe: greenProbe() }), { blocked: false, action: 'finished', stage: '' })
})

test('0024 i: the branch moved after the pass: review again, not ship', () => {
  const u = branched({ ...CHAIN, 'review.md': reviewArt('accepted', round(1, 'pass')) })
  const n = nextStep(u, { probe: movedTo(['src/a.py']) })
  assert.equal(n.stage, 'review')
  assert.match(n.action, /changed after the reviewed commit/)
  assert.equal(nextStep(u, { probe: movedTo(['src/a.py'], [{ name: 'tests', bucket: 'fail' }]) }).stage, 'impl')
  // Closed for a reason that is not movement: nothing.
  const open = branched({ ...CHAIN, 'review.md': reviewArt('accepted', round(1, 'pass', ['- F1 [open] x'])) })
  assert.equal(nextStep(open, { probe: greenProbe() }).stage, '')
})

test('0024: with no repository, the three git cases offer nothing and say --repo', () => {
  for (const u of [asked(), branched(CHAIN), branched({ ...CHAIN, 'review.md': reviewArt('accepted', round(1, 'pass')) })]) {
    const n = nextStep(u)
    assert.equal(n.stage, '')
    assert.match(n.action, /--repo/)
  }
})

test('0024: nextStep never offers a stage whose gate is closed', () => {
  const probes = [greenProbe(), movedTo(['src/a.py']), movedTo(['src/a.py'], [{ name: 't', bucket: 'fail' }])]
  const units = [asked(), branched(CHAIN), branched({ ...CHAIN, 'review.md': reviewArt('accepted', round(1, 'pass')) }), unit({ 'intent.md': art('accepted') })]
  for (const probe of probes) {
    for (const u of units) {
      const { stage } = nextStep(u, { probe })
      if (stage) assert.equal(checkGate(u, stage, { probe }).ok, true, `${stage} offered with its gate closed`)
    }
  }
})

test('cos.mjs next prints one JSON line, and misuse is exit 2', () => {
  const { root } = questionTree({ 'intent.md': '# I\nAuthor: t. Type: feat. Status: accepted.\n' })
  const out = cli('--root', root, 'next', '0001_q')
  assert.equal(out.status, 0)
  assert.deepEqual(JSON.parse(out.stdout), { unit: '0001_q', stage: 'spec', action: 'write-spec — it assesses whether to skip first', blocked: true })
  assert.equal(cli('--root', root, 'next').status, 2)
  assert.equal(cli('--root', root, 'next', '0009_nope').status, 2)
  assert.equal(cli('--root', root, 'next', '0001_q', '--repo', tmpdir()).status, 0)
})

test('status --json carries next.stage for each unit', () => {
  const { root } = questionTree({ 'intent.md': '# I\nAuthor: t. Type: feat. Status: accepted.\n' })
  const out = cli('--root', root, 'status', '--json')
  assert.equal(JSON.parse(out.stdout).units[0].next.stage, 'spec')
})

// --- a finding impl cannot fix waits for a person (0028) ----------------------

const NEEDS = '## Needs a person\n\n- F2: the grant holds no budget for --paid\n- F3: the grant holds no gh\n'
const implText = (needs = NEEDS) => `# Impl: x\nIntent: intent.md. Plan: plan.md. Author: t. Status: accepted.\n\n## What was built\n\nx\n\n${needs}`
const fBlock = (id, text = 'ran it') => `\n### ${id}\nAnswered by: Bao. Date: 2026-09-24. Via: product.\n\n${text}\n`
const REVIEW_HEAD = '# Review: x\nPR: pr.md. Author: t. Status: changes-requested.\n\n'
const ROUND1 = round(1, 'changes-requested', ['- F1 [open] a', '- F2 [open] b', '- F3 [open] c'])
const ROUND2 = round(2, 'changes-requested', [`- F1 [fixed ${FIX}] a`, '- F2 [open] b', '- F3 [open] c'])
const ROUND3 = (f3 = 'needs-person', extra = []) =>
  round(3, 'needs-person', [`- F1 [fixed ${FIX}] a`, '- F2 [needs-person] b', `- F3 [${f3}] c`, ...extra])

// The state of `0017` right after its review round 2, as files on disk (spec R10).
function tree0028({ review, impl = implText() }) {
  const { u } = questionTree({
    'intent.md': '# I\nAuthor: t. Type: fix. Status: accepted.\n',
    'spec.md': '# S\nStatus: accepted.\n',
    'plan.md': '# P\nStatus: accepted.\n',
    'impl.md': impl,
    'pr.md': '# PR\nPR: https://github.com/o/r/pull/7. Status: accepted.\n',
    'review.md': review,
  })
  return u
}

test('0028 R1: impl.md ## Needs a person reads well-formed lines, skips others, stops at Answers', () => {
  const text = `${implText('## Needs a person\n\n- F2: no gh in this grant\n- F3 no colon\n-F4: no space\n- F5:   \n* F6: a star\n- F7: real money\n')}\n## Answers\n\n## Needs a person\n\n- F9: under answers\n`
  assert.deepEqual(parseNeedsPerson(text), [{ id: 'F2', reason: 'no gh in this grant' }, { id: 'F7', reason: 'real money' }])
  assert.deepEqual(parseNeedsPerson(implText('')), [])
  assert.deepEqual(parseNeedsPerson('# Impl\nStatus: accepted.\n\n## Answers\n\n## Needs a person\n\n- F1: x\n'), [])
})

test('0028 R7: parseAnswers reads Câu N and F<n> blocks side by side, the last per id in force', () => {
  const text = `${withAnswers(answerBlock(2, 'Bao', 'two'))}${fBlock('F2', 'first')}${fBlock('F2', 'second')}\n### F3\nno header\n`
  const all = parseAnswers(text)
  assert.deepEqual(all.map((a) => [a.n, a.id, a.text]), [[2, null, 'two'], [null, 'F2', 'first'], [null, 'F2', 'second']])
  const { root } = questionTree({})
  const dir = join(root, '.cos', '0001_q')
  writeFileSync(join(dir, 'review.md'), `${REVIEW_HEAD}${ROUND1}\n## Answers\n${fBlock('F2', 'first')}${fBlock('F2', 'second')}\n### F3\nno header\n`)
  assert.deepEqual(readUnit(dir, '0001_q').artifacts['review.md'].personAnswers, ['F2'])
  // An F<n> block is never read as an answer to a numbered question.
  const u = questionTree({ 'intent.md': `# I\nAuthor: t. Type: fix. Status: accepted.\n\n${withAnswers(fBlock('F1'))}` }).u
  assert.equal(u.artifacts['intent.md'].questions.every((q) => !q.answered), true)
})

test('0028 R3/R4: the three new labels and the needs-person verdict are read; others stay unreadable', () => {
  const r = parseReview(round(1, 'needs-person', ['- F1 [needs-person] a', '- F2 [Claim-Rejected] b', '- F3 [answered] c', '- F4 [waiting] d'])).rounds[0]
  assert.equal(r.verdict, 'needs-person')
  assert.deepEqual(r.findings.map((f) => f.label), ['needs-person', 'claim-rejected', 'answered', 'unreadable'])
})

test('0028 R5: a needs-person round is not counted toward the limit', () => {
  const three = [1, 2, 3].map((n) => round(n, 'changes-requested', ['- F1 [open] x']))
  const text = `${three.join('\n')}\n${round(4, 'needs-person', ['- F1 [needs-person] x'])}`
  assert.match(nextAction(asked(text), 4).action, /needs a person — F1: impl\.md gives no reason/)
  assert.match(nextAction(asked(three.join('\n')), 4).action, /3 of 4 rounds used/)
  assert.match(nextAction(asked(text), 3).action, /needs a person — review used 3 of 3/)
  // One needs-person round alone, limit 1: not the used-up path.
  const one = nextAction(asked(round(1, 'needs-person', ['- F1 [needs-person] x'])), 1)
  assert.doesNotMatch(one.action, /rounds and findings are still open/)
  assert.deepEqual(one.waiting, ['F1'])
})

test('0028 R10 S0: every open finding claimed, no review has confirmed it: review, not impl', () => {
  const n = nextStep(tree0028({ review: `${REVIEW_HEAD}${ROUND1}\n${ROUND2}` }), { probe: greenProbe() })
  assert.equal(n.stage, 'review')
  assert.match(n.action, /claimed in impl\.md ## Needs a person — review confirms or rejects each/)
  assert.equal(n.waiting, undefined)
})

test('0028 R10 S1: the review confirmed both claims: nothing to run, a person is named', () => {
  const u = tree0028({ review: `${REVIEW_HEAD}${ROUND1}\n${ROUND2}\n${ROUND3()}` })
  const n = nextStep(u, { probe: greenProbe() })
  assert.equal(n.stage, '')
  assert.equal(n.blocked, true)
  assert.match(n.action, /^needs a person — F2: the grant holds no budget for --paid; F3: the grant holds no gh — answer each on the Questions tab/)
  assert.deepEqual(n.waiting, ['F2', 'F3'])
  // Files alone settle it: no --repo needed.
  assert.deepEqual(nextStep(u).waiting, ['F2', 'F3'])
  assert.deepEqual(nextAction(u).waiting, ['F2', 'F3'])
  assert.deepEqual(u.personFindings.map((p) => [p.id, p.answered]), [['F2', false], ['F3', false]])
})

test('0028 R10 S2: one answered, one not: still waiting, for the other', () => {
  const u = tree0028({ review: `${REVIEW_HEAD}${ROUND1}\n${ROUND2}\n${ROUND3()}\n## Answers\n${fBlock('F2')}` })
  const n = nextStep(u, { probe: greenProbe() })
  assert.equal(n.stage, '')
  assert.match(n.action, /^needs a person — F3: the grant holds no gh/)
  assert.deepEqual(n.waiting, ['F3'])
})

test('0028 R10 S3: both answered: review reads the answers', () => {
  const u = tree0028({ review: `${REVIEW_HEAD}${ROUND1}\n${ROUND2}\n${ROUND3()}\n## Answers\n${fBlock('F2')}${fBlock('F3')}` })
  const n = nextStep(u, { probe: greenProbe() })
  assert.equal(n.stage, 'review')
  assert.match(n.action, /a person answered F2, F3 in review\.md/)
  assert.equal(n.waiting, undefined)
  assert.equal(checkGate(u, 'review', { probe: greenProbe() }).ok, true)
})

test('0028 R10 escape F4: one open finding impl did not claim sends the unit to impl', () => {
  const r2 = round(2, 'changes-requested', [`- F1 [fixed ${FIX}] a`, '- F2 [open] b', '- F3 [open] c', '- F4 [open] d'])
  assert.equal(nextStep(tree0028({ review: `${REVIEW_HEAD}${ROUND1}\n${r2}` }), { probe: greenProbe() }).stage, 'impl')
})

test('0028 R10 escape claim-rejected: a rejected claim sends the unit to impl', () => {
  const u = tree0028({ review: `${REVIEW_HEAD}${ROUND1}\n${ROUND2}\n${ROUND3('claim-rejected')}` })
  assert.equal(nextStep(u, { probe: greenProbe() }).stage, 'impl')
  assert.deepEqual(u.personFindings, [])
  // Rejected in a changes-requested round, with impl.md still claiming it: impl too (R6 d).
  // Limit 4: that round is the third to ask for changes, and 3 of 3 is the old stop.
  const r3cr = round(3, 'changes-requested', [`- F1 [fixed ${FIX}] a`, '- F2 [needs-person] b', '- F3 [claim-rejected] c'])
  assert.equal(nextStep(tree0028({ review: `${REVIEW_HEAD}${ROUND1}\n${ROUND2}\n${r3cr}` }), { probe: greenProbe(), limit: 4 }).stage, 'impl')
})

test('0028 answered but not settled: a finding a round kept [open] after its answer goes to impl, not review again', () => {
  // Round 3 confirmed both; a person answered both; round 4 closed F2 on its answer and kept
  // F3 open. impl.md still claims F2 and F3 from before round 3: no impl has run since.
  const r4 = round(4, 'changes-requested', [`- F1 [fixed ${FIX}] a`, '- F2 [answered] b', '- F3 [open] c'])
  const review = `${REVIEW_HEAD}${ROUND1}\n${ROUND2}\n${ROUND3()}\n${r4}\n## Answers\n${fBlock('F2')}${fBlock('F3')}`
  const n = nextStep(tree0028({ review }), { probe: greenProbe(), limit: 4 })
  assert.equal(n.stage, 'impl')
  assert.doesNotMatch(n.action, /claimed in impl\.md ## Needs a person/)
  // The same holds for a claim a round rejected and a later round only kept [open].
  const r4b = round(4, 'changes-requested', [`- F1 [fixed ${FIX}] a`, '- F2 [needs-person] b', '- F3 [open] c'])
  const r3cr = round(3, 'changes-requested', [`- F1 [fixed ${FIX}] a`, '- F2 [open] b', '- F3 [claim-rejected] c'])
  assert.equal(nextStep(tree0028({ review: `${REVIEW_HEAD}${ROUND1}\n${ROUND2}\n${r3cr}\n${r4b}` }), { probe: greenProbe(), limit: 5 }).stage, 'impl')
})

test('0028: a needs-person verdict written wrong falls back to the old path', () => {
  const a = tree0028({ review: `${REVIEW_HEAD}${ROUND1}\n${ROUND2}\n${ROUND3('needs-person', ['- F4 [open] d'])}` })
  assert.equal(nextStep(a, { probe: greenProbe() }).stage, 'impl')
  assert.equal(nextStep(a, { probe: greenProbe() }).waiting, undefined)
  const b = tree0028({ review: `${REVIEW_HEAD}${ROUND1}\n${ROUND2}\n${ROUND3('answered')}` })
  assert.equal(nextStep(b, { probe: greenProbe() }).stage, 'impl')
  assert.deepEqual(b.personFindings, [])
})

test('0028: an impl.md with no Needs a person section changes nothing', () => {
  const u = tree0028({ review: `${REVIEW_HEAD}${ROUND1}\n${ROUND2}`, impl: implText('') })
  const n = nextStep(u, { probe: greenProbe() })
  assert.equal(n.stage, 'impl')
  assert.match(n.action, /nothing outside \.cos\/0001_q\/ has reached #7/)
})

test('0028 R8: ship closes a finding marked answered only when review.md holds its answer', () => {
  const pass = round(1, 'pass', [`- F1 [fixed ${FIX}] a`, '- F2 [answered] b'])
  const withBlock = { ...reviewArt('accepted', pass), personAnswers: ['F2'] }
  const g = checkGate(branched({ ...CHAIN, 'review.md': withBlock }), 'ship', { probe: greenProbe() })
  assert.equal(g.ok, true, g.need.join('\n'))
  const bare = checkGate(branched({ ...CHAIN, 'review.md': reviewArt('accepted', pass) }), 'ship', { probe: greenProbe() })
  assert.equal(bare.ok, false)
  assert.match(bare.need.join('\n'), /F2 \[answered, no answer in review\.md\]/)
  for (const label of ['needs-person', 'claim-rejected']) {
    const text = round(1, 'pass', [`- F2 [${label}] b`])
    const shut = checkGate(branched({ ...CHAIN, 'review.md': { ...reviewArt('accepted', text), personAnswers: ['F2'] } }), 'ship', { probe: greenProbe() })
    assert.equal(shut.ok, false)
    assert.match(shut.need.join('\n'), new RegExp(`F2 \\[${label}\\]`))
  }
})

test('0028: cos.mjs next prints waiting only when a person is awaited', () => {
  const { root } = questionTree({
    'intent.md': '# I\nAuthor: t. Type: fix. Status: accepted.\n',
    'spec.md': 'Status: accepted.\n', 'plan.md': 'Status: accepted.\n', 'impl.md': implText(),
    'pr.md': 'PR: https://github.com/o/r/pull/7. Status: accepted.\n',
    'review.md': `${REVIEW_HEAD}${ROUND1}\n${ROUND2}\n${ROUND3()}\n## Answers\n${fBlock('F2')}`,
  })
  const out = JSON.parse(cli('--root', root, 'next', '0001_q').stdout)
  assert.equal(out.stage, '')
  assert.deepEqual(out.waiting, ['F3'])
  const status = JSON.parse(cli('--root', root, 'status', '--json').stdout).units[0]
  assert.deepEqual(status.next.waiting, ['F3'])
  assert.deepEqual(status.personFindings, [
    { id: 'F2', reason: 'the grant holds no budget for --paid', answered: true },
    { id: 'F3', reason: 'the grant holds no gh', answered: false },
  ])
})

test('0028: nextStep never offers a stage whose gate is closed, in any of the new states', () => {
  const reviews = [
    `${REVIEW_HEAD}${ROUND1}\n${ROUND2}`,
    `${REVIEW_HEAD}${ROUND1}\n${ROUND2}\n${ROUND3()}`,
    `${REVIEW_HEAD}${ROUND1}\n${ROUND2}\n${ROUND3()}\n## Answers\n${fBlock('F2')}${fBlock('F3')}`,
  ]
  for (const probe of [greenProbe(), greenProbe([{ name: 't', bucket: 'fail' }]), greenProbe([{ name: 't', bucket: 'pending' }])]) {
    for (const review of reviews) {
      const u = tree0028({ review })
      const { stage } = nextStep(u, { probe })
      if (stage) assert.equal(checkGate(u, stage, { probe }).ok, true, `${stage} offered with its gate closed`)
    }
  }
})

// --- between pr and ship (0035) -------------------------------------------------

const between = (artifacts) => betweenPrAndShip(unit(artifacts))

test('0035: betweenPrAndShip is true only on an accepted pr.md naming a pull request, unfinished', () => {
  const noPrMd = { 'intent.md': art('accepted'), 'spec.md': art('accepted'), 'plan.md': art('accepted'), 'impl.md': art('accepted') }
  assert.equal(between(noPrMd), false, 'no pr.md')
  assert.equal(between({ ...noPrMd, 'pr.md': { ...art('draft'), pr: PR.pr } }), false, 'pr.md draft')
  assert.equal(between({ ...noPrMd, 'pr.md': { ...art('accepted'), pr: null } }), false, 'accepted with no PR:')
  assert.equal(
    between({ ...CHAIN, 'review.md': reviewArt('changes-requested', round(1, 'changes-requested', ['- F1 [open] x'])) }),
    true,
    'accepted with a PR, review changes-requested',
  )
  assert.equal(between({ ...CHAIN, 'plan.md': art('done') }), false, 'plan.md done')
  assert.equal(between({ ...CHAIN, 'review.md': art('rejected') }), false, 'closed by a rejection')
})

test('0035: status --json carries betweenPrAndShip for every unit; gate and next do not change', () => {
  const root = mkdtempSync(join(tmpdir(), 'cos-0035-'))
  const cos = join(root, '.cos')
  const put = (u, f, text) => { mkdirSync(join(cos, u), { recursive: true }); writeFileSync(join(cos, u, f), text) }
  for (const f of ['intent.md', 'spec.md', 'plan.md', 'impl.md']) {
    put('0001_open', f, `# X\n${f === 'intent.md' ? 'Type: feat. ' : ''}Status: accepted.\n`)
  }
  put('0001_open', 'pr.md', '# PR\nPR: https://github.com/o/r/pull/7. Status: accepted.\n')
  put('0002_early', 'intent.md', '# X\nType: fix. Status: draft.\n')
  const cli = (...args) =>
    spawnSync(process.execPath, [fileURLToPath(new URL('./cos.mjs', import.meta.url)), ...args, '--root', root], { encoding: 'utf8' })
  const before = { gate: cli('gate', '0001_open', 'impl'), next: cli('next', '0001_open') }
  const status = JSON.parse(cli('status', '--json').stdout)
  assert.deepEqual(status.units.map((u) => [u.name, u.betweenPrAndShip]), [['0001_open', true], ['0002_early', false]])
  const after = { gate: cli('gate', '0001_open', 'impl'), next: cli('next', '0001_open') }
  assert.equal(after.gate.stdout, before.gate.stdout)
  assert.equal(after.gate.status, before.gate.status)
  assert.equal(after.next.stdout, before.next.stdout)
  assert.ok(!('betweenPrAndShip' in JSON.parse(after.next.stdout)), 'next carries no integration field')
})

test('0033 R11: the plan\'s Impl: label opens and closes no gate, and moves no next', () => {
  const ask = (label) => {
    const root = mkdtempSync(join(tmpdir(), 'cos-0033-'))
    const dir = join(root, '.cos', '0001_same')
    mkdirSync(dir, { recursive: true })
    writeFileSync(join(dir, 'intent.md'), '# X\nType: feat. Status: accepted.\n')
    writeFileSync(join(dir, 'spec.md'), '# X\nStatus: accepted.\n')
    writeFileSync(join(dir, 'plan.md'), `# X\nStatus: accepted.${label === null ? '' : ` Impl: ${label}.`}\n`)
    const cli = (...args) =>
      spawnSync(process.execPath, [fileURLToPath(new URL('./cos.mjs', import.meta.url)), ...args, '--root', root], { encoding: 'utf8' })
    const gate = cli('gate', '0001_same', 'implement')
    const next = cli('next', '0001_same')
    return { code: gate.status, gate: gate.stdout, next: JSON.parse(next.stdout) }
  }
  const [routine, novel, none] = [ask('routine'), ask('novel'), ask(null)]
  assert.equal(routine.code, 0, routine.gate)
  for (const other of [novel, none]) {
    assert.equal(other.code, routine.code)
    assert.equal(other.gate, routine.gate)
    assert.deepEqual(other.next, routine.next)
  }
})

// --- 0047: the outcome a unit was measured against ---------------------------

const OUTCOME_INTENT = [
  '# Intent: x',
  'Author: a. Type: feat. Status: accepted.',
  '',
  '## Proposed outcome',
  '',
  'By 2026-10-07, three of three. Compared against 2026-09-01.',
  '',
  '## Open questions',
  '',
  '1. First?',
  '',
].join('\n')

const outcomeBlock = (lines, by = 'Linh', date = '2026-10-08') =>
  `\n### Outcome\nAnswered by: ${by}. Date: ${date}. Via: product.\n\n${lines.join('\n')}\n`

test('0047: the deadline is the first real date under Proposed outcome, or null', () => {
  assert.equal(parseDeadline(OUTCOME_INTENT), '2026-10-07')
  assert.equal(parseDeadline('## Proposed outcome\n\nOn 2026-02-30, then 2026-03-01.\n'), '2026-03-01')
  assert.equal(parseDeadline('## Problem\n\n2026-10-07\n\n## Proposed outcome\n\nSoon.\n'), null)
  assert.equal(parseDeadline('## Problem\n\n2026-10-07\n'), null)
  assert.equal(parseDeadline(`## Proposed outcome\n\nSoon.\n\n## Answers\n${outcomeBlock(['Result: đạt'])}`), null)
})

test('0047: a valid block needs a known result, Measured by, and Source or Reason by result', () => {
  const text = `${OUTCOME_INTENT}\n## Answers\n` + [
    outcomeBlock(['Result: đạt', 'Measured by: agent', 'Source: npm test, 12 pass']),
    outcomeBlock(['Result: trượt', 'Measured by: Linh']),
    outcomeBlock(['Result: không đo được', 'Measured by: agent', 'Source: x']),
    outcomeBlock(['Result: maybe', 'Measured by: agent', 'Source: x']),
    outcomeBlock(['Result: đạt', 'Source: x']),
    '\n### Outcome\nno header line\n\nResult: đạt\nMeasured by: agent\nSource: x\n',
    outcomeBlock(['Source: board, 2026-10-08', 'Result: Trượt', 'a note line inside', 'Measured by: Linh']),
    outcomeBlock(['Result: không đo được', 'Measured by: agent', 'Reason: no script', '', 'A longer note.']),
  ].join('')
  const { blocks, invalid } = parseOutcome(text)
  assert.equal(invalid, 5)
  assert.deepEqual(blocks.map((b) => b.result), ['met', 'missed', 'unmeasurable'])
  assert.equal(blocks[1].source, 'board, 2026-10-08')
  assert.equal(blocks[1].note, 'a note line inside')
  assert.equal(blocks[2].reason, 'no script')
  assert.equal(blocks[2].note, 'A longer note.')
  const o = unitOutcome(text)
  assert.equal(o.result, 'unmeasurable', 'the last valid block is the one in force')
  assert.equal(o.deadline, '2026-10-07')
  assert.equal(o.invalid, 5)
  assert.equal(o.by, 'Linh')
  assert.equal(o.measuredBy, 'agent')
})

test('0047: with no block, every field but deadline and invalid is null', () => {
  assert.deepEqual(unitOutcome(OUTCOME_INTENT), {
    deadline: '2026-10-07', result: null, by: null, date: null, measuredBy: null,
    source: null, reason: null, note: null, invalid: 0,
  })
})

test('0047 R6: an Outcome block ends the answer above it and is not an answer itself', () => {
  const f = (id, text) => `\n### ${id}\nAnswered by: Linh. Date: 2026-09-23. Via: product.\n\n${text}\n`
  const out = outcomeBlock(['Result: đạt', 'Measured by: agent', 'Source: x'])
  const plain = `${OUTCOME_INTENT}\n## Answers\n${answerBlock(1, 'Linh', 'Yes.')}${f('F2', 'Fine.')}${f('F3', 'Also.')}`
  const mixed = `${OUTCOME_INTENT}\n## Answers\n${answerBlock(1, 'Linh', 'Yes.')}${out}${f('F2', 'Fine.')}${out}${f('F3', 'Also.')}`
  const after = parseAnswers(mixed)
  assert.deepEqual(after, parseAnswers(plain))
  assert.ok(!after.some((a) => a.text.includes('Result:')))
  assert.deepEqual(after.map((a) => a.id ?? a.n), [1, 'F2', 'F3'])
  // `### Outcome` between two F blocks: both still read, neither swallows it.
  assert.equal(after.find((a) => a.id === 'F2').text, 'Fine.')
})

test('0047 R5: an outcome block changes nothing but outcome, for next and every gate', () => {
  const dir = mkdtempSync(join(tmpdir(), 'cos-0047-'))
  for (const f of ['idea.md', 'spec.md', 'impl.md', 'pr.md', 'review.md', 'ship.md']) {
    writeFileSync(join(dir, f), f === 'pr.md' ? 'PR: https://github.com/o/r/pull/7. Status: accepted.\n' : 'Status: accepted.\n')
  }
  writeFileSync(join(dir, 'plan.md'), 'Status: done.\n')
  writeFileSync(join(dir, 'intent.md'), OUTCOME_INTENT)
  const view = () => {
    const u = readUnit(dir, '0047_x')
    const gates = STAGE_NAMES.map((s) => checkGate(u, s, { probe: greenProbe() }))
    const { outcome, ...rest } = u
    return { outcome, json: JSON.stringify({ rest, next: nextAction(u), gates }) }
  }
  const before = view()
  assert.equal(before.outcome.result, null)
  writeFileSync(join(dir, 'intent.md'), `${OUTCOME_INTENT}\n## Answers\n` +
    outcomeBlock(['Result: đạt', 'Measured by: agent', 'Source: x']) + outcomeBlock(['Result: ?']))
  const after = view()
  assert.equal(after.outcome.result, 'met')
  assert.equal(after.outcome.invalid, 1)
  assert.equal(after.json, before.json)
})

// --- 0039: spike, only when the spec left a question unmeasured -----------------

const specText = (concerns, status = 'accepted') =>
  `# Spec: x\nIntent: intent.md. Author: t. Status: ${status}.\n\n## Requirements\n\n- [unmeasured] U9. prose, not a concern\n\n## Concerns\n\n${concerns}\n`
const spikeText = (round, items) =>
  `# Spike: x\nSpec: spec.md. Author: ᛈ Perthro. Round: ${round}. Status: accepted.\n\n` +
  items.map(([id, verdict]) => `## ${id}\n\nVerdict: ${verdict}.\n\n\`\`\`\n$ node -e 1\nok\n\`\`\`\n`).join('\n')
const INTENT_0039 = '# Intent: x\nAuthor: t. Type: feat. Status: accepted.\n'
const spikeUnit = (files) => readUnit(unitDir({ 'intent.md': INTENT_0039, ...files }), '0039_x')

test('parseUnmeasured reads [unmeasured] U<n> items under ## Concerns only (R1)', () => {
  assert.deepEqual(parseUnmeasured(specText('- **C1.** nothing unmeasured here.')), { ids: [], problems: [] })
  assert.deepEqual(parseUnmeasured(specText('- [unmeasured] U1. does it exit?\n* [unmeasured] U2. how long?')).ids, ['U1', 'U2'])
  assert.deepEqual(parseUnmeasured(specText('1. [unmeasured] U3 numbered')).ids, ['U3'])
  // mid-sentence, indented, or outside `## Concerns`: prose
  assert.deepEqual(parseUnmeasured(specText('- C1 says [unmeasured] U1 in passing.\n  - [unmeasured] U2 nested')).ids, [])
  assert.deepEqual(parseUnmeasured('## Requirements\n\n- [unmeasured] U1. x\n').ids, [])
})

test('an [unmeasured] item with no id, or an id twice, is a problem that closes plan (R2)', () => {
  const noId = parseUnmeasured(specText('- [unmeasured] does it exit?'))
  assert.deepEqual(noId.ids, [])
  assert.match(noId.problems[0], /carries no U<n>: "- \[unmeasured\] does it exit\?"/)
  const twice = parseUnmeasured(specText('- [unmeasured] U1. a\n- [unmeasured] U1. b'))
  assert.deepEqual(twice.problems, ['spec.md: U1 is marked [unmeasured] twice'])

  const u = spikeUnit({ 'spec.md': specText('- [unmeasured] does it exit?') })
  assert.match(u.problems.join(' '), /carries no U<n>/)
  assert.match(checkGate(u, 'plan').need.join(' '), /carries no U<n>/)
  assert.equal(nextAction(u).stage, '')
  assert.match(nextAction(u).action, /^fix spec\.md — an \[unmeasured\] item carries no U<n>/)
})

test('parseSpike reads verdicts and blocks per U<n>, and stops at ## Answers (R6)', () => {
  const text =
    'Round: 2. Status: accepted.\n\n## U1\n\nVerdict: holds.\n\n```\n$ x\ny\n```\n\n' +
    '## U2\n\nno verdict here\n\n```\nout\n```\n\n## U3\n\nVerdict: fails.\n\n```\n```\n\n' +
    '## Answers\n\n## U4\n\nVerdict: holds.\n\n```\nz\n```\n'
  assert.deepEqual(parseSpike(text), {
    round: 2,
    items: {
      U1: { verdict: 'holds', hasBlock: true },
      U2: { verdict: null, hasBlock: true },
      U3: { verdict: 'fails', hasBlock: false },
    },
  })
  assert.equal(parseSpike('## U1\nVerdict: holds.\n').round, null)
})

test('parseSpike reads Round: from the header line only, never from a fenced block', () => {
  const text =
    '# Spike: x\nSpec: spec.md. Author: ᛈ Perthro. Status: accepted.\n\n' +
    '## U1\n\nVerdict: fails.\n\n```\n$ cat notes\nRound: 3\n```\n'
  assert.equal(parseSpike(text).round, null)
  const u = spikeUnit({ 'spec.md': specText('- [unmeasured] U1. a'), 'spike.md': text })
  assert.equal(nextAction(u).stage, 'spec')
})

test('a unit with no [unmeasured] item walks the loop as if spike did not exist (R4)', () => {
  const u = spikeUnit({ 'spec.md': specText('- **C1.** none'), 'plan.md': 'Status: accepted.\n' })
  assert.ok(!('unmeasured' in u.artifacts['spec.md']))
  assert.ok(!('citesSpike' in u.artifacts['plan.md']))
  assert.equal(checkGate(u, 'impl').ok, true)
  assert.match(nextAction(u).action, /write-impl/)
  assert.deepEqual(checkGate(u, 'spike').need, ['spike is not required: spec.md has no [unmeasured] item'])
})

test('a skipped spec never needs a spike, even beside a stray spike.md (R5)', () => {
  const u = spikeUnit({
    'spec.md': specText('- [unmeasured] U1. x', 'skipped'),
    'spike.md': spikeText(1, [['U1', 'fails']]),
    'plan.md': 'Status: accepted.\n',
  })
  assert.equal(checkGate(u, 'impl').ok, true)
  assert.match(nextAction(u).action, /write-impl/)
})

test('an unmeasured spec sends the unit to spike, and closes plan until every U<n> holds (R7)', () => {
  const spec = specText('- [unmeasured] U1. a\n- [unmeasured] U2. b')
  const none = spikeUnit({ 'spec.md': spec })
  assert.deepEqual(none.artifacts['spec.md'].unmeasured.ids, ['U1', 'U2'])
  assert.equal(nextAction(none).stage, 'spike')
  assert.equal(checkGate(none, 'spike').ok, true)
  assert.match(checkGate(none, 'plan').need.join('\n'), /spike\.md does not exist/)
  assert.match(checkGate(none, 'plan').need.join('\n'), /U1: spike\.md is missing, not accepted/)

  const noBlock = spikeUnit({ 'spec.md': spec, 'spike.md': spikeText(1, [['U1', 'holds']]) + '\n## U2\n\nVerdict: holds.\n' })
  const need = checkGate(noBlock, 'plan').need
  assert.equal(need.length, 1)
  assert.match(need[0], /^U2: .* no fenced block/)
  assert.equal(nextAction(noBlock).stage, 'spike')

  const held = spikeUnit({ 'spec.md': spec, 'spike.md': spikeText(1, [['U1', 'holds'], ['U2', 'holds']]) })
  assert.equal(checkGate(held, 'plan').ok, true)
  assert.equal(nextAction(held).stage, 'plan')
})

test('spec → spike (U2 fails) → spec again → spike → plan (R8, the Design flow)', () => {
  const first = spikeUnit({
    'spec.md': specText('- [unmeasured] U1. a\n- [unmeasured] U2. b'),
    'spike.md': spikeText(1, [['U1', 'holds'], ['U2', 'fails']]),
  })
  assert.equal(nextAction(first).stage, 'spec')
  assert.match(nextAction(first).action, /U2 does not hold/)
  assert.match(checkGate(first, 'plan').need.join(' '), /U2: spike\.md measured that it does not hold/)
  assert.equal(checkGate(first, 'spec').ok, true)

  const rewritten = spikeUnit({
    'spec.md': specText('- [unmeasured] U1. a\n- [unmeasured] U3. c'),
    'spike.md': spikeText(1, [['U1', 'holds'], ['U2', 'fails']]),
  })
  assert.equal(nextAction(rewritten).stage, 'spike')
  assert.match(nextAction(rewritten).action, /does not measure U3/)

  const measured = spikeUnit({
    'spec.md': specText('- [unmeasured] U1. a\n- [unmeasured] U3. c'),
    'spike.md': spikeText(2, [['U1', 'holds'], ['U3', 'holds']]),
  })
  assert.equal(nextAction(measured).stage, 'plan')
  assert.equal(checkGate(measured, 'plan').ok, true)
})

test('fails ahead of missing: spec is rewritten before the spike is redone (R8)', () => {
  const u = spikeUnit({
    'spec.md': specText('- [unmeasured] U1. a\n- [unmeasured] U2. b'),
    'spike.md': spikeText(1, [['U2', 'fails']]),
  })
  assert.equal(nextAction(u).stage, 'spec')
})

test(`a question still failing at round ${SPIKE_ROUNDS} needs a person`, () => {
  const u = spikeUnit({
    'spec.md': specText('- [unmeasured] U3. c'),
    'spike.md': spikeText(SPIKE_ROUNDS, [['U3', 'fails']]),
  })
  const next = nextAction(u)
  assert.equal(next.stage, '')
  assert.equal(next.blocked, true)
  assert.equal(next.action, `needs a person — spike round ${SPIKE_ROUNDS} of ${SPIKE_ROUNDS} found U3 does not hold`)
})

test('a spec rewritten without its questions still reads the spike beside it', () => {
  const u = spikeUnit({ 'spec.md': specText('- none left'), 'spike.md': spikeText(1, [['U1', 'fails']]) })
  // the old measurement is required to be accepted, but an id the spec dropped is not judged
  assert.equal(nextAction(u).stage, 'plan')
  assert.equal(checkGate(u, 'plan').ok, true)
})

test('with a spike required, impl opens only on a plan that cites spike.md (R9)', () => {
  const files = {
    'spec.md': specText('- [unmeasured] U1. a'),
    'spike.md': spikeText(1, [['U1', 'holds']]),
  }
  const silent = spikeUnit({ ...files, 'plan.md': 'Status: accepted.\n\n1. build it\n' })
  assert.equal(silent.artifacts['plan.md'].citesSpike, false)
  assert.deepEqual(checkGate(silent, 'impl').need, ['plan.md does not cite spike.md — every step that rests on a U<n> cites spike.md ## U<n>'])
  const cites = spikeUnit({ ...files, 'plan.md': 'Status: accepted.\n\n1. build it (spike.md ## U1)\n' })
  assert.equal(checkGate(cites, 'impl').ok, true)
})

// --- 0045: a person pauses or drops a unit ---------------------------------------

const holdBlock = (head, reason, by = 'Leif') => `\n### ${head}\nDecided by: ${by}. Date: 2026-09-24. Via: product.\n\n${reason}\n`
const HELD_INTENT = '# Intent: x\nType: feat. Status: accepted.\n\n## Open questions\n\n1. Một?\n2. Hai?\n\n## Answers\n'

function heldTree(intentTail, extra = {}) {
  const root = mkdtempSync(join(tmpdir(), 'cos-0045-'))
  const dir = join(root, '.cos', '0001_held')
  mkdirSync(dir, { recursive: true })
  writeFileSync(join(dir, 'intent.md'), HELD_INTENT + intentTail)
  for (const [f, text] of Object.entries(extra)) writeFileSync(join(dir, f), text)
  const cli = (...args) =>
    spawnSync(process.execPath, [fileURLToPath(new URL('./cos.mjs', import.meta.url)), ...args, '--root', root], { encoding: 'utf8' })
  return { root, dir, u: readUnit(dir, '0001_held'), cli }
}

test('0045 R9: a hold block ends the answer before it, and is never an answer', () => {
  const text = HELD_INTENT + answerBlock(2, 'A', 'Tách ra.') + holdBlock('Paused', 'chờ 0034')
  const answers = parseAnswers(text)
  assert.equal(answers.length, 1)
  assert.equal(answers[0].text, 'Tách ra.')
  assert.equal(parseHold(text).hold.reason, 'chờ 0034')
})

test('0045 R1: no hold block is hold null and the active moves', () => {
  const { u } = heldTree(answerBlock(1, 'A', 'x'))
  assert.equal(u.hold, null)
  assert.deepEqual(u.holdMoves, ['paused', 'dropped'])
  assert.deepEqual(u.problems, [])
})

test('0045 R9: blocks are read in order and the last valid one decides', () => {
  const walk = (...heads) => parseHold(HELD_INTENT + heads.map((h, i) => holdBlock(h, `r${i}`)).join(''))
  assert.equal(walk('Paused').hold.state, 'paused')
  assert.equal(walk('Paused', 'Resumed').hold, null)
  assert.equal(walk('Dropped').hold.state, 'dropped')
  assert.equal(walk('Paused', 'Dropped').hold.state, 'dropped')
  assert.equal(walk('Dropped', 'Paused').hold.reason, 'r1')
  assert.deepEqual(walk('Dropped', 'Paused', 'Resumed'), { hold: null, problems: [] })
})

test('0045 R9: an invalid move is ignored and reported', () => {
  const resumed = parseHold(HELD_INTENT + holdBlock('Dropped', 'bỏ') + holdBlock('Resumed', 'lại'))
  assert.equal(resumed.hold.state, 'dropped')
  assert.deepEqual(resumed.problems, ['hold block 2 (### Resumed) is not a valid move from dropped — it is ignored'])
  const twice = parseHold(HELD_INTENT + holdBlock('Paused', 'a') + holdBlock('Paused', 'b'))
  assert.equal(twice.hold.reason, 'a')
  assert.equal(twice.problems.length, 1)
  assert.equal(parseHold(HELD_INTENT + holdBlock('Resumed', 'x')).problems.length, 1)
  const { u } = heldTree(holdBlock('Dropped', 'bỏ') + holdBlock('Resumed', 'lại'))
  assert.deepEqual(u.problems, ['intent.md: hold block 2 (### Resumed) is not a valid move from dropped — it is ignored'])
})

test('0045 R9: a block without a well-formed Decided by line is not counted', () => {
  assert.equal(parseHold(`${HELD_INTENT}\n### Paused\n\nkhông ai ký\n`).hold, null)
  assert.equal(parseHold(`${HELD_INTENT}\n### Paused\nDecided by: A\n\nthiếu ngày\n`).hold, null)
  assert.deepEqual(parseHold(`${HELD_INTENT}\n### Paused\n`).problems, [])
})

test('0045 R3: next offers no stage and asks no probe, for paused and dropped', () => {
  const boom = { gh: () => { throw new Error('gh was asked') }, git: () => { throw new Error('git was asked') } }
  const later = { 'spec.md': 'Status: accepted.\n', 'plan.md': 'Status: accepted.\n', 'impl.md': 'Status: accepted.\n', 'pr.md': 'PR: https://github.com/o/r/pull/7. Status: accepted.\n' }
  const paused = heldTree(holdBlock('Paused', 'chờ người'), later)
  const n = nextStep(paused.u, { probe: boom })
  assert.equal(n.stage, '')
  assert.equal(n.blocked, true)
  assert.equal(n.action, 'paused — chờ người (Leif, 2026-09-24) — resume it from the board')
  const dropped = heldTree(holdBlock('Dropped', 'không đáng'), later)
  const d = nextStep(dropped.u, { probe: boom })
  assert.deepEqual(d, { blocked: false, action: 'dropped — không đáng (Leif, 2026-09-24)', stage: '' })
  assert.equal(betweenPrAndShip(paused.u), false)
  assert.equal(betweenPrAndShip(dropped.u), false)
  const cli = JSON.parse(paused.cli('next', '0001_held').stdout)
  assert.equal(cli.stage, '')
  assert.deepEqual(cli.hold, { state: 'paused', reason: 'chờ người', by: 'Leif', date: '2026-09-24' })
})

test('0045 R4: every gate is closed on a held unit, and says why', () => {
  const { u, cli } = heldTree(holdBlock('Paused', 'chờ 0034'), { 'spec.md': 'Status: accepted.\n' })
  for (const s of STAGE_NAMES) {
    const g = checkGate(u, s, { probe: greenProbe() })
    assert.equal(g.ok, false, s)
    assert.deepEqual(g.need, ['the unit is paused: chờ 0034 (Leif, 2026-09-24)'], s)
    const r = cli('gate', '0001_held', s)
    assert.equal(r.status, 1, s)
    assert.match(r.stderr, /the unit is paused: chờ 0034/)
  }
  assert.equal(checkGate(u, 'nope').need[0].startsWith('unknown stage'), true)
})

test('0045 R2: status --json carries hold and holdMoves, and the table says paused —', () => {
  const { cli } = heldTree(holdBlock('Paused', 'chờ 0034'))
  const [held] = JSON.parse(cli('status', '--json').stdout).units
  assert.deepEqual(held.hold, { state: 'paused', reason: 'chờ 0034', by: 'Leif', date: '2026-09-24' })
  assert.deepEqual(held.holdMoves, HOLD_MOVES.paused)
  assert.equal(held.betweenPrAndShip, false)
  assert.match(cli('status').stdout, /\| paused — chờ 0034 \(Leif, 2026-09-24\) — resume it from the board \|/)
})

test('0045 R5: a done plan or a rejection wins over a hold, and the block is reported', () => {
  const done = heldTree(holdBlock('Paused', 'x'), { 'spec.md': 'Status: accepted.\n', 'plan.md': 'Status: done.\n' })
  assert.equal(done.u.hold, null)
  assert.deepEqual(done.u.holdMoves, [])
  assert.ok(done.u.problems.includes('intent.md carries a hold block, but the unit is finished — it is ignored'))
  assert.equal(nextAction(done.u).action, 'finished')
  const closed = heldTree(holdBlock('Dropped', 'x'), { 'spec.md': 'Status: rejected.\n' })
  assert.equal(closed.u.hold, null)
  assert.deepEqual(closed.u.holdMoves, [])
  assert.ok(closed.u.problems.includes('intent.md carries a hold block, but the unit is closed — it is ignored'))
})

test('0045: a unit with no intent has nowhere to hold', () => {
  const dir = mkdtempSync(join(tmpdir(), 'cos-0045-idea-'))
  writeFileSync(join(dir, 'idea.md'), '# Idea\nStatus: accepted.\n')
  const u = readUnit(dir, '0001_x')
  assert.equal(u.hold, null)
  assert.deepEqual(u.holdMoves, [])
})

test('0045 R1: next on an unheld unit carries no hold field', () => {
  const { cli } = heldTree('')
  assert.ok(!('hold' in JSON.parse(cli('next', '0001_held').stdout)))
})

test('0045 R1: every unit in this repository reads unheld and answers as it did', () => {
  for (const u of readdirSync(new URL('../../.cos/', import.meta.url)).filter((d) => /^\d{4}_/.test(d))) {
    const read = readUnit(fileURLToPath(new URL(`../../.cos/${u}`, import.meta.url)), u)
    assert.equal(read.hold, null, u)
    const { hold, holdMoves, ...bare } = read
    assert.deepEqual(nextAction(read), nextAction(bare), u)
  }
})

// --- a finding that does not block (0061) --------------------------------------

const low = (id, label = 'open', what = 'x') => `- ${id} [${label}] a.py:3 — low — ${what}`
const rated = (id, severity, label = 'open') => `- ${id} [${label}] a.py:3 — ${severity} — x`

test('0061 R2: severity is read only between two em dashes right after the location', () => {
  const r = parseReview(round(1, 'changes-requested', [
    '- F1 [open] a.py:3 — Mức thấp — x', '- F2 [open] a.py:3 - low - x', '- F3 [open] a.py:3 – low – x',
    '- F4 [open] a.py:3 — HIGH — x', '- F5 [open] a.py:3 — medium — x', `- F6 [fixed ${FIX}] a.py:3 — low — x`,
    '- F7 [open] no location — low', '- F8 [open] a.py:3 x — low — y',
  ])).rounds[0]
  assert.deepEqual(r.findings.map((f) => [f.id, f.severity]), [
    ['F1', null], ['F2', null], ['F3', null], ['F4', 'high'], ['F5', 'medium'], ['F6', 'low'], ['F7', null], ['F8', null],
  ])
})

test('0061 R3: an open low does not block, unless an earlier round rated the id higher', () => {
  const one = asked(round(1, 'pass', [low('F1'), rated('F2', 'medium'), '- F3 [open] a.py:3 x']))
  assert.deepEqual(nonBlocking(one), [{ id: 'F1', text: 'a.py:3 — low — x' }])
  const lowered = asked(`${round(1, 'changes-requested', [rated('F1', 'high')])}\n${round(2, 'pass', [low('F1')])}`)
  assert.deepEqual(nonBlocking(lowered), [])
  // A severity nobody could read is not a higher one: 0003's old rounds (R12).
  const prose = asked(`${round(1, 'changes-requested', ['- F1 [open] a.py:3 — Mức thấp — x'])}\n${round(2, 'pass', [low('F1')])}`)
  assert.deepEqual(nonBlocking(prose).map((f) => f.id), ['F1'])
  // Only `[open]` is ever let through, whatever the severity says.
  for (const label of ['needs-person', 'claim-rejected', 'answered', 'maybe']) {
    assert.deepEqual(nonBlocking(asked(round(1, 'needs-person', [low('F1', label)]))), [], label)
  }
  assert.deepEqual(nonBlocking(unit(CHAIN)), [])
})

test('0061 R10: status --json carries nonBlocking for each unit and severity for each finding', () => {
  const { root } = questionTree({
    'intent.md': '# I\nAuthor: t. Type: feat. Status: accepted.\n',
    'review.md': `# Review\nStatus: accepted.\n\n${round(1, 'pass', [low('F1'), `- F2 [fixed ${FIX}] b.py:1 — high — y`])}`,
  })
  const out = cli('--root', root, 'status', '--json')
  assert.equal(out.status, 0, out.stderr)
  const got = JSON.parse(out.stdout).units[0]
  assert.deepEqual(got.nonBlocking, [{ id: 'F1', text: 'a.py:3 — low — x' }])
  assert.deepEqual(got.artifacts['review.md'].review.rounds[0].findings.map((f) => f.severity), ['low', 'high'])
  const none = questionTree({ 'intent.md': '# I\nAuthor: t. Type: feat. Status: accepted.\n' })
  assert.deepEqual(none.u.nonBlocking, [])
})

test('0061 R4: ship lets an open low through, and nothing else', () => {
  const ship = (findings) => checkGate(branched({ ...CHAIN, 'review.md': reviewArt('accepted', round(1, 'pass', findings)) }), 'ship', { probe: greenProbe() })
  assert.equal(ship([low('F1')]).ok, true)
  const medium = ship([rated('F1', 'medium')])
  assert.equal(medium.ok, false)
  assert.match(medium.need.join('\n'), /F1 \[open\]/)
  const unrated = ship(['- F1 [open] a.py:3 x'])
  assert.equal(unrated.ok, false)
  assert.match(unrated.need.join('\n'), /F1 \[open\]/)
})

test('0061 R5: a severity lowered between rounds closes ship and says so', () => {
  const text = `${round(1, 'changes-requested', [rated('F1', 'high')])}\n${round(2, 'pass', [low('F1')])}`
  const g = checkGate(branched({ ...CHAIN, 'review.md': reviewArt('accepted', text) }), 'ship', { probe: greenProbe() })
  assert.equal(g.ok, false)
  assert.ok(g.need.includes('F1 is low in review round 2, but review round 1 rated it high — lowering a severity is not a fix: fix it on the branch, or keep it open'), g.need.join('\n'))
})

test('0078 R1: the pass round of 0050, with F4 its only open finding, opens ship', () => {
  // F4 is copied from `0078 spike.md ## U1`, `F4 line verbatim:`, em dashes and all.
  const text = round(1, 'pass', [
    `- F1 [fixed ${FIX}] a.py:3 — low — x`,
    `- F2 [fixed ${FIX}] a.py:3 — medium — x`,
    `- F3 [fixed ${FIX}] a.py:3 — low — x`,
    '- F4 [open] coscc/screens.py:646 — low — Còn hai docstring thuộc cùng loại với F3 mà `9243b7b` chưa sửa, vì vòng 3 không nêu tên chúng.',
  ])
  const u = branched({ ...CHAIN, 'review.md': reviewArt('accepted', text) })
  const g = checkGate(u, 'ship', { probe: greenProbe() })
  assert.equal(g.ok, true, g.need.join('\n'))
  assert.deepEqual(g.need, [])
  assert.deepEqual(nonBlocking(u).map((f) => f.id), ['F4'])
})

test('0078 R2: a lowered finding is named once, on its own line, not among the findings not fixed', () => {
  const ship = (text) => checkGate(branched({ ...CHAIN, 'review.md': reviewArt('accepted', text) }), 'ship', { probe: greenProbe() })
  const lowered = 'F1 is low in review round 2, but review round 1 rated it high — lowering a severity is not a fix: fix it on the branch, or keep it open'
  const a = ship(`${round(1, 'changes-requested', [rated('F1', 'high')])}\n${round(2, 'pass', [low('F1')])}`)
  assert.equal(a.ok, false)
  assert.ok(a.need.includes(lowered), a.need.join('\n'))
  assert.equal(a.need.some((l) => l.includes('still has findings not fixed')), false, a.need.join('\n'))
  const b = ship(`${round(1, 'changes-requested', [rated('F1', 'high'), rated('F2', 'medium')])}\n${round(2, 'pass', [low('F1'), rated('F2', 'medium')])}`)
  assert.equal(b.ok, false)
  assert.ok(b.need.includes(lowered), b.need.join('\n'))
  assert.deepEqual(b.need.filter((l) => l.includes('still has findings not fixed')), ['review round 2 still has findings not fixed: F2 [open]'])
})

test('0061 R12: three counted rounds of prose severities, then a pass of lows: ship opens at the default limit', () => {
  const cr = [1, 2, 3].map((n) => round(n, 'changes-requested', ['- F1 [open] a.py:3 — Mức thấp — biên regex', '- F2 [open] a.py:9 — Mức thấp — y']))
  const text = `${cr.join('\n')}\n${round(4, 'pass', [low('F1'), low('F2')])}`
  const g = checkGate(branched({ ...CHAIN, 'review.md': reviewArt('accepted', text) }), 'ship', { probe: greenProbe(), limit: REVIEW_ROUNDS })
  assert.equal(g.ok, true, g.need.join('\n'))
})

test('0061 R8: a needs-person round beside an open low is still a wait for a person', () => {
  const u = tree0028({ review: `${REVIEW_HEAD}${ROUND1}\n${ROUND2}\n${ROUND3('needs-person', [low('F4')])}` })
  const n = nextAction(u)
  assert.deepEqual(n.waiting, ['F2', 'F3'])
  assert.match(n.action, /^needs a person — F2: the grant holds no budget for --paid; F3: the grant holds no gh/)
  assert.deepEqual(u.personFindings.map((p) => p.id), ['F2', 'F3'])
  assert.deepEqual(u.nonBlocking.map((f) => f.id), ['F4'])
})

test('0061 R8: every blocking finding claimed, one low unclaimed: review, not impl', () => {
  const r2 = round(2, 'changes-requested', [`- F1 [fixed ${FIX}] a`, '- F2 [open] b', '- F3 [open] c', low('F4')])
  assert.equal(nextStep(tree0028({ review: `${REVIEW_HEAD}${ROUND1}\n${r2}` }), { probe: greenProbe() }).stage, 'review')
  // Nothing but lows left in a changes-requested round is not a claim: back to impl.
  const onlyLow = round(1, 'changes-requested', [low('F1')])
  const impl = implText('## Needs a person\n\n- F1: the grant holds no gh\n')
  assert.equal(nextStep(tree0028({ review: `${REVIEW_HEAD}${onlyLow}`, impl }), { probe: greenProbe() }).stage, 'impl')
})

test('0061 R7: a pass that leaves lows open costs no round', () => {
  const first = round(1, 'pass', [low('F1')])
  const cr = [2, 3, 4].map((n) => round(n, 'changes-requested', [low('F1'), '- F2 [open] b.py:1 y']))
  assert.match(nextAction(asked(`${first}\n${cr.join('\n')}`)).action, /needs a person — review used 3 of 3 rounds/)
  assert.match(nextAction(asked(`${first}\n${cr.slice(0, 2).join('\n')}`)).action, /\(2 of 3 rounds used\)/)
})

// --- 0055: the title and body pr.md puts on its pull request -----------------------

// The template of `.claude/skills/write-pr/SKILL.md ## Output`, filled in.
const PR_MD = [
  '# PR: the pr body is taken from pr.md',
  'Intent: intent.md. Impl: impl.md. PR: https://github.com/o/r/pull/7. Author: a. Status: accepted.',
  '',
  '## Where',
  '',
  'https://github.com/o/r/pull/7, branch fix/x, checks pending.',
  '',
  '## Scope of the diff',
  '',
  '## What a reviewer should look at first',
  '',
].join('\n')

test('0055 R1 (a): the template gives a body without the header or the title line', () => {
  const t = prText(PR_MD)
  assert.equal(t.title, 'the pr body is taken from pr.md')
  assert.equal(t.url, 'https://github.com/o/r/pull/7')
  assert.doesNotMatch(t.body, /Status:/)
  assert.doesNotMatch(t.body, /PR: https:\/\//)
  assert.doesNotMatch(t.body, /# PR:/)
  assert.ok(t.body.startsWith('## Where\n'), t.body)
  assert.equal(t.body, PR_MD.split('\n').slice(3).join('\n'))
})

test('0055 R1 (b): a later Status: in the body stays in the body', () => {
  const text = `${PR_MD}Status: pending is what gh said.\n`
  assert.match(prText(text).body, /Status: pending is what gh said\.\n$/)
})

test('0055 R1 (c): no # PR: line, or an empty one, is a null title', () => {
  const without = PR_MD.split('\n').slice(1).join('\n')
  assert.equal(prText(without).title, null)
  assert.ok(prText(without).body.startsWith('## Where'))
  const empty = prText(PR_MD.replace('# PR: the pr body is taken from pr.md', '# PR:   '))
  assert.equal(empty.title, null)
  assert.equal(empty.body, PR_MD.split('\n').slice(3).join('\n'), 'an empty # PR: line leaves the body too')
})

test('0055 R1 (d): the same input gives the same output', () => {
  assert.deepEqual(prText(PR_MD), prText(PR_MD))
})

test('0055 R1 (e): a file with \\r\\n keeps \\r\\n on the lines it keeps', () => {
  const t = prText(PR_MD.replace(/\n/g, '\r\n'))
  assert.equal(t.title, 'the pr body is taken from pr.md')
  assert.ok(t.body.startsWith('## Where\r\n'), JSON.stringify(t.body))
  assert.equal(t.body, PR_MD.split('\n').slice(3).join('\r\n'))
})

test('0055: a header on the title line itself drops only that one line', () => {
  assert.equal(prText('# PR: x Status: accepted.\n\nbody\n').body, 'body\n')
})

const prTree = (files) => {
  const root = mkdtempSync(join(tmpdir(), 'cos-0055-'))
  for (const [unitName, text] of Object.entries(files)) {
    mkdirSync(join(root, '.cos', unitName), { recursive: true })
    writeFileSync(join(root, '.cos', unitName, 'intent.md'), '# Intent: x\nAuthor: a. Type: fix. Status: accepted.\n')
    if (text !== null) writeFileSync(join(root, '.cos', unitName, 'pr.md'), text)
  }
  return root
}

test('0055 R2: pr-text prints what prText reads, with the unit and its status', () => {
  const root = prTree({ '0001_a': PR_MD })
  const out = cli('--root', root, 'pr-text', '0001_a')
  assert.equal(out.status, 0, out.stderr)
  assert.deepEqual(JSON.parse(out.stdout), { unit: '0001_a', ...prText(PR_MD), status: 'accepted' })
})

test('0055 R2: no unit or no pr.md is 1; a missing or malformed name is 2', () => {
  const root = prTree({ '0001_a': null })
  const none = cli('--root', root, 'pr-text', '0002_b')
  assert.equal(none.status, 1)
  assert.match(none.stderr, /No such work unit/)
  const nofile = cli('--root', root, 'pr-text', '0001_a')
  assert.equal(nofile.status, 1)
  assert.match(nofile.stderr, /0001_a has no pr\.md/)
  assert.equal(cli('--root', root, 'pr-text').status, 2)
  assert.equal(cli('--root', root, 'pr-text', '../0001_a').status, 2)
})

test('0055 R2: --repo and --reserve-from are refused by pr-text', () => {
  const root = prTree({ '0001_a': PR_MD })
  const repo = cli('--root', root, 'pr-text', '0001_a', '--repo', root)
  assert.equal(repo.status, 2)
  assert.match(repo.stderr, /--repo applies only to/)
  assert.equal(cli('--root', root, '--reserve-from', root, 'pr-text', '0001_a').status, 2)
})

test('0055 R2: pr-text writes nothing and leaves status as it was', () => {
  const root = prTree({ '0001_a': PR_MD, '0002_b': null })
  const listing = () =>
    readdirSync(join(root, '.cos'), { recursive: true })
      .sort()
      .map((p) => `${p} ${statSync(join(root, '.cos', p)).mtimeMs}`)
  const before = [cli('--root', root, 'status', '--json').stdout, listing()]
  assert.equal(cli('--root', root, 'pr-text', '0001_a').status, 0)
  assert.deepEqual([cli('--root', root, 'status', '--json').stdout, listing()], before)
})

// --- a UI unit ships only with screenshots a review looked at (0083) ----------

const REPO = fileURLToPath(new URL('../../', import.meta.url))

test('0083 R2: parseStandard reads the globs under paths: in the front-matter, and nothing else', () => {
  const text = '---\npaths:\n  - "coscc/screens.py"\n  - \'coscc/**\'\n  - a/*/b.py\nother: x\n  - "not/this.py"\n---\n\npaths:\n  - "nor/this.py"\n'
  assert.deepEqual(parseStandard(text), ['coscc/screens.py', 'coscc/**', 'a/*/b.py'])
  assert.deepEqual(parseStandard('# No front-matter\npaths:\n  - "x.py"\n'), [])
  assert.deepEqual(parseStandard('---\npaths:\n---\n'), [])
  assert.deepEqual(parseStandard('---\npaths:\n  - "x.py"\n'), [], 'a block never closed is not a block')
})

test('0083 R3: globMatch — ** crosses directories, * and ? stay inside one', () => {
  assert.equal(globMatch('coscc/screens.py', 'coscc/screens.py'), true)
  assert.equal(globMatch('coscc/screens.py', 'coscc/screens_py'), false, 'a dot is a dot')
  assert.equal(globMatch('coscc/screens.py', 'x/coscc/screens.py'), false)
  assert.equal(globMatch('coscc/**', 'coscc/a/b/c.py'), true)
  assert.equal(globMatch('coscc/**', 'coscc2/a.py'), false)
  assert.equal(globMatch('a/*/b.py', 'a/x/b.py'), true)
  assert.equal(globMatch('a/*/b.py', 'a/x/y/b.py'), false)
  assert.equal(globMatch('**/x.py', 'x.py'), true)
  assert.equal(globMatch('**/x.py', 'a/b/x.py'), true)
  assert.equal(globMatch('**/x.py', 'a/bx.py'), false)
  assert.equal(globMatch('coscc/?i.py', 'coscc/ui.py'), true)
  assert.equal(globMatch('coscc/?i.py', 'coscc//i.py'), false)
  assert.deepEqual(uiFiles(['coscc/ui.py', 'coscc/runner.py', 'README.md'], ['coscc/ui.py', '*.md']), ['coscc/ui.py', 'README.md'])
})

test('0083 R2: every glob of this checkout\'s standard names a file git tracks', () => {
  const globs = parseStandard(readFileSync(join(REPO, UI_STANDARD), 'utf8'))
  assert.ok(globs.length > 0)
  const tracked = spawnSync('git', ['ls-files'], { cwd: REPO, encoding: 'utf8' }).stdout.split('\n').filter(Boolean)
  for (const g of globs) assert.ok(tracked.some((p) => globMatch(g, p)), `${g} matches no tracked file`)
  assert.deepEqual(makeProbe(REPO).ui(), { path: UI_STANDARD, globs })
  assert.equal(makeProbe(mkdtempSync(join(tmpdir(), 'cos-noui-'))).ui(), null)
})

test('0083 R2: no other tracked file holds a copy of the list', () => {
  const globs = parseStandard(readFileSync(join(REPO, UI_STANDARD), 'utf8'))
  const tracked = spawnSync('git', ['ls-files'], { cwd: REPO, encoding: 'utf8' }).stdout.split('\n').filter(Boolean)
  const copies = tracked.filter((p) => p !== UI_STANDARD && !p.startsWith('.cos/')).filter((p) => {
    let text
    try {
      text = readFileSync(join(REPO, p), 'utf8')
    } catch {
      return false
    }
    return globs.every((g) => text.includes(g))
  })
  assert.deepEqual(copies, [])
})

const SHOT = '- .screens/board-1440x900.png — 1440×900 — /board — no violation'
const screens = ({ taken = SHA, by = 'an agent session (write-review)', standard = UI_STANDARD, shots = [SHOT], head = null } = {}) =>
  `\n### Screens\n\n${head ?? `Taken at: ${taken}. Standard: \`${standard}\`. Looked at by: ${by}, from screenshots.`}\n\n${shots.join('\n')}\n`

test('0083 R9: parseReview reads ### Screens, and a round without one reads null', () => {
  const text = `${round(1, 'changes-requested', ['- F1 [open] x'])}\n${round(2, 'pass', [`- F1 [fixed ${FIX}] x`])}${screens({
    shots: [SHOT, '- `.screens/board-390x844.png` — 390x844 — `/unit?ws=proj&id=0002_open-question&tab=questions` — S3: a /tmp path', 'prose between lines'],
  })}`
  const [one, two] = parseReview(text).rounds
  assert.equal(one.screens, null)
  assert.deepEqual(two.screens, {
    taken: SHA, standard: UI_STANDARD, by: 'an agent session (write-review)',
    header: `Taken at: ${SHA}. Standard: \`${UI_STANDARD}\`. Looked at by: an agent session (write-review), from screenshots.`,
    shots: [
      { path: '.screens/board-1440x900.png', size: '1440x900', address: '/board', result: 'no violation' },
      { path: '.screens/board-390x844.png', size: '390x844', address: '/unit?ws=proj&id=0002_open-question&tab=questions', result: 'S3: a /tmp path' },
    ],
  })
  // Findings still end where ### Screens begins, and the round's text keeps the section.
  assert.deepEqual(two.findings.map((f) => f.id), ['F1'])
  assert.match(two.text, /### Screens/)
  const bad = parseReview(`${round(1, 'pass')}${screens({ head: 'Looked at it.' })}`).rounds[0].screens
  assert.deepEqual([bad.taken, bad.standard, bad.by, bad.header], [null, null, null, 'Looked at it.'])
})

test('0083 R10: screensProblems names each thing wrong with the words', () => {
  const read = (s) => parseReview(`${round(1, 'pass')}${s}`).rounds[0].screens
  assert.deepEqual(screensProblems(read(screens()), UI_STANDARD), [])
  assert.match(screensProblems(null, UI_STANDARD).join('\n'), /no ### Screens/)
  assert.match(screensProblems(read(screens({ head: 'Taken at: abc. Looked.' })), UI_STANDARD).join('\n'), /first line of its ### Screens is not/)
  assert.match(screensProblems(read(screens({ by: 'Bao' })), UI_STANDARD).join('\n'), /"Looked at by: Bao", which does not say it was an agent/)
  assert.match(screensProblems(read(screens({ standard: '.claude/rules/other.md' })), UI_STANDARD).join('\n'), /names the standard \.claude\/rules\/other\.md, not/)
  assert.match(screensProblems(read(screens({ shots: ['- a.jpg — 1×1 — / — x'] })), UI_STANDARD).join('\n'), /lists no screenshot/)
})

test('0083 R11: an open low against the standard blocks; one that is not still does not', () => {
  const u = asked(round(1, 'pass', ['- F1 [open] coscc/screens.py:10 — low — S3 shows a full sha', low('F2'), '- F3 [open] a.py:3 — low — Sx is not a rule id']))
  assert.deepEqual(nonBlocking(u).map((f) => f.id), ['F2', 'F3'])
  const g = checkGate(branched({ ...CHAIN, 'review.md': reviewArt('accepted', round(1, 'pass', ['- F1 [open] coscc/screens.py:10 — low — S3 shows a full sha'])) }), 'ship', { probe: greenProbe() })
  assert.equal(g.ok, false)
  assert.match(g.need.join('\n'), /F1 \[open\]/)
})

// A probe whose repository has a standard listing one file, and whose branch diff against
// the trunk is `files`. `extra` overrides any other git answer.
const UI = { path: UI_STANDARD, globs: ['coscc/screens.py'] }
const uiProbe = (files, extra = {}, ui = UI) => ({
  ...greenProbe(undefined, { [`diff --name-only origin/main...${SHA}`]: ok(files.join('\n')), ...extra }),
  ui: () => ui,
})
const passed = (tail = '') => branched({ ...CHAIN, 'review.md': reviewArt('accepted', `${round(1, 'pass')}${tail}`) })

test('0083 R12: a unit that changes no screen reads exactly as with no standard', () => {
  const u = passed()
  const before = checkGate(u, 'ship', { probe: greenProbe() })
  for (const files of [['coscc/runner.py'], ['.cos/0001_x/review.md'], []]) {
    assert.deepEqual(checkGate(u, 'ship', { probe: uiProbe(files) }), before)
    assert.deepEqual(nextStep(u, { probe: uiProbe(files) }), nextStep(u, { probe: greenProbe() }))
  }
  // A unit's own files are left out even when a glob would take them.
  assert.deepEqual(checkGate(u, 'ship', { probe: uiProbe(['.cos/0001_x/x.py'], {}, { path: UI_STANDARD, globs: ['**/*.py'] }) }), before)
  // A standard with no globs is no standard.
  assert.deepEqual(checkGate(u, 'ship', { probe: uiProbe(['coscc/screens.py'], {}, { path: UI_STANDARD, globs: [] }) }), before)
})

test('0083 R10: a UI unit whose pass has no ### Screens cannot ship, and next offers review', () => {
  const u = passed()
  const g = checkGate(u, 'ship', { probe: uiProbe(['coscc/screens.py']) })
  assert.equal(g.ok, false)
  assert.equal(g.need.length, 1)
  assert.match(g.need[0], /review round 1 passed, but it has no ### Screens .* this unit changes coscc\/screens\.py, which \.claude\/rules\/ui-standard\.md counts as screens/)
  const n = nextStep(u, { probe: uiProbe(['coscc/screens.py']) })
  assert.equal(n.stage, 'review')
  assert.match(n.action, /no ### Screens/)
})

test('0083 R10: each condition, broken once, closes ship and says which', () => {
  const ui = ['coscc/screens.py']
  const closed = (tail, extra = {}) => {
    const g = checkGate(passed(tail), 'ship', { probe: uiProbe(ui, extra) })
    assert.equal(g.ok, false)
    assert.equal(nextStep(passed(tail), { probe: uiProbe(ui, extra) }).stage, 'review')
    return g.need.join('\n')
  }
  assert.match(closed(screens({ by: 'Bao' })), /does not say it was an agent/)
  assert.match(closed(screens({ standard: 'STANDARD.md' })), /names the standard STANDARD\.md/)
  assert.match(closed(screens({ shots: [] })), /lists no screenshot/)
  const TAKEN = 'e'.repeat(40)
  assert.match(closed(screens({ taken: TAKEN }), { [`merge-base --is-ancestor ${TAKEN} ${SHA}`]: { code: 1, out: '', err: '' } }),
    new RegExp(`taken at ${TAKEN}, which is not an ancestor of the reviewed commit ${SHA}`))
  assert.match(closed(screens({ taken: TAKEN }), { [`diff --name-only ${TAKEN}..${SHA}`]: ok('coscc/runner.py\ncoscc/screens.py\n') }),
    new RegExp(`coscc/screens\\.py changed after the screenshots of review round 1 were taken at ${TAKEN}`))
})

test('0083 R10: valid ### Screens on a UI unit opens ship, pinned to the head', () => {
  const TAKEN = 'e'.repeat(40)
  const g = checkGate(passed(screens({ taken: TAKEN })), 'ship', { probe: uiProbe(['coscc/screens.py'], { [`diff --name-only ${TAKEN}..${SHA}`]: ok('coscc/runner.py\n') }) })
  assert.deepEqual(g, { ok: true, need: [], head: SHA })
})

test('0083 C7: neither origin/main nor main readable closes ship, and no round is offered', () => {
  const fail = { code: 128, out: '', err: 'fatal: bad revision' }
  const probe = uiProbe([], { [`diff --name-only origin/main...${SHA}`]: fail, [`diff --name-only main...${SHA}`]: fail })
  const g = checkGate(passed(), 'ship', { probe })
  assert.equal(g.ok, false)
  assert.match(g.need[0], /cannot tell whether 0001_x changes a screen: .*the gate does not fetch/)
  assert.equal(nextStep(passed(), { probe }).stage, '')
  // main alone is enough.
  const local = uiProbe([], { [`diff --name-only origin/main...${SHA}`]: fail, [`diff --name-only main...${SHA}`]: ok('coscc/screens.py\n') })
  assert.match(checkGate(passed(), 'ship', { probe: local }).need[0], /no ### Screens/)
})

test('0083 R12: no standard asks git nothing more; a standard and no screen, one diff more', () => {
  const counting = (probe) => {
    const calls = []
    return { calls, probe: { ...probe, git: (...args) => (calls.push(args.join(' ')), probe.git(...args)) } }
  }
  const u = passed()
  const plain = counting(greenProbe())
  checkGate(u, 'ship', { probe: plain.probe })
  const empty = counting(uiProbe(['coscc/screens.py'], {}, null))
  checkGate(u, 'ship', { probe: empty.probe })
  assert.deepEqual(empty.calls, plain.calls)
  const some = counting(uiProbe(['coscc/runner.py']))
  checkGate(u, 'ship', { probe: some.probe })
  assert.deepEqual(some.calls, [...plain.calls, `diff --name-only origin/main...${SHA}`])
  // A unit closed for an earlier reason never reaches the question.
  const stuck = counting(uiProbe(['coscc/screens.py']))
  checkGate(branched({ ...CHAIN, 'review.md': reviewArt('accepted', round(1, 'pass', ['- F1 [open] x'])) }), 'ship', { probe: stuck.probe })
  assert.deepEqual(stuck.calls, [])
})

// --- `0085`: a review that ran out of turns -------------------------------------

const incompleteRound = (n) =>
  `## Round ${n}\n\nReviewed: ${SHA}. Verdict: incomplete.\n\n### Reviewed so far\n\n- a.py\n\n### Findings\n\n- F1 [open] a.py:3 — high — x\n\n### What was not reviewed\n\n- b.py\n`
const reviewOfRounds = (status, rounds) =>
  branched({ ...CHAIN, 'review.md': reviewArt(status, `# Review: x\nStatus: ${status}.\n\n${rounds.join('\n')}`) })
const cr = (n) => round(n, 'changes-requested', ['- F1 [open] x'])

test('0085 R8 a: an incomplete round is read as one, with its verdict', () => {
  const { rounds } = parseReview(`# Review\nStatus: draft.\n\n${incompleteRound(1)}`)
  assert.deepEqual(rounds.map((r) => [r.n, r.verdict, r.reviewed]), [[1, 'incomplete', SHA]])
  assert.deepEqual(rounds[0].findings.map((f) => [f.id, f.label]), [['F1', 'open']])
})

test('0085 R8 b: draft over an incomplete round offers review on green, impl on red', () => {
  const u = reviewOfRounds('draft', [cr(1), incompleteRound(2)])
  const green = nextStep(u, { probe: greenProbe() })
  assert.equal(green.stage, 'review')
  assert.match(green.action, /review round 2 is incomplete — write-review again; CI is green: write-review/)
  assert.equal(nextStep(u, { probe: greenProbe([{ name: 'tests', bucket: 'fail' }]) }).stage, 'impl')
  assert.equal(nextStep(u, { probe: greenProbe([{ name: 'tests', bucket: 'pending' }]) }).stage, '')
  // No probe: nothing, and it says --repo, as every other CI-bound answer does.
  const bare = nextStep(u)
  assert.equal(bare.stage, '')
  assert.match(bare.action, /pass --repo/)
  assert.doesNotMatch(bare.action, /finish and accept/)
  assert.equal(checkGate(u, 'review', { probe: greenProbe() }).ok, true)
  // The ship gate stays closed on it: the draft header closes it before any round is read.
  const ship = checkGate(u, 'ship', { probe: greenProbe() })
  assert.equal(ship.ok, false)
  assert.match(ship.need.join('\n'), /review\.md is "draft", not accepted/)
  // Under a header that would reach the rounds, the verdict itself closes it.
  const accepted = reviewOfRounds('accepted', [incompleteRound(1)])
  assert.match(checkGate(accepted, 'ship', { probe: greenProbe() }).need.join('\n'), /verdict "incomplete", not pass/)
})

test('0085 R9 c: an incomplete round never changes how many rounds are used', () => {
  const used = (rounds, limit = 3) => nextStep(reviewOfRounds('changes-requested', rounds), { limit }).action
  assert.match(used([cr(1), cr(2)]), /\(2 of 3 rounds used\)/)
  assert.match(used([cr(1), incompleteRound(2), cr(3)]), /\(2 of 3 rounds used\)/)
  assert.match(used([cr(1), cr(2)], 2), /needs a person/)
  assert.match(used([cr(1), incompleteRound(2), cr(3)], 2), /needs a person/)
  // While the incomplete round is last, the rounds before it are held to the limit too.
  assert.equal(nextStep(reviewOfRounds('draft', [cr(1), incompleteRound(2)]), { limit: 2, probe: greenProbe() }).stage, 'review')
  const spent = reviewOfRounds('draft', [cr(1), cr(2), incompleteRound(3)])
  assert.match(nextStep(spent, { limit: 2, probe: greenProbe() }).action, /needs a person/)
  assert.equal(checkGate(spent, 'review', { limit: 2, probe: greenProbe() }).ok, false)
  // The floor survives the draft an incomplete round leaves: a full round with no readable
  // verdict before it still counts as one, as it would under `changes-requested`.
  const unread = `## Round 1\n\nReviewed: somewhere. Verdict: maybe.\n\n### Findings\n\n- F1 [open] x\n`
  assert.match(nextStep(reviewOfRounds('changes-requested', [unread]), { limit: 1 }).action, /needs a person/)
  assert.match(nextStep(reviewOfRounds('draft', [unread, incompleteRound(2)]), { limit: 1 }).action, /needs a person/)
  assert.equal(nextStep(reviewOfRounds('draft', [incompleteRound(1)]), { limit: 1, probe: greenProbe() }).stage, 'review')
})

test('0085 R8 d: a draft whose last round is not incomplete is still finished by hand', () => {
  for (const verdict of ['changes-requested', 'pass']) {
    const n = nextStep(reviewOfRounds('draft', [round(1, verdict, ['- F1 [open] x'])]), { probe: greenProbe() })
    assert.equal(n.stage, '')
    assert.match(n.action, /finish and accept review\.md/)
  }
  assert.match(nextStep(reviewOfRounds('draft', [])).action, /finish and accept review\.md/)
  // An incomplete round under any other header is not the closing turn's.
  assert.doesNotMatch(nextStep(reviewOfRounds('changes-requested', [cr(1), incompleteRound(2)]), { probe: greenProbe() }).action, /is incomplete/)
})

// --- 0044: an answer Jera gave opens nothing a person's does not ------------------------

const specWith = (by, via) =>
  `# Spec: x\nIntent: intent.md. Author: t. Status: accepted.\n\n## Open questions\n\n1. a?\n2. b?\n\n## Answers\n` +
  `\n### Câu 1\nAnswered by: ${by}. Date: 2026-09-25. Via: ${via}.\n\nyes\n` +
  `\n### Câu 2\nAnswered by: ${by}. Date: 2026-09-25. Via: ${via}.\n\nno\n`

test('0044 R14: gate and next answer the same whether a person or Jera answered', () => {
  const files = (by, via) => ({
    'intent.md': '# I\nAuthor: t. Type: feat. Status: accepted.\n\n## Open questions\n\n1. c?\n' +
      `\n## Answers\n\n### Câu 1\nAnswered by: ${by}. Date: 2026-09-25. Via: ${via}.\n\nc\n`,
    'spec.md': specWith(by, via),
  })
  for (const more of [{}, { 'plan.md': '# P\nStatus: accepted.\n' }]) {
    const person = questionTree({ ...files('owner', 'product'), ...more }).u
    const jera = questionTree({ ...files('Jera', 'precedent'), ...more }).u
    assert.equal(jera.artifacts['spec.md'].questions.every((q) => q.answered && q.answer.by === 'Jera'), true)
    for (const stage of [...STAGE_NAMES, 'implement']) {
      assert.deepEqual(checkGate(jera, stage), checkGate(person, stage), stage)
      assert.deepEqual(checkGate(jera, stage, { probe: greenProbe() }), checkGate(person, stage, { probe: greenProbe() }), stage)
    }
    assert.deepEqual(nextAction(jera), nextAction(person))
    assert.deepEqual(nextStep(jera, { probe: greenProbe() }), nextStep(person, { probe: greenProbe() }))
  }
})

test('0044 R14: a Jera block in spec.md does not change what a review waits on', () => {
  const build = (spec) => questionTree({
    'intent.md': '# I\nAuthor: t. Type: fix. Status: accepted.\n',
    'spec.md': spec,
    'plan.md': '# P\nStatus: accepted.\n',
    'impl.md': implText(),
    'pr.md': '# PR\nPR: https://github.com/o/r/pull/7. Status: accepted.\n',
    'review.md': `${REVIEW_HEAD}${ROUND1}\n${ROUND2}\n${ROUND3()}`,
  }).u
  const person = build(specWith('owner', 'product'))
  const jera = build(specWith('Jera', 'precedent'))
  assert.deepEqual(jera.personFindings, person.personFindings)
  assert.deepEqual(nextStep(jera, { probe: greenProbe() }).waiting, ['F2', 'F3'])
  assert.deepEqual(nextStep(jera, { probe: greenProbe() }), nextStep(person, { probe: greenProbe() }))
  assert.deepEqual(nextAction(jera), nextAction(person))
  for (const stage of STAGE_NAMES) {
    assert.deepEqual(checkGate(jera, stage, { probe: greenProbe() }), checkGate(person, stage, { probe: greenProbe() }), stage)
  }
})

// --- the stage a unit is at (0100) ---------------------------------------------

test('0100: stageAt is the stage next names, else the last artifact on disk, else the first stage', () => {
  const at = (artifacts) => { const u = unit(artifacts); return stageAt(u, nextAction(u)) }
  assert.equal(at({ 'intent.md': art('accepted'), 'spec.md': art('accepted') }), 'plan', 'next.stage has a value')
  assert.equal(at({ 'intent.md': art('accepted'), 'spec.md': art('accepted'), 'plan.md': art('draft') }), 'plan', 'plan.md draft')
  const asked = { ...CHAIN, 'review.md': reviewArt('changes-requested', round(1, 'changes-requested', ['- F1 [open] x'])) }
  assert.equal(nextAction(unit(asked)).stage, '')
  assert.equal(at(asked), 'review', 'review.md changes-requested')
  assert.equal(at({ 'idea.md': art('accepted') }), 'intent', 'pre-intent: only an accepted idea')
  assert.equal(stageAt(unit({}), { stage: '' }), 'intent', 'no artifact at all')
})

test('0100: status --json carries at and next.why for every unit', () => {
  const root = mkdtempSync(join(tmpdir(), 'cos-0100-'))
  const cos = join(root, '.cos')
  const put = (u, f, text) => { mkdirSync(join(cos, u), { recursive: true }); writeFileSync(join(cos, u, f), text) }
  put('0001_open', 'intent.md', '# X\nType: feat. Status: accepted.\n')
  put('0002_draft', 'intent.md', '# X\nType: fix. Status: draft.\n')
  put('0003_done', 'intent.md', '# X\nType: fix. Status: accepted.\n')
  put('0003_done', 'plan.md', '# X\nStatus: done.\n')
  const cli = (...args) =>
    spawnSync(process.execPath, [fileURLToPath(new URL('./cos.mjs', import.meta.url)), ...args, '--root', root], { encoding: 'utf8' })
  const status = JSON.parse(cli('status', '--json').stdout)
  assert.deepEqual(
    status.units.map((u) => [u.name, u.at, u.next.why]),
    [['0001_open', 'spec', 'missing'], ['0002_draft', 'intent', 'draft'], ['0003_done', 'plan', 'finished']],
  )
  const names = status.stages.map((s) => s.name)
  assert.ok(status.units.every((u) => names.includes(u.at) && typeof u.next.why === 'string'))
})

test('0100: the line cos.mjs next prints carries no why', () => {
  const root = mkdtempSync(join(tmpdir(), 'cos-0100-'))
  const dir = join(root, '.cos', '0001_open')
  mkdirSync(dir, { recursive: true })
  writeFileSync(join(dir, 'intent.md'), '# X\nType: feat. Status: accepted.\n')
  const run = spawnSync(process.execPath, [fileURLToPath(new URL('./cos.mjs', import.meta.url)), 'next', '0001_open', '--root', root], { encoding: 'utf8' })
  const line = JSON.parse(run.stdout)
  assert.equal(line.stage, 'spec')
  assert.ok(!('why' in line), 'next carries no why')
  assert.ok(!('at' in line), 'next carries no at')
})

// --- 0054: an accepted stage run again from the board -----------------------------

// A unit with every artifact up to a passed review, like the board's `0003_awaiting-ship`.
const RERUN_FILES = {
  'intent.md': '# Intent: x\nType: feat. Status: accepted.\n\n## Open questions\n\n1. Một?\n',
  'spec.md': '# Spec: x\nIntent: intent.md. Status: accepted.\n\nR1.\n',
  'plan.md': '# Plan: x\nStatus: accepted.\n\n1. build it\n',
  'impl.md': '# Impl: x\nStatus: accepted.\n\nbuilt\n',
  'pr.md': '# PR: x\nPR: https://github.com/o/r/pull/7. Status: accepted.\n\nbody\n',
  'review.md': `# Review: x\nStatus: accepted.\n\n${round(1, 'pass')}`,
}

function rerunTree(files = RERUN_FILES, name = '0003_awaiting-ship') {
  const root = mkdtempSync(join(tmpdir(), 'cos-0054-'))
  const dir = join(root, '.cos', name)
  mkdirSync(dir, { recursive: true })
  for (const [f, text] of Object.entries(files)) writeFileSync(join(dir, f), text)
  const cli = (...args) =>
    spawnSync(process.execPath, [fileURLToPath(new URL('./cos.mjs', import.meta.url)), ...args, '--root', root], { encoding: 'utf8' })
  const read = () => readUnit(dir, name)
  // What the app does with the block: append it under `intent.md ## Answers`.
  const append = (file, tail) => {
    const was = readFileSync(join(dir, file), 'utf8')
    writeFileSync(join(dir, file), was + (was.includes('\n## Answers') ? '' : '\n## Answers\n') + '\n' + tail)
  }
  const rerun = (stage) => {
    const out = cli('rerun', name, stage)
    assert.equal(out.status, 0, out.stderr)
    const answer = JSON.parse(out.stdout)
    append('intent.md', answer.block)
    return answer
  }
  return { root, dir, name, cli, read, append, rerun, write: (f, t) => writeFileSync(join(dir, f), t) }
}

test('0054 R4: the hash above ## Answers does not move when an answer is appended', () => {
  const text = '# Spec: x\nStatus: accepted.\n\nR1.\n'
  const hash = aboveAnswers(text)
  assert.match(hash, /^[0-9a-f]{64}$/)
  // The two ways a section is opened: the runner's `with_answers`, and the app's append.
  assert.equal(aboveAnswers(`${text}\n## Answers\n\n### Câu 1\nAnswered by: A. Date: 2026-09-26. Via: product.\n\nx\n`), hash)
  assert.equal(aboveAnswers(`${text.trimEnd()}\n\n## Answers\n`), hash)
  assert.notEqual(aboveAnswers(text.replace('R1.', 'R1, rewritten.')), hash)
})

test('0054: a ### Rerun block ends the answer before it and is never an answer', () => {
  const block = '### Rerun\nRequested by: owner. Date: 2026-09-26. Via: product.\nStage: pr.\nStale: pr.md sha256:' + 'c'.repeat(64) + '\n'
  const text = withAnswers(answerBlock(1, 'A', 'Có.'), `\n${block}`, answerBlock(2, 'B', 'Không.'))
  assert.deepEqual(parseAnswers(text).map((a) => [a.n, a.text]), [[1, 'Có.'], [2, 'Không.']])
  assert.equal(parseHold(text).hold, null)
  assert.deepEqual(parseReruns(text).reruns, [
    { stage: 'pr', by: 'owner', date: '2026-09-26', stale: { 'pr.md': 'c'.repeat(64) } },
  ])
})

test('0054: a malformed ### Rerun block is ignored and reported', () => {
  const t = rerunTree()
  t.append('intent.md', '### Rerun\nStage: pr.\nStale: pr.md sha256:' + aboveAnswers(RERUN_FILES['pr.md']) + '\n')
  const u = t.read()
  assert.equal(u.artifacts['pr.md'].stale, undefined)
  assert.match(u.problems.join('\n'), /intent\.md: rerun block 1 has no well-formed/)
})

test('0054 R1, R2: a unit up to a passed review is offered intent, spec, plan and pr', () => {
  const t = rerunTree()
  const out = t.cli('rerun', t.name)
  assert.equal(out.status, 0, out.stderr)
  const answer = JSON.parse(out.stdout)
  assert.deepEqual(answer.offers.map((o) => o.stage), ['intent', 'spec', 'plan', 'pr'])
  assert.equal(answer.why, '')
  const later = Object.fromEntries(answer.offers.map((o) => [o.stage, o.later]))
  assert.deepEqual(later.pr, ['review', 'ship'])
  assert.deepEqual(later.plan, ['impl', 'pr', 'review', 'ship'])
  assert.deepEqual(later.intent, ['spec', 'plan', 'impl', 'pr', 'review', 'ship'])
  assert.deepEqual(RERUNNABLE, ['intent', 'spec', 'spike', 'plan', 'pr'])
})

test('0054 R3: the block names the stage, owner and a hash per artifact, and no approval', () => {
  const t = rerunTree()
  const out = t.cli('rerun', t.name, 'pr')
  assert.equal(out.status, 0, out.stderr)
  const { stage, later, block } = JSON.parse(out.stdout)
  assert.equal(stage, 'pr')
  assert.deepEqual(later, ['review', 'ship'])
  const lines = block.trimEnd().split('\n')
  assert.equal(lines[0], '### Rerun')
  assert.match(lines[1], /^Requested by: owner\. Date: \d{4}-\d{2}-\d{2}\. Via: product\.$/)
  assert.equal(lines[2], 'Stage: pr.')
  // `ship.md` does not exist, so it has no line.
  assert.deepEqual(lines.slice(3), [
    `Stale: pr.md sha256:${aboveAnswers(RERUN_FILES['pr.md'])}`,
    `Stale: review.md sha256:${aboveAnswers(RERUN_FILES['review.md'])}`,
  ])
  assert.doesNotMatch(block, /approved|accepted|Decided by/i)
})

test('0054 R5, R10: rerunning pr closes ship until pr and then review are written again', () => {
  const t = rerunTree()
  const probe = greenProbe()
  assert.equal(checkGate(t.read(), 'ship', { probe }).need.some((n) => /stale/.test(n)), false)
  t.rerun('pr')
  let u = t.read()
  assert.deepEqual(u.artifacts['pr.md'].stale, { stage: 'pr', date: u.artifacts['review.md'].stale.date })
  // The stage run again comes first: a rerun that never ran is offered again.
  assert.equal(nextStep(u, { probe }).stage, 'pr')
  assert.match(nextStep(u, { probe }).action, /^pr\.md is stale — pr was rerun on .*: write-pr again$/)
  // An answer appended to pr.md does not make it fresh.
  t.append('pr.md', answerBlock(1, 'A', 'x'))
  assert.ok(t.read().artifacts['pr.md'].stale)

  t.write('pr.md', RERUN_FILES['pr.md'].replace('body', 'a new body'))
  u = t.read()
  assert.equal(u.artifacts['pr.md'].stale, undefined)
  assert.ok(u.artifacts['review.md'].stale)
  assert.equal(nextStep(u, { probe }).stage, 'review')
  const ship = checkGate(u, 'ship', { probe })
  assert.equal(ship.ok, false)
  assert.match(ship.need.join('\n'), /review\.md is stale: pr was rerun on .* — run review again first/)
  // Red CI sends a stale review back to impl, as a missing one would be.
  assert.equal(nextStep(u, { probe: greenProbe([{ name: 'tests', bucket: 'fail' }]) }).stage, 'impl')
  // With no repository, `next` offers nothing and says why, the review first.
  assert.match(nextStep(u).action, /^review\.md is stale/)
  assert.equal(nextStep(u).stage, '')

  t.write('review.md', `${RERUN_FILES['review.md']}${round(2, 'pass')}`)
  assert.equal(t.read().artifacts['review.md'].stale, undefined)
  assert.equal(checkGate(t.read(), 'ship').need.some((n) => /stale/.test(n)), false)
})

test('0054 R5: a stale artifact before the target closes its gate, and names the stage run again', () => {
  const t = rerunTree()
  t.rerun('plan')
  const u = t.read()
  for (const f of ['plan.md', 'impl.md', 'pr.md', 'review.md']) assert.ok(u.artifacts[f].stale, f)
  assert.equal(u.artifacts['spec.md'].stale, undefined)
  assert.equal(checkGate(u, 'plan').ok, true)
  const impl = checkGate(u, 'impl')
  assert.equal(impl.ok, false)
  assert.deepEqual(impl.need, [`plan.md is stale: plan was rerun on ${u.artifacts['plan.md'].stale.date} — run plan again first`])
  assert.equal(nextAction(u).stage, 'plan')
  // Already stale: not offered again, the run button offers it.
  const offers = JSON.parse(t.cli('rerun', t.name).stdout).offers.map((o) => o.stage)
  assert.deepEqual(offers, ['intent', 'spec'])
  assert.equal(t.cli('rerun', t.name, 'plan').status, 1)
})

test('0054 R4: a changes-requested review and a spike the spec no longer needs are never stale', () => {
  const review = `# Review: x\nStatus: changes-requested.\n\n${round(1, 'changes-requested', ['- F1 [open] x'])}`
  const spike = '# Spike: x\nStatus: accepted.\n\n## U1\n\nVerdict: holds.\n'
  const t = rerunTree({ ...RERUN_FILES, 'review.md': review, 'spike.md': spike })
  t.append('intent.md', [
    '### Rerun', 'Requested by: owner. Date: 2026-09-26. Via: product.', 'Stage: spec.',
    `Stale: spike.md sha256:${aboveAnswers(spike)}`, `Stale: review.md sha256:${aboveAnswers(review)}`, '',
  ].join('\n'))
  const u = t.read()
  assert.equal(u.artifacts['review.md'].stale, undefined)
  assert.equal(u.artifacts['spike.md'].stale, undefined)
})

test('0054 R1: nothing is offered on a finished, held or closed unit', () => {
  const done = rerunTree({ ...RERUN_FILES, 'plan.md': '# Plan: x\nStatus: done.\n' })
  assert.deepEqual(JSON.parse(done.cli('rerun', done.name).stdout), {
    unit: done.name, offers: [], why: 'the unit is finished: plan.md is done',
  })
  const refused = done.cli('rerun', done.name, 'pr')
  assert.equal(refused.status, 1)
  assert.match(refused.stderr, /pr cannot be run again for .*: the unit is finished/)

  const held = rerunTree()
  held.append('intent.md', holdBlock('Paused', 'chờ'))
  assert.deepEqual(JSON.parse(held.cli('rerun', held.name).stdout).offers, [])

  const closed = rerunTree({ ...RERUN_FILES, 'impl.md': '# Impl: x\nStatus: rejected.\n' })
  assert.match(JSON.parse(closed.cli('rerun', closed.name).stdout).why, /impl\.md is rejected/)
})

test('0054 R1: only an accepted artifact is offered, spike only when the spec needs one', () => {
  const fresh = rerunTree({ 'intent.md': RERUN_FILES['intent.md'] }, '0001_fresh-intent')
  assert.deepEqual(JSON.parse(fresh.cli('rerun', fresh.name).stdout).offers, [
    { stage: 'intent', later: ['spec', 'plan', 'impl', 'pr', 'review', 'ship'] },
  ])
  const draft = rerunTree({ ...RERUN_FILES, 'plan.md': '# Plan: x\nStatus: draft.\n' })
  assert.deepEqual(JSON.parse(draft.cli('rerun', draft.name).stdout).offers.map((o) => o.stage), ['intent', 'spec'])
  // A skipped spec counts as settled; its later list has no spike.
  const skipped = rerunTree({ ...RERUN_FILES, 'spec.md': '# Spec: x\nStatus: skipped.\n' })
  assert.deepEqual(JSON.parse(skipped.cli('rerun', skipped.name).stdout).offers[1],
    { stage: 'spec', later: ['plan', 'impl', 'pr', 'review', 'ship'] })

  const spec = '# Spec: x\nStatus: accepted.\n\n## Concerns\n\n- [unmeasured] U1 nhanh không?\n'
  const spike = '# Spike: x\nStatus: accepted.\n\n## U1\n\nVerdict: holds.\n\n```\n$ x\n1\n```\n'
  const plan = '# Plan: x\nStatus: accepted.\n\n1. build it (spike.md ## U1)\n'
  const measured = rerunTree({ ...RERUN_FILES, 'spec.md': spec, 'spike.md': spike, 'plan.md': plan })
  const offers = JSON.parse(measured.cli('rerun', measured.name).stdout).offers
  assert.deepEqual(offers.map((o) => o.stage), ['intent', 'spec', 'spike', 'plan', 'pr'])
  assert.deepEqual(offers[1].later, ['spike', 'plan', 'impl', 'pr', 'review', 'ship'])
})

test('0054: rerun refuses a stage it does not offer, an unknown one, and --repo', () => {
  const t = rerunTree()
  const review = t.cli('rerun', t.name, 'review')
  assert.equal(review.status, 1)
  assert.match(review.stderr, /review cannot be run again from the board — only intent, spec, spike, plan, pr/)
  assert.equal(t.cli('rerun', t.name, 'spike').status, 1)
  assert.equal(t.cli('rerun', t.name, 'nonsense').status, 2)
  assert.equal(t.cli('rerun', '0009_nope').status, 2)
  assert.equal(t.cli('rerun', '../x').status, 2)
  assert.equal(t.cli('rerun').status, 2)
  const repo = t.cli('rerun', t.name, '--repo', t.root)
  assert.equal(repo.status, 2)
  assert.match(repo.stderr, /--repo applies only to `gate` and `next`/)
})

// --- 0106: a draft whose questions are all answered names its stage as `rerun` ---

const DRAFT_INTENT = '# Intent: x\nType: feat. Status: draft.\n\n## Open questions\n\n1. Một?\n2. Hai?\n\n## Answers\n'

function answeredTree(files) {
  const { root, u } = questionTree(files)
  const next = () => JSON.parse(cli('--root', root, 'next', '0001_q').stdout)
  return { root, u, next }
}

test('0106 R1: a draft intent with every question answered is rerun: intent, and nothing else changes', () => {
  const { next } = answeredTree({ 'intent.md': DRAFT_INTENT + answerBlock(1, 'A', 'x') + answerBlock(2, 'A', 'y') })
  assert.deepEqual(next(), { unit: '0001_q', stage: '', action: 'finish and accept intent.md', blocked: true, rerun: 'intent' })
})

test('0106 R1: one question left unanswered is no rerun', () => {
  const { u, next } = answeredTree({ 'intent.md': DRAFT_INTENT + answerBlock(1, 'A', 'x') })
  assert.equal('rerun' in next(), false)
  assert.equal(nextStep(u).rerun, undefined)
})

test('0106 R1: a held unit is no rerun, even with every question answered', () => {
  const { next } = answeredTree({ 'intent.md': DRAFT_INTENT + answerBlock(1, 'A', 'x') + answerBlock(2, 'A', 'y') + holdBlock('Paused', 'chờ') })
  const n = next()
  assert.equal(n.hold.state, 'paused')
  assert.equal('rerun' in n, false)
})

test('0106 R1: a draft with no questions, or a draft impl, is no rerun', () => {
  assert.equal('rerun' in answeredTree({ 'intent.md': '# I\nType: feat. Status: draft.\n' }).next(), false)
  const impl = answeredTree({
    'intent.md': '# I\nType: feat. Status: accepted.\n',
    'spec.md': '# S\nStatus: accepted.\n',
    'plan.md': '# P\nStatus: accepted.\n',
    'impl.md': '# Impl\nStatus: draft.\n\n## Open questions\n\n1. Một?\n\n## Answers\n' + answerBlock(1, 'A', 'x'),
  })
  assert.equal('rerun' in impl.next(), false)
})

test('0106 R1: a spec draft answered in full is rerun: spec', () => {
  const { u } = answeredTree({
    'intent.md': '# I\nType: feat. Status: accepted.\n',
    'spec.md': '# S\nStatus: draft.\n\n## Open questions\n\n1. Một?\n\n## Answers\n' + answerBlock(1, 'A', 'x'),
  })
  assert.equal(nextStep(u).rerun, 'spec')
})

test('0106 R1: status --json and nextAction never carry rerun', () => {
  const { root, u } = answeredTree({ 'intent.md': DRAFT_INTENT + answerBlock(1, 'A', 'x') + answerBlock(2, 'A', 'y') })
  assert.equal('rerun' in nextAction(u), false)
  assert.equal(cli('--root', root, 'status', '--json').stdout.includes('rerun'), false)
})

// --- `0111`: a rebase no longer leaves review with stale screenshots -------------

const OLD = 'c'.repeat(40)
const MANIFEST = { head: OLD, dirty: false, addresses: ['/board'], hits: [{ address: '/board', size: '1440x900', kind: 'path', snippet: '/tmp/x' }], shots: [] }
// A probe for `screensAnswer`: the branch changes `files`, `.screens/manifest.json` reads
// `manifest`, and the manifest's head is no longer an ancestor of `HEAD` unless `ancestor`.
const answerProbe = ({ files = ['coscc/screens.py'], manifest = MANIFEST, ancestor = 1, ui = UI, git = {} } = {}) => {
  const calls = []
  return {
    calls,
    ui: () => ui,
    manifest: () => manifest,
    git: (...args) => {
      const key = args.join(' ')
      calls.push(key)
      if (key in git) return git[key]
      if (key === 'diff --name-only origin/main...HEAD') return ok(files.join('\n'))
      if (key === `merge-base --is-ancestor ${OLD} HEAD`) return { code: ancestor, out: '', err: '' }
      return ok()
    },
  }
}

test('0111 R1, R2: a UI unit whose clean manifest names a rewritten head is taken again', () => {
  assert.deepEqual(screensAnswer(unit({}), answerProbe()), {
    unit: '0001_x', ui: ['coscc/screens.py'],
    manifest: { head: OLD, dirty: false, addresses: ['/board'], hits: MANIFEST.hits },
    rewritten: true, retake: true, why: '',
  })
  // A commit gone from the object store is rewritten too: git exits 128, not 1.
  assert.equal(screensAnswer(unit({}), answerProbe({ ancestor: 128 })).retake, true)
})

test('0111 R2: each condition, broken once, is no retake and says which', () => {
  const said = (opts) => {
    const a = screensAnswer(unit({}), answerProbe(opts))
    assert.equal(a.retake, false)
    return a
  }
  assert.match(said({ files: ['coscc/runner.py'] }).why, /changes no file the UI standard counts as a screen/)
  assert.match(said({ manifest: null }).why, /no readable \.screens\/manifest\.json/)
  assert.equal(said({ manifest: null }).manifest, null)
  assert.match(said({ manifest: [1, 2] }).why, /no readable/)
  assert.match(said({ manifest: { ...MANIFEST, addresses: [] } }).why, /lists no addresses/)
  assert.match(said({ manifest: { ...MANIFEST, addresses: undefined } }).why, /lists no addresses/)
  assert.match(said({ manifest: { ...MANIFEST, dirty: true } }).why, /uncommitted changes/)
  assert.match(said({ manifest: { ...MANIFEST, dirty: undefined } }).why, /uncommitted changes/)
  const kept = said({ ancestor: 0 })
  assert.equal(kept.rewritten, false)
  assert.match(kept.why, new RegExp(`head ${OLD} is still an ancestor of HEAD`))
  // A head that is no commit name is not handed to git at all.
  const odd = answerProbe({ manifest: { ...MANIFEST, head: '--output=/tmp/x' } })
  const a = screensAnswer(unit({}), odd)
  assert.equal(a.retake, false)
  assert.match(a.why, /names no commit/)
  assert.equal(odd.calls.some((c) => c.startsWith('merge-base')), false)
})

test('0111 R1: no standard, or one with no globs, is ui [] and asks git no diff', () => {
  for (const ui of [null, { path: UI_STANDARD, globs: [] }]) {
    const probe = answerProbe({ ui })
    const a = screensAnswer(unit({}), probe)
    assert.deepEqual([a.ui, a.retake], [[], false])
    assert.equal(probe.calls.some((c) => c.startsWith('diff')), false)
  }
})

test('0111 R1: git that cannot diff is no retake, and why is its error; main alone is enough', () => {
  const fail = { code: 128, out: '', err: 'fatal: bad revision' }
  const a = screensAnswer(unit({}), answerProbe({ git: { 'diff --name-only origin/main...HEAD': fail, 'diff --name-only main...HEAD': fail } }))
  assert.deepEqual([a.ui, a.retake], [[], false])
  assert.match(a.why, /cannot tell whether 0001_x changes a screen: .*fatal: bad revision/)
  const local = screensAnswer(unit({}), answerProbe({ git: { 'diff --name-only origin/main...HEAD': fail, 'diff --name-only main...HEAD': ok('coscc/screens.py\n') } }))
  assert.equal(local.retake, true)
})

test('0111 R9: screensAnswer and the ship gate leave out the same files', () => {
  // The unit's own `.cos/` files are left out of both, even when a glob takes them.
  const globs = { path: UI_STANDARD, globs: ['**/*.py'] }
  assert.deepEqual(screensAnswer(unit({}), answerProbe({ files: ['.cos/0001_x/x.py'], ui: globs })).ui, [])
  const g = checkGate(passed(), 'ship', { probe: uiProbe(['.cos/0001_x/x.py'], {}, globs) })
  assert.deepEqual(g, checkGate(passed(), 'ship', { probe: greenProbe() }))
  assert.equal(typeof screensNeeds, 'function')
})

test('0111 R1: cos.mjs screens, on a real repository', () => {
  const root = mkdtempSync(join(tmpdir(), 'cos-0111-'))
  const store = join(root, 'store')
  const repo = join(root, 'repo')
  mkdirSync(join(store, '.cos', '0001_x'), { recursive: true })
  writeFileSync(join(store, '.cos', '0001_x', 'intent.md'), '# I\nType: fix. Status: accepted.\n')
  mkdirSync(join(repo, '.claude', 'rules'), { recursive: true })
  mkdirSync(join(repo, 'coscc'), { recursive: true })
  const sh = (...args) => {
    const r = spawnSync('git', ['-c', 'user.name=T', '-c', 'user.email=t@example.invalid', '-c', 'commit.gpgsign=false', ...args], { cwd: repo, encoding: 'utf8' })
    assert.equal(r.status, 0, r.stderr)
    return r.stdout.trim()
  }
  sh('init', '-q', '-b', 'main')
  writeFileSync(join(repo, UI_STANDARD), '---\npaths:\n  - "coscc/screens.py"\n---\n')
  writeFileSync(join(repo, '.gitignore'), '.screens/\n')
  sh('add', '.')
  sh('commit', '-q', '-m', 'first')
  sh('switch', '-q', '-c', 'fix/x')
  writeFileSync(join(repo, 'coscc', 'screens.py'), '# a screen\n')
  sh('add', '.')
  sh('commit', '-q', '-m', 'a screen')
  const taken = sh('rev-parse', 'HEAD')
  sh('commit', '-q', '--amend', '-m', 'a screen, rewritten')
  mkdirSync(join(repo, '.screens'))
  writeFileSync(join(repo, '.screens', 'manifest.json'), JSON.stringify({ ...MANIFEST, head: taken }))

  const run = (...args) => cli('--root', store, ...args)
  const out = run('screens', '0001_x', '--repo', repo)
  assert.equal(out.status, 0, out.stderr)
  const a = JSON.parse(out.stdout)
  assert.deepEqual([a.ui, a.manifest.head, a.rewritten, a.retake, a.why], [['coscc/screens.py'], taken, true, true, ''])
  // Taken again at HEAD: nothing to do.
  writeFileSync(join(repo, '.screens', 'manifest.json'), JSON.stringify({ ...MANIFEST, head: sh('rev-parse', 'HEAD') }))
  assert.equal(JSON.parse(run('screens', '0001_x', '--repo', repo).stdout).retake, false)
  // Misuse is exit 2.
  assert.equal(run('screens').status, 2)
  assert.equal(run('screens', '0002_none', '--repo', repo).status, 2)
  assert.equal(run('screens', '../x', '--repo', repo).status, 2)
  const bare = run('screens', '0001_x')
  assert.equal(bare.status, 2)
  assert.match(bare.stderr, /pass --repo/)
  // It writes nothing into the store.
  assert.equal(readdirSync(join(store, '.cos', '0001_x')).join(','), 'intent.md')
})
