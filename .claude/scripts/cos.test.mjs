import { test } from 'node:test'
import assert from 'node:assert/strict'
import { cpSync, existsSync, mkdtempSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { spawn, spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { dirname, join, resolve } from 'node:path'
import {
  parseStatus, parseSkipReason, checkGate, nextAction, nextNumber, readUnit as readUnitWith, STAGE_NAMES,
  BRANCH_TYPES, branchProblem, tagProblem, isPrerelease, tagVersion, versionProblem, parseType,
  unitBranch, VERSION_SOURCE, parseQuestions, parseAnswers, parsePr, parseReview, REVIEW_ROUNDS,
  reviewRounds, nextStep, parseNeedsPerson, betweenPrAndShip, parseDeadline, parseOutcome, unitOutcome,
  parseUnmeasured, parseSpike, SPIKE_ROUNDS, parseHold, HOLD_MOVES, nonBlocking, prText, prScope,
  UI_STANDARD, parseStandard, globMatch, uiFiles, screensProblems, makeProbe, stageAt,
  aboveAnswers, parseReruns, RERUNNABLE, screensAnswer, screensNeeds, parseShip, normalizePatch, openLines,
  parseMoreRounds, reviewLimit, moreRounds, branchChecks, notAWorkBranch,
  parseIdea, parseLinks, parseIdeaRef, parseUnitRef, WAITING_ON, titleProblem,
  unitMeta, readIdeas, NEEDS_STATE,
} from './cos.mjs'
import { createHash } from 'node:crypto'

// --- 0135: test glue, never in `cos.mjs` (plan step 8) --------------------------------
// Since `0135` every deciding command, and `readUnit`, decides on the app's snapshot alone.
// The tests below were written against files, so this builds the snapshot the app would
// build from them: `meta`'s own readers (`unitMeta`, `readIdeas`), one entry per directory,
// as `coscc/units/meta.py` imports a store. `stores` maps a workspace name to its `.cos/`;
// `own` is the name of the store the command reads, `''` when it has none.
const entryFrom = (m) => ({
  artifacts: Object.fromEntries(Object.entries(m.artifacts).map(([f, a]) => [f, { status: a.status, raw: a.raw, questions: a.questions }])),
  type: m.type ?? null,
  links: m.links ?? { idea: null, repo: null, dependsOn: null },
  holds: (m.holds ?? []).filter((h) => h.by !== null),
  answers: m.answers,
  unknowns: [],
})
function stateFor(stores, own = '') {
  const units = {}
  const ideas = {}
  for (const [ws, cos] of stores) {
    if (!existsSync(cos)) continue
    for (const e of readdirSync(cos, { withFileTypes: true })) {
      if (e.isDirectory() && e.name !== 'ideas') units[`${ws}/${e.name}`] = entryFrom(unitMeta(join(cos, e.name)))
    }
    ideas[ws] = existsSync(join(cos, 'ideas')) ? readIdeas(cos) : []
  }
  return { workspace: own, workspaces: [...stores.keys()].filter(Boolean), units, ideas }
}
// A store root (the directory `--root` takes) and `[name, root]` pairs, as `--peer` named them.
function stateOfRoots(root, peers = []) {
  const at = resolve(root)
  const own = peers.find(([, d]) => resolve(d) === at)?.[0] ?? ''
  return stateFor(new Map([[own, join(at, '.cos')], ...peers.filter(([n]) => n !== own).map(([n, d]) => [n, join(resolve(d), '.cos')])]), own)
}
// The unit read is entered under the name it is read by, whatever its directory is called.
function readUnit(dir, name, { state, peers, cosDir } = {}) {
  if (!state) {
    state = stateOfRoots(dirname(cosDir ?? dirname(dir)), [...(peers ?? [])])
    state.units[`${state.workspace}/${name}`] = entryFrom(unitMeta(dir))
  }
  return readUnitWith(dir, name, { state })
}

// `type` is what `readUnit` reads off `Type: feat`; since `0049` the `review` and `ship` gates
// hold `pr.md`'s title to it.
const unit = (artifacts) => ({ name: '0001_x', type: 'feat', artifacts, problems: [] })
const art = (status) => ({ status, skipReason: null })

// A probe that answers the way git and gh would, without either. `checks` is what
// `gh pr checks --json name,bucket` prints; `git` maps an argument string to an answer.
const ok = (out = '') => ({ code: 0, out, err: '' })
// `view` is what `gh pr view --json state,headRefOid,mergeCommit,mergedAt,title` prints; `null` means the head is the
// reviewed commit (`SHA`, declared further down), so the diff to it is empty. The title is `PR`'s
// unless `view` names one; `title: undefined` is gh giving none.
const titled = (view) => ('title' in view ? view : { ...view, title: 'feat(0001): x' })
const greenProbe = (checks = [{ name: 'tests', bucket: 'pass' }], git = {}, view = null) => ({
  gh: (...args) =>
    args[1] === 'view'
      ? ok(JSON.stringify(titled(view ?? { state: 'OPEN', headRefOid: SHA })))
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
  const pr = { ...art('accepted'), pr: { url: 'https://github.com/o/r/pull/7', number: 7 }, title: 'feat(0001): x' }
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
const SCRIPT = fileURLToPath(new URL('./cos.mjs', import.meta.url))
const DECIDING = new Set(['status', 'gate', 'next', 'rerun', 'unit-branch', 'pr-text', 'screens'])
const VALUED = new Set(['--root', '--repo', '--reserve-from', '--state', '--peer'])
// `0135` glue: a deciding command given no `--state` is handed the snapshot of its files, and
// `--peer <ws>=<dir>` becomes that workspace in it. Anything else is spawned as it is.
const cosRun = (args, options = {}) => {
  const cmd = args.find((a, i) => !a.startsWith('--') && !VALUED.has(args[i - 1]))
  if (args.includes('--state') || !DECIDING.has(cmd)) return spawnSync(process.execPath, [SCRIPT, ...args], { encoding: 'utf8', ...options })
  const kept = []
  const peers = []
  let root = fileURLToPath(new URL('../..', import.meta.url))
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--peer' && args[i + 1]?.includes('=')) {
      const at = args[++i].indexOf('=')
      peers.push([args[i].slice(0, at), args[i].slice(at + 1)])
      continue
    }
    if (args[i] === '--root' && args[i + 1]) root = args[i + 1]
    kept.push(args[i])
  }
  const input = JSON.stringify(stateOfRoots(root, peers))
  return spawnSync(process.execPath, [SCRIPT, ...kept, '--state', '-'], { encoding: 'utf8', input, ...options })
}
const cli = (...args) => cosRun(args)

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
  const child = spawn(process.execPath, [script, '--root', root, '--state', '-', 'status', '--json'])
  child.stdin.end(JSON.stringify(stateOfRoots(root)))
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
const PR = { ...art('accepted'), pr: { url: 'https://github.com/o/r/pull/7', number: 7 }, title: 'feat(0001): x' }
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
  // `0103`: a red read also asks for the head's branch; a valid one adds nothing to the line.
  const gh = (code, out, err = '') => ({
    gh: (...a) => (a[1] === 'view' ? ok('{"headRefName":"feat/x"}\n') : { code, out, err }),
    git: () => ok(),
  })
  const red ='[{"bucket":"fail","name":"tests"},{"bucket":"pass","name":"branch-name"}]\n'
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
  assert.match(checkGate(u, 'ship', { probe: greenProbe(undefined, {}, { state: 'CLOSED', headRefOid: SHA }) }).need[0], /#7 is CLOSED, not open/)
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
  const out = cosRun(['status'], {
    encoding: 'utf8', env: { ...process.env, COS_REVIEW_ROUNDS: 'three' },
  })
  assert.equal(out.status, 2)
  assert.match(out.stderr, /COS_REVIEW_ROUNDS/)
})

test('with --root and no --repo, review says there is no repository instead of reading this one', () => {
  const { root } = questionTree({
    'intent.md': '# I\nAuthor: t. Type: feat. Status: accepted.\n',
    'spec.md': 'Status: accepted.\n', 'plan.md': 'Status: accepted.\n', 'impl.md': 'Status: accepted.\n',
    'pr.md': '# PR: feat(0001): x\nPR: https://github.com/o/r/pull/1. Status: accepted.\n',
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
  // A rewrite whose patch cannot be compared still goes to review (`0121` R3).
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
    'pr.md': '# PR: fix(0001): x\nPR: https://github.com/o/r/pull/7. Status: accepted.\n',
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
    cosRun([...args, '--root', root], { encoding: 'utf8' })
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
      cosRun([...args, '--root', root], { encoding: 'utf8' })
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

test('0136 R4: a stage result decides the spec\'s U<n> and the spike\'s verdicts, not the files', () => {
  const dir = unitDir({
    'intent.md': INTENT_0039,
    'spec.md': specText('- **C1.** nothing unmeasured here.'),
    'spike.md': spikeText(1, [['U1', 'holds']]),
  })
  const read = (spec, spike) => {
    const state = stateOfRoots(dirname(dirname(dir)))
    const entry = (state.units[`${state.workspace}/0039_x`] = entryFrom(unitMeta(dir)))
    entry.artifacts['spec.md'].result = { stage: 'spec', judgement: 'ready', questions: [], ...spec }
    if (spike) entry.artifacts['spike.md'].result = { stage: 'spike', judgement: 'ready', questions: [], ...spike }
    return readUnit(dir, '0039_x', { state })
  }
  // The file names no U<n>; the object does, so the unit needs a spike.
  const asked = read({ unmeasured: ['U1'] }, { verdicts: [{ id: 'U1', verdict: 'fails' }] })
  assert.deepEqual(asked.artifacts['spec.md'].unmeasured.ids, ['U1'])
  // The file says U1 holds; the object says it fails, so plan stays shut.
  assert.equal(checkGate(asked, 'plan').ok, false)
  assert.match(checkGate(asked, 'plan').need.join('\n'), /U1: spike\.md measured that it does not hold/)
  const held = read({ unmeasured: ['U1'] }, { verdicts: [{ id: 'U1', verdict: 'holds' }] })
  assert.equal(checkGate(held, 'plan').ok, true)
  // With no U<n> in the object, `unmeasured` is absent, as it is for a file with none.
  assert.ok(!('unmeasured' in read({ unmeasured: [] }).artifacts['spec.md']))
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
    cosRun([...args, '--root', root], { encoding: 'utf8' })
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
  assert.deepEqual(JSON.parse(out.stdout), {
    unit: '0001_a', ...prText(PR_MD), status: 'accepted',
    titleProblem: titleProblem(prText(PR_MD).title, 'fix', '0001'),
  })
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

// --- 0122: the scope pr.md states, read beside its title and body -----------------

const SCOPED = PR_MD.replace(
  '## Scope of the diff\n\n',
  '## Scope of the diff\n\n3 files, +120/-7\n- `a.py`\n- `docs/b c.md`\n- `.claude/x.mjs`\n\nCounted by gh pr view.\n\n',
)

test('0122 R3: a scope written to its grammar is read as counts and paths', () => {
  assert.deepEqual(prText(SCOPED).scope, {
    files: 3, additions: 120, deletions: 7, paths: ['a.py', 'docs/b c.md', '.claude/x.mjs'],
  })
  assert.deepEqual(prScope('## Scope of the diff\n1 files, +0/-0\n'), { files: 1, additions: 0, deletions: 0, paths: [] })
})

test('0122 R3: no Scope of the diff heading is a null scope', () => {
  assert.equal(prScope(''), null)
  assert.equal(prScope('# PR: x\nStatus: accepted.\n\n## Where\n\n3 files, +1/-1\n'), null)
  assert.equal(prScope('### Scope of the diff\n\n3 files, +1/-1\n'), null)
  assert.equal(prText(PR_MD).scope, null, 'an empty section is no scope')
  assert.equal(prScope('x\n## Scope of the diff'), null, 'a heading on the last line')
})

test('0122 R3: the old git diff --stat line is a null scope', () => {
  // `.cos/0014_product-cannot-start-a-work-unit/pr.md:19`, verbatim.
  const old = PR_MD.replace('## Scope of the diff\n\n', '## Scope of the diff\n\n`git diff --stat main...HEAD`: **27 file, +2413 −84**.\n\n')
  assert.equal(prText(old).scope, null)
  for (const line of ['1 file, +1/-1', '**3 files, +1/-1**', '3 files, +1/−1', '3 files, +1/-1 from main']) {
    assert.equal(prScope(`## Scope of the diff\n\n${line}\n`), null, line)
  }
})

test('0122 R3: a path listed twice is a null scope', () => {
  assert.equal(prScope('## Scope of the diff\n\n2 files, +1/-1\n- `a.py`\n- `b.py`\n- `a.py`\n'), null)
})

test('0122 R3: prose after the list is not a path, and the body is kept byte for byte', () => {
  const text = SCOPED.replace('Counted by gh pr view.', 'Counted by gh pr view.\n- `z.py`')
  assert.deepEqual(prScope(text).paths, ['a.py', 'docs/b c.md', '.claude/x.mjs'])
  assert.deepEqual(prScope('## Scope of the diff\n\n1 files, +1/-1\n- `a.py`\n## Next\n- `b.py`\n').paths, ['a.py'])
  assert.equal(prText(SCOPED).body, SCOPED.split('\n').slice(3).join('\n'))
})

test('0122 R3: a file with \\r\\n reads the same scope', () => {
  const crlf = SCOPED.replace(/\n/g, '\r\n')
  assert.deepEqual(prText(crlf).scope, prText(SCOPED).scope)
  assert.equal(prText(crlf).body, SCOPED.split('\n').slice(3).join('\r\n'))
})

test('0122 R3: pr-text prints scope beside title, body, url and status', () => {
  const root = prTree({ '0001_a': SCOPED })
  const out = cli('--root', root, 'pr-text', '0001_a')
  assert.equal(out.status, 0, out.stderr)
  const got = JSON.parse(out.stdout)
  assert.deepEqual(got, { unit: '0001_a', ...prText(SCOPED), status: 'accepted', titleProblem: titleProblem(prText(SCOPED).title, 'fix', '0001') })
  assert.deepEqual(Object.keys(got).sort(), ['body', 'scope', 'status', 'title', 'titleProblem', 'unit', 'url'])
  assert.equal(got.scope.files, 3)
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

// --- `0027`: a round that drops a finding an earlier one raised --------------------

// The header, `## Round 1` and `## Round 2` of `0017`'s `review.md`, copied verbatim. Round 2
// lists F1 alone, under a header that says `accepted` because round 3 came later.
const FIXTURE_0017 = readFileSync(new URL('./testdata/0017-review-rounds-1-2.md', import.meta.url), 'utf8')
const asked0017 = (text) => text.replace('Status: accepted.', 'Status: changes-requested.')
const ROUND1_0017 = FIXTURE_0017.slice(0, FIXTURE_0017.indexOf('\n## Round 2\n') + 1)
const two = ['- F1 [open] a.py:1 — high — x', '- F2 [open] b.py:2 — high — y']

test('0027 R1: a round that omits an earlier id is unfinished only under changes-requested', () => {
  const read = (...rounds) =>
    parseReview(`# Review: x\nStatus: changes-requested.\n\n${rounds.join('\n')}`).rounds.map((r) => [r.n, r.dropped, r.unfinished])
  assert.deepEqual(read(round(1, 'changes-requested', two)), [[1, [], false]])
  // Carrying every id forward, fixed or open, is a full round; dropping one is not.
  const carried = round(2, 'changes-requested', [`- F1 [fixed ${FIX}] a.py:1 — high — x`, two[1]])
  assert.deepEqual(read(round(1, 'changes-requested', two), carried).at(-1), [2, [], false])
  assert.deepEqual(read(round(1, 'changes-requested', two), cr(2)).at(-1), [2, ['F2'], true])
  // Every earlier id, in the order first raised, from rounds of any verdict.
  const three = [...two, '- F3 [open] c.py:3 — low — z']
  assert.deepEqual(read(round(1, 'changes-requested', two), round(2, 'changes-requested', three), cr(3)).at(-1), [3, ['F2', 'F3'], true])
  assert.deepEqual(read(incompleteRound(1), round(2, 'changes-requested', ['- F2 [open] y'])).at(-1), [2, ['F1'], true])
  const unread = `## Round 1\n\nReviewed: somewhere. Verdict: maybe.\n\n### Findings\n\n- F1 [open] x\n`
  assert.deepEqual(read(unread, round(2, 'changes-requested', ['- F2 [open] y'])).at(-1), [2, ['F1'], true])
  // A pass or a needs-person round that drops an id still names it, but is not unfinished.
  for (const verdict of ['pass', 'needs-person']) {
    assert.deepEqual(read(round(1, 'changes-requested', two), round(2, verdict, [`- F1 [fixed ${FIX}] x`])).at(-1), [2, ['F2'], false])
  }
  // An id named only in prose, outside `### Findings`, is dropped all the same.
  const prose = `## Round 2\n\nReviewed: ${SHA}. Verdict: changes-requested.\n\nF2 is still open.\n\n### Findings\n\n- F1 [open] x\n\n### What was not reviewed\n\n- F2 [open] y\n`
  assert.deepEqual(read(round(1, 'changes-requested', two), prose).at(-1), [2, ['F2'], true])
  // `0017`'s round 2 drops everything round 1 raised but F1.
  const r0017 = parseReview(FIXTURE_0017).rounds
  assert.deepEqual(r0017.map((r) => [r.n, r.verdict, r.dropped, r.unfinished]), [
    [1, 'changes-requested', [], false],
    [2, 'changes-requested', ['F2', 'F3', 'F4', 'F5'], true],
  ])
})

test('0027 R2 b: a changes-requested round that carries every earlier id counts exactly one more', () => {
  const used = (rounds, limit = 3) => nextStep(reviewOfRounds('changes-requested', rounds), { limit }).action
  const first = round(1, 'changes-requested', two)
  assert.match(used([first]), /\(1 of 3 rounds used\)/)
  assert.match(used([first, round(2, 'changes-requested', two)]), /\(2 of 3 rounds used\)/)
  assert.match(used([first, round(2, 'changes-requested', two), cr(3)]), /\(2 of 3 rounds used\)/)
  // Once a full round follows the unfinished one, the loop goes on counting from there.
  assert.match(used([first, cr(2), round(3, 'changes-requested', two)]), /\(2 of 3 rounds used\)/)
})

test('0027 R3: an unfinished last round sends next to review with its ids, CI permitting, and leaves the review gate open', () => {
  const u = reviewOfRounds('changes-requested', [round(1, 'changes-requested', two), cr(2)])
  const green = nextStep(u, { probe: greenProbe() })
  assert.equal(green.stage, 'review')
  assert.equal(
    green.action,
    'review round 2 left out findings an earlier round raised — write-review again (1 of 3 rounds used); CI is green: write-review',
  )
  // Review F1: the ids are a list of their own, whichever stage CI leaves it at (S5).
  assert.deepEqual(green.dropped, ['F2'])
  assert.deepEqual(nextAction(u).dropped, ['F2'])
  const red = nextStep(u, { probe: greenProbe([{ name: 'tests', bucket: 'fail' }]) })
  assert.deepEqual([red.stage, red.dropped], ['impl', ['F2']])
  assert.equal(nextStep(u, { probe: greenProbe([{ name: 'tests', bucket: 'pending' }]) }).stage, '')
  const bare = nextStep(u)
  assert.equal(bare.stage, '')
  assert.match(bare.action, /left out findings an earlier round raised.*pass --repo/)
  assert.deepEqual(bare.dropped, ['F2'])
  assert.equal(checkGate(u, 'review', { probe: greenProbe() }).ok, true)
  // Any other answer carries no `dropped`.
  assert.equal('dropped' in nextStep(reviewOfRounds('changes-requested', [round(1, 'changes-requested', two)])), false)
  assert.equal('dropped' in nextStep(reviewOfRounds('draft', [cr(1), incompleteRound(2)]), { probe: greenProbe() }), false)
})

test('0027 R3: an unfinished round at the limit still stops at needs a person', () => {
  const first = round(1, 'changes-requested', two)
  const u = reviewOfRounds('changes-requested', [first, cr(2)])
  assert.match(nextStep(u, { limit: 1, probe: greenProbe() }).action, /^needs a person — review used 1 of 1 rounds/)
  assert.equal(checkGate(u, 'review', { limit: 1, probe: greenProbe() }).ok, false)
  const spent = reviewOfRounds('changes-requested', [first, round(2, 'changes-requested', two), cr(3)])
  assert.match(nextStep(spent, { limit: 2, probe: greenProbe() }).action, /^needs a person — review used 2 of 2 rounds/)
  assert.equal(nextStep(spent, { limit: 3, probe: greenProbe() }).stage, 'review')
})

test('0027 R2: an incomplete round 1 then an unfinished round 2 uses no round', () => {
  const u = reviewOfRounds('changes-requested', [incompleteRound(1), round(2, 'changes-requested', ['- F2 [open] y'])])
  const n = nextStep(u, { limit: 1, probe: greenProbe() })
  assert.equal(n.stage, 'review')
  assert.match(n.action, /left out findings .*\(0 of 1 rounds used\)/)
  assert.deepEqual(n.dropped, ['F1'])
  // The floor stays for a round whose verdict could not be read, as `0085` keeps it.
  const unread = `## Round 1\n\nReviewed: somewhere. Verdict: maybe.\n\n### Findings\n\n- F1 [open] x\n`
  const floor = reviewOfRounds('changes-requested', [unread, round(2, 'changes-requested', ['- F2 [open] y'])])
  assert.match(nextStep(floor, { limit: 1, probe: greenProbe() }).action, /^needs a person — review used 1 of 1 rounds/)
})

// A unit whose `review.md` is `text`, asked through the command the way the board asks it.
const tree0027 = (text) => {
  const t = rerunTree({ ...RERUN_FILES, 'review.md': text }, '0017_units-share-one-working-tree')
  const run = (limit, ...args) =>
    cosRun([...args, '--root', t.root], {
      encoding: 'utf8', env: { ...process.env, COS_REVIEW_ROUNDS: String(limit) },
    })
  const used = (limit) => {
    const out = run(limit, 'next', t.name)
    assert.equal(out.status, 0, out.stderr)
    // `(N of L rounds used)`, or at the limit `review used N of L rounds`.
    const m = JSON.parse(out.stdout).action.match(/(\d+) of (\d+) rounds/)
    assert.ok(m, out.stdout)
    assert.equal(Number(m[2]), limit)
    return Number(m[1])
  }
  return { ...t, run, used }
}

test("0027 R2 a: 0017's round 2 adds no round to the count", () => {
  const both = tree0027(asked0017(FIXTURE_0017))
  const one = tree0027(asked0017(ROUND1_0017))
  const ids = parseReview(ROUND1_0017).rounds[0].findings.map((f) => f.id)
  assert.deepEqual(ids, ['F1', 'F2', 'F3', 'F4', 'F5'])
  const full = tree0027(asked0017(ROUND1_0017) + '\n' + round(2, 'changes-requested', ids.map((id) => `- ${id} [open] x`)))
  for (const limit of [1, 2, 3]) {
    assert.equal(both.used(limit), one.used(limit), `limit ${limit}`)
    assert.equal(full.used(limit), one.used(limit) + 1, `limit ${limit}`)
  }
  // What was read goes out through status --json, where the app takes it from.
  const status = JSON.parse(both.run(3, 'status', '--json').stdout)
  const rounds = status.units[0].artifacts['review.md'].review.rounds
  assert.deepEqual(rounds.map((r) => [r.n, r.dropped, r.unfinished]), [[1, [], false], [2, ['F2', 'F3', 'F4', 'F5'], true]])
  assert.deepEqual(status.units[0].next.dropped, ['F2', 'F3', 'F4', 'F5'])
  // `next` hands the ids over as a list beside its sentence, which names none of them.
  const next = JSON.parse(both.run(3, 'next', both.name).stdout)
  assert.deepEqual(next.dropped, ['F2', 'F3', 'F4', 'F5'])
  assert.doesNotMatch(next.action, /F\d/)
  assert.equal('dropped' in JSON.parse(one.run(3, 'next', one.name).stdout), false)
})

test("0027 R4: reading 0017's rounds leaves review.md byte for byte", () => {
  const t = tree0027(asked0017(FIXTURE_0017))
  const file = join(t.dir, 'review.md')
  const before = readFileSync(file)
  for (const args of [['status', '--json'], ['next', t.name], ['gate', t.name, 'review']]) {
    const out = t.run(3, ...args)
    assert.notEqual(out.status, 2, out.stderr)
  }
  assert.ok(readFileSync(file).equals(before))
  assert.ok(readFileSync(new URL('./testdata/0017-review-rounds-1-2.md', import.meta.url)).equals(Buffer.from(FIXTURE_0017)))
})

test('0027 R5: the ship gate names the same dropped ids, in the same words', () => {
  const text = `# Review: x\nStatus: accepted.\n\n${round(1, 'changes-requested', [...two, '- F3 [open] c'])}\n${round(2, 'pass', [`- F1 [fixed ${FIX}] x`])}`
  const last = parseReview(text).rounds.at(-1)
  assert.deepEqual(last.dropped, ['F2', 'F3'])
  const need = checkGate(branched({ ...CHAIN, 'review.md': reviewArt('accepted', text) }), 'ship', { probe: greenProbe() }).need
  assert.ok(need.includes('review round 2 drops findings an earlier round raised: F2, F3 — carry each one forward, fixed or open'), need.join('\n'))
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
    cosRun([...args, '--root', root], { encoding: 'utf8' })
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
  const run = cosRun(['next', '0001_open', '--root', root], { encoding: 'utf8' })
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
  'pr.md': '# PR: feat(0003): x\nPR: https://github.com/o/r/pull/7. Status: accepted.\n\nbody\n',
  'review.md': `# Review: x\nStatus: accepted.\n\n${round(1, 'pass')}`,
}

function rerunTree(files = RERUN_FILES, name = '0003_awaiting-ship') {
  const root = mkdtempSync(join(tmpdir(), 'cos-0054-'))
  const dir = join(root, '.cos', name)
  mkdirSync(dir, { recursive: true })
  for (const [f, text] of Object.entries(files)) writeFileSync(join(dir, f), text)
  const cli = (...args) =>
    cosRun([...args, '--root', root], { encoding: 'utf8' })
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

test('0106 R1: a draft with no questions is no rerun', () => {
  assert.equal('rerun' in answeredTree({ 'intent.md': '# I\nType: feat. Status: draft.\n' }).next(), false)
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

// --- 0115: a draft impl.md asks a person, and runs again on the answer ------------

// Every stage before `impl` accepted, so `decide` reaches `impl.md`.
const BEFORE_IMPL = {
  'intent.md': '# I\nType: feat. Status: accepted.\n',
  'spec.md': '# S\nStatus: accepted.\n',
  'plan.md': '# P\nStatus: accepted.\n',
}
const DRAFT_IMPL = '# Impl\nStatus: draft.\n\n## Open questions\n\n1. Chạy lệnh X rồi đưa kết quả?\n'
const implTree = (impl, extra = {}) => answeredTree({ ...BEFORE_IMPL, 'impl.md': impl, ...extra })

test('0115 R1: a draft impl with an open question is listed and counted', () => {
  const { root } = implTree(DRAFT_IMPL)
  const out = cli('--root', root, 'status', '--json')
  assert.equal(out.status, 0, out.stderr)
  const u = JSON.parse(out.stdout).units[0]
  assert.deepEqual(u.questions.map(({ artifact, n, answered }) => ({ artifact, n, answered })), [
    { artifact: 'impl.md', n: 1, answered: false },
  ])
  assert.equal(u.counted, 'impl.md')
  assert.equal(u.open, 1)
})

test('0115 R3: a draft impl answered in full is rerun: impl', () => {
  const { next } = implTree(`${DRAFT_IMPL}\n## Answers\n${answerBlock(1, 'A', 'x')}`)
  assert.deepEqual(next(), { unit: '0001_q', stage: '', action: 'finish and accept impl.md', blocked: true, rerun: 'impl' })
})

test('0115 R3: a draft impl with one question unanswered is no rerun', () => {
  const two = DRAFT_IMPL + '2. Đăng nhập rồi báo lại?\n'
  const { u, next } = implTree(`${two}\n## Answers\n${answerBlock(1, 'A', 'x')}`)
  assert.equal('rerun' in next(), false)
  assert.equal(nextStep(u).rerun, undefined)
})

test('0115 R3: a draft impl with no questions is no rerun', () => {
  assert.equal('rerun' in implTree('# Impl\nStatus: draft.\n\n## What is still open\n\nx\n').next(), false)
})

test('0115 R3: a paused or dropped unit with an answered draft impl is no rerun', () => {
  const impl = `${DRAFT_IMPL}\n## Answers\n${answerBlock(1, 'A', 'x')}`
  for (const [head, state] of [['Paused', 'paused'], ['Dropped', 'dropped']]) {
    const intent = `${BEFORE_IMPL['intent.md']}\n## Answers\n${holdBlock(head, 'chờ')}`
    const n = implTree(impl, { 'intent.md': intent }).next()
    assert.equal(n.hold.state, state)
    assert.equal('rerun' in n, false)
  }
})

test('0115 R3: plan.md draft keeps the impl gate closed', () => {
  const { root } = implTree(`${DRAFT_IMPL}\n## Answers\n${answerBlock(1, 'A', 'x')}`, { 'plan.md': '# P\nStatus: draft.\n' })
  const out = cli('--root', root, 'gate', '0001_q', 'impl')
  assert.equal(out.status, 1)
  assert.match(out.stdout + out.stderr, /plan\.md is "draft"/)
})

test('0115 R4: status --json names the stages an answered draft runs again', () => {
  const { root } = implTree(DRAFT_IMPL)
  const data = JSON.parse(cli('--root', root, 'status', '--json').stdout)
  assert.deepEqual(data.afterAnswers, ['intent', 'spec', 'spike', 'plan', 'impl'])
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

// --- 0112: a passed unit behind main, and the ship.md a refused merge leaves ------------

const SHIP_OLD = '# Ship: x\nReview: review.md. Author: A. Status: draft.\n\n## What went out\n\nNothing merged.\n'
const shipDraft = (n, refused = 'the head branch is not up to date with the base branch') =>
  `# Ship: x\nReview: review.md. Round: ${n}. Author: A. Status: draft.\n\n## What went out\n\nNothing merged.\n${refused === null ? '' : `Refused: ${refused}\n`}\n## What is still open\n\nRefused: not this one\n`
// What `readUnit` attaches, without a directory.
const shipArt = (text) => {
  const ship = parseShip(text)
  return { ...art(parseStatus(text)), ...(ship.round !== null || ship.refused !== null ? { ship } : {}) }
}
const TRUNK = 'refs/remotes/origin/main'
const behindBy = (k, git = {}) => greenProbe(undefined, {
  [`merge-base --is-ancestor ${TRUNK} ${SHA}`]: { code: 1, out: '', err: '' },
  [`rev-list --count ${SHA}..${TRUNK}`]: ok(`${k}\n`),
  ...git,
})

test('0112 R5: parseShip reads Round from the header and the first Refused line of What went out', () => {
  assert.deepEqual(parseShip(shipDraft(2)), { round: 2, refused: 'the head branch is not up to date with the base branch' })
  assert.deepEqual(parseShip(shipDraft(3, null)), { round: 3, refused: null })
  assert.deepEqual(parseShip(SHIP_OLD), { round: null, refused: null })
  // Indented is not the line.
  assert.equal(parseShip('# S\nReview: review.md. Round: 1. Status: draft.\n\n## What went out\n\n  Refused: no\n').refused, null)
})

test('0112 R5: readUnit attaches ship only to a ship.md that says something', () => {
  const root = mkdtempSync(join(tmpdir(), 'cos-0112-'))
  const dir = join(root, '0001_x')
  mkdirSync(dir)
  writeFileSync(join(dir, 'ship.md'), shipDraft(1))
  assert.deepEqual(readUnit(dir, '0001_x').artifacts['ship.md'].ship, { round: 1, refused: 'the head branch is not up to date with the base branch' })
  writeFileSync(join(dir, 'ship.md'), SHIP_OLD)
  assert.equal('ship' in readUnit(dir, '0001_x').artifacts['ship.md'], false)
})

test('0112 R3: a pull request behind origin/main closes ship, says by how much, and names no full sha', () => {
  const u = branched({ ...CHAIN, 'review.md': reviewArt('accepted', round(1, 'pass')) })
  const g = checkGate(u, 'ship', { probe: behindBy(3) })
  assert.equal(g.ok, false)
  assert.match(g.need[0], /#7 is 3 commit\(s\) behind origin\/main — integrate, then review again/)
  assert.doesNotMatch(g.need.join('\n'), /[0-9a-f]{40}/)
  // A count git cannot give still closes the gate, with git's words.
  const unknown = behindBy(0, { [`rev-list --count ${SHA}..${TRUNK}`]: { code: 128, out: '', err: 'bad revision' } })
  assert.match(checkGate(u, 'ship', { probe: unknown }).need[0], /unknown number of commits \(git said: bad revision\) behind origin\/main/)
})

test('0112 R3: no origin/main here, or origin/main an ancestor of the head: ship opens as before', () => {
  const u = branched({ ...CHAIN, 'review.md': reviewArt('accepted', round(1, 'pass')) })
  const noTrunk = behindBy(3, { [`rev-parse --verify --quiet ${TRUNK}`]: { code: 1, out: '', err: '' } })
  assert.deepEqual(checkGate(u, 'ship', { probe: noTrunk }), { ok: true, need: [], head: SHA })
  assert.deepEqual(checkGate(u, 'ship', { probe: greenProbe() }), { ok: true, need: [], head: SHA })
})

test('0112 R3: a head already rebased goes to review, not to the behind reason', () => {
  const u = branched({ ...CHAIN, 'review.md': reviewArt('accepted', round(1, 'pass')) })
  const n = nextStep(u, { probe: behindBy(2, { [`merge-base --is-ancestor ${SHA} refs/heads/feat/x`]: { code: 1, out: '', err: '' } }) })
  assert.equal(n.stage, 'review')
  const behind = nextStep(u, { probe: behindBy(2) })
  assert.equal(behind.stage, '')
  assert.match(behind.action, /2 commit\(s\) behind origin\/main/)
})

test('0112 R6 a: a draft ship.md from an older round is a missing one: ship, review, or the gate\'s reason', () => {
  const REB = 'd'.repeat(40)
  const text = `${round(1, 'pass')}\n${round(2, 'pass').replace(SHA, REB)}`
  const u = branched({ ...CHAIN, 'review.md': reviewArt('accepted', text), 'ship.md': shipArt(shipDraft(1)) })
  const at = (git) => greenProbe(undefined, git, { state: 'OPEN', headRefOid: REB })
  assert.deepEqual(nextStep(u, { probe: at({}) }), { blocked: true, action: `write-ship — merge with --match-head-commit ${REB}`, stage: 'ship' })
  assert.equal(nextStep(u, { probe: at({ [`diff --name-only ${REB}..${REB}`]: ok('src/a.py') }) }).stage, 'review')
  const behind = at({
    [`merge-base --is-ancestor ${TRUNK} ${REB}`]: { code: 1, out: '', err: '' },
    [`rev-list --count ${REB}..${TRUNK}`]: ok('1'),
  })
  const n = nextStep(u, { probe: behind })
  assert.equal(n.stage, '')
  assert.match(n.action, /1 commit\(s\) behind origin\/main/)
})

test('0112 R6 b: a draft ship.md from the last round asks the gate, and an open one stops on the Refused line', () => {
  const u = (refused) => branched({ ...CHAIN, 'review.md': reviewArt('accepted', round(1, 'pass')), 'ship.md': shipArt(shipDraft(1, refused)) })
  // Behind: the R3 reason, which the autopilot integrates on (R1).
  const behind = nextStep(u(), { probe: behindBy(4) })
  assert.equal(behind.stage, '')
  assert.match(behind.action, /4 commit\(s\) behind origin\/main — integrate, then review again/)
  // Rebased since: another round (R2).
  const rebased = behindBy(0, { [`merge-base --is-ancestor ${SHA} refs/heads/feat/x`]: { code: 1, out: '', err: '' } })
  assert.equal(nextStep(u(), { probe: rebased }).stage, 'review')
  // Open: refused for something the gate cannot see, and the stop says what.
  const open = nextStep(u('you do not have permission to merge'), { probe: greenProbe() })
  assert.deepEqual(open, { blocked: true, action: 'ship was refused: you do not have permission to merge — finish and accept ship.md', stage: '' })
  assert.match(nextStep(u(null), { probe: greenProbe() }).action, /ship was refused: ship\.md names no refusal/)
})

test('0112 R6 c: a draft ship.md with no Round stops as it always did', () => {
  const u = branched({ ...CHAIN, 'review.md': reviewArt('accepted', round(1, 'pass')), 'ship.md': shipArt(SHIP_OLD) })
  assert.deepEqual(nextStep(u, { probe: behindBy(4) }), { blocked: true, action: 'finish and accept ship.md', stage: '' })
  assert.deepEqual(nextAction(u), { blocked: true, action: 'finish and accept ship.md', stage: '' })
})

test('0112 R2: a pass, then a rebase, with no ship.md, is review — not a draft stop', () => {
  const REB = 'd'.repeat(40)
  const notAncestor = { code: 1, out: '', err: '' }
  const afterRebase = greenProbe(undefined, {
    [`merge-base --is-ancestor ${SHA} refs/heads/feat/x`]: notAncestor,
    [`merge-base --is-ancestor ${SHA} refs/remotes/origin/feat/x`]: notAncestor,
    [`merge-base --is-ancestor ${SHA} ${REB}`]: notAncestor,
  }, { state: 'OPEN', headRefOid: REB })
  const u = branched({ ...CHAIN, 'review.md': reviewArt('accepted', round(1, 'pass')) })
  const n = nextStep(u, { probe: afterRebase })
  assert.equal(n.stage, 'review')
  assert.doesNotMatch(n.action, /finish and accept/)
})

test('0112 review F1: status offers no acceptance of a ship.md naming its Round, and still does of one naming none', () => {
  const status = (ship) => {
    const { root } = questionTree({
      'intent.md': '# I\nAuthor: t. Type: fix. Status: accepted.\n',
      'spec.md': 'Status: accepted.\n', 'plan.md': 'Status: accepted.\n', 'impl.md': implText(''),
      'pr.md': 'PR: https://github.com/o/r/pull/7. Status: accepted.\n',
      'review.md': `# Review: x\nPR: pr.md. Author: t. Status: accepted.\n\n${round(1, 'pass')}`,
      'ship.md': ship,
    })
    return JSON.parse(cli('--root', root, 'status', '--json').stdout).units[0]
  }
  const refused = status(shipDraft(1))
  assert.deepEqual(refused.next, { blocked: true, action: 'ship after review round 1 did not merge — the next step says what runs now', stage: '', why: 'ship-refused' })
  // Still in the window, at `ship`: the board reads the integration and the autopilot fetches.
  assert.equal(refused.at, 'ship')
  assert.equal(refused.betweenPrAndShip, true)
  // R6 c: a ship.md from before `0112` is any draft, byte for byte.
  assert.deepEqual(status(SHIP_OLD).next, { blocked: true, action: 'finish and accept ship.md', stage: '', why: 'draft' })
  const u = branched({ ...CHAIN, 'review.md': reviewArt('accepted', round(1, 'pass')), 'ship.md': shipArt(shipDraft(1)) })
  assert.doesNotMatch(nextAction(u).action, /accept/)
  // With no repository, `next` says it needs one, as the gate does.
  assert.equal(nextStep(u).stage, '')
  assert.match(nextStep(u).action, /--repo/)
})

// --- 0067: a clean rebase does not void a passing review ------------------------------

const REB = 'd'.repeat(40)
const hunk = (at, context = 'line 28', blob = '1111111..2222222') =>
  `diff --git a/a.txt b/a.txt\nindex ${blob} 100644\n--- a/a.txt\n+++ b/a.txt\n@@ -${at},7 +${at},7 @@ def f():\n line 27\n ${context}\n line 29\n-line 30\n+line 30, changed\n line 31\n line 32\n line 33\n`
const PATCH_R = hunk(27)
// A pass on `SHA`, then a rebase to `REB`: the reviewed commit is on none of the three refs.
// `patch(commit)` is what `git diff` prints for the unit at that commit, `base(commit)` what
// `git merge-base` does; every call, git's and gh's, lands in `calls`. `head: SHA` leaves the
// pull request's head where the pass was, with only the two branch refs rewritten.
const rebasedProbe = ({ patch = (c) => (c === SHA ? PATCH_R : hunk(28, 'line 28', '3333333..4444444')), base = () => ok(`${'f'.repeat(40)}\n`), checks = [{ name: 'tests', bucket: 'pass' }], calls = [], head = REB } = {}) => ({
  gh: (...a) => {
    calls.push(['gh', ...a].join(' '))
    return a[1] === 'view' ? ok(JSON.stringify(titled({ state: 'OPEN', headRefOid: head }))) : ok(JSON.stringify(checks))
  },
  git: (...a) => {
    calls.push(a.join(' '))
    if (a[0] === 'merge-base' && a[1] === '--is-ancestor') return a[2] === SHA && a[3] !== SHA ? { code: 1, out: '', err: '' } : ok()
    if (a[0] === 'merge-base') return base(a[1])
    if (a[0] === 'diff' && a[1] === '--no-color') return ok(patch(a.at(-4)))
    return ok()
  },
})
const passedOnce = () => branched({ ...CHAIN, 'review.md': reviewArt('accepted', round(1, 'pass')) })

test('0067 R1: normalizePatch drops index lines and hunk numbers, and keeps every other byte', () => {
  const text = [
    'diff --git a/a.txt b/a.txt', 'index 1111111..2222222 100644', '--- a/a.txt', '+++ b/a.txt',
    '@@ -27,7 +27,7 @@ def f():', ' line 27', '+index 1111111..2222222', '- @@ -1 +1 @@', '\\ No newline at end of file',
    'diff --git a/b.bin b/b.bin', `index ${'1'.repeat(40)}..${'2'.repeat(40)}`, 'GIT binary patch', 'literal 7', 'zcmZQzWMXDwWn^Ms0000A0RR91', '',
    '@@ -1 +1 @@', '',
  ].join('\n')
  assert.equal(normalizePatch(text), [
    'diff --git a/a.txt b/a.txt', '--- a/a.txt', '+++ b/a.txt',
    '@@ @@ def f():', ' line 27', '+index 1111111..2222222', '- @@ -1 +1 @@', '\\ No newline at end of file',
    'diff --git a/b.bin b/b.bin', 'GIT binary patch', 'literal 7', 'zcmZQzWMXDwWn^Ms0000A0RR91', '',
    '@@ @@', '',
  ].join('\n'))
  // Only the numbers differ: equal once normalized. A line of context differs: not.
  assert.equal(normalizePatch(hunk(27)), normalizePatch(hunk(28, 'line 28', 'abcdef0..0fedcba')))
  assert.notEqual(normalizePatch(hunk(27)), normalizePatch(hunk(27, 'line 28, changed by main')))
})

test('0067 R1: a merge-base that names no commit is an error, never a clean rebase', () => {
  const u = passedOnce()
  for (const [base, said] of [
    [() => ok(''), /its patch could not be compared with the reviewed one: git merge-base printed no commit/],
    [() => ok('not a sha\n'), /git merge-base printed no commit/],
    [() => ({ code: 1, out: '', err: 'fatal: no merge base' }), /could not be compared .*fatal: no merge base/],
  ]) {
    const g = checkGate(u, 'ship', { probe: rebasedProbe({ base }) })
    assert.equal(g.ok, false)
    assert.match(g.need[0], /rewritten after the pass \(a rebase does this\): review its new head in another round/)
    assert.match(g.need[0], said)
    assert.equal(nextStep(u, { probe: rebasedProbe({ base }) }).stage, 'review')
  }
  // Two empty patches are equal, so an empty one from a failing diff must not be read as one.
  const failing = rebasedProbe()
  const git = failing.git
  failing.git = (...a) => (a[0] === 'diff' && a[1] === '--no-color' ? { code: 128, out: '', err: 'fatal: bad object' } : git(...a))
  assert.match(checkGate(u, 'ship', { probe: failing }).need[0], /could not be compared .*fatal: bad object/)
})

test('0067 R2: a rewritten ref whose patch is unchanged opens ship once CI is green, and the gate names both commits', () => {
  const u = passedOnce()
  assert.deepEqual(checkGate(u, 'ship', { probe: rebasedProbe() }), { ok: true, need: [], head: REB, rebased: { reviewed: SHA, head: REB } })
  // The reviewed patch is taken once, for all three refs: four merge-bases, not six.
  const calls = []
  checkGate(u, 'ship', { probe: rebasedProbe({ calls }) })
  assert.equal(calls.filter((c) => c.startsWith('merge-base ') && !c.includes('--is-ancestor')).length, 4)
  assert.equal(calls.filter((c) => c.startsWith('gh pr checks')).length, 1)
  const n = nextStep(u, { probe: rebasedProbe() })
  assert.equal(n.stage, 'ship')
  assert.match(n.action, new RegExp(`--match-head-commit ${REB}$`))
})

test('0067 review F1: a branch ref rewritten clean while the pull request head is still the reviewed commit names no rebase', () => {
  const u = passedOnce()
  const calls = []
  // The gate stays as a head that did not move leaves it: open on that head, CI not asked.
  assert.deepEqual(checkGate(u, 'ship', { probe: rebasedProbe({ head: SHA, calls }) }), { ok: true, need: [], head: SHA })
  assert.deepEqual(calls.filter((c) => c.startsWith('gh pr checks')), [])
  assert.equal(nextStep(u, { probe: rebasedProbe({ head: SHA, checks: [{ name: 'tests', bucket: 'fail' }] }) }).stage, 'ship')
  // A merge refused as not up to date is not cured by a rewrite the pull request never saw.
  const refused = branched({ ...CHAIN, 'review.md': reviewArt('accepted', round(1, 'pass')), 'ship.md': shipArt(shipDraft(1)) })
  assert.deepEqual(nextStep(refused, { probe: rebasedProbe({ head: SHA }) }), {
    blocked: true, action: 'ship was refused: the head branch is not up to date with the base branch — finish and accept ship.md', stage: '',
  })
})

test('0067 R3: red, pending, none or unreadable CI on a clean rebase closes ship with said.ci and never said.moved', () => {
  const u = passedOnce()
  const unreadable = rebasedProbe()
  const gh = unreadable.gh
  unreadable.gh = (...a) => (a[1] === 'checks' ? { code: 1, out: '', err: 'HTTP 401' } : gh(...a))
  for (const [probe, said, stage] of [
    [rebasedProbe({ checks: [{ name: 'tests', bucket: 'fail' }] }), /^CI is red on #7: tests — back to impl/, 'impl'],
    [rebasedProbe({ checks: [{ name: 'tests', bucket: 'pending' }] }), /^CI has not finished on #7: tests/, ''],
    [rebasedProbe({ checks: [] }), /^#7 reports no required checks/, ''],
    [unreadable, /^cannot read the required checks of #7: HTTP 401$/, ''],
  ]) {
    const g = checkGate(u, 'ship', { probe })
    assert.equal(g.ok, false)
    assert.equal(g.need.length, 1)
    assert.match(g.need[0], said)
    assert.doesNotMatch(g.need[0], /rewritten/)
    // Neither `moved` nor `screens`: `next` sends it back to impl on red, and waits otherwise.
    const n = nextStep(u, { probe })
    assert.equal(n.stage, stage)
    assert.match(n.action, said)
  }
})

test('0067 R4: a patch that differs names its files, one that cannot be compared quotes git, and both still ask for a round', () => {
  const u = passedOnce()
  const other = 'diff --git a/b.txt b/b.txt\n--- a/b.txt\n+++ b/b.txt\n@@ -1 +1 @@\n-x\n+y\n'
  const extra = 'diff --git a/c.txt b/c.txt\nnew file mode 100644\n--- /dev/null\n+++ b/c.txt\n@@ -0,0 +1 @@\n+z\n'
  const patch = (c) => (c === SHA ? `${PATCH_R}${other}` : `${hunk(28, 'line 28, changed by main')}${other}${extra}`)
  const g = checkGate(u, 'ship', { probe: rebasedProbe({ patch }) })
  assert.equal(g.ok, false)
  assert.match(g.need[0], /^the reviewed commit a{40} is not on refs\/heads\/feat\/x — the branch was rewritten after the pass/)
  assert.match(g.need[0], / — its patch differs from the reviewed one in a\.txt, c\.txt$/)
  assert.equal(nextStep(u, { probe: rebasedProbe({ patch }) }).stage, 'review')
  // Quoted paths are named by their path.
  const quoted = (c) => (c === SHA ? PATCH_R : `${PATCH_R}diff --git "a/sp ace\\t.txt" "b/sp ace\\t.txt"\n+x\n`)
  assert.match(checkGate(u, 'ship', { probe: rebasedProbe({ patch: quoted }) }).need[0], /differs from the reviewed one in sp ace\\t\.txt$/)
  const noTrunk = rebasedProbe()
  const git = noTrunk.git
  noTrunk.git = (...a) => (a[0] === 'rev-parse' && a[3]?.endsWith('/main') ? { code: 1, out: '', err: '' } : git(...a))
  const lost = checkGate(u, 'ship', { probe: noTrunk })
  assert.match(lost.need[0], /could not be compared with the reviewed one: there is no origin\/main and no main here/)
  assert.equal(nextStep(u, { probe: noTrunk }).stage, 'review')
})

test('0067 R7: a unit not rebased asks git no merge-base to trunk, no patch diff and gh no checks', () => {
  const u = passedOnce()
  const calls = []
  const probe = greenProbe()
  const { git, gh } = probe
  probe.git = (...a) => (calls.push(a.join(' ')), git(...a))
  probe.gh = (...a) => (calls.push(['gh', ...a].join(' ')), gh(...a))
  assert.deepEqual(checkGate(u, 'ship', { probe }), { ok: true, need: [], head: SHA })
  assert.deepEqual(calls.filter((c) => c.startsWith('merge-base ') && !c.startsWith('merge-base --is-ancestor ')), [])
  assert.deepEqual(calls.filter((c) => c.startsWith('diff --no-color')), [])
  assert.deepEqual(calls.filter((c) => c.startsWith('gh pr checks')), [])
})

test('0067 R5: next offers ship on a green clean rebase, impl on red, nothing while CI runs or reports none — never review', () => {
  const u = passedOnce()
  assert.equal(nextStep(u, { probe: rebasedProbe() }).stage, 'ship')
  assert.equal(nextStep(u, { probe: rebasedProbe({ checks: [{ name: 'tests', bucket: 'fail' }] }) }).stage, 'impl')
  for (const checks of [[{ name: 'tests', bucket: 'pending' }], []]) {
    const n = nextStep(u, { probe: rebasedProbe({ checks }) })
    assert.equal(n.stage, '')
    assert.notEqual(n.stage, 'review')
  }
})

test('0067 R5: a ship refused as not up to date and then rebased clean is offered ship again; another refusal still stops', () => {
  const u = (refused) => branched({ ...CHAIN, 'review.md': reviewArt('accepted', round(1, 'pass')), 'ship.md': shipArt(shipDraft(1, refused)) })
  assert.deepEqual(nextStep(u(), { probe: rebasedProbe() }), { blocked: true, action: `write-ship — merge with --match-head-commit ${REB}`, stage: 'ship' })
  assert.equal(nextStep(u(), { probe: rebasedProbe({ checks: [{ name: 'tests', bucket: 'fail' }] }) }).stage, 'impl')
  assert.equal(nextStep(u(), { probe: rebasedProbe({ checks: [{ name: 'tests', bucket: 'pending' }] }) }).stage, '')
  // Refused for something else: a clean rebase does not cure it.
  assert.deepEqual(nextStep(u('you do not have permission to merge'), { probe: rebasedProbe() }), {
    blocked: true, action: 'ship was refused: you do not have permission to merge — finish and accept ship.md', stage: '',
  })
  // Not rebased at all: `0112` R6 b stands.
  assert.match(nextStep(u(), { probe: greenProbe() }).action, /^ship was refused: the head branch is not up to date/)
})

test('0067 R6: gate prints a second line naming the reviewed commit and the new head, with no full sha', () => {
  assert.deepEqual(openLines('ship', '0001_x', { head: SHA }), [`open: ship may proceed for 0001_x — merge with --match-head-commit ${SHA}`])
  assert.deepEqual(openLines('impl', '0001_x', {}), ['open: impl may proceed for 0001_x'])
  const [first, second, ...rest] = openLines('ship', '0001_x', { head: REB, rebased: { reviewed: SHA, head: REB } })
  assert.equal(first, `open: ship may proceed for 0001_x — merge with --match-head-commit ${REB}`)
  assert.equal(second, "the reviewed commit aaaaaaa was rebased to ddddddd and the unit's patch is unchanged — no review round is needed")
  assert.doesNotMatch(second, /[0-9a-f]{40}/)
  assert.deepEqual(rest, [])
})

// R9: the cases `spec.md ## Answers, câu 1` measured, on a real repository. `a.txt` has 60
// lines and the unit changes line 30; `.cos/0001_x/` is the unit's own. Only `gh` is faked.
const LINE30 = 'line 30, changed by the unit'
const swap = (from, to) => (l) => { l[l.indexOf(from)] = to }
function rebaseRepo({ binary = false } = {}) {
  const repo = mkdtempSync(join(tmpdir(), 'cos-0067-'))
  const sh = (...args) => {
    const r = spawnSync('git', ['-c', 'user.name=T', '-c', 'user.email=t@example.invalid', '-c', 'commit.gpgsign=false', ...args], { cwd: repo, encoding: 'utf8' })
    assert.equal(r.status, 0, r.stderr)
    return r.stdout.trim()
  }
  const file = join(repo, 'a.txt')
  const edit = (fn) => {
    const l = readFileSync(file, 'utf8').split('\n').slice(0, -1)
    fn(l)
    writeFileSync(file, `${l.join('\n')}\n`)
  }
  const commit = (...flags) => {
    sh('add', '-A')
    sh('commit', '-q', ...flags)
    return sh('rev-parse', 'HEAD')
  }
  // The unit's change, on whatever is checked out.
  const theUnit = (bin = [0, 1, 2, 0, 254, 9]) => {
    edit(swap('line 30', LINE30))
    if (binary) writeFileSync(join(repo, 'b.bin'), Buffer.from(bin))
    mkdirSync(join(repo, '.cos', '0001_x'), { recursive: true })
    writeFileSync(join(repo, '.cos', '0001_x', 'review.md'), 'round 1\n')
  }
  sh('init', '-q', '-b', 'main')
  writeFileSync(file, `${Array.from({ length: 60 }, (_, i) => `line ${i + 1}`).join('\n')}\n`)
  if (binary) writeFileSync(join(repo, 'b.bin'), Buffer.from([0, 1, 2, 0, 255]))
  commit('-m', 'first')
  sh('switch', '-q', '-c', 'feat/x')
  theUnit()
  const R = commit('-m', 'the unit')
  // `main` moves, and `origin/main` with it, as a fetch would.
  const onMain = (fn) => {
    sh('switch', '-q', 'main')
    edit(fn)
    commit('-m', 'main moves')
    sh('update-ref', 'refs/remotes/origin/main', 'main')
    sh('switch', '-q', 'feat/x')
  }
  return { repo, sh, commit, theUnit, R, onMain }
}
const realProbe = (repo, head, checks = [{ name: 'tests', bucket: 'pass' }]) => ({
  ...makeProbe(repo),
  gh: (...a) => (a[1] === 'view' ? ok(JSON.stringify(titled({ state: 'OPEN', headRefOid: head }))) : ok(JSON.stringify(checks))),
})
const passedAt = (R) => branched({ ...CHAIN, 'review.md': reviewArt('accepted', round(1, 'pass').replace(SHA, R)) })
const farFromTheHunk = (l) => l.splice(5, 0, 'inserted by main')

test('0067 R9: main inserts a line far from the hunk — a clean rebase, ship opens on green, next offers ship', () => {
  const { repo, sh, R, onMain } = rebaseRepo()
  onMain(farFromTheHunk)
  sh('rebase', '-q', 'main')
  const H = sh('rev-parse', 'HEAD')
  assert.notEqual(H, R)
  const u = passedAt(R)
  assert.deepEqual(checkGate(u, 'ship', { probe: realProbe(repo, H) }), { ok: true, need: [], head: H, rebased: { reviewed: R, head: H } })
  const n = nextStep(u, { probe: realProbe(repo, H) })
  assert.equal(n.stage, 'ship')
  assert.match(n.action, new RegExp(`--match-head-commit ${H}$`))
})

test('0067 R9: main changes a line of the 3-line context — not clean, next offers review', () => {
  const { repo, sh, R, onMain } = rebaseRepo()
  onMain(swap('line 28', 'line 28, changed by main'))
  sh('rebase', '-q', 'main')
  const H = sh('rev-parse', 'HEAD')
  const u = passedAt(R)
  const g = checkGate(u, 'ship', { probe: realProbe(repo, H) })
  assert.equal(g.ok, false)
  assert.match(g.need[0], /its patch differs from the reviewed one in a\.txt$/)
  assert.equal(nextStep(u, { probe: realProbe(repo, H) }).stage, 'review')
})

test('0067 R9: a conflict resolved on the adjacent line — not clean', () => {
  const { repo, sh, commit, theUnit, R, onMain } = rebaseRepo()
  onMain(swap('line 31', 'line 31, changed by main'))
  // What resolving the conflict ends on: the new main, and the unit's line 30 applied again.
  sh('switch', '-q', '-C', 'feat/x', 'main')
  theUnit()
  const H = commit('-m', 'the unit, resolved')
  const g = checkGate(passedAt(R), 'ship', { probe: realProbe(repo, H) })
  assert.equal(g.ok, false)
  assert.match(g.need.join('\n'), /its patch differs from the reviewed one in a\.txt/)
})

test('0067 R9: a binary file the unit changes comes out different while the text is identical — not clean, and the file is named', () => {
  const { repo, sh, commit, R, onMain } = rebaseRepo({ binary: true })
  onMain(farFromTheHunk)
  sh('rebase', '-q', 'main')
  const clean = sh('rev-parse', 'HEAD')
  assert.equal(checkGate(passedAt(R), 'ship', { probe: realProbe(repo, clean) }).ok, true)
  writeFileSync(join(repo, 'b.bin'), Buffer.from([0, 1, 2, 0, 254, 8]))
  const H = commit('--amend', '--no-edit')
  const g = checkGate(passedAt(R), 'ship', { probe: realProbe(repo, H) })
  assert.equal(g.ok, false)
  assert.match(g.need[0], /its patch differs from the reviewed one in b\.bin$/)
})

test('0067 R9: red, pending or none on a clean rebase closes ship, and next never offers review', () => {
  const { repo, sh, R, onMain } = rebaseRepo()
  onMain(farFromTheHunk)
  sh('rebase', '-q', 'main')
  const H = sh('rev-parse', 'HEAD')
  const u = passedAt(R)
  for (const [checks, stage] of [[[{ name: 'tests', bucket: 'fail' }], 'impl'], [[{ name: 'tests', bucket: 'pending' }], ''], [[], '']]) {
    assert.equal(checkGate(u, 'ship', { probe: realProbe(repo, H, checks) }).ok, false)
    assert.equal(nextStep(u, { probe: realProbe(repo, H, checks) }).stage, stage)
  }
})

test('0067 R9: a head that differs only under .cos/<unit>/ is clean', () => {
  const { repo, sh, commit, R, onMain } = rebaseRepo()
  onMain(farFromTheHunk)
  sh('rebase', '-q', 'main')
  writeFileSync(join(repo, '.cos', '0001_x', 'review.md'), 'round 1\nround 2\n')
  writeFileSync(join(repo, '.cos', '0001_x', 'ship.md'), 'draft\n')
  const H = commit('-m', 'the unit records its round')
  assert.equal(checkGate(passedAt(R), 'ship', { probe: realProbe(repo, H) }).ok, true)
  // Another unit's files are the unit's patch like any other.
  mkdirSync(join(repo, '.cos', '0002_y'))
  writeFileSync(join(repo, '.cos', '0002_y', 'intent.md'), 'x\n')
  const other = commit('-m', 'another unit')
  assert.match(checkGate(passedAt(R), 'ship', { probe: realProbe(repo, other) }).need[0], /differs from the reviewed one in \.cos\/0002_y\/intent\.md$/)
})

// --- 0121: a rebase alone answers no finding ------------------------------------------

// The 0115 case: two rounds asked for changes on `SHA`, with a `low` open beside a `medium`.
const TWO_ASKED = [1, 2].map((n) => round(n, 'changes-requested', [rated('F1', 'low'), rated('F2', 'medium')])).join('\n')

test('0121 R1: changes asked, then only a clean rebase — next offers impl and asks gh no checks', () => {
  const u = asked(TWO_ASKED)
  const calls = []
  const n = nextStep(u, { probe: rebasedProbe({ calls }) })
  assert.equal(n.stage, 'impl')
  for (const said of [/aaaaaaa/, /ddddddd/, /unchanged/, /open findings of review round 2/]) assert.match(n.action, said)
  assert.doesNotMatch(n.action, /[0-9a-f]{40}/)
  assert.deepEqual(calls.filter((c) => c.startsWith('gh pr checks')), [])
  for (const bucket of ['fail', 'pending']) {
    assert.equal(nextStep(u, { probe: rebasedProbe({ checks: [{ name: 'tests', bucket }] }) }).stage, 'impl')
  }
})

test('0121 R1: the 0115 case on a real repository — round 2 asked for changes, main moved, the branch was rebased, CI is green: impl', () => {
  const { repo, sh, R, onMain } = rebaseRepo()
  onMain(farFromTheHunk)
  sh('rebase', '-q', 'main')
  const H = sh('rev-parse', 'HEAD')
  assert.notEqual(H, R)
  const u = asked(TWO_ASKED.replaceAll(SHA, R))
  assert.equal(nextStep(u, { probe: realProbe(repo, H) }).stage, 'impl')
  // R6: the review gate is as it was — `next` chooses, the gate does not close.
  assert.equal(checkGate(u, 'review', { probe: realProbe(repo, H) }).ok, true)
})

test('0121 R2: a rebase with a fix commit on top goes to review on green, impl on red, nothing while CI runs', () => {
  const { repo, sh, commit, R, onMain } = rebaseRepo()
  onMain(farFromTheHunk)
  sh('rebase', '-q', 'main')
  const file = join(repo, 'a.txt')
  writeFileSync(file, readFileSync(file, 'utf8').replace('line 40\n', 'line 40, fixed by the unit\n'))
  const H = commit('-m', 'the fix')
  const u = asked(TWO_ASKED.replaceAll(SHA, R))
  const n = nextStep(u, { probe: realProbe(repo, H) })
  assert.equal(n.stage, 'review')
  assert.match(n.action, /differs from the reviewed one in a\.txt/)
  assert.equal(nextStep(u, { probe: realProbe(repo, H, [{ name: 'tests', bucket: 'fail' }]) }).stage, 'impl')
  assert.equal(nextStep(u, { probe: realProbe(repo, H, [{ name: 'tests', bucket: 'pending' }]) }).stage, '')
})

test('0121 R2: a rebase whose patch differs in one file names it and goes to review', () => {
  const patch = (c) => (c === SHA ? PATCH_R : hunk(28, 'line 28, changed by main'))
  const n = nextStep(asked(TWO_ASKED), { probe: rebasedProbe({ patch }) })
  assert.equal(n.stage, 'review')
  assert.match(n.action, /was rewritten past aaaaaaa — its patch differs from the reviewed one in a\.txt/)
})

test('0121 R3: a rewrite that cannot be compared goes to review on green and says why', () => {
  const noTrunk = rebasedProbe()
  const git = noTrunk.git
  noTrunk.git = (...a) => (a[0] === 'rev-parse' && a[3]?.endsWith('/main') ? { code: 1, out: '', err: '' } : git(...a))
  const n = nextStep(asked(TWO_ASKED), { probe: noTrunk })
  assert.equal(n.stage, 'review')
  assert.match(n.action, /could not be compared with the reviewed one: there is no origin\/main and no main here/)
})

test('0121 R4: a head that was not rewritten asks git for no patch and no merge-base to trunk', () => {
  for (const [files, stage] of [[['src/a.py'], 'review'], [['.cos/0001_x/review.md'], 'impl']]) {
    const calls = []
    const probe = movedTo(files)
    const { git } = probe
    probe.git = (...a) => (calls.push(a.join(' ')), git(...a))
    assert.equal(nextStep(asked(TWO_ASKED), { probe }).stage, stage)
    assert.deepEqual(calls.filter((c) => c.startsWith('diff --no-color')), [])
    assert.deepEqual(calls.filter((c) => c.startsWith('merge-base ') && !c.startsWith('merge-base --is-ancestor ')), [])
  }
})

// --- 0125: a passing review the ship gate cannot read loops ------------------------------

const FULL_PAGE = '- .screens/board-1440x900.png — 1440×900 (full page) — /board — no violation'
// Screenshots taken off the branch: whatever else a round says, ship stays closed on
// `said.screens`, the one reason `next` sends an unchanged head back to review.
const STALE_AT = '7'.repeat(40)
const offBranch = screens({ taken: STALE_AT })
const NO = { code: 1, out: '', err: '' }
// A UI unit whose pull request's head is `head`, with the screenshots of a round on `SHA` or
// `REB` taken at `STALE_AT`, which is an ancestor of neither. `git` overrides any other answer.
const stuckProbe = (git = {}, head = SHA) => ({
  ...greenProbe(undefined, {
    [`diff --name-only origin/main...${head}`]: ok('coscc/screens.py'),
    [`merge-base --is-ancestor ${STALE_AT} ${SHA}`]: NO,
    [`merge-base --is-ancestor ${STALE_AT} ${REB}`]: NO,
    ...git,
  }, { state: 'OPEN', headRefOid: head }),
  ui: () => UI,
})
const passes = (...on) => on.map((sha, i) => `${round(i + 1, 'pass').replace(SHA, sha)}${offBranch}`).join('\n')
const passedOn = (...on) => branched({ ...CHAIN, 'review.md': reviewArt('accepted', passes(...on)) })

test('0125 R1: a screenshot line may carry a parenthesised note after its size, and size drops it', () => {
  const shots = (line) => parseReview(`${round(1, 'pass')}${screens({ shots: [line] })}`).rounds[0].screens.shots
  assert.deepEqual(shots(FULL_PAGE), [{ path: '.screens/board-1440x900.png', size: '1440x900', address: '/board', result: 'no violation' }])
  assert.deepEqual(shots(SHOT), [{ path: '.screens/board-1440x900.png', size: '1440x900', address: '/board', result: 'no violation' }])
})

test('0125 R2: 0120\'s five passing rounds with "1440×900 (full page)" open ship', () => {
  // `SHA` stands for 0120's fbdd49b: five passes on one head, each with the full-page line.
  const text = [1, 2, 3, 4, 5].map((n) => `${round(n, 'pass')}${screens({ shots: [FULL_PAGE] })}`).join('\n')
  const u = branched({ ...CHAIN, 'review.md': reviewArt('accepted', text) })
  const probe = uiProbe(['coscc/screens.py'])
  for (const r of parseReview(text).rounds) assert.deepEqual(screensProblems(r.screens, UI_STANDARD), [])
  const g = checkGate(u, 'ship', { probe })
  assert.equal(g.need.some((n) => /lists no screenshot/.test(n)), false)
  assert.deepEqual(g, { ok: true, need: [], head: SHA })
  assert.equal(nextStep(u, { probe }).stage, 'ship')
})

test('0125 R3: one pass the ship gate stays closed on sends next to review, and the review gate names why', () => {
  const u = passedOn(SHA)
  assert.equal(nextStep(u, { probe: stuckProbe() }).stage, 'review')
  const g = checkGate(u, 'review', { probe: stuckProbe() })
  assert.equal(g.ok, true)
  assert.deepEqual([g.retry.n, g.retry.reviewed], [1, SHA])
  assert.match(g.retry.need.join('; '), /not an ancestor/)
  assert.match(openLines('review', '0001_x', { retry: g.retry })[1],
    /^review round 1 passed on aaaaaaa and the ship gate is still closed: .*not an ancestor.* — this round is the one retry/)
})

test('0125 R3: the review gate adds nothing when the stop has come, when ship would open, or when the last round did not pass', () => {
  assert.deepEqual(checkGate(passedOn(SHA, SHA), 'review', { probe: stuckProbe() }), { ok: true, need: [] })
  const text = [1, 2, 3, 4, 5].map((n) => `${round(n, 'pass')}${screens({ shots: [FULL_PAGE] })}`).join('\n')
  assert.deepEqual(checkGate(branched({ ...CHAIN, 'review.md': reviewArt('accepted', text) }), 'review', { probe: uiProbe(['coscc/screens.py']) }), { ok: true, need: [] })
  const calls = []
  const probe = stuckProbe()
  const counted = { ...probe, gh: (...a) => (calls.push(a.join(' ')), probe.gh(...a)) }
  assert.deepEqual(checkGate(asked(), 'review', { probe: counted }), { ok: true, need: [] })
  assert.equal(calls.length, 1)
  assert.match(calls[0], /^pr checks /)
})

test('0125 R4: a second pass on the same head that leaves ship closed stops, with the gate\'s reasons', () => {
  const n = nextStep(passedOn(SHA, SHA), { probe: stuckProbe() })
  assert.equal(n.stage, '')
  assert.equal(n.blocked, true)
  assert.match(n.action, /^needs a person — review rounds 1 and 2 both passed on aaaaaaa and ship is still closed: /)
  assert.match(n.action, /not an ancestor of the reviewed commit/)
})

test('0125 R4: the same stop in the ship-refused branch', () => {
  const u = branched({ ...CHAIN, 'review.md': reviewArt('accepted', passes(SHA, SHA)), 'ship.md': shipArt(shipDraft(2)) })
  const n = nextStep(u, { probe: stuckProbe() })
  assert.equal(n.stage, '')
  assert.match(n.action, /^needs a person — review rounds 1 and 2 both passed on aaaaaaa and ship is still closed: .*not an ancestor of the reviewed commit/)
})

test('0125 R4: two passes on different heads do not stop', () => {
  const u = passedOn(SHA, REB)
  // Every git answer the test does not name is `ok('')`, which reads as the same head; so the
  // comparison between the two rounds is named here, both ways a head can differ.
  const moved = stuckProbe({ [`diff --name-only ${SHA}..${REB}`]: ok('coscc/runner.py') }, REB)
  assert.equal(nextStep(u, { probe: moved }).stage, 'review')
  const rewritten = stuckProbe({ [`merge-base --is-ancestor ${SHA} ${REB}`]: NO }, REB)
  assert.equal(nextStep(u, { probe: rewritten }).stage, 'review')
  // And the second of them is the retry: the review gate says so.
  assert.equal(checkGate(u, 'review', { probe: moved }).retry.n, 2)
})

test('0125 R4: a round whose reviewed commit is not here stops and says so', () => {
  const u = passedOn(SHA, REB)
  const gone = nextStep(u, { probe: stuckProbe({ [`cat-file -e ${SHA}^{commit}`]: { code: 1, out: '', err: 'fatal: not a valid object' } }, REB) })
  assert.equal(gone.stage, '')
  assert.match(gone.action, new RegExp(`^needs a person — the reviewed commit ${SHA} of review round 1 is not in this repository`))
  const broken = nextStep(u, { probe: stuckProbe({ [`diff --name-only ${SHA}..${REB}`]: { code: 128, out: '', err: 'fatal: bad object' } }, REB) })
  assert.equal(broken.stage, '')
  assert.match(broken.action, /^needs a person — git could not diff .*fatal: bad object/)
})

test('0125 review F2: where git cannot compare the two rounds, the stop does not say they share a head', () => {
  const u = passedOn(SHA, REB)
  const gone = nextStep(u, { probe: stuckProbe({ [`cat-file -e ${SHA}^{commit}`]: NO }, REB) })
  const broken = nextStep(u, { probe: stuckProbe({ [`diff --name-only ${SHA}..${REB}`]: { code: 128, out: '', err: 'fatal: bad object' } }, REB) })
  for (const { action } of [gone, broken]) {
    assert.doesNotMatch(action, /both passed/)
    assert.match(action, /review rounds 1 and 2 passed on aaaaaaa and ddddddd, not known to be one head, and ship is still closed: /)
  }
})

test('0125 review F1: a pull request behind the reviewed commit gets one retry, then stops', () => {
  // BEHIND is the reviewed commit's parent: the round was taken on a commit not pushed yet.
  const BEHIND = '6'.repeat(40)
  const probe = greenProbe(undefined, {
    [`merge-base --is-ancestor ${SHA} ${BEHIND}`]: NO,
    [`merge-base --is-ancestor ${BEHIND} ${SHA}`]: ok(),
  }, { state: 'OPEN', headRefOid: BEHIND })
  const once = passedOn(SHA)
  assert.equal(nextStep(once, { probe }).stage, 'review')
  assert.match(checkGate(once, 'review', { probe }).retry.need.join('; '), /is not on the head of #7/)
  const twice = nextStep(passedOn(SHA, SHA), { probe })
  assert.equal(twice.stage, '')
  assert.match(twice.action, /^needs a person — review rounds 1 and 2 both passed on aaaaaaa and ship is still closed: .*is not on the head of #7/)
  // A head that is not an ancestor, a rebase, is a new head: another round, as before.
  const rebased = greenProbe(undefined, {
    [`merge-base --is-ancestor ${SHA} ${REB}`]: NO,
    [`merge-base --is-ancestor ${REB} ${SHA}`]: NO,
  }, { state: 'OPEN', headRefOid: REB })
  assert.equal(nextStep(passedOn(SHA, SHA), { probe: rebased }).stage, 'review')
})

test('0125 R6: a commit outside .cos/ on the pull request head, or a rejected review, is the way out', () => {
  // HEAD2 follows SHA, so it is not an ancestor of it; the default `ok()` would say it is.
  const n = nextStep(passedOn(SHA, SHA), { probe: stuckProbe({ [`diff --name-only ${SHA}..${HEAD2}`]: ok('coscc/runner.py'), [`merge-base --is-ancestor ${HEAD2} ${SHA}`]: NO }, HEAD2) })
  assert.equal(n.stage, 'review')
  const rejected = branched({ ...CHAIN, 'review.md': reviewArt('rejected', passes(SHA, SHA)) })
  const r = nextStep(rejected, { probe: stuckProbe() })
  assert.deepEqual(r, nextAction(rejected))
  assert.doesNotMatch(r.action, /needs a person/)
})

test('0125 R7: the one retry does not read COS_REVIEW_ROUNDS', () => {
  const u = passedOn(SHA, SHA)
  const at = (limit) => nextStep(u, { probe: stuckProbe(), limit })
  assert.deepEqual(at(1), at(10))
  assert.deepEqual(at(1), nextStep(u, { probe: stuckProbe() }))
  assert.equal(at(1).stage, '')
})

// --- 0116: a pull request merged before ship.md was written ------------------------------

const MERGE = '9'.repeat(40)
const MERGED_AT = '2026-09-20T13:33:07Z'
const mergedView = (over = {}) => ({ state: 'MERGED', headRefOid: SHA, mergeCommit: { oid: MERGE }, mergedAt: MERGED_AT, ...over })
// A pull request GitHub reports merged as `MERGE`, which is here and on origin/main unless
// `git` says otherwise. Every call, git's and gh's, lands in `calls`.
const mergedProbe = ({ view = mergedView(), git = {}, checks, calls = [] } = {}) => {
  const probe = greenProbe(checks, git, view)
  return {
    gh: (...a) => (calls.push(['gh', ...a].join(' ')), probe.gh(...a)),
    git: (...a) => (calls.push(a.join(' ')), probe.git(...a)),
  }
}
const GONE = {
  'rev-parse --verify --quiet refs/heads/feat/x': NO,
  'rev-parse --verify --quiet refs/remotes/origin/feat/x': NO,
}

test('0116 R1: a merged pull request opens ship to record it when its branch is gone, local and origin', () => {
  const calls = []
  const g = checkGate(passedOnce(), 'ship', { probe: mergedProbe({ git: GONE, calls }) })
  assert.deepEqual(g, { ok: true, need: [], merged: { number: 7, commit: MERGE, at: MERGED_AT, head: SHA } })
  // One reading of the pull request, the merge commit asked for twice, and nothing else.
  assert.deepEqual(calls, [
    'gh pr view 7 --json state,headRefOid,mergeCommit,mergedAt,title',
    `cat-file -e ${MERGE}^{commit}`,
    `merge-base --is-ancestor ${MERGE} refs/remotes/origin/main`,
  ])
})

test('0116 R1: a merged pull request opens ship to record it while its branch is still here', () => {
  const u = passedOnce()
  assert.deepEqual(checkGate(u, 'ship', { probe: mergedProbe() }), { ok: true, need: [], merged: { number: 7, commit: MERGE, at: MERGED_AT, head: SHA } })
  // Nothing that speaks of a merge still to come closes it: red CI, a head behind origin/main,
  // code after the pass, a UI unit's screenshots. The head it merged is carried for ship.md.
  const calls = []
  const late = mergedProbe({
    view: mergedView({ headRefOid: HEAD2 }),
    checks: [{ name: 'tests', bucket: 'fail' }],
    git: {
      [`merge-base --is-ancestor ${TRUNK} ${HEAD2}`]: NO,
      [`diff --name-only ${SHA}..${HEAD2}`]: ok('src/a.py'),
      [`diff --name-only origin/main...${HEAD2}`]: ok('coscc/screens.py'),
    },
    calls,
  })
  const g = checkGate(u, 'ship', { probe: { ...late, ui: () => UI } })
  assert.equal(g.ok, true)
  assert.equal(g.merged.head, HEAD2)
  assert.equal('head' in g, false)
  assert.deepEqual(calls.filter((c) => c.startsWith('gh pr checks') || c.startsWith('rev-parse') || c.startsWith('diff')), [])
})

test('0116 R1: the open line names the pull request, the merge commit and when it merged, and no --match-head-commit', () => {
  const g = checkGate(passedOnce(), 'ship', { probe: mergedProbe({ git: GONE }) })
  const lines = openLines('ship', '0001_x', g)
  assert.deepEqual(lines, [`open: ship may proceed for 0001_x — #7 was merged as ${MERGE} at ${MERGED_AT}: record it in ship.md; do not merge`])
  assert.doesNotMatch(lines.join('\n'), /--match-head-commit/)
})

test('0116 R2: a merge commit that cannot be read, is not here, or is not on origin/main closes ship: fetch, then ask again', () => {
  const u = passedOnce()
  for (const [probe, said] of [
    [mergedProbe({ view: mergedView({ mergeCommit: null }) }), /^cannot read the merge commit of #7: gh gave mergeCommit none and mergedAt 2026-09-20T13:33:07Z — fetch, then ask again$/],
    [mergedProbe({ view: mergedView({ mergeCommit: { oid: 'abc' } }) }), /^cannot read the merge commit of #7: gh gave mergeCommit abc and/],
    [mergedProbe({ view: mergedView({ mergedAt: '' }) }), new RegExp(`^cannot read the merge commit of #7: gh gave mergeCommit ${MERGE} and mergedAt none — fetch, then ask again$`)],
    [mergedProbe({ git: { ...GONE, [`cat-file -e ${MERGE}^{commit}`]: NO } }), new RegExp(`^the merge commit ${MERGE} of #7 is not in this repository — fetch, then ask again$`)],
    [mergedProbe({ git: { [`merge-base --is-ancestor ${MERGE} refs/remotes/origin/main`]: NO } }), new RegExp(`^the merge commit ${MERGE} of #7 is not on origin/main here — fetch, then ask again$`)],
  ]) {
    const g = checkGate(u, 'ship', { probe })
    assert.deepEqual([g.ok, g.need.length], [false, 1])
    assert.match(g.need[0], said)
    assert.doesNotMatch(g.need[0], /MERGED, not open/)
    const n = nextStep(u, { probe })
    assert.deepEqual([n.stage, n.blocked], ['', true])
    assert.match(n.action, said)
  }
})

test('0116 R3: a pull request closed without merging still closes ship with the old sentence, and the unit is not finished', () => {
  const u = passedOnce()
  for (const git of [{}, GONE]) {
    const probe = mergedProbe({ view: { state: 'CLOSED', headRefOid: SHA, mergeCommit: null, mergedAt: null }, git })
    assert.deepEqual(checkGate(u, 'ship', { probe }), { ok: false, need: ['#7 is CLOSED, not open — there is nothing to merge'] })
    assert.deepEqual(nextStep(u, { probe }), { blocked: true, action: '#7 is CLOSED, not open — there is nothing to merge', stage: '' })
  }
})

test('0116 R4: next offers write-ship for a merged pull request when ship.md is missing, stale or a refused draft, and never says MERGED, not open', () => {
  const action = `write-ship — #7 was merged as ${MERGE} at ${MERGED_AT}: record it in ship.md; do not merge`
  const stale = { ...art('accepted'), stale: { stage: 'review', date: '2026-09-26' } }
  const refused = shipArt(shipDraft(1, 'failed to delete local branch fix/x: cannot switch to main'))
  for (const ship of [undefined, stale, refused]) {
    const u = branched({ ...CHAIN, 'review.md': reviewArt('accepted', round(1, 'pass')), ...(ship ? { 'ship.md': ship } : {}) })
    for (const git of [{}, GONE]) {
      const n = nextStep(u, { probe: mergedProbe({ git }) })
      assert.deepEqual(n, { blocked: true, action, stage: 'ship' })
      assert.doesNotMatch(n.action, /MERGED, not open/)
    }
  }
})

test('0116 R5: an accepted ship.md is finished for a merged pull request without asking gh, with or without a repository', () => {
  const u = branched({ ...CHAIN, 'review.md': reviewArt('accepted', round(1, 'pass')), 'ship.md': art('accepted') })
  const calls = []
  for (const probe of [mergedProbe({ calls }), null]) {
    assert.deepEqual(nextStep(u, { probe }), { blocked: false, action: 'finished', stage: '' })
  }
  assert.deepEqual(calls, [])
})

// --- 0081: a unit stuck at the review limit is given a round more ------------------

// The bytes `POST /api/units/more-rounds` appends (`coscc/more_rounds.py`), date fixed.
const MORE = '\n### More rounds\nDecided by: owner. Date: 2026-09-27. Via: product.\nRounds: 1\n'
const stuckRounds = (n) => [...Array(n)].map((_, i) => round(i + 1, 'changes-requested', ['- F1 [open] a'])).join('\n')
const STUCK = `${REVIEW_HEAD}${stuckRounds(3)}`
const withMore = (review, ...blocks) => `${review}\n## Answers\n${blocks.join('')}`
const stuckFiles = (review) => ({
  'intent.md': '# I\nAuthor: t. Type: feat. Status: accepted.\n',
  'spec.md': '# S\nStatus: accepted.\n',
  'plan.md': '# P\nStatus: accepted.\n',
  'impl.md': implText(''),
  'pr.md': '# PR: feat(0001): x\nPR: https://github.com/o/r/pull/7. Status: accepted.\n',
  'review.md': review,
})
const stuck = (review, extra = {}) => questionTree({ ...stuckFiles(review), ...extra }).u

test('0081 R3: a more rounds block lifts the review limit of its unit alone', () => {
  const none = stuck(STUCK)
  assert.match(checkGate(none, 'review', { probe: greenProbe(), limit: 3 }).need[0], /needs a person — review used 3 of 3 rounds/)
  assert.match(nextAction(none, 3).action, /needs a person/)
  const given = stuck(withMore(STUCK, MORE))
  assert.equal(given.artifacts['review.md'].roundsGranted, 1)
  assert.deepEqual(given.problems, [])
  assert.equal(checkGate(given, 'review', { probe: greenProbe(), limit: 3 }).ok, true)
  assert.doesNotMatch(nextAction(given, 3).action, /needs a person/)
  assert.match(nextAction(given, 3).action, /3 of 4 rounds used/)
  assert.doesNotMatch(nextStep(given, { probe: greenProbe(), limit: 3 }).action, /needs a person/)
})

test('0081 R7: a unit that used its granted round needs a person again at the new limit', () => {
  const again = stuck(withMore(`${REVIEW_HEAD}${stuckRounds(4)}`, MORE))
  assert.match(checkGate(again, 'review', { probe: greenProbe(), limit: 3 }).need[0], /needs a person — review used 4 of 4 rounds/)
  assert.match(nextAction(again, 3).action, /review used 4 of 4 rounds/)
  assert.equal(moreRounds(again, 3), true)
  // Two blocks add up.
  assert.equal(reviewLimit(stuck(withMore(`${REVIEW_HEAD}${stuckRounds(4)}`, MORE, MORE)), 3), 5)
})

test('0081 R2: a more rounds block with no Decided by or no Rounds line is not counted and is reported', () => {
  const bad = [
    ['\n### More rounds\nRounds: 1\n', 'Decided by'],
    ['\n### More rounds\nDecided by: owner. Date: 2026-09-27. Via: product.\nRounds: 0\n', 'Rounds'],
    ['\n### More rounds\nDecided by: owner. Date: 2026-09-27. Via: product.\nRounds: x\n', 'Rounds'],
  ]
  for (const [block, line] of bad) {
    const u = stuck(withMore(STUCK, block))
    assert.equal('roundsGranted' in u.artifacts['review.md'], false, block)
    assert.deepEqual(u.problems, [`review.md: more rounds block 1 has no valid ${line} line — it is not counted`], block)
    assert.match(checkGate(u, 'review', { probe: greenProbe(), limit: 3 }).need[0], /needs a person — review used 3 of 3/)
  }
  // A bad block is numbered among the more rounds blocks only, and does not void a good one.
  const mixed = parseMoreRounds(withMore(STUCK, fBlock('F1'), MORE, bad[1][0]))
  assert.deepEqual(mixed, { granted: 1, problems: ['more rounds block 2 has no valid Rounds line — it is not counted'] })
})

test('0081 R2: a more rounds block is never an answer and never the tail of an F answer', () => {
  const answers = parseAnswers(withMore(STUCK, fBlock('F1', 'đã chạy'), MORE))
  assert.equal(answers.length, 1)
  assert.equal(answers[0].id, 'F1')
  assert.equal(answers[0].text, 'đã chạy')
  assert.doesNotMatch(answers[0].text, /Rounds:/)
  // Nor is it a round: `parseReview` stops at `## Answers`.
  assert.equal(parseReview(withMore(STUCK, MORE)).rounds.length, 3)
})

test('0081 R4: moreRounds is attached only to a unit out of rounds and neither field to any other unit', () => {
  const HOLD = '# I\nAuthor: t. Type: feat. Status: accepted.\n\n## Answers\n\n### Paused\nDecided by: owner. Date: 2026-09-27. Via: product.\n\nchờ\n'
  const cases = {
    out: [stuck(STUCK), true],
    notYet: [stuck(`${REVIEW_HEAD}${stuckRounds(1)}`), false],
    done: [stuck(STUCK, { 'plan.md': '# P\nStatus: done.\n' }), false],
    paused: [stuck(STUCK, { 'intent.md': HOLD }), false],
    rejected: [stuck(STUCK, { 'spec.md': '# S\nStatus: rejected.\n' }), false],
  }
  for (const [name, [u, want]] of Object.entries(cases)) {
    assert.equal(moreRounds(u, 3), want, name)
    assert.equal('roundsGranted' in u.artifacts['review.md'], false, name)
  }
  // Through `status --json`: the key is on the stuck unit's row and on no other.
  const { root } = questionTree(stuckFiles(STUCK))
  const second = join(root, '.cos', '0002_q')
  mkdirSync(second)
  for (const [f, text] of Object.entries(stuckFiles(`${REVIEW_HEAD}${stuckRounds(1)}`))) writeFileSync(join(second, f), text)
  const out = cli('status', '--json', '--root', root)
  assert.equal(out.status, 0, out.stderr)
  const rows = Object.fromEntries(JSON.parse(out.stdout).units.map((u) => [u.name, u]))
  assert.equal(rows['0001_q'].moreRounds, true)
  assert.equal('moreRounds' in rows['0002_q'], false)
  assert.equal('roundsGranted' in rows['0001_q'].artifacts['review.md'], false)
})

test('0081 R9: a more rounds block changes no answer of the ship gate', () => {
  const findings = [rated('F1', 'medium'), '- F2 [open] a.py:3 — low — S3 a SHA on the card']
  const ship = (review) => checkGate(stuck(review), 'ship', { probe: greenProbe(), limit: 3 })
  const one = `${REVIEW_HEAD}${round(1, 'changes-requested', findings)}`
  assert.deepEqual(ship(withMore(one, MORE)), ship(one))
  assert.equal(ship(withMore(one, MORE)).ok, false)
  // With the rounds used up, the block that lifts review still leaves ship as it was.
  const used = `${REVIEW_HEAD}${[1, 2, 3].map((n) => round(n, 'changes-requested', findings)).join('\n')}`
  assert.equal(checkGate(stuck(withMore(used, MORE)), 'review', { probe: greenProbe(), limit: 3 }).ok, true)
  assert.deepEqual(ship(withMore(used, MORE)), ship(used))
  assert.equal(ship(withMore(used, MORE)).ok, false)
})

// --- 0103: a red check no rerun can fix just stops ---------------------------------------

// `.github/workflows/pr.yml:26-37` under its first lines, copied so the proof still runs
// where `.claude/` was copied without `.github/`.
const PR_YML = {
  path: '.github/workflows/pr.yml',
  text: [
    'name: pr', '', 'on:', '  pull_request:', '', 'permissions:', '  contents: read', '',
    'jobs:',
    '  branch-name:',
    '    runs-on: ubuntu-latest',
    '    steps:',
    '      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7',
    '      - name: The source branch follows the grammar in .claude/CLAUDE.md',
    '        # `github.head_ref` is the branch the pull request comes from. The check is the',
    '        # same one that runs locally, from the same file, so CI and `cos.mjs` cannot',
    '        # disagree about what a valid name is.',
    '        run: node .claude/scripts/cos.mjs check-branch "$HEAD_REF"',
    '        env:',
    '          HEAD_REF: ${{ github.head_ref }}',
    '',
  ].join('\n'),
}
// #97 as `spike.md ## U1` read it: `seq 30` and `seq 34`.
const HEAD_97 = 'feat/open-questions-wait-for-the-originator-even-when-precedent-answers-them'
const REASON_97 = 'the slug is 71 characters, over the 60 allowed'
const RED_97 = [{ name: 'branch-name', bucket: 'fail' }, { name: 'tests', bucket: 'pending' }]
const STOP_97 = `needs a person — CI is red on #7: branch-name — branch-name checks the branch name, and no rerun or impl can fix it: "${HEAD_97}" is not a work branch: ${REASON_97}`
const RED_LINE = (names) => `CI is red on #7: ${names} — back to impl: fix on the branch and push`
// Any probe, with `gh pr view --json headRefName` answering `head` (`null`: gh fails) and the
// workflows `workflows`. Every `gh` call lands in `calls`.
const headProbe = (probe, { head = HEAD_97, workflows = [PR_YML], calls = [] } = {}) => ({
  ...probe,
  gh: (...a) => {
    calls.push(['gh', ...a].join(' '))
    if (a.at(-1) !== 'headRefName') return probe.gh(...a)
    return head === null ? { code: 1, out: '', err: 'HTTP 502: Bad Gateway' } : ok(JSON.stringify({ headRefName: head }))
  },
  workflows: () => workflows,
})

test('0103 R8: #97 — branch-name red while tests pend: next stops, needs a person, with the check and check-branch\'s line', () => {
  const u = branched({ ...CHAIN, 'pr.md': { ...PR, pr: { url: 'https://github.com/o/r/pull/97', number: 97 } } })
  assert.equal(branchProblem(HEAD_97), REASON_97)
  const n = nextStep(u, { probe: headProbe(greenProbe(RED_97)) })
  assert.equal(n.stage, '')
  assert.equal(n.blocked, true)
  assert.ok(n.action.startsWith('needs a person — '), n.action)
  for (const part of ['#97', 'branch-name', REASON_97]) assert.ok(n.action.includes(part), part)
  assert.equal(n.action, STOP_97.replace('#7', '#97'))
  // `tests` still running is not waited on (R5), and nothing sends it to impl.
  assert.doesNotMatch(n.action, /has not finished|back to impl/)
})

test('0103 R8: a code failure goes back to impl with the red line byte for byte', () => {
  const checks = [{ name: 'branch-name', bucket: 'pass' }, { name: 'tests', bucket: 'fail' }]
  const n = nextStep(branched(CHAIN), { probe: headProbe(greenProbe(checks), { head: 'feat/x' }) })
  assert.deepEqual(n, { blocked: true, action: 'CI is red on #7: tests — back to impl: fix on the branch and push', stage: 'impl' })
})

test('0103 R4 a: a red branch-name check on a valid head goes to impl, saying why cannot be told from here', () => {
  const n = nextStep(branched(CHAIN), { probe: headProbe(greenProbe([{ name: 'branch-name', bucket: 'fail' }]), { head: 'feat/x' }) })
  assert.deepEqual(n, {
    blocked: true,
    action: `${RED_LINE('branch-name')} — branch-name checks the branch name, yet "feat/x" passes check-branch here, so why it failed cannot be told from here`,
    stage: 'impl',
  })
})

test('0103 R4 b, c: a bad head no red check runs check-branch for, or a head gh cannot read, goes to impl with the line kept and a clause after it', () => {
  const u = branched(CHAIN)
  const b = (names) => `${RED_LINE(names)} — "${HEAD_97}" is not a work branch: ${REASON_97}, but no red check runs cos.mjs check-branch in .github/workflows/, so whether that is why cannot be told from here`
  // (b): the red check is another one, or no workflow names the red one.
  for (const [checks, workflows, names] of [
    [[{ name: 'tests', bucket: 'fail' }, { name: 'branch-name', bucket: 'pass' }], [PR_YML], 'tests'],
    [[{ name: 'branch-name', bucket: 'fail' }], [], 'branch-name'],
    [[{ name: 'branch-name', bucket: 'cancel' }], [{ path: '.github/workflows/x.yml', text: 'jobs:\n  branch-name:\n    steps:\n      - run: npm test\n' }], 'branch-name'],
  ]) {
    assert.deepEqual(nextStep(u, { probe: headProbe(greenProbe(checks), { workflows }) }), { blocked: true, action: b(names), stage: 'impl' })
  }
  // (c): gh cannot say which branch, so #97's checks go to impl as they did before `0103`.
  const c = nextStep(u, { probe: headProbe(greenProbe(RED_97), { head: null }) })
  assert.deepEqual(c, {
    blocked: true,
    action: `${RED_LINE('branch-name')} — cannot read the branch of #7 (HTTP 502: Bad Gateway), so whether its name is why cannot be told from here`,
    stage: 'impl',
  })
  // An answer with no `headRefName` in it is no branch either.
  assert.match(nextStep(u, { probe: { ...headProbe(greenProbe(RED_97)), gh: greenProbe(RED_97).gh } }).action, /cannot read the branch of #7 \(\{"state":"OPEN"/)
})

test('0103 R3: every branch of next that reads CI stops on the same line, and the review and ship gates close on it', () => {
  const probe = () => headProbe(greenProbe(RED_97))
  const rebased = () => headProbe(rebasedProbe({ checks: RED_97 }))
  const stale = { ...reviewArt('accepted', round(1, 'pass')), stale: { stage: 'impl', date: '2026-09-26' } }
  const passed = { ...CHAIN, 'review.md': reviewArt('accepted', round(1, 'pass')) }
  for (const [label, u, p] of [
    ['pr done, no review', branched(CHAIN), probe()],
    ['a stale review', branched({ ...CHAIN, 'review.md': stale }), probe()],
    ['person-answered', tree0028({ review: `${REVIEW_HEAD}${ROUND1}\n${ROUND2}\n${ROUND3()}\n## Answers\n${fBlock('F2')}${fBlock('F3')}` }), probe()],
    ['every open finding claimed', tree0028({ review: `${REVIEW_HEAD}${ROUND1}\n${ROUND2}` }), probe()],
    ['review-incomplete', reviewOfRounds('draft', [cr(1), incompleteRound(2)]), probe()],
    ['changes-requested with a fix', asked(), headProbe(movedTo(['src/a.py'], RED_97))],
    ['ship after a clean rebase', branched(passed), rebased()],
    ['ship-refused after a clean rebase', branched({ ...passed, 'ship.md': shipArt(shipDraft(1)) }), rebased()],
  ]) {
    assert.deepEqual(nextStep(u, { probe: p }), { blocked: true, action: STOP_97, stage: '' }, label)
  }
  assert.deepEqual(checkGate(branched(CHAIN), 'review', { probe: probe() }), { ok: false, need: [STOP_97] })
  assert.deepEqual(checkGate(branched(passed), 'ship', { probe: rebased() }), { ok: false, need: [STOP_97] })
})

test('0103 R7: a red read asks gh once more for the head, a green read never does', () => {
  const read = ['gh pr checks 7 --required --json name,bucket']
  for (const [checks, more] of [
    [[{ name: 'tests', bucket: 'pass' }], []],
    [[{ name: 'tests', bucket: 'pending' }], []],
    [[], []],
    [[{ name: 'tests', bucket: 'fail' }], ['gh pr view 7 --json headRefName']],
    [RED_97, ['gh pr view 7 --json headRefName']],
  ]) {
    const calls = []
    checkGate(branched(CHAIN), 'review', { probe: headProbe(greenProbe(checks), { calls }) })
    assert.deepEqual(calls, [...read, ...more])
  }
})

test('0103 R2 a: branchChecks finds the job whose run step calls cos.mjs check-branch, by its name or its key, and nothing else', () => {
  const wf = (text, path = '.github/workflows/x.yml') => ({ path, text })
  assert.deepEqual(branchChecks([PR_YML]), ['branch-name'])
  // The job's own `name:`, quoted or not, over its key; a `.yaml` file; a `run: |` block.
  const named = wf('jobs:\n  names:\n    name: "Branch name" # the check\n    runs-on: x\n    steps:\n      - run: node .claude/scripts/cos.mjs check-branch "$H"\n')
  assert.deepEqual(branchChecks([named]), ['Branch name'])
  const block = wf("jobs:\n  a:\n    name: 'the branch'\n    steps:\n      - name: check\n        run: |\n          set -e\n          node .claude/scripts/cos.mjs check-branch \"$H\"\n  b:\n    steps:\n      - run: npm test\n", '.github/workflows/y.yaml')
  assert.deepEqual(branchChecks([block]), ['the branch'])
  assert.deepEqual(branchChecks([PR_YML, named, block]), ['branch-name', 'Branch name', 'the branch'])
  // A step's `name:` is not the job's, at any indentation.
  assert.deepEqual(branchChecks([wf('jobs:\n  b:\n    steps:\n    - name: a step\n      run: node cos.mjs check-branch x\n')]), ['b'])
  for (const text of [
    // A comment is not a call, inline or inside a block.
    'jobs:\n  tests:\n    steps:\n      - name: not the job\n        # node .claude/scripts/cos.mjs check-branch\n        run: npm test\n  lint:\n    steps:\n    - name: x\n      run: |\n        # cos.mjs check-branch "$H"\n        echo ok\n',
    // A name built from an expression is not known here (spec C4).
    'jobs:\n  b:\n    name: ${{ matrix.os }} branch\n    steps:\n      - run: node cos.mjs check-branch x\n',
    // The words anywhere but a `run:` step, or no `jobs:` at all.
    'jobs:\n  b:\n    env:\n      X: node cos.mjs check-branch\n    steps:\n      - run: echo\n',
    'name: cos.mjs check-branch\non: push\n',
  ]) {
    assert.deepEqual(branchChecks([wf(text)]), [], text)
  }
  // The workflow this repository runs, when it has one.
  const real = makeProbe(REPO).workflows()
  if (real.some((f) => f.path === PR_YML.path)) assert.ok(branchChecks(real).includes('branch-name'))
})

test('0103: makeProbe reads the workflows of the repository it is given, and none when it has none', () => {
  const repo = mkdtempSync(join(tmpdir(), 'cos-wf-'))
  assert.deepEqual(makeProbe(repo).workflows(), [])
  const dir = join(repo, '.github', 'workflows')
  mkdirSync(dir, { recursive: true })
  writeFileSync(join(dir, 'pr.yml'), PR_YML.text)
  writeFileSync(join(dir, 'b.yaml'), 'jobs: {}\n')
  writeFileSync(join(dir, 'README.md'), 'not a workflow')
  assert.deepEqual(makeProbe(repo).workflows(), [{ path: '.github/workflows/b.yaml', text: 'jobs: {}\n' }, PR_YML])
  assert.deepEqual(branchChecks(makeProbe(repo).workflows()), ['branch-name'])
})

test('0103: check-branch prints notAWorkBranch\'s line unchanged', () => {
  const out = cli('check-branch', HEAD_97)
  assert.equal(out.status, 1)
  assert.equal(out.stderr, `"${HEAD_97}" is not a work branch: ${REASON_97}\n`)
  assert.equal(notAWorkBranch(HEAD_97, REASON_97), out.stderr.trimEnd())
  assert.equal(cli('check-branch', 'feat/x').stdout, 'feat/x\n')
})

// --- 0040: one idea, several units, several repositories ----------------------------

const I40 = (status, header = '') => `# Intent: x\nAuthor: a. Type: feat. Status: ${status}.${header ? `\n${header}` : ''}\n`
// A store root whose `.cos/` holds `units`, `{ name: { file: text } }`, and `ideas`.
const store40 = (units, ideas = {}) => {
  const root = mkdtempSync(join(tmpdir(), 'cos-0040-'))
  mkdirSync(join(root, '.cos'))
  for (const [u, files] of Object.entries(units)) {
    mkdirSync(join(root, '.cos', u))
    for (const [f, t] of Object.entries(files)) writeFileSync(join(root, '.cos', u, f), t)
  }
  if (Object.keys(ideas).length) mkdirSync(join(root, '.cos', 'ideas'))
  for (const [f, t] of Object.entries(ideas)) writeFileSync(join(root, '.cos', 'ideas', f), t)
  return root
}
const IDEA40 = (...lines) =>
  `# Idea: one feature\nAuthor: the originator. Status: accepted.\n\n## In their own words\n\nboth sides\n\n## Units\n\n${lines.map((l) => `${l}\n`).join('')}`
// A unit whose next stage is `impl`, its links in its header.
const toImpl = (header) => ({ 'intent.md': I40('accepted', header), 'spec.md': '# Spec\nStatus: skipped.\n', 'plan.md': '# Plan\nStatus: accepted.\n' })
// Workspace `a` holds the unit depended on; `b` holds the idea and the unit that waits.
const pair40 = ({ ship = 'draft', aIntent = I40('accepted'), line = '- b/0001_y. Depends on: a/0001_x.', header = 'Idea: ideas/0001_f.md. Repo: b. Depends on: a/0001_x.' } = {}) => {
  const a = store40({ '0001_x': { 'intent.md': aIntent, ...(ship ? { 'ship.md': `# Ship\nStatus: ${ship}.\n` } : {}) } })
  const b = store40({ '0001_y': toImpl(header) }, { '0001_f.md': IDEA40('- a/0001_x.', line) })
  return { a, b }
}
const json40 = (out) => JSON.parse(out.stdout)

test('0040 R1: new-idea gives 0001 then 0002 and leaves new-path numbering alone', () => {
  const root = store40({ '0007_u': { 'intent.md': I40('accepted') } })
  const first = cli('--root', root, 'new-idea', 'one')
  assert.equal(first.status, 0, first.stderr)
  assert.equal(first.stdout, '.cos/ideas/0001_one.md\n')
  assert.deepEqual(readdirSync(join(root, '.cos')), ['0007_u'], 'new-idea creates nothing')
  mkdirSync(join(root, '.cos', 'ideas'))
  writeFileSync(join(root, first.stdout.trim()), IDEA40())
  assert.equal(cli('--root', root, 'new-idea', 'two').stdout, '.cos/ideas/0002_two.md\n')
  assert.equal(cli('--root', root, 'new-path', 'z').stdout, '.cos/0008_z\n')
})

test('0040 R1: new-idea refuses a bad slug with exit 2', () => {
  const root = store40({})
  for (const slug of ['Bad_Slug', 'a'.repeat(61), '']) {
    const out = cli('--root', root, 'new-idea', slug)
    assert.equal(out.status, 2, slug)
    assert.equal(out.stdout, '')
  }
})

test('0040 R2, R3: a store with ideas/ reports no problem about it', () => {
  const { b } = pair40()
  const status = json40(cli('--root', b, 'status', '--json'))
  assert.deepEqual(status.units.map((u) => u.name), ['0001_y'])
  assert.ok(!status.units.some((u) => u.problems.some((p) => /ideas/.test(p) && !/Depends on/.test(p))), JSON.stringify(status.units))
  assert.deepEqual(status.ideas, [{
    id: '0001_f', title: 'one feature', status: 'accepted', problems: [],
    units: [{ ref: 'a/0001_x', dependsOn: [] }, { ref: 'b/0001_y', dependsOn: ['a/0001_x'] }],
  }])
})

// The sha256 of `status --json` of the store below, its root blanked, as the `cos.mjs` of
// `5556245` printed it, before `0040` touched the file.
const BEFORE_0040 = 'f7851805e41dcd5ee4c4d9e8fb136515e23784eea2dc82e79fda647524e3f3cb'

test('0040 R2: status --json without ideas/ is byte-identical', () => {
  const root = store40({
    // The fields in the body, not the header, are not read.
    '0001_plain': {
      'intent.md': `${I40('accepted')}\n## Problem\n\nIdea: ideas/0001_y.md. Repo: x. Depends on: 0002_other.\n`,
      'spec.md': '# Spec\nStatus: skipped.\n',
      'plan.md': '# Plan\nStatus: accepted.\n',
    },
    '0002_other': { 'intent.md': `${I40('draft')}\n## Open questions\n\n1. Which?\n` },
    '0003_shipped': Object.fromEntries(['intent', 'spec', 'plan', 'impl', 'pr', 'review', 'ship'].map((s) => [`${s}.md`, s === 'intent' ? I40('accepted') : `# ${s}\nStatus: accepted.\n`])),
  })
  const out = cli('--root', root, 'status', '--json').stdout
  assert.equal(createHash('sha256').update(out.split(join(root, '.cos')).join('<root>')).digest('hex'), BEFORE_0040)
  assert.doesNotMatch(out, /"ideas":|"idea":|"repo":|"dependsOn":/)
})

test('0040 R4: a child unit carries idea, repo and dependsOn in status --json', () => {
  const { a, b } = pair40()
  const [u] = json40(cli('--root', b, 'status', '--json', '--peer', `a=${a}`)).units
  assert.equal(u.idea, 'ideas/0001_f.md')
  assert.equal(u.repo, 'b')
  assert.deepEqual(u.dependsOn, [{ ref: 'a/0001_x', merged: false, why: 'not merged: its ship.md is not accepted' }])
  assert.deepEqual(u.problems, [])
})

test('0040 R4: a unit without those headers has none of the keys', () => {
  const { a } = pair40()
  const [u] = json40(cli('--root', a, 'status', '--json')).units
  for (const key of ['idea', 'repo', 'dependsOn']) assert.ok(!(key in u), key)
})

test('0040 R4: the header fields read to whitespace, less the closing dot, and a list splits on commas', () => {
  assert.deepEqual(parseLinks('# Intent: x\nIdea: a/ideas/0001_f.md. Repo: b. Depends on: a/0001_x, 0002_y. Status: accepted.\n'),
    { idea: 'a/ideas/0001_f.md', repo: 'b', dependsOn: ['a/0001_x', '0002_y'] })
  assert.deepEqual(parseLinks('# Intent: Idea: no\nStatus: accepted.\n\n## Problem\n\nRepo: body.\n'), { idea: null, repo: null, dependsOn: null })
  assert.deepEqual(parseIdeaRef('ideas/0001_f.md'), { ws: null, file: '0001_f.md' })
  assert.deepEqual(parseIdeaRef('proj/ideas/0001_f.md'), { ws: 'proj', file: '0001_f.md' })
  assert.equal(parseIdeaRef('proj/ideas/0001_f'), null)
  assert.equal(parseIdeaRef('a/b/ideas/0001_f.md'), null)
  assert.deepEqual(parseUnitRef('api/0001_x'), { ws: 'api', name: '0001_x' })
  assert.equal(parseUnitRef('api/1_x'), null)
})

test('0040 R3: a line under ## Units that is not the grammar is a problem of that idea', () => {
  const idea = parseIdea(IDEA40('- a/0001_x.', '- 0002_no-workspace.', 'a note', '- b/0003_z. Depends on: nowhere.'))
  assert.deepEqual(idea.units, [{ ref: 'a/0001_x', dependsOn: [] }])
  assert.equal(idea.problems.length, 3)
  assert.deepEqual(parseIdea('# Idea: x\nStatus: accepted.\n').problems, ['has no ## Units section'])
})

test('0040 R6: a missing peer, a missing idea file and an unlisted unit each land in problems', () => {
  const b = store40({
    '0002_p': toImpl('Repo: b. Depends on: c/0001_z.'),
    '0003_q': toImpl('Idea: ideas/0009_none.md. Repo: b.'),
    '0004_r': toImpl('Idea: ideas/0001_f.md. Repo: b.'),
    '0005_s': toImpl('Idea: ideas/0001_f.md.'),
  }, { '0001_f.md': IDEA40('- b/0005_s.') })
  const units = json40(cli('--root', b, 'status', '--json')).units
  const said = (name) => units.find((u) => u.name === name).problems.join('\n')
  // `0135`: the snapshot names the workspaces, where `--peer` once named stores.
  assert.match(said('0002_p'), /Depends on: c\/0001_z — the app has no workspace named c/)
  assert.match(said('0003_q'), /Idea: ideas\/0009_none\.md — the app knows no \/ideas\/0009_none\.md/)
  assert.match(said('0004_r'), /does not list b\/0004_r under ## Units/)
  assert.match(said('0005_s'), /declares no Repo:/)
})

// `0135`: `--peer` is gone. Every use of it, well formed or not, on any command, is misuse.
test('0040 R5, since 0135: --peer exits 2 wherever it is given', () => {
  const root = store40({})
  for (const args of [['status', '--peer', 'a=/x'], ['status', '--peer', 'a/b=/x'], ['status', '--peer'], ['new-path', 'x', '--peer', 'a=/x']]) {
    const out = spawnSync(process.execPath, [SCRIPT, '--root', root, ...args, '--state', '-'], { encoding: 'utf8', input: JSON.stringify(stateOfRoots(root)) })
    assert.equal(out.status, 2, args.join(' '))
    assert.match(out.stderr, /--peer is gone since 0135/)
    assert.equal(out.stdout, '')
  }
})

test('0040 R7: impl gate stays shut while the dependency\'s ship.md is not accepted', () => {
  for (const ship of ['draft', null]) {
    const { a, b } = pair40({ ship })
    const out = cli('--root', b, 'gate', '0001_y', 'impl', '--peer', `a=${a}`)
    assert.equal(out.status, 1, String(ship))
    assert.match(out.stderr, /waits on a\/0001_x: not merged: its ship\.md is not accepted/)
    // `plan` is not `impl`: the wait closes nothing else.
    assert.equal(cli('--root', b, 'gate', '0001_y', 'plan', '--peer', `a=${a}`).status, 0)
  }
})

test('0040 R7: next says why dependency and names the ref', () => {
  const { a, b } = pair40()
  assert.deepEqual(json40(cli('--root', b, 'next', '0001_y', '--peer', `a=${a}`)),
    { unit: '0001_y', stage: '', action: `${WAITING_ON}a/0001_x to merge`, blocked: true, why: 'dependency' })
  const [u] = json40(cli('--root', b, 'status', '--json', '--peer', `a=${a}`)).units
  assert.equal(u.next.why, 'dependency')
  assert.equal(u.next.action, `${WAITING_ON}a/0001_x to merge`)
})

test('0040 R8: impl gate opens once the dependency\'s ship.md is accepted', () => {
  const { a, b } = pair40({ ship: 'accepted' })
  const out = cli('--root', b, 'gate', '0001_y', 'impl', '--peer', `a=${a}`)
  assert.equal(out.status, 0, out.stderr)
  const next = json40(cli('--root', b, 'next', '0001_y', '--peer', `a=${a}`))
  assert.equal(next.stage, 'impl')
  assert.ok(!('why' in next), 'why is carried only by a wait')
})

test('0040 R7: a workspace the snapshot does not name shuts impl and says so', () => {
  const { b } = pair40({ ship: 'accepted' })
  const out = cli('--root', b, 'gate', '0001_y', 'impl')
  assert.equal(out.status, 1)
  assert.match(out.stderr, /waits on a\/0001_x: the app has no workspace named a/)
})

test('0040 R9: a Depends on that differs from the idea\'s line shuts impl', () => {
  // The idea lists no dependency; the header declares one.
  let { a, b } = pair40({ ship: 'accepted', line: '- b/0001_y.' })
  let out = cli('--root', b, 'gate', '0001_y', 'impl', '--peer', `a=${a}`)
  assert.equal(out.status, 1)
  assert.match(out.stderr, /lists b\/0001_y with Depends on: nothing, but intent\.md declares a\/0001_x — the two must match/)
  // The header lacks the field the idea lists.
  ;({ a, b } = pair40({ ship: 'accepted', header: 'Idea: ideas/0001_f.md. Repo: b.' }))
  out = cli('--root', b, 'gate', '0001_y', 'impl', '--peer', `a=${a}`)
  assert.equal(out.status, 1)
  assert.match(out.stderr, /Depends on: a\/0001_x, but intent\.md declares none/)
  assert.equal(json40(cli('--root', b, 'next', '0001_y', '--peer', `a=${a}`)).stage, '')
  // Both agree, one of them without its `<ws>`: the unit's own.
  ;({ a, b } = pair40({ ship: 'accepted', line: '- b/0001_y. Depends on: b/0002_z.', header: 'Idea: ideas/0001_f.md. Repo: b. Depends on: 0002_z.' }))
  mkdirSync(join(b, '.cos', '0002_z'))
  writeFileSync(join(b, '.cos', '0002_z', 'intent.md'), I40('accepted'))
  writeFileSync(join(b, '.cos', '0002_z', 'ship.md'), '# Ship\nStatus: accepted.\n')
  out = cli('--root', b, 'gate', '0001_y', 'impl', '--peer', `a=${a}`)
  assert.equal(out.status, 0, out.stderr)
})

test('0040 R7: a dropped dependency shuts impl and says dropped', () => {
  const { a, b } = pair40({ ship: null, aIntent: `${I40('accepted')}\n## Answers\n${holdBlock('Dropped', 'Not needed.')}` })
  const out = cli('--root', b, 'gate', '0001_y', 'impl', '--peer', `a=${a}`)
  assert.equal(out.status, 1)
  assert.match(out.stderr, /waits on a\/0001_x: dropped/)
  const rejected = pair40({ ship: null, aIntent: I40('rejected') })
  assert.match(cli('--root', rejected.b, 'gate', '0001_y', 'impl', '--peer', `a=${rejected.a}`).stderr, /waits on a\/0001_x: rejected: its intent\.md is rejected/)
})

test('0040 R9: an unreadable Idea: shuts impl but no other gate', () => {
  const b = store40({ '0001_y': toImpl('Idea: b/ideas/0001_gone.md. Repo: b.') })
  for (const stage of ['spec', 'plan']) assert.equal(cli('--root', b, 'gate', '0001_y', stage, '--peer', `b=${b}`).status, 0, stage)
  const out = cli('--root', b, 'gate', '0001_y', 'impl', '--peer', `b=${b}`)
  assert.equal(out.status, 1)
  assert.match(out.stderr, /Idea: b\/ideas\/0001_gone\.md cannot be read: the app knows no b\/ideas\/0001_gone\.md/)
  const next = json40(cli('--root', b, 'next', '0001_y', '--peer', `b=${b}`))
  assert.equal(next.stage, '')
  assert.match(next.action, /^fix the idea link — /)
  assert.ok(!('why' in next))
})

test('0040 R7: a red check that sends the work back to impl waits on the dependency too', () => {
  const red = greenProbe([{ name: 'tests', bucket: 'fail' }])
  const files = (header, n) => ({ ...toImpl(header), 'impl.md': '# Impl\nStatus: accepted.\n', 'pr.md': PR_MD.replace('# PR: the pr body is taken from pr.md', `# PR: feat(${n}): x`) })
  const a = store40({ '0001_x': { 'intent.md': I40('accepted') } })
  const b = store40({ '0001_y': files('Repo: b. Depends on: a/0001_x.', '0001'), '0002_free': files('Repo: b.', '0002') })
  const peers = new Map([['a', a]])
  const read = (name) => readUnit(join(b, '.cos', name), name, { peers, cosDir: join(b, '.cos') })
  assert.equal(nextStep(read('0002_free'), { probe: red }).stage, 'impl', 'without a dependency, red goes back to impl')
  assert.deepEqual(nextStep(read('0001_y'), { probe: red }),
    { blocked: true, action: `${WAITING_ON}a/0001_x to merge`, stage: '', why: 'dependency' })
})

// --- 0049: the pull request's title is pr.md's, in one grammar, still so at ship ----------

test('0049 R3: titleProblem refuses the titles the spec names and passes the one it names', () => {
  const refused = [
    ['wip: impl run 1 stopped at max_turns before committing', '0049', /is not <type>\(<NNNN>\): <text>/],
    ['a clean rebase voids a passing review', '0049', /is not <type>\(<NNNN>\): <text>/],
    ['feat(0049): x', '0049', /type is "feat", but intent\.md declares Type: fix/],
    ['fix(0049): x', '0048', /names unit 0049, not 0048/],
    ['fix(0049): wip x', '0049', /opens with "wip"/],
    ['fix(0049): WIP: x', '0049', /opens with "wip"/],
    ['fix(0049): tiêu đề', '0049', /carries "ê", a letter with a diacritic/],
    ['fix(0049): de đi', '0049', /carries "đ"/],
    ['wibble(0049): x', '0049', /type "wibble" is not one of feat, fix/],
    [null, '0049', /no title/],
    ['', '0049', /no title/],
  ]
  for (const [title, number, why] of refused) assert.match(titleProblem(title, 'fix', number) ?? 'null', why, String(title))
  assert.equal(titleProblem('fix(0049): a pr title is taken from pr.md', 'fix', '0049'), null)
  // `wip` is a word, not a prefix: `wipe` is text like any other.
  assert.equal(titleProblem('fix(0049): wipe the stale manifest', 'fix', '0049'), null)
})

test('0049 R3: an em dash passes and a decomposed Vietnamese letter does not', () => {
  assert.equal(titleProblem('fix(0049): a title — with an em dash', 'fix', '0049'), null)
  const decomposed = 'fix(0049): tiêu'
  assert.ok(!decomposed.includes('ê'), 'the ê is a base and a combining mark')
  assert.match(titleProblem(decomposed, 'fix', '0049'), /carries "ê"/)
})

test('0049 R3: a unit with no type is named as the reason', () => {
  assert.match(titleProblem('fix(0049): x', null, '0049'), /intent\.md declares no Type/)
  const u = { ...unit({ ...CHAIN }), type: undefined }
  assert.match(checkGate(u, 'review').need.join('\n'), /intent\.md declares no Type/)
})

test('0049 R4: pr-text prints titleProblem beside the fields it printed before, and its exit code is unchanged', () => {
  const good = PR_MD.replace('# PR: the pr body is taken from pr.md', '# PR: fix(0049): a pr title is taken from pr.md')
  const root = prTree({ '0049_a': good, '0050_b': PR_MD })
  const ok49 = cli('--root', root, 'pr-text', '0049_a')
  assert.equal(ok49.status, 0, ok49.stderr)
  assert.deepEqual(JSON.parse(ok49.stdout), { unit: '0049_a', ...prText(good), status: 'accepted', titleProblem: null })
  const bad = cli('--root', root, 'pr-text', '0050_b')
  assert.equal(bad.status, 0, 'a title outside the grammar is still printed, exit 0')
  const got = JSON.parse(bad.stdout)
  assert.equal(got.title, 'the pr body is taken from pr.md')
  assert.match(got.titleProblem, /is not <type>\(<NNNN>\): <text>/)
  assert.deepEqual(Object.keys(got).sort(), ['body', 'scope', 'status', 'title', 'titleProblem', 'unit', 'url'])
})

// Every call, git's and gh's, lands in `calls`.
const counted = (probe, calls) => ({
  ...probe,
  gh: (...a) => (calls.push(['gh', ...a].join(' ')), probe.gh(...a)),
  git: (...a) => (calls.push(a.join(' ')), probe.git(...a)),
})
const WIP = 'wip: impl run 1 stopped at max_turns before committing'

test('0049 R5: the review gate is closed on a title outside the grammar, before gh is asked', () => {
  const calls = []
  const u = branched({ ...CHAIN, 'pr.md': { ...PR, title: WIP } })
  const g = checkGate(u, 'review', { probe: counted(greenProbe(), calls) })
  assert.equal(g.ok, false)
  assert.deepEqual(g.need, [`${titleProblem(WIP, 'feat', '0001')} — the pr stage writes the # PR: line of pr.md again`])
  assert.deepEqual(calls, [])
  // Read off the file, as the board reads it: a pr.md with no # PR: line at all.
  const { u: read } = questionTree({
    'intent.md': '# I\nAuthor: t. Type: feat. Status: accepted.\n',
    'spec.md': 'Status: accepted.\n', 'plan.md': 'Status: accepted.\n', 'impl.md': 'Status: accepted.\n',
    'pr.md': 'PR: https://github.com/o/r/pull/1. Status: accepted.\n',
  })
  assert.match(checkGate(read, 'review', { probe: counted(greenProbe(), calls) }).need[0], /pr\.md has no title/)
  assert.deepEqual(calls, [])
  // And with a good one the gate reads CI as before.
  assert.equal(checkGate(branched(CHAIN), 'review', { probe: greenProbe() }).ok, true)
})

test('0049 R5: next names no stage for a unit whose pr.md title is outside the grammar', () => {
  const calls = []
  const n = nextStep(branched({ ...CHAIN, 'pr.md': { ...PR, title: 'a clean rebase voids a passing review' } }), { probe: counted(greenProbe(), calls) })
  assert.equal(n.stage, '')
  assert.equal(n.blocked, true)
  assert.match(n.action, /the title "a clean rebase voids a passing review" is not <type>\(<NNNN>\): <text> — the pr stage writes/)
  assert.deepEqual(calls, [])
})

const passedTitled = (title) => branched({ ...CHAIN, 'pr.md': { ...PR, title }, 'review.md': reviewArt('accepted', round(1, 'pass')) })

test('0049 R6: the ship gate is closed on a title outside the grammar, before gh is asked', () => {
  const calls = []
  const g = checkGate(passedTitled('fix(0001): x'), 'ship', { probe: counted(greenProbe(), calls) })
  assert.equal(g.ok, false)
  assert.deepEqual(g.need, [`${titleProblem('fix(0001): x', 'feat', '0001')} — the pr stage writes the # PR: line of pr.md again`])
  assert.deepEqual(calls, [])
})

test('0049 R6: the ship gate is closed when the open pull request\'s title differs from pr.md, and names both', () => {
  const differs = greenProbe(undefined, {}, { state: 'OPEN', headRefOid: SHA, title: 'wip: something else' })
  const g = checkGate(passedOnce(), 'ship', { probe: differs })
  assert.equal(g.ok, false)
  assert.equal(g.need.length, 1, 'the title is the only reason')
  assert.equal(g.need[0], '#7 carries the title "wip: something else", not pr.md\'s "feat(0001): x" — put pr.md onto it (write-pr step 5), or start ship from the board, which does that first')
  // gh giving no title is not a title that matches.
  const none = checkGate(passedOnce(), 'ship', { probe: greenProbe(undefined, {}, { state: 'OPEN', headRefOid: SHA, title: undefined }) })
  assert.equal(none.ok, false)
  assert.match(none.need[0], /#7 carries no title gh could read, not pr\.md's "feat\(0001\): x"/)
  // Only once everything else is open: a finding still open is named, and the title is not.
  const open = branched({ ...CHAIN, 'review.md': reviewArt('accepted', round(1, 'pass', ['- F1 [open] x'])) })
  assert.doesNotMatch(checkGate(open, 'ship', { probe: differs }).need.join('\n'), /carries the title/)
})

test('0049 R6: titles that differ only in surrounding whitespace pass', () => {
  const spaced = greenProbe(undefined, {}, { state: 'OPEN', headRefOid: SHA, title: '  feat(0001): x \n' })
  assert.deepEqual(checkGate(passedOnce(), 'ship', { probe: spaced }), { ok: true, need: [], head: SHA })
  assert.equal(checkGate(passedOnce(), 'ship', { probe: greenProbe(undefined, {}, { state: 'OPEN', headRefOid: SHA, title: 'feat(0001): X' }) }).ok, false)
})

test('0049 R6: a merged pull request\'s title is not compared', () => {
  const g = checkGate(passedOnce(), 'ship', { probe: mergedProbe({ view: mergedView({ title: 'wip: not the one pr.md gives' }) }) })
  assert.deepEqual(g, { ok: true, need: [], merged: { number: 7, commit: MERGE, at: MERGED_AT, head: SHA } })
})

test('0049 R6: the title is read from the one gh pr view the ship gate already asks', () => {
  const calls = []
  assert.equal(checkGate(passedOnce(), 'ship', { probe: counted(greenProbe(), calls) }).ok, true)
  assert.deepEqual(calls.filter((c) => c.startsWith('gh ')), ['gh pr view 7 --json state,headRefOid,mergeCommit,mergedAt,title'])
  const differs = []
  checkGate(passedOnce(), 'ship', { probe: counted(greenProbe(undefined, {}, { state: 'OPEN', headRefOid: SHA, title: 'feat(0001): y' }), differs) })
  assert.deepEqual(differs.filter((c) => c.startsWith('gh ')), ['gh pr view 7 --json state,headRefOid,mergeCommit,mergedAt,title'])
})

test('0049: next offers ship when a differing title is the only thing closing its gate', () => {
  const differs = greenProbe(undefined, {}, { state: 'OPEN', headRefOid: SHA, title: 'feat(0001): y' })
  const n = nextStep(passedOnce(), { probe: differs })
  assert.equal(n.stage, 'ship')
  assert.equal(n.blocked, true)
  assert.match(n.action, /^write-ship — #7 carries the title "feat\(0001\): y", not pr\.md's "feat\(0001\): x"/)
  // A draft ship.md a refused merge left, against the last round: the same.
  const refused = branched({
    ...CHAIN, 'review.md': reviewArt('accepted', round(1, 'pass')),
    'ship.md': { ...art('draft'), ship: { round: 1, refused: 'Pull request is not mergeable' } },
  })
  assert.deepEqual(nextStep(refused, { probe: differs }), {
    blocked: true, stage: 'ship',
    action: 'write-ship — #7 carries the title "feat(0001): y", not pr.md\'s "feat(0001): x" — put pr.md onto it (write-pr step 5), or start ship from the board, which does that first',
  })
  // Any other reason alongside it and ship is not offered: the title is compared last.
  const behind = greenProbe(undefined, { [`merge-base --is-ancestor refs/remotes/origin/main ${SHA}`]: { code: 1, out: '', err: '' } }, { state: 'OPEN', headRefOid: SHA, title: 'feat(0001): y' })
  const late = nextStep(passedOnce(), { probe: behind })
  assert.equal(late.stage, '')
  assert.doesNotMatch(late.action, /carries the title/)
})

test('0049 R1: no skill opens a pull request with --fill, and write-pr names the title grammar', () => {
  const skills = join(REPO, '.claude', 'skills')
  const files = readdirSync(skills, { recursive: true }).filter((p) => p.endsWith('.md'))
  assert.ok(files.length > 5, files.join(', '))
  for (const f of files) assert.doesNotMatch(readFileSync(join(skills, f), 'utf8'), /--fill/, f)
  const pr = readFileSync(join(skills, 'write-pr', 'SKILL.md'), 'utf8')
  assert.match(pr, /<type>\(<NNNN>\): <text>/)
  assert.match(pr, /gh pr create --title/)
  assert.match(pr, /cos\.mjs pr-text <NNNN_slug>/)
})

// --- 0135: metadata read once, by `meta` ----------------------------------------

const META_STORE = fileURLToPath(new URL('../../coscc/units/testdata/meta_store', import.meta.url))
const META_BEFORE = fileURLToPath(new URL('../../coscc/units/testdata/meta_store_before.json', import.meta.url))
const metaOf = (...args) => {
  const out = cli('--root', META_STORE, 'meta', ...args)
  assert.equal(out.status, 0, out.stderr)
  return JSON.parse(out.stdout)
}

test('meta prints every field status reads, from the same parsers', () => {
  const { units, ideas } = metaOf()
  // Every directory, the one misnamed among them; never `ideas/`.
  assert.deepEqual(Object.keys(units).sort(), readdirSync(join(META_STORE, '.cos')).filter((d) => d !== 'ideas').sort())
  assert.equal(units['0016_bad-status'].artifacts['spec.md'].status, null)
  assert.equal(units['0016_bad-status'].artifacts['spec.md'].raw, 'approved')
  assert.equal(units['0015_no-status'].artifacts['intent.md'].raw, null)
  assert.equal(units['0010_full-loop'].artifacts['plan.md'].status, 'done')
  assert.equal(units['0014_changes-requested'].artifacts['review.md'].status, 'changes-requested')
  const spec = readFileSync(join(META_STORE, '.cos', '0013_open-question', 'spec.md'), 'utf8')
  assert.deepEqual(units['0013_open-question'].artifacts['spec.md'].questions, parseQuestions(spec))
  assert.deepEqual(units['0013_open-question'].answers, parseAnswers(spec).map((a) => ({ artifact: 'spec.md', ...a })))
  assert.equal(units['0013_open-question'].artifacts['spec.md'].sha256, createHash('sha256').update(spec).digest('hex'))
  assert.deepEqual(units['0014_changes-requested'].answers.map((a) => a.id), ['F1'])
  const intent = readFileSync(join(META_STORE, '.cos', '0011_paused-then-resumed', 'intent.md'), 'utf8')
  assert.deepEqual(units['0011_paused-then-resumed'].holds.map((h) => h.state), ['paused', 'active'])
  assert.deepEqual(parseHold(intent), { hold: null, problems: [] })
  assert.equal(units['0003_old-unit'].type, null)
  assert.equal(units['0017_linked'].type, 'feat')
  assert.deepEqual(units['0017_linked'].links, { idea: 'ideas/0001_x.md', repo: 'proj', dependsOn: ['0010_full-loop'] })
  assert.deepEqual(ideas.map((i) => i.units), [[{ ref: 'proj/0017_linked', dependsOn: ['proj/0010_full-loop'] }]])
})

test('meta of one unit reads only the artifacts named, and intent.md brings its header', () => {
  const only = metaOf('0013_open-question', 'spec.md').units['0013_open-question']
  assert.deepEqual(Object.keys(only.artifacts), ['spec.md'])
  assert.equal(only.type, undefined)
  assert.equal(metaOf('0013_open-question', 'intent.md').units['0013_open-question'].type, 'feat')
  assert.equal(cli('--root', META_STORE, 'meta', '0013_open-question', 'notes.md').status, 2)
  assert.equal(cli('--root', META_STORE, 'meta', '../x').status, 2)
})

// Test glue, never in `cos.mjs` (plan step 8): the snapshot the app would build from `meta`,
// for a store whose one workspace is `proj`.
const snapshotOf = ({ units, ideas }) => ({
  workspace: 'proj',
  workspaces: ['proj'],
  units: Object.fromEntries(Object.entries(units).map(([name, m]) => [`proj/${name}`, {
    artifacts: Object.fromEntries(Object.entries(m.artifacts).map(([f, a]) => [f, { status: a.status, raw: a.raw, questions: a.questions }])),
    type: m.type ?? null,
    links: m.links ?? { idea: null, repo: null, dependsOn: null },
    holds: (m.holds ?? []).filter((h) => h.by !== null),
    answers: m.answers,
    unknowns: [],
  }])),
  ideas: { proj: ideas ?? [] },
})

const strip = (text) => {
  const lines = text.split('\n')
  const cut = lines.findIndex((l) => l === '## Open questions' || l === '## Answers')
  return (cut === -1 ? lines : lines.slice(0, cut)).join('\n')
    .replace(/\b(Status|Type|Idea|Repo|Depends on):\s*[^\s]+(?:,\s*[^\s]+)*\.?/g, '')
}

test('0135 R6: stripping Status, Type, Idea, Depends on, Open questions and Answers lines leaves status, next and gate unchanged', () => {
  const dir = mkdtempSync(join(tmpdir(), 'cos-0135-'))
  cpSync(META_STORE, dir, { recursive: true })
  const state = JSON.stringify(snapshotOf(metaOf()))
  const ask = (...args) => {
    const out = cosRun(['--root', dir, '--state', '-', ...args], { encoding: 'utf8', input: state })
    return { status: out.status, stdout: out.stdout.replace(dir, '<root>'), stderr: out.stderr }
  }
  const questions = [['status', '--json'], ['next', '0013_open-question'], ['next', '0017_linked'], ['next', '0011_paused-then-resumed'],
    ['gate', '0013_open-question', 'plan'], ['gate', '0012_dropped', 'spec'], ['gate', '0017_linked', 'spec']]
  const before = questions.map((q) => ask(...q))
  // Real answers, not seven refusals: status reads, the draft spec shuts `plan`, the drop shuts `spec`.
  assert.deepEqual(before.map((b) => b.status), [0, 0, 0, 0, 1, 1, 0])
  const files = () => cli('--root', dir, 'status', '--json').stdout.replace(dir, '<root>')
  const unstripped = files()
  const cos = join(dir, '.cos')
  for (const unit of readdirSync(cos)) {
    for (const f of readdirSync(join(cos, unit))) {
      const path = join(cos, unit, f)
      writeFileSync(path, strip(readFileSync(path, 'utf8')))
    }
  }
  assert.equal(readFileSync(join(cos, '0013_open-question', 'spec.md'), 'utf8').includes('Status:'), false)
  const after = questions.map((q) => ask(...q))
  assert.deepEqual(after, before)
  // Without `--state` the same files now say something else, so what was stripped was read.
  assert.notEqual(files(), unstripped)
})

test('0135 R6: a deciding command without --state exits 2 and says it needs the app', () => {
  for (const args of [['status'], ['status', '--json'], ['gate', '0010_full-loop', 'spec'], ['next', '0010_full-loop'],
    ['rerun', '0010_full-loop'], ['unit-branch', '0010_full-loop'], ['pr-text', '0010_full-loop']]) {
    const out = spawnSync(process.execPath, [SCRIPT, '--root', META_STORE, ...args], { encoding: 'utf8' })
    assert.equal(out.status, 2, args.join(' '))
    assert.match(out.stderr, new RegExp(NEEDS_STATE.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
    assert.equal(out.stdout, '')
  }
  // `meta`, `new-path` and the `check-*` commands read no metadata, and need none.
  assert.equal(cli('--root', META_STORE, 'meta').status, 0)
})

test('0135 R6: changing a status in the snapshot changes the output', () => {
  const state = snapshotOf(metaOf())
  const ask = (s) => spawnSync(process.execPath, [SCRIPT, '--root', META_STORE, '--state', '-', 'next', '0013_open-question'], { encoding: 'utf8', input: JSON.stringify(s) })
  const before = JSON.parse(ask(state).stdout)
  state.units['proj/0013_open-question'].artifacts['spec.md'].status = 'accepted'
  state.units['proj/0013_open-question'].artifacts['spec.md'].questions = null
  const after = JSON.parse(ask(state).stdout)
  assert.notDeepEqual(after, before)
  assert.equal(after.stage, 'plan')
})

test('status --json of the fixture store is what it was before meta existed', () => {
  const out = cli('--root', META_STORE, 'status', '--json')
  assert.equal(out.status, 0, out.stderr)
  const now = JSON.parse(out.stdout)
  const before = JSON.parse(readFileSync(META_BEFORE, 'utf8'))
  assert.deepEqual({ ...now, root: before.root }, before)
})
