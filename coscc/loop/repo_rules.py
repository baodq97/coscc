"""The rules that ask the repository: `review`, `ship`, and `screens`.

The screens a unit changes, what the gate asks git and gh, `pass_left_closed`,
`branch_checks` and `run` (`screens`). Everything here asks the `probe` (`coscc.loop.probe`) and nothing else outside the
unit's files; `said` is the dict the gate fills in for `nextStep` to read, mutated in place.
"""

from __future__ import annotations

import os
import re

from coscc.loop import UNDEFINED, UNIT_RE, dig, js, nullish, stringify, trim, trim_start, truthy
from coscc.loop.model import (
    branch_problem,
    needs_a_person,
    not_a_work_branch,
    out_of_rounds,
    person_answers,
    pr_of,
    read_unit,
    review_limit,
    review_of,
    rounds_used,
    severity_rule,
)
from coscc.units import pr_title
from coscc.loop.probe import (
    DOT,
    NOT_S,
    S,
    make_probe,
    parse_json,
    ui_files,
)

__all__ = [
    "branch_checks",
    "changed_since",
    "ci_needs",
    "make_probe",
    "merged_line",
    "merged_needs",
    "normalize_patch",
    "pass_left_closed",
    "patch_files",
    "pr_head",
    "pr_view",
    "rebase_clean",
    "rebase_why",
    "red_needs",
    "review_needs",
    "run",
    "screens_answer",
    "screens_needs",
    "screens_problems",
    "ship_needs",
    "ui_changed",
    "unit_patch",
]

NO_REPO = "no repository given — pass --repo <dir>"


def _join(items, sep):
    """`items.join(sep)`: a `null` or `undefined` item is the empty string."""
    return sep.join("" if x is None or x is UNDEFINED else js(x) for x in items)


def _utf16(s):
    """The sort key of JavaScript's default `sort`: UTF-16 code units, not code points."""
    return s.encode("utf-16-be", "surrogatepass")


def _told(r):
    """What a `gh` answer said: its `stderr`, else its `stdout`, trimmed, else that it was silent."""
    return trim(r["err"] or r["out"]) or f"gh exited {js(r['code'])} and said nothing"


def _lines(text, own=None):
    """`text.split('\\n').map(trim).filter(Boolean)`, less the lines under `own` when given."""
    trimmed = [trim(line) for line in text.split("\n")]
    return [line for line in trimmed if line and not (own and line.startswith(own))]


# --- the screens a unit changes (0083) ---------------------------------------------------


def screens_problems(screens, standard_path):
    """the one place the words of a passing round's `### Screens` are judged. Returns
    what is wrong with them, `[]` when nothing is."""
    if not truthy(screens):
        return [
            "it has no ### Screens — review opens every screenshot in .screens/manifest.json "
            "and writes the section"
        ]
    problems = []
    taken, by, standard = dig(screens, "taken"), dig(screens, "by"), dig(screens, "standard")
    if not truthy(taken):
        problems.append(
            'the first line of its ### Screens is not "Taken at: <sha>. Standard: <path>. '
            'Looked at by: <which agent session>, from screenshots."'
        )
    else:
        # Constraint two of the intent: whoever looked is named as an agent, never read as a person.
        if not re.search(r"\bagent\b", js(by), re.ASCII | re.IGNORECASE):
            problems.append(
                f'its ### Screens says "Looked at by: {js(by)}", which does not say it was an agent'
            )
        if standard != standard_path:
            problems.append(
                f"its ### Screens names the standard {js(standard)}, not {standard_path}"
            )
    if not dig(screens, "shots"):
        problems.append(
            "its ### Screens lists no screenshot — one line per image, "
            '"- <path>.png — <W>×<H> — <address> — <result>"'
        )
    return problems


