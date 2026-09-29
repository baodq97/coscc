"""Jera: an agent that answers a unit's open questions from precedent.

`0044_open-questions-wait-for-the-originator-even-when-precedent-answers-them`. Not a stage
and unknown to `cos.mjs`: one tool-less session per press of *Ask Jera*, or per pick of the
autopilot (`0101` R1), under the grant `precedent` (`coscc/agent/policy.py`) with a ceiling
set by the prompt's length (`grant_for_prompt`). Everything it may read is in its prompt;
since `0136` R8 everything it decides is one object it hands back through `submit`, which
the app filters and writes. Its reply is not read.
`Service.precedent` is the one caller that writes; `scripts/verify_0044.py --measure` asks
and writes nothing.

Every function here but `ask` is pure: no disk, no network, no clock.

What is decided here, and what is not:
- The store of precedent (`entries`) is the Settings text and every answer in force in the
  same workspace, less Jera's own and the asked unit's (`spec.md` R8). An answer a later
  block replaced is not precedent: it is a decision that was changed (`plan.md`, *Những gì
  cố ý không làm*).
- A verdict `answer` survives only with every citation in that store, or `practice` on a
  question of category `other` (`0101` R6), and a category outside the four that need a
  person (`verdicts`, R4-R6; `0101` R7). Which category a question is in is still Jera's
  word (`spec.md` C2), and so is which best practice `practice` stands for (`0101` C2);
  nothing here can check either.
- Since `0137` every entry says who decided it (`decided_by`: `originator`, `delegated` or
  `inferred`), and a verdict `answer` that cites no `originator` or `delegated` entry and
  no `practice` needs a person (`verdicts`, R8): an agent's inference never settles a
  question by itself. The label is only as true as the name in `Answered by:` (`0137`
  spec ## Concerns).
"""

from __future__ import annotations

import dataclasses
import math
import re
from typing import Any, Iterable, Mapping

from coscc.agent.submit import AGAIN as SUBMIT_AGAIN

AGENT = "Jera"
VIA = "precedent"
ACTOR = f"agent:{AGENT}"

# `0101` R7, `intent.md ## Answers, câu 4`. A question in one of these is a person's, whatever
# precedent says, and no text in Settings removes one (`0101` C1). `0044`'s
# `business-tradeoff` is gone (`0101` C3): a reply still saying it is a category the app
# does not know, and needs a person.
NEEDS_PERSON = (
    "product-direction",
    "security-or-permissions",
    "significant-spend",
    "external-action",
)
CATEGORIES = NEEDS_PERSON + ("other",)
ANSWER, PERSON = "answer", "needs-person"

# `0101` R6. The one citation that is no entry of the store: a best practice Jera names in
# `text`. It settles only a question of category `other`, and has no source anyone can check.
PRACTICE = "practice"

# `0101` R5. A session's ceiling grows with its prompt. The line runs through two sessions
# measured with a cold cache: 64220 characters cost $0.486468 (`0101` spike.md ## U2, run 1),
# 178405 cost $1.213064 (`0101` spec.md R5 point 2). The margin 1.25 and the cap $3.00 are
# chosen, not measured.
PRECEDENT_FLOOR_USD = 1.00
PRECEDENT_MAX_USD = 3.00
_USD_FIXED, _USD_PER_CHAR, _MARGIN = 0.08, 0.0000064, 1.25

# `0101` R8. What the prompt says about deciding, unless Settings holds `decision_rules`.
# Words to the model, so English; no person, company or repository is named (`intent.md
# ## Answers`, câu 6).
DEFAULT_RULES = (
    "Decide a question yourself when precedent below settles it, or when it is of category "
    "`other` and a widely accepted best practice settles it. Leave it to the person when it is "
    "about product direction, security or permissions, significant spend, or an action outside "
    "this repository, or when neither precedent nor a best practice settles it. Whatever the "
    "verdict, give exactly one proposed answer in `text`, never a list of options."
)

# The last line of every block Jera writes; `cites_of` reads it back for the board.
CITES = "Tiền lệ:"

# `spec.md` R4. Said for a question the reply left out or said nothing usable about.
NOT_ANSWERED = "Jera did not answer this question."

