"""The rules that decide a unit: `decide`, the gates, `next`, and the commands that print them.

`decide_files`, `evaluate`/`gate_answer`/`gate_reasons`, `step_of`/`next_answer`/
`next_reasons`, and `cmd_status`, `cmd_next`, `cmd_gate`. What needs git or the pull request is asked of a probe
(`coscc.loop.repo_rules`); here every question is answered from the unit's files and the
snapshot alone. Every reason code goes through `code`, so none leaves `REASONS`.
"""

from __future__ import annotations

import os
import re
from typing import Any

from coscc.loop import (
    DEFAULT,
    REVIEW_ROUNDS,
    SPIKE_ROUNDS,
    UNDEFINED,
    code,
    dig,
    js,
    nullish,
    proc_of,
    stringify,
    truthy,
)
from coscc.loop.model import (
    agent_skip,
    ended_of,
    every_open_claimed,
    incomplete_draft,
    last_round,
    link_needs,
    links_of,
    missing,
    needs_a_person,
    needs_person_claims,
    out_of_rounds,
    person_findings,
    passed_over,
    pr_of,
    present,
    proc,
    read_all,
    read_unit,
    review_limit,
    rounds_used,
    route,
    settled,
    walk,
    skipped_by,
    spike_findings,
    spike_needs,
    status_of,
    wait_on_dependencies,
)
from coscc.loop.repo_rules import (
    changed_since,
    make_probe,
    merged_line,
    pass_left_closed,
    pr_head,
    rebase_clean,
    rebase_why,
    review_needs,
    ship_needs,
    unit_patch,
)

A = re.ASCII

# --- one action per unit -----------------------------------------------------------------


def decide(unit, limit=REVIEW_ROUNDS):
    """`nextAction`, plus `why`: which rule answered. `impl` waits on dependencies."""
    return wait_on_dependencies(unit, decide_files(unit, limit))


def _hint_word(s):
    return s["hint"].split(" ")[0]