def screens_needs(unit, probe, last, said):
    """asked by `ship_needs` only once every earlier check has passed. With no
    standard, or one with no globs, it asks nothing at all; for a unit that changes no screen it
    asks one `git diff`. `said["screens"]` sends `nextStep` to another review round — except when
    git could not say which files changed, which another round would not cure."""
    found = ui_changed(unit, probe, said.get("head"))
    if "error" in found:
        return [found["error"]]
    changed, standard = found["changed"], found["standard"]
    if not changed:
        return []
    why = f"this unit changes {', '.join(changed)}, which {standard['path']} counts as screens"
    need = [
        f"review round {last['n']} passed, but {p} — {why}"
        for p in screens_problems(last["screens"], standard["path"])
    ]
    if not need:
        taken = last["screens"]["taken"]
        if probe.git("merge-base", "--is-ancestor", taken, last["reviewed"])["code"] != 0:
            need.append(
                f"the screenshots of review round {last['n']} were taken at {taken}, which is "
                f"not an ancestor of the reviewed commit {last['reviewed']} — take them again "
                "on the branch, and review them"
            )
        else:
            after = probe.git("diff", "--name-only", f"{taken}..{last['reviewed']}")
            if after["code"] != 0:
                need.append(f"git could not diff {taken}..{last['reviewed']}: {trim(after['err'])}")
            else:
                moved = ui_files(_lines(after["out"]), standard["globs"])
                if moved:
                    need.append(
                        f"{', '.join(moved)} changed after the screenshots of review round "
                        f"{last['n']} were taken at {taken} — take them again, and review them"
                    )
    if need:
        said["screens"] = True
    return need


def ui_changed(unit, probe, head):
    """The files among what `head` changed since it left the trunk that the standard counts as
    screens: `{ changed, standard }`, or `{ error }` when git could not say. `changed` is `[]`
    with no standard, or one with no globs, and then git is not asked."""
    ui = getattr(probe, "ui", None)
    standard = nullish(ui() if ui else None)
    if not standard or not standard["globs"]:
        return {"changed": [], "standard": standard}
    # Three dots: from the merge-base, in one command. The gate does not fetch.
    diff = probe.git("diff", "--name-only", f"origin/main...{js(head)}")
    if diff["code"] != 0:
        local = probe.git("diff", "--name-only", f"main...{js(head)}")
        if local["code"] != 0:
            return {
                "error": f"cannot tell whether {unit['name']} changes a screen: git could not "
                f"diff origin/main...{js(head)} ({trim(diff['err'])}) nor main...{js(head)} "
                f"({trim(local['err'])}) — the gate does not fetch"
            }
        diff = local
    own = f".cos/{unit['name']}/"
    return {"changed": ui_files(_lines(diff["out"], own), standard["globs"]), "standard": standard}


def screens_answer(unit, probe):
    """. Whether the app should take a UI unit's screenshots again before `review`:
    the screens the branch changes, what `.screens/manifest.json` says, and whether its `head` is
    still an ancestor of `HEAD`. Reads only; no gate is opened or closed by it."""
    found = ui_changed(unit, probe, "HEAD")
    error, changed = found.get("error"), found.get("changed")
    manifest_of = getattr(probe, "manifest", None)
    read = nullish(manifest_of() if manifest_of else None)
    manifest = (
        {
            "head": nullish(read.get("head")),
            "dirty": nullish(read.get("dirty")),
            "addresses": nullish(read.get("addresses")),
            "hits": read["hits"] if isinstance(read.get("hits"), list) else [],
        }
        if isinstance(read, dict)
        else None
    )
    # A head that is no commit name is not handed to git as an argument.
    head = manifest["head"] if manifest else None
    named = isinstance(head, str) and re.fullmatch(r"[0-9a-f]{7,40}", head) is not None
    # Exit non-zero also when the commit is gone from the object store: rewritten all the same.
    rewritten = named and probe.git("merge-base", "--is-ancestor", head, "HEAD")["code"] != 0
    addresses = manifest["addresses"] if manifest else None
    addressed = (
        isinstance(addresses, list)
        and len(addresses) > 0
        and all(isinstance(a, str) for a in addresses)
    )
    if error is not None:
        why = error
    elif not changed:
        why = "this unit changes no file the UI standard counts as a screen"
    elif not manifest:
        why = "there is no readable .screens/manifest.json in this checkout"
    elif not addressed:
        why = "the manifest lists no addresses"
    elif manifest["dirty"] is not False:
        why = "the manifest was taken on a tree with uncommitted changes"
    elif not named:
        why = "the manifest names no commit"
    elif not rewritten:
        why = f"the manifest's head {js(head)} is still an ancestor of HEAD"
    else:
        why = ""
    return {
        "unit": unit["name"],
        "ui": nullish(changed, []),
        "manifest": manifest,
        "rewritten": rewritten,
        "retake": why == "",
        "why": why,
    }


# --- what the gate asks git and gh -------------------------------------------------------