# `0136` R2, R8. Why a run is `failed` when Jera called `submit` with nothing it kept.
NO_OBJECT = "no-submission: Jera handed back no verdicts through submit"

# `0137` R1. Who decided an entry, computed on every read and never typed by anyone.
ORIGINATOR, DELEGATED, INFERRED = "originator", "delegated", "inferred"
WHO = (ORIGINATOR, DELEGATED, INFERRED)

# `0137` R2. Names that are an agent's whatever the agent table says: Jera, and Leif, who
# answers in the originator's place from outside the app.
AGENTS_ALWAYS = ("Jera", "Leif")

# `0137` R10. The last line of a block written under a delegation, `Theo ủy quyền: D<n>`.
DELEGATION = "Theo ủy quyền:"
_DELEGATION_LINE = re.compile(r"^Theo ủy quyền: (D\d+)$")

# The fixed word `Answered by:` carries when a request names nobody (`service.common.OWNER`).
OWNER = "owner"

# `0137` R8. The reason a verdict citing only inferences needs a person.
ONLY_INFERRED = "It rests only on agents' inferences."

# `0137` R3. What each kind of entry says of itself.
PREFS_SOURCE = "Settings: Decision preferences"
EVERY_WORKSPACE, THIS_WORKSPACE, NO_END = "every workspace", "this workspace", "no end date"
ANSWER_SCOPE, ANSWER_TERM = "that question of that unit", "until a later block replaces it"


def is_jera(name: Any) -> bool:
    """`spec.md` R11: the one name a person may not answer under."""
    return str(name or "").strip().lower() == AGENT.lower()


def opens_with(by: Any, names: Iterable[Any]) -> bool:
    """`0137` R1: `by` opens with one of `names`, case aside, and the name ends there or at a
    character that is not a letter — `Leif (CoS)` does, `Leifson` does not."""
    b = str(by or "").strip().casefold()
    for n in names:
        n = str(n or "").strip().casefold()
        if n and b.startswith(n) and (len(b) == len(n) or not b[len(n)].isalpha()):
            return True
    return False


def is_agent_name(by: Any, names: Iterable[Any]) -> bool:
    """`0137` R2. `by` is an agent's: it opens with a name in `names` or `AGENTS_ALWAYS`."""
    return opens_with(by, [*names, *AGENTS_ALWAYS])


def decision_id(d: Mapping[str, Any]) -> str:
    return f"D{d.get('id')}"


def in_force(d: Mapping[str, Any], day: str, workspace: str) -> bool:
    """`0137` R4. Decision `d` holds on `day` (ISO) in `workspace` (a slot): from its first day,
    to its last if it has one, before the day it was withdrawn, and in its workspace or all."""
    day = str(day or "")
    start, until = str(d.get("from_day") or ""), str(d.get("until_day") or "")
    gone, where = str(d.get("withdrawn") or ""), str(d.get("workspace") or "")
    return (bool(day) and bool(start) and start <= day and (not until or day <= until)
            and (not gone or day < gone) and (not where or where == workspace))


def delegation_of(text: Any) -> str:
    """`0137` R10. The `D<n>` of a block's last non-empty line `Theo ủy quyền: D<n>`, else `""`."""
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    found = _DELEGATION_LINE.match(lines[-1]) if lines else None
    return found.group(1) if found else ""


def decided_by(answer: Mapping[str, Any], workspace: str, decisions: Iterable[Mapping[str, Any]],
               mine: Iterable[str], agents: Iterable[str]) -> str:
    """`0137` R1. Who decided one answer in force, in this order:

    1. `delegated`: its last line cites a `delegation` in force on its `Date:` in this
       workspace, and `by` opens with the name of the agent that delegation names;
    2. `originator`: `by` is `owner` or a name marked "This was me", and no agent's;
    3. `inferred`: everything else, every block of Leif's among it."""
    by = str(answer.get("by") or "").strip()
    cited = delegation_of(answer.get("text"))
    if cited:
        d = next((d for d in decisions if decision_id(d) == cited), None)
        if (d is not None and d.get("kind") == "delegation"
                and in_force(d, str(answer.get("date") or ""), workspace)
                and opens_with(by, [d.get("agent")])):
            return DELEGATED
    key = by.casefold()
    if (key == OWNER or key in {str(m).strip().casefold() for m in mine}) and not is_agent_name(by, agents):
        return ORIGINATOR
    return INFERRED