def decide_files(unit, limit):  # noqa: C901 - a port of `decideFiles` kept whole
    limit = review_limit(unit, limit)
    if ended_of(unit) == "finished":
        return {"blocked": False, "action": "finished", "stage": "", "why": "finished"}

    hold = nullish(unit.get("hold"))
    if hold:
        how = " — resume it from the board" if hold["state"] == "paused" else ""
        return {
            "blocked": hold["state"] == "paused",
            "action": f"{hold['state']} — {js(hold['reason'])} ({js(hold['by'])}, {js(hold['date'])}){how}",
            "stage": "",
            "why": code(hold.get("code") or hold["state"]),
        }

    p = proc(unit)
    for name in route(unit):
        s = p.by_name[name]
        status = status_of(unit, s["file"])
        if name == p.spike and status == "accepted":
            found = spike_findings(unit)
            if found["fails"] and found["round"] >= SPIKE_ROUNDS:
                return {
                    "blocked": True,
                    "action": (
                        f"needs a person — spike round {js(found['round'])} of {SPIKE_ROUNDS} "
                        f"found {', '.join(found['fails'])} does not hold"
                    ),
                    "stage": "",
                    "why": code("needs-person"),
                }
            if found["fails"]:
                return {
                    "blocked": True,
                    "action": (
                        f"{_hint_word(p.by_name[p.measured or ''])} again — {s['file']} measured "
                        f"{', '.join(found['fails'])} does not hold"
                    ),
                    "stage": p.measured,
                    "why": code("spike-fails"),
                }
            if found["missing"]:
                return {
                    "blocked": True,
                    "action": (
                        f"{_hint_word(s)} again — {s['file']} does not measure "
                        f"{', '.join(found['missing'])}"
                    ),
                    "stage": name,
                    "why": code("spike-missing"),
                }
        if status is None:
            if present(unit, s["file"]):
                return {
                    "blocked": True,
                    "action": f"fix {s['file']} — it has no status: no record was handed back for it",
                    "stage": "",
                    "why": code("unreadable"),
                }
            if s.get("optional"):
                continue
            return {
                "blocked": True,
                "action": s["hint"],
                "stage": s["name"],
                "why": code("missing"),
            }
        if status == "rejected":
            return {
                "blocked": False,
                "action": f"closed — {s['name']} rejected",
                "stage": "",
                "why": code("rejected"),
            }
        if agent_skip(unit, s["file"]):
            return {
                "blocked": True,
                "action": (
                    f"{skipped_by(unit, s['file'])} — a person records the skip (coscc skip), "
                    f"or runs {_hint_word(s)}"
                ),
                "stage": "",
                "why": code("agent-cannot-skip"),
            }
        stale = dig(unit, "artifacts", s["file"], "stale")
        if truthy(stale):
            return {
                "blocked": True,
                "action": (
                    f"{s['file']} is stale — {js(stale['stage'])} was rerun on "
                    f"{js(stale['date'])}: {_hint_word(s)} again"
                ),
                "stage": s["name"],
                "why": code("stale"),
            }
        if name == p.review and incomplete_draft(unit):
            used = rounds_used(unit)
            if used >= limit:
                return {
                    "blocked": True,
                    "action": needs_a_person(used, limit),
                    "stage": "",
                    "why": code("needs-person"),
                }
            return {
                "blocked": True,
                "action": (
                    f"review round {js(last_round(unit)['n'])} is incomplete — "
                    f"{_hint_word(s)} again"
                ),
                "stage": name,
                "why": code("review-incomplete"),
            }
        if status == "draft" and unit["artifacts"][s["file"]].get("leftLane"):
            fast = p.fast or {"back": [], "passed": [""]}
            return {
                "blocked": True,
                "action": (
                    f"{_hint_word(s)} — {name}'s {', '.join(fast['back'])} has the fix leaving "
                    f"the fast lane: {name} runs again on {p.file(fast['passed'][-1])}"
                ),
                "stage": s["name"],
                "why": code("missing"),
            }
        if status == "draft":
            refused = (
                nullish(dig(unit, "artifacts", s["file"], "merge", "round"))
                if name == p.merge
                else None
            )
            # A round and no `Refused:` line: ship asked GitHub to merge and has not written the
            # outcome yet, which is no refusal.
            if (
                refused is not None
                and last_round(unit)
                and nullish(dig(unit, "artifacts", s["file"], "merge", "refused")) is None
            ):
                number = nullish(dig(pr_of(unit), "number"))
                return {
                    "blocked": True,
                    "action": f"{name} is merging #{js(number) if number is not None else '?'} — wait",
                    "stage": "",
                    "why": code("ship-merging"),
                }
            if refused is not None and last_round(unit):
                return {
                    "blocked": True,
                    "action": (
                        f"{name} after review round {js(refused)} did not merge — "
                        "the next step says what runs now"
                    ),
                    "stage": "",
                    "why": code("ship-refused"),
                }
            questions = nullish(dig(unit, "artifacts", s["file"], "questions"), [])
            answered = (
                "answers" in p.info[name]["rerun"]
                and len(questions) > 0
                and all(q["answered"] for q in questions)
            )
            out = {
                "blocked": True,
                "action": f"finish and accept {s['file']}",
                "stage": "",
                "why": code("draft"),
            }
            if answered:
                out["rerun"] = s["name"]
            elif (
                p.info[name]["by"] == "session" and not questions and not needs_person_claims(unit)
            ):
                # An impl that stopped with its draft asking nothing and handing nothing to a
                # person has more to write; only the autopilot reads this, the board offers
                # `stage` alone. A draft plan never gets one: its open points are a person's.
                out["continue"] = s["name"]
            return out
        if status == "changes-requested":
            used = rounds_used(unit)
            if used >= limit:
                return {
                    "blocked": True,
                    "action": needs_a_person(used, limit),
                    "stage": "",
                    "why": code("needs-person"),
                }
            last = last_round(unit)
            if last and last.get("unfinished"):
                return {
                    "blocked": True,
                    "action": (
                        f"review round {js(last['n'])} left out findings an earlier round raised"
                        f" — write-review again ({used} of {limit} rounds used)"
                    ),
                    "stage": p.review,
                    "dropped": last["dropped"],
                    "why": code("review-incomplete"),
                }
            person = person_findings(unit)
            if person is not None:
                waiting = [p for p in person if not p["answered"]]
                if waiting:
                    told = "; ".join(f"{p['id']}: {p['reason']}" for p in waiting)
                    return {
                        "blocked": True,
                        "action": (
                            f"needs a person — {told} — answer each on the Questions tab "
                            "(POST /api/units/answer, review.md), then write-review"
                        ),
                        "stage": "",
                        "waiting": [p["id"] for p in waiting],
                        "why": code("awaits-person"),
                    }
                return {
                    "blocked": True,
                    "action": (
                        f"a person answered {', '.join(p['id'] for p in person)} in review.md"
                        " — write-review again"
                    ),
                    "stage": "",
                    "why": code("person-answered"),
                }
            return {
                "blocked": True,
                "action": (
                    f"fix the open findings of review round "
                    f"{js(nullish(last['n'] if last else None, used))} on the branch, then "
                    f"write-review again ({used} of {limit} rounds used)"
                ),
                "stage": "",
                "why": code("changes-requested"),
            }
    return {"blocked": False, "action": "finished", "stage": "", "why": code("finished")}