def review_needs(unit, probe, limit, said=None):
    """`review` may begin only on an open pull request whose required checks are green (
    spec, Answers, Câu 2). Nothing green to read is not read as green.

    `said["ci"]` records what CI said, once it was asked: `red`, `unfixable`, `pending`, `none`,
    `unreadable` or `green`."""
    said = {} if said is None else said
    need = []
    pr = pr_of(unit)
    if not pr:
        need.append("no pull request is recorded — the pr stage opens one")
    if out_of_rounds(unit, limit):
        need.append(needs_a_person(rounds_used(unit), review_limit(unit, limit)))
    if need or not pr:
        return need
    if not probe:
        return [NO_REPO]
    return ci_needs(probe, pr, said)


def ci_needs(probe, pr, said):
    """The required checks of `pr`, read once: `[]` when green, else why not, with `said["ci"]`
    set. Shared by `review` and, after a clean rebase, `ship`."""
    r = probe.gh("pr", "checks", str(pr["number"]), "--required", "--json", "name,bucket")
    # `gh pr checks` exits non-zero when a check failed or is pending, and still prints the
    # JSON. So the output is read first and the exit code only when there is none.
    checks = parse_json(r["out"])
    if not isinstance(checks, list):
        said["ci"] = "unreadable"
        return [f"cannot read the required checks of #{pr['number']}: {_told(r)}"]
    if not checks:
        said["ci"] = "none"
        return [f"#{pr['number']} reports no required checks — nothing green to read is not green"]
    red = [dig(c, "name") for c in checks if dig(c, "bucket") in ("fail", "cancel")]
    # Before the checks still running: one red check no rerun can fix settles it.
    if any(dig(c, "bucket") in ("fail", "cancel") for c in checks):
        return red_needs(probe, pr, red, said)
    waiting = [dig(c, "name") for c in checks if dig(c, "bucket") not in ("pass", "skipping")]
    if waiting:
        said["ci"] = "pending"
        return [
            f"CI has not finished on #{pr['number']}: {_join(waiting, ', ')} — wait, then ask again"
        ]
    said["ci"] = "green"
    return []


def red_needs(probe, pr, red, said):
    """a red check is impl's to fix, unless it is the harness's branch-name check and
    `check-branch` refuses the head GitHub reports. That one stops for a person, `said["ci"]`
    `unfixable`. The head is asked for here and only here."""
    said["ci"] = "red"
    number, names = pr["number"], _join(red, ", ")
    line = f"CI is red on #{number}: {names} — back to impl: fix on the branch and push"
    view = probe.gh("pr", "view", str(number), "--json", "headRefName")
    head = dig(parse_json(view["out"]), "headRefName")
    if not isinstance(head, str) or not head:
        return [
            f"{line} — cannot read the branch of #{number} ({_told(view)}), so whether its name "
            "is why cannot be told from here"
        ]
    workflows = getattr(probe, "workflows", None)
    named = set(branch_checks(workflows() if workflows else []))
    stuck = [name for name in red if name in named]
    problem = branch_problem(head)
    if stuck and problem:
        said["ci"] = "unfixable"
        return [
            f"needs a person — CI is red on #{number}: {names} — {_join(stuck, ', ')} checks "
            f"the branch name, and no rerun or impl can fix it: {not_a_work_branch(head, problem)}"
        ]
    if stuck:
        return [
            f'{line} — {_join(stuck, ", ")} checks the branch name, yet "{head}" passes '
            "check-branch here, so why it failed cannot be told from here"
        ]
    if problem:
        return [
            f"{line} — {not_a_work_branch(head, problem)}, but no red check runs coscc.loop "
            "check-branch in .github/workflows/, so whether that is why cannot be told from here"
        ]
    return [line]


def pr_view(probe, pr):
    """The pull request as GitHub reports it, from one `gh pr view`: `{ state, head, merged,
    title }`, `merged` being `{ commit, at }` on a `MERGED` one and `None` otherwise, `title`
    `None` when gh gave none, or `{ error }`."""
    view = probe.gh(
        "pr", "view", str(pr["number"]), "--json", "state,headRefOid,mergeCommit,mergedAt,title"
    )
    info = parse_json(view["out"])
    head = dig(info, "headRefOid")
    if not isinstance(head, str):
        return {"error": f"cannot read the head of #{pr['number']}: {_told(view)}"}
    state = dig(info, "state")
    merged = (
        {
            "commit": nullish(dig(info, "mergeCommit", "oid")),
            "at": nullish(dig(info, "mergedAt")),
        }
        if state == "MERGED"
        else None
    )
    title = dig(info, "title")
    return {
        "state": state,
        "head": head,
        "merged": merged,
        "title": title if isinstance(title, str) else None,
    }