def entry_id(unit: str, artifact: str, n: Any) -> str:
    return f"{unit}/{artifact}#Câu {n}"


def entries(units: Iterable[dict[str, Any]], prefs_text: str, exclude_units: Iterable[str], *,
            workspace: str = "", decisions: Iterable[Mapping[str, Any]] = (), mine: Iterable[str] = (),
            agents: Iterable[str] = (), today: str = "") -> list[dict[str, str]]:
    """`spec.md` R8, `0137` R3. `[{id, text, who, source, scope, term}]`: each paragraph of the
    Settings text as `pref:<k>`, each of the person's `decisions` in force `today` in
    `workspace` as `D<n>`, then each answer in force the board read carries (`answers`,
    `coscc/units/board.py`), which also has its `date`.

    Left out: an answer Jera gave (by its name or by `Via: precedent`), and every answer of a
    unit in `exclude_units` — the asked unit itself, so a question never cites its own
    answer. Leif's answers stay in (`spec.md` C3), labelled `inferred` (`0137` R1)."""
    out: list[dict[str, str]] = []
    paragraphs = [p.strip() for p in re.split(r"\n[ \t]*\n", prefs_text or "") if p.strip()]
    for k, p in enumerate(paragraphs, 1):
        out.append({"id": f"pref:{k}", "text": p, "who": ORIGINATOR, "source": PREFS_SOURCE,
                    "scope": EVERY_WORKSPACE, "term": NO_END})
    decisions = list(decisions)
    mine, agents = list(mine), list(agents)
    for d in decisions:
        if not in_force(d, today, workspace):
            continue
        text = str(d.get("text") or "").strip()
        if d.get("kind") == "delegation":
            text = f"Delegation to {d.get('agent')} for: {d.get('covers')}.\n{text}"
        until = str(d.get("until_day") or "")
        # The workspace by what it is to the asked unit, not by name: the prompt names none.
        out.append({"id": decision_id(d), "text": text, "who": ORIGINATOR, "source": str(d.get("source") or ""),
                    "scope": THIS_WORKSPACE if d.get("workspace") else EVERY_WORKSPACE,
                    "term": f"until {until}" if until else "until withdrawn"})
    excluded = set(exclude_units)
    for u in units:
        name = str(u.get("name") or "")
        if name in excluded:
            continue
        for a in u.get("answers") or []:
            if is_jera(a.get("by")) or str(a.get("via") or "") == VIA:
                continue
            artifact, day = str(a.get("artifact") or ""), str(a.get("date") or "")
            out.append({
                "id": entry_id(name, artifact, a.get("n")),
                "text": f"Question: {str(a.get('question') or '').strip()}\n"
                        f"Answer ({str(a.get('by') or '').strip()}): {str(a.get('text') or '').strip()}",
                "who": decided_by(a, workspace, decisions, mine, agents),
                "source": f"{name}/{artifact} ## Answers, câu {a.get('n')}" + (f", {day}" if day else ""),
                "scope": ANSWER_SCOPE, "term": ANSWER_TERM, "date": day,
            })
    return out


def asked(unit: dict[str, Any]) -> list[dict[str, Any]]:
    """`spec.md` R2. The questions Jera is given: not answered, not in `review.md`, on a unit
    that is neither finished nor closed. `[{unit, artifact, n, text}]`."""
    # `0136` R11: by the rule of `decide` that answered, never by the words of `next`.
    if str(unit.get("why") or "") in ("finished", "rejected"):
        return []
    return [
        {"unit": str(unit.get("name") or ""), "artifact": str(q["artifact"]), "n": int(q["n"]),
         "text": str(q.get("text") or "")}
        for q in unit.get("questions") or []
        if not q.get("answered") and q.get("artifact") != "review.md"
    ]


def ceiling(chars: int) -> float:
    """`0101` R5. The `max_budget_usd` of a Jera session whose prompt is `chars` long: the
    measured line with its margin, up to the next $0.05, and never under $1.00. Past
    `PRECEDENT_MAX_USD` no session opens; that is the caller's to refuse."""
    raw = _MARGIN * (_USD_FIXED + _USD_PER_CHAR * max(0, int(chars)))
    # Rounded before the ceiling, so a float's last bit does not add a step.
    return max(PRECEDENT_FLOOR_USD, round(math.ceil(round(raw * 20, 9)) / 20, 2))