# --- does a stage have what it needs ----------------------------------------------------


def gate_answer(unit, stage, probe=None, limit=REVIEW_ROUNDS):
    """`checkGate`, plus `reasons`, the codes `gate --json` hands out."""
    r = evaluate(unit, stage, probe, limit)
    ok, need, said = r["ok"], r["need"], r["said"]
    p = proc(unit)
    last = last_round(unit)
    if ok and probe and stage == p.review and dig(last, "verdict") == "pass":
        ship = evaluate(unit, p.merge, probe, limit)
        stuck = pass_left_closed(unit, probe, ship)
        if stuck and not stuck["stop"]:
            return {
                "ok": ok,
                "need": need,
                "retry": {"n": stuck["last"]["n"], "reviewed": stuck["last"]["reviewed"],
                          "need": ship["need"]},
            }  # fmt: skip
    if ok and said.get("merged"):
        return {
            "ok": ok,
            "need": need,
            "merged": said["merged"],
            "reasons": [code("recording-ship")],
        }
    reasons = [] if ok else gate_reasons(unit, stage, need, said)
    if not ok or not said.get("head"):
        return {"ok": ok, "need": need, "reasons": reasons}
    if said.get("rebased"):
        return {
            "ok": ok, "need": need, "head": said["head"], "rebased": said["rebased"],
            "reasons": reasons,
        }  # fmt: skip
    return {"ok": ok, "need": need, "head": said["head"], "reasons": reasons}


def _before(unit, target):
    """The states the unit walks before `target`, in its process's order, the optional ones left out."""
    p = proc(unit)
    path = route(unit)
    return [
        p.by_name[n] for n in p.order[: p.at(target)] if n in path and not p.info[n]["optional"]
    ]


def _after_spike(unit, target):
    p = proc(unit)
    return bool(p.spike) and p.at(target) > p.at(p.spike)


def gate_reasons(unit, stage, need, said):
    """the codes of a closed gate, never none, the first the one fixed first."""
    p = proc(unit)
    if p.by_name.get(stage) is None:
        return [code("unreadable")]
    if unit.get("hold"):
        if unit["hold"].get("code"):
            return [code(unit["hold"]["code"])]
        return [code("dropped") if unit["hold"]["state"] == "dropped" else code("paused")]
    over = passed_over(unit, stage)
    codes = [code(over[0])] if over and over[0] else []
    for s in _before(unit, stage):
        status = status_of(unit, s["file"])
        if status is None:
            codes.append(code("missing"))
        elif status == "rejected":
            codes.extend([code("rejected"), code("closed")])
        elif agent_skip(unit, s["file"]):
            codes.append(code("agent-cannot-skip"))
        elif not settled(status):
            codes.append(code("draft"))
        elif truthy(unit["artifacts"][s["file"]].get("stale")):
            codes.append(code("stale"))
    if _after_spike(unit, stage) and spike_needs(unit):
        fails = spike_findings(unit)["fails"]
        codes.append(code("spike-fails") if fails else code("spike-missing"))
    ci = said.get("ci")
    if ci == "pending":
        codes.append(code("ci-pending"))
    if ci == "red":
        codes.append(code("ci-red"))
    if ci == "unfixable":
        codes.extend([code("ci-unfixable"), code("needs-person")])
    if stage in p.waits:
        kept = links_of(unit)
        if kept["waiting"]:
            codes.append(code("waiting-on"))
    if not codes and need:
        codes.append(code("gate-closed"))
    return list(dict.fromkeys(codes))