def pr_head(probe, pr, view=None):
    """The pull request's head on GitHub, as `{ head }`, or `{ error }` saying why not. Shared by
    `ship_needs` and `nextStep`. `view` is one the caller already read."""
    view = pr_view(probe, pr) if view is None else view
    if "error" in view:
        return {"error": view["error"]}
    if view["state"] != "OPEN":
        return {
            "error": f"#{pr['number']} is {js(view['state'])}, not open — there is nothing to merge"
        }
    if probe.git("cat-file", "-e", f"{view['head']}^{{commit}}")["code"] != 0:
        return {
            "error": f"the head of #{pr['number']}, {view['head']}, is not in this repository — "
            "someone pushed from elsewhere: fetch, then ask again"
        }
    return {"head": view["head"]}


def ship_needs(unit, probe, said=None):  # noqa: C901, PLR0915 - `shipNeeds` kept whole
    """`ship` merges. It may do so only after a pass that left nothing open, whose history is
    intact, and after which no code reached the branch.

    `said["head"]` is set to the pull request head the gate checked, so the merge can be pinned
    to it. a ref rewritten after the pass no longer closes the gate by itself when the
    unit's patch there is the reviewed one (`rebase_clean`): it sets `said["rebased"]` then."""
    said = {} if said is None else said
    rounds = review_of(unit)
    if not rounds:
        return ["review.md has no ## Round — nothing says what was reviewed or found"]
    last = rounds[-1]
    need = []
    if last["verdict"] != "pass":
        verdict = js(nullish(last["verdict"], "unreadable"))
        need.append(
            f'review round {last["n"]} has verdict "{verdict}", not pass — '
            "its first line is Reviewed: <sha>. Verdict: pass."
        )
    # `[answered]` closes a finding only when `review.md ## Answers` holds a
    # block for that id. `[needs-person]` and `[claim-rejected]` never close one.
    answered = person_answers(unit)
    # a finding that does not block is left open on purpose; `ship.md` lists it.
    # A lowered one still blocks, but is named only on its own line below.
    rule = severity_rule(unit)
    passes = {f["id"] for f in [*rule["nonBlocking"], *rule["demoted"]]}
    open_ = [
        f
        for f in last["findings"]
        if f["label"] != "fixed"
        and not (f["label"] == "answered" and f["id"] in answered)
        and f["id"] not in passes
    ]
    for d in rule["demoted"]:
        need.append(
            f"{d['id']} is low in review round {last['n']}, but review round {d['round']} rated "
            f"it {d['severity']} — lowering a severity is not a fix: fix it on the branch, or "
            "keep it open"
        )
    if open_:

        def named(f):
            if f["label"] == "answered":
                return f"{f['id']} [answered, no answer in review.md]"
            return f"{f['id']} [{f['label']}]"

        need.append(
            f"review round {last['n']} still has findings not fixed: "
            f"{', '.join(named(f) for f in open_)}"
        )
    # `parseReview` worked out what the last round dropped.
    if last.get("dropped"):
        need.append(
            f"review round {last['n']} drops findings an earlier round raised: "
            f"{', '.join(last['dropped'])} — carry each one forward, fixed or open"
        )
    numbers = [r["n"] for r in rounds]
    if any(n != i + 1 for i, n in enumerate(numbers)):
        need.append(
            f"review rounds are numbered {', '.join(str(n) for n in numbers)}, not 1 to "
            f"{len(rounds)} — a round was removed or renumbered"
        )
    if not last["reviewed"]:
        need.append(f"review round {last['n']} names no reviewed commit — Reviewed: <sha>")
    if need:
        return need

    if not probe:
        return [NO_REPO]
    if not unit.get("branch"):
        return ["the unit has no branch — intent.md must declare a Type"]
    pr = pr_of(unit)
    if not pr:
        return ["no pull request is recorded — nothing says what ship would merge"]
    # the pull request is read before the branch is looked for — after
    # `--delete-branch` there may be no ref left, and a merged one needs none. Any state but
    # these two closes the gate as it always did.
    view = pr_view(probe, pr)
    if "error" in view:
        return [view["error"]]
    if view["state"] == "MERGED":
        return merged_needs(probe, pr, view, said)
    if view["state"] != "OPEN":
        return [pr_head(probe, pr, view)["error"]]
    refs = [
        ref
        for ref in (f"refs/heads/{unit['branch']}", f"refs/remotes/origin/{unit['branch']}")
        if probe.git("rev-parse", "--verify", "--quiet", ref)["code"] == 0
    ]
    if not refs:
        return [f"no branch {unit['branch']} here, local or origin — the gate does not fetch"]
    if probe.git("cat-file", "-e", f"{last['reviewed']}^{{commit}}")["code"] != 0:
        return [f"the reviewed commit {last['reviewed']} is not in this repository"]

    # What the merge lands is the pull request's head on GitHub, not a ref here: a push from
    # another checkout moves it and leaves `origin/<branch>` stale, since the gate does not fetch
    # . So the head is asked for and checked like a ref.
    read = pr_head(probe, pr, view)
    if "error" in read:
        return [read["error"]]
    refs.append(read["head"])
    said["head"] = read["head"]
    # the reviewed commit's patch, taken only once a ref is found rewritten, and then
    # once for all of them — a unit never rebased asks git nothing more.
    reviewed_patch = None
    rebased = False
    for ref in refs:
        name = f"the head of #{pr['number']} ({ref})" if ref == said["head"] else ref
        since = changed_since(probe, unit, last["reviewed"], ref)
        if "error" in since:
            need.append(since["error"])
            continue
        # `said["moved"]`: what the pass reviewed is no longer what would merge. The cure for
        # both is another round, which is what `nextStep` offers when it sees this. A rewrite
        # that left the unit's patch as it was is not one.
        if since["rewritten"]:
            if reviewed_patch is None:
                reviewed_patch = unit_patch(probe, unit, last["reviewed"])
            compared = rebase_clean(probe, unit, reviewed_patch, ref)
            # Only the pull request's head is what merges: a local ref rewritten clean while the
            # head is still the reviewed commit leaves the gate as it was, and names no rebase.
            if compared.get("clean"):
                if ref == said["head"]:
                    rebased = True
                continue
            said["moved"] = True
            need.append(
                f"the reviewed commit {last['reviewed']} is not on {name} — the branch was "
                "rewritten after the pass (a rebase does this): review its new head in another "
                "round; a round that passes does not count toward the limit — "
                f"{rebase_why(compared)}"
            )
            continue
        if since["files"]:
            said["moved"] = True
            need.append(
                f"{name} changed after the reviewed commit {last['reviewed']}: "
                f"{', '.join(since['files'])} — review again"
            )
    if need:
        return need
    # a clean rebase stands in for the round only once CI is green on it — CI is what
    # is left to catch a conflict with no conflicting line. Before `behind`: a head just rebased
    # is up to date, and "wait for CI" is then the true reason.
    if rebased:
        said["rebased"] = {"reviewed": last["reviewed"], "head": said["head"]}
        ci = ci_needs(probe, pr, said)
        if ci:
            return ci
    # after `moved`, so a head already rebased goes to review rather than here. A pull
    # request behind `origin/main` is one GitHub refuses to merge; the gate says so first, off
    # the ref as it is — it does not fetch, the autopilot does. No `origin/main` here, no
    # opinion: GitHub still decides.
    trunk = "refs/remotes/origin/main"
    if (
        probe.git("rev-parse", "--verify", "--quiet", trunk)["code"] == 0
        and probe.git("merge-base", "--is-ancestor", trunk, said["head"])["code"] != 0
    ):
        count = probe.git("rev-list", "--count", f"{said['head']}..{trunk}")
        k = _count(count["out"]) if count["code"] == 0 else None
        if k is not None:
            said["behind"] = k
        if k is not None:
            by = f"{'NaN' if k != k else k} commit(s)"
        else:
            told = trim(count["err"] or count["out"]) or f"exit {js(count['code'])}"
            by = f"an unknown number of commits (git said: {told})"
        return [
            f"#{pr['number']} is {by} behind origin/main — integrate, then review again only if "
            "the rebase changes the unit's patch: one that leaves it unchanged opens ship once "
            "CI is green; a round that passes does not count toward the limit"
        ]
    screens = screens_needs(unit, probe, last, said)
    if screens:
        return screens
    # , last: when this closes the gate it is the only reason, so `nextStep` may offer
    # `ship`, which the app starts by putting the unit's title up. `said["title"]` says so.
    mine = pr_title(unit["name"], unit.get("type"))
    if view["title"] is not None and trim(view["title"]) == mine:
        return []
    said["title"] = "differs"
    theirs = "no title gh could read" if view["title"] is None else f'the title "{view["title"]}"'
    return [
        f'#{pr["number"]} carries {theirs}, not the unit\'s "{mine}" — start ship from the board, '
        "which puts it onto the pull request first"
    ]