def grant_for_prompt(grant: Any, prompt: str) -> Any:
    """`0101` R5, Design 2. The grant `precedent` with the ceiling of this prompt; `Grant` is
    frozen, so a copy."""
    return dataclasses.replace(grant, max_budget_usd=ceiling(len(prompt)))


def build_prompt(questions: list[dict[str, Any]], store: list[dict[str, str]], rules: str = DEFAULT_RULES) -> str:
    """`spec.md` Design 4. In English; the text Jera writes is Vietnamese because it goes into
    `.cos/`. No path and no workspace name: only questions, the rules in force (`0101` R8)
    and the store, by id."""
    lines = [
        f"You are {AGENT}. You answer the open questions of one unit of work from precedent: "
        "decisions already made in this project, listed under *Precedent* below. You have no "
        "tools but `submit`; everything you may use is in this prompt.",
        "",
        "For every question under *Questions*, give exactly one verdict:",
        "- `answer`: the precedent settles it. `cites` lists the id of every precedent entry "
        "you relied on, copied exactly. An answer with no citation, or one citing an id not "
        "listed below, is not written.",
        "- `needs-person`: it needs the person who started this work. `text` is your proposed "
        "answer all the same, and `reason` says in one line why a person must decide.",
        "",
        "`category` is one of: " + ", ".join(f"`{c}`" for c in CATEGORIES) + ". Every question "
        "that is about " + ", ".join(f"`{c}`" for c in NEEDS_PERSON) + " is `needs-person`, "
        "whatever precedent says; use `other` for everything else.",
        "",
        f"Best practice may settle a question of category `other` that no entry below settles: "
        f"cite the id `{PRACTICE}`, and name that best practice in one sentence of `text`. "
        f"`{PRACTICE}` settles no question of any other category. When neither settles a "
        "question, it is `needs-person`.",
        "",
        "## Rules",
        "",
        (rules or DEFAULT_RULES).strip(),
        "",
        "Write `text` and `reason` in Vietnamese: they are added to the unit's files. No line of "
        "`text` may start with `#`.",
        "",
        "Hand your verdicts back through the `submit` tool; the app does not read your reply. "
        "The object, one element per question:",
        "```json",
        '{"verdicts":[{"artifact":"spec.md","n":1,"verdict":"answer","category":"other","text":"...",'
        '"reason":"","cites":["pref:1"]}]}',
        "```",
        f"If `submit` returns an error: {SUBMIT_AGAIN}",
        "",
        "## Questions",
    ]
    for q in questions:
        lines += ["", f"### {q['artifact']}, question {q['n']}", str(q.get("text") or "").strip()]
    # `0137` R7. Outside `## Rules`, so `decision_rules` cannot take it out.
    lines += ["", "## Precedent", "", WEIGHING]
    for who, title, said, same in _PARTS:
        part = [e for e in store if e.get("who", INFERRED) == who]
        lines += ["", f"### {title}", said]
        if not part:
            lines += ["", "(none)"]
        for e in part:
            lines += ["", f"#### {e['id']}"]
            if e.get("date") is not None:
                # An answer: its scope and term are always those of an answer.
                lines.append(f"Date: {e['date']}")
            else:
                if e.get("source"):
                    lines.append(f"Source: {e['source']}")
                for field in ("scope", "term"):
                    if e.get(field) and e[field] != same[field]:
                        lines.append(f"{field.capitalize()}: {e[field]}")
            lines.append(e["text"])
    return "\n".join(lines) + "\n"


# `0137` R7. The three things the prompt always says about weighing precedent.
WEIGHING = (
    "An entry under *Agents' inferences* never settles a question by itself: an `answer` must "
    "also cite an entry of one of the other two parts, or `practice`. When an inference "
    "contradicts an entry of the person's, the person's entry wins. A delegation lets only the "
    "agent it names decide, and only the kind of question it says it covers."
)

