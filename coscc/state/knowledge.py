"""The *Knowledge* screen (`0131` R24-R26): what `Service.knowledge_page` read, made readable.

`StudioState` inherits it and reads it once per arrival at `/knowledge`, as *Cost* is read.
Nothing here decides or writes: no button, no route, no gather.
"""

from __future__ import annotations

import dataclasses

import reflex as rx

from coscc.web import present

# R26: a status nobody has checked says so, rather than "pass".
NOT_CHECKED = "not checked yet"
# R22's verdicts, for the reader of an English screen (S6).
VERDICTS = {"đạt": "passes", "không đạt": "fails", "chưa đủ mẫu": "too few units yet"}


@dataclasses.dataclass
class KnowledgeEntry:
    """One entry: its sources without their workspace, which only `slots` names (S3)."""

    id: str = ""
    scope: str = ""
    statement: str = ""
    sources: list[str] = dataclasses.field(default_factory=list)
    slots: str = ""
    measured: str = ""
    status: str = ""
    reason: str = ""
    color: str = "gray"


@dataclasses.dataclass
class KnowledgeGather:
    at: str = ""
    unit: str = ""
    outcome: str = ""
    cost: str = ""
    reason: str = ""


@dataclasses.dataclass
class KnowledgeStep:
    unit: str = ""
    stage: str = ""
    at: str = ""
    arm: str = ""
    carried: str = ""
    withheld: str = ""


@dataclasses.dataclass
class KnowledgeMetric:
    metric: str = ""
    on: str = ""
    off: str = ""


def _figure(value) -> str:
    if value is None:
        return "—"
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


def knowledge_fields(page: dict) -> dict:
    """Every field of the screen from `Service.knowledge_page`'s answer."""
    checked = page.get("checked")
    sha = str((checked or {}).get("sha") or "")
    entries = []
    for e in page.get("entries") or []:
        broken = e.get("broken")
        status, color = ((NOT_CHECKED, "gray") if broken is None else ("pass", "grass") if not broken
                         else ("broken", "red"))
        sources = e.get("sources") or []
        entries.append(KnowledgeEntry(
            id=e["id"], scope=e["scope"], statement=e["statement"],
            sources=[" · ".join(x for x in (s.get("unit"), s.get("file"), s.get("anchor")) if x) for s in sources],
            slots=", ".join(sorted({s.get("slot") or "" for s in sources} - {""})),
            measured=present.day(e.get("measured")), status=status, reason=str(broken or ""), color=color,
        ))
    last = page.get("last_gather")
    gathers = [KnowledgeGather(
        at=present.when(last.get("at")), unit=last.get("unit") or last.get("mode") or "",
        outcome=last.get("outcome") or "", cost=present.money(last.get("cost_usd")),
        reason=last.get("reason") or "",
    )] if last else []
    steps = [KnowledgeStep(
        unit=r["unit"], stage=r["stage"], at=present.when(r["at"]), arm=r["arm"],
        carried=", ".join(r["ids"]) or "—",
        withheld=", ".join(f"{w.get('id')}: {w.get('reason')}" for w in r["withheld"]) or "—",
    ) for r in page.get("recent") or []]
    m = page.get("measure") or {}
    on, off = m.get("on") or {}, m.get("off") or {}
    metrics = [KnowledgeMetric(metric="Units", on=_figure(on.get("n")), off=_figure(off.get("n")))]
    for key, label, how in (("turns", "Turns", "median"), ("usd", "Cost ($)", "median"),
                            ("changes_requested", "Changes requested", "mean"), ("ci_red", "Red CI", "mean")):
        metrics.append(KnowledgeMetric(metric=f"{label}, {how}", on=_figure((on.get(how) or {}).get(key)),
                                       off=_figure((off.get(how) or {}).get(key))))
    metrics.append(KnowledgeMetric(metric="Cost with gathering ($)", on=_figure(m.get("usd_on_with_gather")),
                                   off=_figure((off.get("median") or {}).get("usd"))))
    reduction = m.get("reduction")
    return {
        "kn_note": page.get("note") or "",
        "kn_log_note": page.get("log_note") or "",
        "kn_checked": (f"Checked {present.when(checked.get('at'))} on origin/main {sha[:12]}" if sha
                       else f"Checked {present.when(checked.get('at'))}") if checked else "Not checked yet.",
        "kn_entries": entries,
        "kn_gathers": gathers,
        "kn_steps": steps,
        "kn_metrics": metrics,
        "kn_verdict": VERDICTS.get(str(m.get("verdict") or ""), str(m.get("verdict") or "—")),
        "kn_reduction": "—" if reduction is None else f"{reduction:.0%}",
        "kn_deadline": present.day(m.get("deadline")),
    }


class KnowledgeMixin(rx.State, mixin=True):

    kn_note: str = ""
    kn_log_note: str = ""
    kn_checked: str = ""
    kn_entries: list[KnowledgeEntry] = []
    kn_gathers: list[KnowledgeGather] = []
    kn_steps: list[KnowledgeStep] = []
    kn_metrics: list[KnowledgeMetric] = []
    kn_verdict: str = "—"
    kn_reduction: str = "—"
    kn_deadline: str = ""

    def _load_knowledge(self) -> None:
        """R24: one read of the store, `health.json` and the run log per arrival."""
        from coscc.service import Invalid
        from coscc.state import SERVICE

        if not self.cwd:
            return
        try:
            page = SERVICE.knowledge_page(self.cwd)
        except Invalid as e:
            self._fail(e)
            return
        for name, value in knowledge_fields(page).items():
            setattr(self, name, value)