def _count(text):
    """`Number(text.trim())` of what `git rev-list --count` printed: a whole number."""
    s = trim(text)
    if s == "":
        return 0
    return int(s) if re.fullmatch(r"[0-9]+", s) else float("nan")


def merged_needs(probe, pr, view, said):
    """a pull request already merged — by a `ship` whose `--delete-branch` then exited 1,
    or by hand — leaves `ship` only its record to write. The gate asks that the merge commit is
    here and on `origin/main`, and nothing else. It sets `said["merged"]` and no `said["head"]`,
    so there is nothing to pin."""
    commit, at = view["merged"]["commit"], view["merged"]["at"]
    again = "— fetch, then ask again"
    if (
        not isinstance(commit, str)
        or not re.fullmatch(r"[0-9a-f]{40}", commit)
        or not isinstance(at, str)
        or not at
    ):
        return [
            f"cannot read the merge commit of #{pr['number']}: gh gave mergeCommit "
            f"{js(nullish(commit, 'none'))} and mergedAt {js(at) if truthy(at) else 'none'} {again}"
        ]
    if probe.git("cat-file", "-e", f"{commit}^{{commit}}")["code"] != 0:
        return [f"the merge commit {commit} of #{pr['number']} is not in this repository {again}"]
    if probe.git("merge-base", "--is-ancestor", commit, "refs/remotes/origin/main")["code"] != 0:
        return [f"the merge commit {commit} of #{pr['number']} is not on origin/main here {again}"]
    said["merged"] = {"number": pr["number"], "commit": commit, "at": at, "head": view["head"]}
    return []