# `0137` R7. The three parts of `## Precedent`, in order: who, heading, the sentence saying
# what every entry in it holds for, and that scope and term, which an entry repeats only when
# its own differ.
_PARTS = (
    (ORIGINATOR, "The person's decisions",
     "A preference or decision holds in every workspace with no end date unless it says otherwise; "
     "a dated answer answers that question of that unit until a later block replaces it.",
     {"scope": EVERY_WORKSPACE, "term": NO_END}),
    (DELEGATED, "Answers the person delegated",
     "Each answers that question of that unit, until a later block replaces it.",
     {"scope": ANSWER_SCOPE, "term": ANSWER_TERM}),
    (INFERRED, "Agents' inferences",
     "Each answers that question of that unit, until a later block replaces it.",
     {"scope": ANSWER_SCOPE, "term": ANSWER_TERM}),
)


def _number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def verdicts(submitted: Mapping[str, Any] | None, questions: list[dict[str, Any]],
             who: Mapping[str, str]) -> dict[str, Any]:
    """`spec.md` R4, R5, R6. `{failed, verdicts, ignored}`.

    `submitted` is the object Jera handed back through `submit` (`0136` R8), `None` when it
    handed back none; the reply's words are never read for it. `who` maps every id of the
    store Jera was given to who decided that entry (`0137` R1).

    `failed` is a reason when there is no object, and then nothing else is read.
    Otherwise `verdicts` has one element per question in `questions`, in their order, each
    `{artifact, n, question, verdict, category, text, reason, cites}`. An element naming a
    question not in `questions` — any `review.md`, any `F<n>` — is `ignored` and reaches
    nothing. Every downgrade to `needs-person` says why in `reason`.
    """
    data = (submitted or {}).get("verdicts") if isinstance(submitted, Mapping) else None
    if not isinstance(data, list):
        return {"failed": NO_OBJECT, "verdicts": [], "ignored": []}
    known = set(who)
    wanted = {(q["artifact"], int(q["n"])): q for q in questions}
    said: dict[tuple[str, int], dict[str, Any]] = {}
    ignored: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            ignored.append({"item": str(item)[:200], "reason": "not an object"})
            continue
        at = (str(item.get("artifact") or ""), _number(item.get("n")))
        if at not in wanted:
            ignored.append({"artifact": at[0], "n": item.get("n"), "reason": "not a question Jera was asked"})
            continue
        if at in said:
            ignored.append({"artifact": at[0], "n": at[1], "reason": "a second verdict for one question"})
            continue
        said[at] = item

    out = []
    for at, q in wanted.items():
        item = said.get(at)
        base = {"artifact": at[0], "n": at[1], "question": q.get("text", "")}
        if item is None:
            out.append({**base, "verdict": PERSON, "category": "", "text": "", "reason": NOT_ANSWERED, "cites": []})
            continue
        verdict = item.get("verdict")
        category = item.get("category")
        text = item.get("text")
        reason = item.get("reason")
        cites = item.get("cites")
        text = text.strip() if isinstance(text, str) else ""
        reason = reason.strip() if isinstance(reason, str) else ""
        category = category.strip() if isinstance(category, str) else ""
        well_formed = (
            verdict in (ANSWER, PERSON)
            and isinstance(item.get("text"), str)
            and (cites is None or (isinstance(cites, list) and all(isinstance(c, str) for c in cites)))
        )
        cites = list(dict.fromkeys(c.strip() for c in cites if c.strip())) if isinstance(cites, list) and well_formed else []
        v = {**base, "verdict": verdict, "category": category, "text": text, "reason": reason, "cites": cites}
        if not well_formed:
            v.update(verdict=PERSON, reason=NOT_ANSWERED)
        elif verdict == ANSWER:
            unknown = [c for c in cites if c not in known and c != PRACTICE]
            why = ""
            if not text:
                why = "The answer is empty."
            elif not cites:
                why = "It cites no precedent."
            elif unknown:
                why = f"It cites {', '.join(unknown)}, which is not in the precedent it was given."
            elif PRACTICE in cites and category != "other":
                # `0101` R6: a best practice stands in for precedent on `other` alone.
                why = f"It cites {PRACTICE}, which settles only a question of category other."
            elif category in NEEDS_PERSON:
                why = f"Its category, {category}, needs a person."
            elif category not in CATEGORIES:
                why = f"Its category, {category or '(none)'}, is not one the app knows."
            elif any(line.lstrip().startswith("#") for line in text.splitlines()):
                why = "A line of it starts with #, which an answer may not."
            elif not any(c == PRACTICE or who.get(c) in (ORIGINATOR, DELEGATED) for c in cites):
                # `0137` R8, last so it counts only what no older reason already took.
                why = ONLY_INFERRED
            if why:
                v.update(verdict=PERSON, reason=why)
        if v["verdict"] == PERSON and not v["reason"]:
            v["reason"] = "Jera gave no reason."
        if v["verdict"] == PERSON and not v["text"]:
            v["reason"] = (v["reason"] + " " if v["reason"] else "") + "Its proposal is empty."
        out.append(v)
    return {"failed": None, "verdicts": out, "ignored": ignored}