def evaluate(unit, stage, probe=None, limit=REVIEW_ROUNDS):
    """`{"ok", "need", "said"}`: `said` is what `review` and `ship` learned on the way."""
    p = proc(unit)
    if p.by_name.get(stage) is None:
        return {
            "ok": False,
            "need": [f'unknown stage "{js(stage)}" — use one of {", ".join(p.names)}'],
            "said": {},
        }

    hold = nullish(unit.get("hold"))
    if hold:
        return {
            "ok": False,
            "need": [
                f"the unit is {hold['state']}: {js(hold['reason'])} "
                f"({js(hold['by'])}, {js(hold['date'])})"
            ],
            "said": {},
        }

    need = []
    over = passed_over(unit, stage)
    if over:
        need.append(over[1])
    for s in _before(unit, stage):
        status = status_of(unit, s["file"])
        can_skip = "skipped" in s["statuses"]
        if status is None:
            need.append(
                f"{missing(unit, s['file'])} — write it, or a person records the skip (coscc skip)"
                if can_skip
                else missing(unit, s["file"])
            )
        elif agent_skip(unit, s["file"]):
            need.append(skipped_by(unit, s["file"]))
        elif not settled(status):
            need.append(
                f'{s["file"]} is "{js(status)}", not accepted{" or skipped" if can_skip else ""}'
            )
        elif truthy(unit["artifacts"][s["file"]].get("stale")):
            stale = unit["artifacts"][s["file"]]["stale"]
            need.append(
                f"{s['file']} is stale: {js(stale['stage'])} was rerun on {js(stale['date'])} "
                f"— run {s['name']} again first"
            )

    if _after_spike(unit, stage):
        need.extend(spike_needs(unit))
        if (
            p.at(stage) > p.at(p.rests)
            and p.spike in route(unit)
            and not dig(unit, "artifacts", p.file(p.rests), "restsOn")
        ):
            need.append(
                f"{p.file(p.rests)} rests on no U<n> — its record's rests_on names the spike items "
                "its steps rest on"
            )

    said: dict = {}
    if not need and stage == p.review:
        need.extend(review_needs(unit, probe, limit, said))
    if not need and stage == p.merge:
        need.extend(ship_needs(unit, probe, said))
    if stage in p.waits:
        need.extend(link_needs(unit))
    return {"ok": not need, "need": need, "said": said}


# --- the next stage to run --------------------------------------------------------------


def next_answer(unit, probe=None, limit=REVIEW_ROUNDS):
    """`nextStep`, plus `reasons`, the codes of what settled it."""
    seen: dict = {}
    answer = wait_on_dependencies(unit, step_of(unit, probe, limit, seen))
    return {**answer, "reasons": next_reasons(answer, seen)}


def next_reasons(answer, seen):
    if answer.get("why") == "dependency":
        return [code("dependency"), code("waiting-on")]
    if answer.get("why"):
        return [answer["why"]]
    codes = [seen["why"]] if seen.get("why") else []
    if seen.get("why") == "rejected":
        codes.append(code("closed"))
    ci = dig(seen, "said", "ci")
    if ci == "pending":
        codes.append(code("ci-pending"))
    if ci == "red":
        codes.append(code("ci-red"))
    if ci == "unfixable":
        codes.extend([code("ci-unfixable"), code("needs-person")])
    if seen.get("stop"):
        codes.append(code("needs-person"))
    if seen.get("recorded"):
        codes.append(code("recording-ship"))
    return list(dict.fromkeys(codes))


def _none(action):
    return {"blocked": True, "action": action, "stage": ""}