def merged_line(merged):
    """What the `ship` gate's open line and `next`'s action both say of a merged pull request
    , so the two cannot word it differently."""
    return (
        f"#{js(merged['number'])} was merged as {js(merged['commit'])} at {js(merged['at'])}: "
        "record it in ship.md; do not merge"
    )


def changed_since(probe, unit, reviewed, ref):
    """What reached `ref` after `reviewed`, outside the unit's own `.cos/` files:
    `{ rewritten: True }` when `reviewed` is not on it at all, `{ files }` otherwise, or
    `{ error }`. Whether a rewrite changed the unit's patch is `rebase_clean`'s to say."""
    if probe.git("merge-base", "--is-ancestor", reviewed, ref)["code"] != 0:
        return {"rewritten": True, "files": []}
    diff = probe.git("diff", "--name-only", f"{reviewed}..{ref}")
    if diff["code"] != 0:
        return {"error": f"git could not diff {reviewed}..{ref}: {trim(diff['err'])}"}
    return {"rewritten": False, "files": _lines(diff["out"], f".cos/{unit['name']}/")}


INDEX_LINE = re.compile(r"index [0-9a-f]+\.\.[0-9a-f]+(?: [0-7]{6})?")
HUNK_HEAD = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", re.ASCII)


def normalize_patch(text):
    """a patch with what a rebase alone moves taken out — each `index <blob>..<blob>`
    line, and the numbers of each `@@ -a,b +c,d @@` — and every other byte kept."""
    kept = [line for line in text.split("\n") if not INDEX_LINE.fullmatch(line)]
    return "\n".join(HUNK_HEAD.sub("@@ @@", line) for line in kept)


def unit_patch(probe, unit, commit):
    """The unit's patch at `commit`, normalized: from its merge-base with the trunk, outside
    `.cos/<unit>/`, in a format no config of the checkout can change. `{ patch }` or `{ error }`
    — a git that fails is never an empty patch, since two empty patches match."""
    trunk = next(
        (
            ref
            for ref in ("refs/remotes/origin/main", "refs/heads/main")
            if probe.git("rev-parse", "--verify", "--quiet", ref)["code"] == 0
        ),
        None,
    )
    if not trunk:
        return {"error": "there is no origin/main and no main here to take the merge-base from"}
    base = probe.git("merge-base", commit, trunk)
    sha = trim(base["out"])
    if base["code"] != 0 or not re.fullmatch(r"[0-9a-f]{40}", sha):
        if base["code"] != 0:
            said = trim(base["err"] or base["out"]) or f"exit {js(base['code'])}"
            return {"error": f"git merge-base {commit} {trunk}: {said}"}
        return {"error": "git merge-base printed no commit"}
    diff = probe.git(
        "diff", "--no-color", "--no-ext-diff", "--no-textconv", "--no-renames", "--unified=3",
        "--binary", "--src-prefix=a/", "--dst-prefix=b/", sha, commit, "--", ":/",
        f":(top,exclude).cos/{unit['name']}/",
    )  # fmt: skip
    if diff["code"] != 0:
        said = trim(diff["err"] or diff["out"]) or f"exit {js(diff['code'])}"
        return {"error": f"git could not diff {sha}..{commit}: {said}"}
    return {"patch": normalize_patch(diff["out"])}