def block_text(v: dict[str, Any]) -> str:
    """The body of the `### Câu N` block for one `answer` verdict: its words, then its cites."""
    return f"{v['text']}\n\n{CITES} {'; '.join(v['cites'])}"


def cites_of(text: str) -> list[str]:
    """The citations of a block `block_text` wrote, read back from its last `Tiền lệ:` line."""
    for line in reversed((text or "").splitlines()):
        if line.startswith(CITES):
            return [c.strip() for c in line[len(CITES):].split(";") if c.strip()]
    return []


def words_of(text: str) -> str:
    """The words of a block `block_text` wrote, without the `Tiền lệ:` line `cites_of` reads."""
    lines = (text or "").splitlines()
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].startswith(CITES):
            del lines[i]
            break
    return "\n".join(lines).strip()


async def ask(sessions: Any, cwd: str, prompt: str, grant: Any, model: str | None,
              effort: str | None, owner: dict[str, Any] | None = None,
              resume: dict[str, Any] | None = None,
              channel: Any = None) -> tuple[str, dict[str, Any], str]:
    """One tool-less session, as `Service.propose_estimates` runs its own. `(reply, end,
    failure)`: `end` holds `session_id`, `cost` and `terminal_reason`; `failure` is `""`
    unless the session broke or stopped at a ceiling, which counts as broken.

    `owner` and `resume` are `0138`'s, as `Sessions.stream` takes them: a session an update
    pauses raises `Suspended` out of here, and one taken up again goes on from its row.

    `channel` (`0136` R8) is the `submit.Collector` the session hands its object to; the
    grant must carry `submits` for the gate to let the call through. A knowledge batch
    passes none, and its session has no tool at all."""
    from coscc.runner import CEILING_MARKERS, Denials, permission_gate
    from coscc.agent.sessions import StepHandle, Suspended
    from coscc.agent.submit import SERVER
    from coscc.service.resume import nothing, resume_kwargs

    reply, end, failure = "", {}, ""
    given = resume_kwargs(resume, grant, prompt)
    used_up = given.pop("used_up", "")
    try:
        async for kind, payload in nothing() if used_up else sessions.stream(
            cwd, given.pop("text"), given.pop("session_id"), tools=[],
            # `tools=[]` still lets MCP tools through (`sessions.py`); the gate refuses every
            # one but `submit`, and that only on a grant that `submits`.
            can_use_tool=permission_gate(grant, cwd, Denials()), step=StepHandle(),
            **given,
            **({"mcp_servers": {SERVER: channel.server()}} if channel is not None else {}),
            **({"owner": owner} if owner is not None else {}),
            **({"model": model} if model is not None else {}),
            **({"effort": effort} if effort is not None else {}),
        ):
            if kind == "chunk":
                reply += payload
            elif kind == "session":
                end["session_id"] = str(payload)
            elif kind == "done":
                end.update(session_id=payload.get("session_id", end.get("session_id", "")),
                           cost=payload.get("cost") or {},
                           terminal_reason=str(payload.get("terminal_reason") or ""))
    except Suspended:
        raise
    except Exception as e:  # noqa: BLE001 — recorded as the reason
        failure = f"the session failed: {e}"
    terminal = end.get("terminal_reason", "") or used_up
    if not failure and any(m in terminal for m in CEILING_MARKERS):
        failure = f"the session stopped at a ceiling ({terminal}); nothing was written"
    return reply, end, failure
