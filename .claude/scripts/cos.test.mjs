import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'
import {
  parseStatus, parseSkipReason, checkGate, nextAction, nextNumber, readUnit, STAGE_NAMES,
  BRANCH_TYPES, branchProblem, tagProblem, isPrerelease, tagVersion, versionProblem, parseType,
  unitBranch, VERSION_SOURCE,
} from './cos.mjs'

const unit = (artifacts) => ({ name: '0001_x', artifacts, problems: [] })
const art = (status) => ({ status, skipReason: null })

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
  assert.equal(checkGate(unit({ ...withImpl, 'pr.md': art('accepted') }), 'review').ok, true)
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
    writeFileSync(join(dir, f), 'Status: accepted.\n')
  }
  const u = readUnit(dir, '0009_widened')
  assert.deepEqual(u.problems, [])
  assert.equal(Object.keys(u.artifacts).length, 8)

  writeFileSync(join(dir, 'notes.md'), 'x')
  assert.match(readUnit(dir, '0009_widened').problems[0], /unexpected file\(s\): notes\.md/)
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