def step_of(unit, probe, limit, seen):  # noqa: C901, PLR0915 - a port of `stepOf`
    p = proc(unit)
    review, merge, fixer = p.review, p.merge, p.fixer
    base = decide(unit, limit)
    why = base["why"]
    next_ = {k: v for k, v in base.items() if k != "why"}
    seen["why"] = why

    def evaluate_(stage):
        g = evaluate(unit, stage, probe, limit)
        seen["said"] = g["said"]
        return g

    def on_review(prefix):
        g = evaluate_(review)
        if g["said"].get("ci") == "unfixable":
            return _none("; ".join(g["need"]))
        reasons = "; ".join([*prefix, *g["need"]])
        if g["ok"]:
            return {
                "blocked": True,
                "action": "; ".join([*prefix, f"CI is green: {p.info[str(review)]['hint']}"]),
                "stage": review,
            }
        if g["said"].get("ci") == "red":
            return {"blocked": True, "action": reasons, "stage": fixer}
        return _none(reasons)

    def again(g):
        stuck = pass_left_closed(unit, probe, g)
        if not (stuck and stuck["stop"]):
            return on_review(g["need"])
        seen["stop"] = True
        return _none(stuck["stop"])

    if why in ("paused", "dropped"):
        return next_
    if why == "dependency":
        return base

    due = why in ("missing", "stale")
    if due and next_["stage"] == review:
        return on_review([next_["action"]] if why == "stale" else [])

    def recorded(g):
        seen["recorded"] = True
        return {
            "blocked": True,
            "action": f"{merge} — {merged_line(g['said']['merged'])}",
            "stage": merge,
        }

    if due and next_["stage"] == merge:
        g = evaluate_(merge)
        if g["ok"] and g["said"].get("merged"):
            return recorded(g)
        if g["ok"]:
            return {
                **next_,
                "action": f"{next_['action']} — merge with --match-head-commit {g['said']['head']}",
            }
        if g["said"].get("ci") == "unfixable":
            return _none("; ".join(g["need"]))
        if g["said"].get("moved") or g["said"].get("screens"):
            return again(g)
        if g["said"].get("rebased") and g["said"].get("ci") == "red":
            return {"blocked": True, "action": "; ".join(g["need"]), "stage": fixer}
        if g["said"].get("title") == "differs":
            return {
                "blocked": True,
                "action": f"{next_['action']} — {'; '.join(g['need'])}",
                "stage": merge,
            }
        return _none("; ".join(g["need"]))

    if why == "ship-merging":
        # The merge GitHub made, once the commit is here, is recorded as after any `ship`. Until
        # then a wait: what the gate still needs (the merge commit not fetched) is no refusal.
        g = evaluate_(merge)
        if g["ok"] and g["said"].get("merged"):
            return recorded(g)
        seen["said"] = {}
        return next_

    if why == "ship-refused":
        ship = unit["artifacts"][p.file(merge)]["merge"]
        last = last_round(unit)
        g = evaluate_(merge)
        if g["ok"] and g["said"].get("merged"):
            return recorded(g)
        if g["said"].get("ci") == "unfixable":
            return _none("; ".join(g["need"]))
        if g["said"].get("moved") or g["said"].get("screens"):
            return again(g)
        if g["said"].get("rebased") and g["said"].get("ci") == "red":
            return {"blocked": True, "action": "; ".join(g["need"]), "stage": fixer}
        if g["said"].get("title") == "differs":
            return {"blocked": True, "action": f"{merge} — {'; '.join(g['need'])}", "stage": merge}
        if not g["ok"]:
            return _none("; ".join(g["need"]))
        cured = truthy(g["said"].get("rebased")) and re.search(
            r"not up to date", nullish(ship["refused"], ""), A | re.I
        )
        if ship["round"] < last["n"] or cured:
            return {
                "blocked": True,
                "action": f"{merge} — merge with --match-head-commit {g['said']['head']}",
                "stage": merge,
            }
        return _none(
            f"{merge} was refused: "
            f"{nullish(ship['refused'], f'{p.file(merge)} names no refusal')}"
            f" — finish and accept {p.file(merge)}"
        )

    if why == "awaits-person":
        return next_
    if why == "person-answered":
        return on_review([next_["action"]])
    if why == "review-incomplete":
        out = on_review([next_["action"]])
        return {**out, "dropped": next_["dropped"]} if truthy(next_.get("dropped")) else out

    if why == "changes-requested":
        if every_open_claimed(unit):
            return on_review(
                [
                    f"every open finding of review round {js(last_round(unit)['n'])} is claimed "
                    "as needing a person — review confirms or rejects each"
                ]
            )
        if not probe:
            return _none(f"{next_['action']} — no repository given to tell which: pass --repo")
        pr = pr_of(unit)
        if not pr:
            return _none(f"{next_['action']} — no pull request is recorded to read the branch from")
        last = last_round(unit)
        if not (last and last["reviewed"]):
            return _none(
                f"{next_['action']} — review round {js(last['n'] if last else '?')} names no "
                "reviewed commit, so a fix cannot be told from none"
            )
        read = pr_head(probe, pr)
        if read.get("error"):
            return _none(f"{next_['action']} — {read['error']}")
        if probe.git("cat-file", "-e", f"{last['reviewed']}^{{commit}}")["code"] != 0:
            return _none(
                f"{next_['action']} — the reviewed commit {last['reviewed']} is not in this "
                "repository: fetch, then ask again"
            )
        since = changed_since(probe, unit, last["reviewed"], read["head"])
        if since.get("error"):
            return _none(f"{next_['action']} — {since['error']}")
        if since.get("rewritten"):
            compared = rebase_clean(
                probe, unit, unit_patch(probe, unit, last["reviewed"]), read["head"]
            )
            if compared.get("clean"):
                return {
                    "blocked": True,
                    "action": (
                        f"{next_['action']} — {last['reviewed'][:7]} was rebased to "
                        f"{read['head'][:7]} and the unit's patch is unchanged: the open "
                        f"findings of review round {js(last['n'])} are still to be fixed on the "
                        "branch, then push"
                    ),
                    "stage": fixer,
                }
            return on_review(
                [
                    f"#{js(pr['number'])} was rewritten past {last['reviewed'][:7]} — "
                    f"{rebase_why(compared)}"
                ]
            )
        if not since["files"]:
            return {
                "blocked": True,
                "action": (
                    f"{next_['action']} — nothing outside .cos/{unit['name']}/ has reached "
                    f"#{js(pr['number'])} since {last['reviewed'][:7]}: {fixer}, then push"
                ),
                "stage": fixer,
            }
        return on_review([f"#{js(pr['number'])} moved past {last['reviewed'][:7]}"])

    return next_