def patch_files(patch):
    """A patch cut into one block per file, keyed by its `b/` path. With renames off a header
    names one path twice, `a/P b/P`, quoted or not, so the second half is the path."""
    files = {}
    path = None
    # The patch's last newline ends its last line, so a file is the same block last or not.
    body = patch[:-1] if patch.endswith("\n") else patch
    for line in body.split("\n"):
        if line.startswith("diff --git "):
            rest = line[len("diff --git ") :]
            path = rest[(len(rest) + 1) // 2 :]
            path = re.sub(r'^"?b/', "", path)
            path = re.sub(r'"\Z', "", path)
            files[path] = ""
        if path is not None:
            files[path] = f"{files[path]}{line}\n"
    return files


def rebase_clean(probe, unit, reviewed, ref):
    """is `ref` a clean rebase of the reviewed commit? `reviewed` is that commit's
    `unit_patch`, computed once by the caller for every ref. `{ clean: True }`,
    `{ differs: [files] }` or `{ error }`; what cannot be compared is never clean."""
    if reviewed.get("error"):
        return {"error": reviewed["error"]}
    now = unit_patch(probe, unit, ref)
    if now.get("error"):
        return {"error": now["error"]}
    if now["patch"] == reviewed["patch"]:
        return {"clean": True}
    a = patch_files(reviewed["patch"])
    b = patch_files(now["patch"])
    every = dict.fromkeys([*a, *b])
    differs = sorted((p for p in every if a.get(p) != b.get(p)), key=_utf16)
    return {"differs": differs if differs else ["(the text before the first file)"]}


def rebase_why(compared):
    """Why a `rebase_clean` answer is not clean, in the words the `ship` gate and `next` both
    print."""
    if compared.get("differs"):
        return f"its patch differs from the reviewed one in {', '.join(compared['differs'])}"
    return f"its patch could not be compared with the reviewed one: {js(compared.get('error'))}"


# --- the next stage to run ---------------------------------------------------------------


def pass_left_closed(unit, probe, g):
    """the one place two questions are answered — does the last pass leave `ship`
    closed for the one reason another round cures, and was the round before it a pass on the same
    head? `g` is the `ship` gate's `evaluate`: `{ok, need, said}`. `None` when the first answer is
    no. Else `{ last, need, stop }`: `stop` is `None` while the one retry is still to come, or the
    sentence the unit stops on. No limit is read: one retry is a choice, not a setting."""
    rounds = review_of(unit)
    last = rounds[-1] if rounds else None
    if last is None or last["verdict"] != "pass":
        return None
    said = g["said"]

    def unpushed():
        head = said.get("head")
        return bool(
            head
            and head != last["reviewed"]
            and probe.git("merge-base", "--is-ancestor", head, last["reviewed"])["code"] == 0
        )

    if said.get("moved"):
        if not unpushed():
            return None
    elif not said.get("screens"):
        return None
    prev = rounds[-2] if len(rounds) > 1 else None
    if prev is None or prev["verdict"] != "pass" or not prev["reviewed"]:
        return {"last": last, "need": g["need"], "stop": None}

    def short(r):
        return r["reviewed"][:7]

    # Where git cannot compare the two rounds, the sentence does not say they share a head.
    def stop(why="", known=True):
        if known or prev["reviewed"] == last["reviewed"]:
            heads = f"both passed on {short(last)}"
        else:
            heads = f"passed on {short(prev)} and {short(last)}, not known to be one head,"
        return (
            f"needs a person — {why}review rounds {prev['n']} and {last['n']} {heads} and ship "
            f"is still closed: {'; '.join(g['need'])}"
        )

    # Another round cannot bring back a commit that is not here.
    if probe.git("cat-file", "-e", f"{prev['reviewed']}^{{commit}}")["code"] != 0:
        why = (
            f"the reviewed commit {prev['reviewed']} of review round {prev['n']} is not in "
            "this repository; "
        )
        return {"last": last, "need": g["need"], "stop": stop(why, False)}
    since = changed_since(probe, unit, prev["reviewed"], last["reviewed"])
    if "error" in since:
        return {"last": last, "need": g["need"], "stop": stop(f"{since['error']}; ", False)}
    if not since["rewritten"] and not since["files"]:
        return {"last": last, "need": g["need"], "stop": stop()}
    return {"last": last, "need": g["need"], "stop": None}


# --- the checks a workflow names ---------------------------------------------------------

CALLS = re.compile(f"\\bcoscc\\.loop{S}+check-branch\\b", re.ASCII)
JOBS_LINE = re.compile(f"jobs:{S}*(#{DOT}*)?")
DOUBLE_QUOTED = re.compile(f'"([^"]*)"{S}*(#{DOT}*)?')
SINGLE_QUOTED = re.compile(f"'([^']*)'{S}*(#{DOT}*)?")
TRAILING_COMMENT = re.compile(f"{S}+#{DOT}*\\Z")
JOB_KEY = re.compile("(\"[^\"]+\"|'[^']+'|" + NOT_S + f"[^:]*?):{S}*(#{DOT}*)?")
NAME_LINE = re.compile(f"name:{S}*({DOT}*)")
RUN_LINE = re.compile(f"(-{S}+)?run:{S}*({DOT}*)")


def _unquote(v):
    m = DOUBLE_QUOTED.fullmatch(v) or SINGLE_QUOTED.fullmatch(v)
    return m[1] if m else trim(TRAILING_COMMENT.sub("", v, count=1))


def _indent(line):
    return len(line) - len(trim_start(line))


def branch_checks(files):
    """the check names of the jobs whose `run:` step calls `coscc.loop check-branch`
    (`uv run python -m coscc.loop check-branch`),
    read from each workflow's text, `{ path, text }`. Not a YAML parser, and not meant to be one:
    a job it cannot read is not found, and a red check not found goes back to impl as it did
    before. The name is the job's own `name:`, else its key; a `name:` built from
    `${{ }}` is not known here, so that job is not found either. A line that is only a comment
    counts for nothing."""
    found = []

    def close(job):
        name = job["name"] if job else None
        if job and job["runs"] and isinstance(name, str) and name and name not in found:
            found.append(name)

    for file in files:
        lines = re.split(r"\r?\n", file["text"])
        start = next((i for i, line in enumerate(lines) if JOBS_LINE.fullmatch(line)), -1)
        if start == -1:
            continue
        job_indent = None
        job = None
        block = None
        for line in lines[start + 1 :]:
            trimmed = trim(line)
            if not trimmed or trimmed.startswith("#"):
                continue
            indent = _indent(line)
            if indent == 0:
                break
            # The lines of a `run: |` block, or of a `run:` whose value starts on the next line.
            if block is not None and indent > block:
                if job is not None and CALLS.search(trimmed):
                    job["runs"] = True
                continue
            block = None
            if job_indent is None:
                job_indent = indent
            if indent < job_indent:
                break
            if indent == job_indent:
                close(job)
                key = JOB_KEY.fullmatch(trimmed)
                job = {"name": _unquote(key[1]), "child": None, "runs": False} if key else None
                continue
            if not job:
                continue
            if job["child"] is None:
                job["child"] = indent
            name = NAME_LINE.fullmatch(trimmed) if indent == job["child"] else None
            if name:
                value = name[1]
                is_block = "${{" in value or re.match(r"[|>]", value)
                job["name"] = None if is_block else _unquote(value)
                continue
            run = RUN_LINE.fullmatch(trimmed)
            if not run:
                continue
            value = run[2]
            if not value or re.match(r"[|>]", value):
                block = indent + len(run[1] or "")
            elif CALLS.search(value):
                job["runs"] = True
        close(job)
    return found


# --- the command -------------------------------------------------------------------------


def run(args, out, err):
    """`cmdScreens`: whether the app should take a UI unit's screenshots again before `review`,
    as one line of JSON. Exit 0 whatever it says; exit 2 is misuse."""
    unit_name = args.rest[0] if args.rest else None
    if not unit_name:
        err("usage: python -m coscc.loop screens <NNNN_slug> [--repo <dir>]")
        return 2
    if not UNIT_RE.fullmatch(unit_name):
        err(f'Invalid unit name "{unit_name}": expected NNNN_slug.')
        return 2
    folder = os.path.join(args.cos_dir, unit_name)
    if not os.path.exists(folder):
        err(f"No such work unit: {unit_name}")
        return 2
    if not args.repo_dir:
        err("screens needs the checkout its screenshots are in — pass --repo <dir>")
        return 2
    unit = read_unit(folder, unit_name, args.state)
    out(stringify(screens_answer(unit, make_probe(args.repo_dir))))
    return 0