# --- commands ---------------------------------------------------------------------------

DASH = "—"
CODE = {
    "draft": "d",
    "accepted": "A",
    "rejected": "x",
    "skipped": "s",
    "changes-requested": "c",
}
# `CODE[status]` on a JavaScript object: `constructor` is on every one, and not nullish.
CODE_INHERITED = {"constructor": "function Object() { [native code] }"}


def cell(u, f):
    a = u["artifacts"].get(f)
    if a is None:
        return DASH
    status = a["status"]
    if status in CODE:
        return CODE[status]
    return CODE_INHERITED.get(status, "?") if isinstance(status, str) else "?"


def between_pr_and_ship(unit, limit=REVIEW_ROUNDS):
    """an accepted `pr.md` naming a pull request, on a unit neither ended nor held."""
    if status_of(unit, proc(unit).file(proc(unit).pr)) != "accepted":
        return False
    if not truthy(pr_of(unit)):
        return False
    return decide(unit, limit)["why"] not in ("finished", "rejected", "paused", "dropped")


def more_rounds(unit, limit=REVIEW_ROUNDS):
    """out of review rounds with findings open, and neither ended nor held."""
    if decide(unit, limit)["why"] in ("finished", "rejected", "paused", "dropped"):
        return False
    return out_of_rounds(unit, limit)


def stage_at(unit, next_):
    """the stage a unit is at, for a board that draws one column per stage."""
    if next_["stage"]:
        return next_["stage"]
    p = proc(unit)
    last = [s for s in p.stages if present(unit, s["file"])]
    if last:
        return last[-1]["name"]
    return p.opener


def _join(base, name):
    """`path.join(base, name)`: normalised, a trailing slash kept."""
    p = os.path.normpath(f"{base}/{name}")
    return p + "/" if name.endswith("/") and not p.endswith("/") else p


def cmd_status(json, cos_dir, limit, state, out):
    units = read_all(cos_dir, state)
    rows: list[Any] = []
    for u in units:
        next_ = {k: v for k, v in decide(u, limit).items() if k not in ("rerun", "continue")}
        row = {
            **u,
            "next": next_,
            "at": stage_at(u, next_),
            "betweenPrAndShip": between_pr_and_ship(u, limit),
        }
        if more_rounds(u, limit):
            row["moreRounds"] = True
        rows.append(row)

    if json:
        p = proc_of(DEFAULT)
        body = {
            "root": cos_dir,
            "stages": p.stages,
            "afterAnswers": p.rerun("answers"),
            "units": rows,
        }
        out(stringify(body, 2))
        return 0

    if not units:
        out("No work units yet. `write-intent` opens one.")
        return 0

    p = proc_of(DEFAULT)
    out(f"| Unit | {' | '.join(p.names)} | Next action |")
    out(f"|---|{'|'.join('---' for _ in p.names)}|---|")
    for u in rows:
        cells = " | ".join(cell(u, f) for f in p.files)
        out(f"| {u['name']} | {cells} | {u['next']['action']} |")
    out(
        "\nA accepted · d draft · c changes-requested · s skipped · x rejected · "
        f"{DASH} not started"
    )

    problems = [*(f"{u['name']}: {p}" for u in rows for p in u["problems"])]
    if problems:
        out("\nProblems (report these, do not infer past them):")
        for p in problems:
            out(f"  - {p}")
    return 0


def cmd_next(unit_name, cos_dir, repo_dir, limit, state, out, err):
    if not unit_name:
        err("usage: python -m coscc.loop next <NNNN_slug> [--repo <dir>] --state <file|->")
        return 2
    dir_ = _join(cos_dir, unit_name)
    if not os.path.exists(dir_):
        err(f"No such work unit: {unit_name}")
        return 2
    probe = make_probe(repo_dir) if repo_dir else None
    unit = read_unit(dir_, unit_name, state)
    answer = next_answer(unit, probe, limit)
    body = {
        "unit": unit_name,
        "stage": answer["stage"],
        "action": answer["action"],
        "blocked": answer["blocked"],
    }
    if len(answer.get("waiting") or []):
        body["waiting"] = answer["waiting"]
    if len(answer.get("dropped") or []):
        body["dropped"] = answer["dropped"]
    if unit["hold"]:
        body["hold"] = unit["hold"]
    if answer.get("rerun"):
        body["rerun"] = answer["rerun"]
    if answer.get("continue"):
        body["continue"] = answer["continue"]
    if answer.get("why") == "dependency":
        body["why"] = answer["why"]
    body["reasons"] = answer["reasons"]
    body["process"] = unit["process"]
    out(stringify(body))
    return 0


def open_lines(stage, unit_name, head=None, rebased=None, retry=None, merged=None):
    """What `gate` prints when it opens, the pin and the notes a clean rebase or a retry add."""
    if merged:
        pin = f" — {merged_line(merged)}"
    elif head:
        pin = f" — merge with --match-head-commit {head}"
    else:
        pin = ""
    lines = [f"open: {stage} may proceed for {unit_name}{pin}"]
    if rebased:
        lines.append(
            f"the reviewed commit {rebased['reviewed'][:7]} was rebased to {rebased['head'][:7]} "
            "and the unit's patch is unchanged — no review round is needed"
        )
    if retry:
        lines.append(
            f"review round {js(retry['n'])} passed on {retry['reviewed'][:7]} and the ship gate "
            f"is still closed: {'; '.join(retry['need'])} — this round is the one retry: fix "
            "what that names in this round; a second passing round on the same head that "
            "leaves ship closed stops the unit for a person"
        )
    return lines


def cmd_gate(unit_name, stage, cos_dir, repo_dir, limit, state, json, out, err):
    if not unit_name or not stage:
        err(
            f"usage: python -m coscc.loop gate <NNNN_slug> <{'|'.join(proc_of(DEFAULT).names)}> [--json] "
            "[--repo <dir>] --state <file|->"
        )
        return 2
    dir_ = _join(cos_dir, unit_name)
    if not os.path.exists(dir_):
        err(f"No such work unit: {unit_name}")
        return 2
    probe = make_probe(repo_dir) if repo_dir else None
    unit = read_unit(dir_, unit_name, state)
    a = gate_answer(unit, stage, probe, limit)
    ok, need = a["ok"], a["need"]
    rebased = a.get("rebased")
    reasons = a.get("reasons", UNDEFINED)
    if ok:
        lines = open_lines(
            stage, unit_name, a.get("head"), rebased, a.get("retry"), a.get("merged")
        )
    else:
        lines = [f"blocked: {stage} cannot proceed for {unit_name}", *(f"  - {n}" for n in need)]
    if json:
        body = {"ok": ok, "lines": lines, "reasons": reasons}
        if rebased:
            body["rebased"] = rebased
        via = walk(unit)[1]
        if via:
            body["via"] = via
        out(stringify(body))
    else:
        for line in lines:
            (out if ok else err)(line)
    return 0 if ok else 1


def run(args, out, err):
    """The dispatch for the three commands this module answers."""
    rest = args.rest
    if args.cmd == "status":
        return cmd_status("--json" in rest, args.cos_dir, args.limit, args.state, out)
    if args.cmd == "gate":
        words = [w for w in rest if w != "--json"]
        unit_name = words[0] if words else None
        stage = words[1] if len(words) > 1 else None
        return cmd_gate(
            unit_name, stage, args.cos_dir, args.repo_dir, args.limit, args.state,
            "--json" in rest, out, err,
        )  # fmt: skip
    return cmd_next(
        rest[0] if rest else None, args.cos_dir, args.repo_dir, args.limit, args.state, out, err
    )
