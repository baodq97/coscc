"""Vault: secrets an agent can use and never read, and the page a person keeps them on.

Agent side: one in-process MCP server per run (`vault_list`, `vault_exec`, `vault_generate`, none
taking a value), a guard holding `pr`, `ship` and integration while a value is in the unit's work,
and a prompt block. Person side: a plain HTML page, and one POST a value goes in by. The store,
filter, scan and runner are `coscc/vault/`'s; what this is not is in `coscc/features/vault.md`.
"""

from __future__ import annotations

import asyncio
import html
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import parse_qs, quote

from claude_agent_sdk import McpServerConfig, SdkMcpTool, create_sdk_mcp_server, tool
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import BaseRoute

from coscc import vault
from coscc.agent import policy
from coscc.data import Busy
from coscc.hooks import Block, Facts, Guard, Parts, Tool
from coscc.plugin import Ctx, Plugin, body
from coscc.service.common import Invalid

FEATURE = "vault"
HUMAN = "human:owner"
# The stages the tool is offered to, and the ones the guard holds.
TOOL_STAGES = ("impl", "spike")
LEAK_STAGES = ("pr", "ship", "integrate")
TOOL_NAMES = ("vault_list", "vault_exec", "vault_generate")
# Seconds a command may run: the default and the ceiling. Chosen, not measured.
TIMEOUT_DEFAULT = 120
TIMEOUT_MAX = 600
# A value shorter than this is masked but noisy: the page says so once. Chosen, not measured.
SHORT_BYTES = 8
# The most a form may carry (a value is at most 64 KiB, the rest is names). Chosen, not measured.
MAX_FORM = 128 * 1024

StoreOf = Callable[[Ctx], vault.Store]


def make_store(ctx: Ctx) -> vault.Store:
    return vault.Store(ctx.data)


def _lazy(ctx: Ctx, store_of: StoreOf | None) -> Callable[[], vault.Store]:
    """The store, built when first asked: a build of the app opens no database."""
    held: list[vault.Store] = []

    def get() -> vault.Store:
        if not held:
            held.append((store_of or make_store)(ctx))
        return held[0]

    return get


def _named(raw: str, tier: str = "ws") -> str:
    """`ws:db` or `global:db` as written, or a bare `db` in `tier`."""
    raw = raw.strip()
    return raw if raw.startswith(("ws:", "global:")) else f"{tier}:{raw}"


def _text(obj: dict[str, Any], refused: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"content": [{"type": "text", "text": json.dumps(obj)}]}
    if refused:
        out["is_error"] = True
    return out


def _no(reason: str) -> dict[str, Any]:
    return _text({"result": "refused", "reason": reason}, True)


def _uses(raw: Any) -> list[vault.Use] | str:
    """The `uses` argument as `Use`s, or the sentence saying what is wrong with it."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        return "uses is a list of {name, mode, var}"
    found: list[vault.Use] = []
    for i, item in enumerate(raw):
        got = [item.get(k, "") for k in ("name", "mode", "var")] if isinstance(item, dict) else []
        if len(got) != 3 or not all(isinstance(g, str) for g in got) or not (got[0] and got[1]):
            return f"uses[{i}] is {{name, mode, var}} in text"
        found.append(vault.Use(str(got[0]), str(got[1]), str(got[2])))
    return found


def _seconds(raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        return TIMEOUT_DEFAULT
    return min(max(raw, 1), TIMEOUT_MAX)


def _agent_name(raw: str) -> str:
    """The name of a secret the agent makes: only `ws:` is its to make."""
    name = _named(raw)
    if not name.startswith("ws:"):
        raise vault.BadSecret("an agent makes only ws: secrets")
    return name


class Handlers:
    """The three tools of one run; `facts` is that run's."""

    def __init__(self, facts: Facts, ctx: Ctx, get: Callable[[], vault.Store]) -> None:
        self.facts, self.ctx, self.get = facts, ctx, get

    def _listing(self) -> dict[str, Any]:
        key, stage = self.facts.workspace_key, self.facts.stage
        rows = []
        for s in self.get().visible(key):
            now = s.has_value and any(not vault.policy(s, key, stage, m) for m in s.modes)
            rows.append({**_meta(s, key), "usable_now": now})
        return _text({"secrets": rows})

    def _grant(self) -> policy.Grant:
        grant = policy.grant_for(self.facts.stage)
        return replace(grant, commands=self.facts.commands, protected=self.get().protected())

    def _run(self, command: str, uses: list[vault.Use], timeout: int, capture: str) -> Any:
        f = self.facts
        return vault.run(
            self.get(),
            command=command,
            uses=uses,
            grant=self._grant(),
            workspace=f.workspace_key,
            stage=f.stage,
            unit=f.unit,
            run=f.run,
            cwd=f.scratch or f.tree,
            journal=self.ctx.journal(),
            timeout=timeout,
            capture=capture,
            actor=f"agent:{f.stage}",
        )

    async def _execute(self, args: dict[str, Any]) -> dict[str, Any]:
        command, capture = args.get("command"), args.get("capture") or ""
        uses = _uses(args.get("uses"))
        if not isinstance(command, str) or not command.strip():
            return _no("command is a shell line")
        if isinstance(uses, str) or not isinstance(capture, str):
            return _no(uses if isinstance(uses, str) else "capture is a ws: name")
        # The kernel lets the tool through by name; the line is this handler's to check.
        words = policy.check_command(self._grant(), command, unit=self.facts.unit)
        if words:
            return _text({"result": "command-refused", "reason": words}, True)
        timeout = _seconds(args.get("timeout"))
        try:
            got = await asyncio.to_thread(self._run, command, uses, timeout, capture)
        except vault.BadSecret as e:
            return _no(str(e))
        return _text(_shown(got, bool(capture)), bool(got.refusals))

    async def _generate(self, args: dict[str, Any]) -> dict[str, Any]:
        raw, said = args.get("name"), args.get("description", "")
        if not isinstance(raw, str) or not isinstance(said, str):
            return _no("name and description are text")
        f = self.facts
        actor = f"agent:{f.stage}"
        try:
            name = _agent_name(raw)
            if self.get().get(name, f.workspace_key) is not None:
                raise vault.BadSecret(f"{name} already exists; it is not replaced")
            made = await asyncio.to_thread(
                vault.generate, self.get(), name, f.workspace_key, said, actor
            )
        except vault.BadSecret as e:
            return _no(str(e))
        vault.record(
            self.ctx.journal(), "create", made.name, f.workspace_key, actor, via="generate"
        )
        return _text({"result": "made", "name": made.name})


def _shown(got: vault.Result, captured: bool) -> dict[str, Any]:
    """What a run hands the model: the filtered streams and counts, never a value."""
    if got.refused:
        return {"result": "command-refused", "reason": got.refused}
    if got.refusals:
        return {
            "result": "refused",
            "refusals": [{"secret": n, "code": c, "reason": r} for n, c, r in got.refusals],
        }
    out: dict[str, Any] = {"exit_code": got.exit_code, "stderr": got.stderr, "masked": got.masked}
    out |= {"captured_bytes": got.captured} if captured else {"stdout": got.stdout}
    return out


def build_tools(facts: Facts, ctx: Ctx, get: Callable[[], vault.Store]) -> list[SdkMcpTool[Any]]:
    """The run's three tools. None has a parameter a value could ride in."""
    h = Handlers(facts, ctx, get)

    async def _list(_args: dict[str, Any]) -> dict[str, Any]:
        return h._listing()

    def _obj(props: dict[str, Any], *need: str) -> dict[str, Any]:
        return {"type": "object", "properties": props, "required": list(need)}

    text = {"type": "string"}
    use = _obj(
        {"name": text, "mode": {"type": "string", "enum": list(vault.MODES)}, "var": text},
        "name",
        "mode",
    )
    exec_schema = _obj(
        {
            "command": {"type": "string", "description": "one shell line; {{secret:<name>}} ok"},
            "uses": {"type": "array", "items": use},
            "timeout": {"type": "integer", "description": f"seconds, at most {TIMEOUT_MAX}"},
            "capture": {"type": "string", "description": "a new ws: name to keep stdout in"},
        },
        "command",
    )
    make_schema = _obj({"name": text, "description": text}, "name")
    return [
        tool("vault_list", "List the secrets this step may see, never their values.", {})(_list),
        tool(
            "vault_exec",
            "Run one command with secrets passed in; the output is filtered of them.",
            exec_schema,
        )(h._execute),
        tool("vault_generate", "Make a random ws: secret; its value is never shown.", make_schema)(
            h._generate
        ),
    ]


def _leaks(ctx: Ctx, get: Callable[[], vault.Store], facts: Facts) -> str | None:
    if facts.stage not in LEAK_STAGES:
        return None
    store = get()
    if not any(s.has_value for s in store.visible(facts.workspace_key)):
        return None
    journal = ctx.journal()
    if journal is None:
        return "the run log cannot be read, so the work cannot be scanned"
    values = store.values_for(facts.workspace_key)
    sources = vault.unit_sources(
        journal,
        facts.workspace_key,
        facts.unit,
        facts.directory,
        facts.tree,
        pull_request=facts.stage == "ship",
    )
    hits = vault.scan(values, sources)
    base = {"kind": "vault-leak", "workspace": facts.workspace_key, "unit": facts.unit}
    base |= {"stage": facts.stage, "scan": uuid.uuid4().hex[:12]}
    for h in hits:
        journal.append({**base, "name": h.name, "where": h.where, "form": h.form})
    if hits:
        names = ", ".join(sorted({h.name for h in hits}))
        return f"the value of {names} appears in this unit's work; remove it, then try again"
    # A clean scan of a unit held before says so, so the page stops naming it.
    before = journal.records(facts.workspace_key, facts.unit, kind="vault-leak")
    if before and not before[-1].get("clear"):
        journal.append({**base, "clear": True})
    return None


def _block(get: Callable[[], vault.Store], facts: Facts) -> str:
    """Names, descriptions and ways of passing for the stage that may use a secret; names alone
    for the rest. No value, and nothing on a step taken up again."""
    if facts.resumed:
        return ""
    secrets = get().visible(facts.workspace_key)
    if not secrets:
        return ""
    usable = [s for s in secrets if facts.stage in s.stages and facts.stage in TOOL_STAGES]
    rest = [f"`{s.name}`" for s in secrets if s not in usable]
    parts = ["# Secrets", ""]
    if usable:
        parts += [
            "This workspace keeps secrets you can use and never read: `vault_list` lists them and "
            "`vault_exec` runs one command with them passed in, its output filtered of them. "
            "`vault_generate` makes a random `ws:` secret. A value is never yours to see or type.",
            "",
        ]
        for s in usable:
            said = f" - {s.description}" if s.description else ""
            parts.append(f"- `{s.name}`{said} (passed as: {', '.join(s.modes)})")
    if rest:
        parts += [""] * bool(usable) + ["Names only, not for this step: " + ", ".join(rest) + "."]
    return "\n".join(parts)


def agent(ctx: Ctx, store_of: StoreOf | None = None) -> Parts:
    get = _lazy(ctx, store_of)

    def make(facts: Facts) -> McpServerConfig:
        return create_sdk_mcp_server("vault", "1.0.0", build_tools(facts, ctx, get))

    return Parts(
        tools=(Tool("vault", TOOL_NAMES, TOOL_STAGES, make),),
        guards=(Guard("vault-leak", lambda facts: _leaks(ctx, get, facts)),),
        blocks=(Block("vault", lambda facts: _block(get, facts)),),
    )


_CSS = """
:root { color-scheme: light dark; }
body { font: 14px/1.5 system-ui, sans-serif; margin: 0 auto; max-width: 960px; padding: 24px 16px; }
header { display: flex; gap: 12px; align-items: baseline; }
h1, h2 { font-size: 18px; margin: 20px 0 8px; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; vertical-align: top; padding: 8px; border-bottom: 1px solid #8884; }
label { display: inline-block; margin-right: 10px; }
.tag { font-size: 12px; border: 1px solid #8886; border-radius: 6px; padding: 0 6px; }
form.row { display: grid; gap: 6px; max-width: 420px; }
"""

_PAGE_JS = """
(function () {
  function boxes(row, name) {
    var all = row.querySelectorAll("input[name=" + name + "]:checked");
    return Array.prototype.map.call(all, function (x) { return x.value; });
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest("button[data-act]") : null;
    if (!b) return;
    var sent = {cwd: document.body.getAttribute("data-cwd"),
                name: b.getAttribute("data-name"), tier: b.getAttribute("data-tier")};
    var ask = b.getAttribute("data-ask");
    if (ask && !window.confirm(ask)) return;
    if (b.getAttribute("data-act") === "policy") {
      sent.stages = boxes(b.closest("tr"), "stage");
      sent.modes = boxes(b.closest("tr"), "mode");
    }
    fetch("/api/vault/" + b.getAttribute("data-act"), {
      method: "POST", credentials: "same-origin",
      headers: {"content-type": "application/json"}, body: JSON.stringify(sent)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (r.ok) window.location.reload();
        else document.getElementById("msg").textContent = j.error || "That did not work.";
      });
    });
  });
})();
"""


def _e(text: object) -> str:
    return html.escape(str(text), quote=True)


def _shell(title: str, inner: str, cwd: str = "") -> str:
    data = f' data-cwd="{_e(cwd)}"' if cwd else ""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_e(title)}</title><style>{_CSS}</style></head><body{data}>"
        f'<header><a href="/">Board</a><h1>{_e(title)}</h1></header>{inner}</body></html>'
    )


def _checks(name: str, options: Sequence[str], on: Sequence[str], disabled: bool = False) -> str:
    dis = " disabled" if disabled else ""
    return "".join(
        f'<label><input type="checkbox" name="{name}" value="{_e(o)}"'
        f"{' checked' if o in on else ''}{dis}> {_e(o)}</label>"
        for o in options
    )


def _replace_form(cwd: str, s: vault.Secret) -> str:
    return (
        '<form method="post" action="/api/vault/secrets" class="row">'
        f'<input type="hidden" name="cwd" value="{_e(cwd)}">'
        f'<input type="hidden" name="tier" value="{_e(s.tier)}">'
        f'<input type="hidden" name="name" value="{_e(s.name)}">'
        '<input type="password" name="value" autocomplete="new-password" required '
        f'aria-label="New value of {_e(s.name)}">'
        '<button type="submit">Replace value</button></form>'
    )


def _button(act: str, s: vault.Secret, label: str, ask: str = "") -> str:
    said = f' data-ask="{_e(ask)}"' if ask else ""
    return (
        f'<button type="button" data-act="{act}" data-name="{_e(s.name)}" '
        f'data-tier="{_e(s.tier)}"{said}>{_e(label)}</button>'
    )


def _row(cwd: str, s: vault.Secret, on: bool) -> str:
    tags = ""
    if not s.has_value:
        tags += ' <span class="tag">no value</span>'
    if s.broker:
        tags += ' <span class="tag">ssh only</span>'
    modes = "ssh only" if s.broker else _checks("mode", vault.MODES, s.modes, not on)
    acts = []
    if on:
        acts.append(_button("policy", s, "Save"))
    if s.tier == "global":
        acts.append(_button("revoke", s, "Revoke"))
    ask = "Delete this secret?"
    if s.tier == "global":
        ask = "Delete this secret for every workspace?"
    acts.append(_button("delete", s, "Delete", ask))
    replace_form = _replace_form(cwd, s) if on else ""
    return (
        f"<tr><td><b>{_e(s.name)}</b>{tags}</td><td>{_e(s.description)}</td>"
        f"<td>{_checks('stage', vault.VAULT_STAGES, s.stages, not on)}</td><td>{modes}</td>"
        f"<td>{' '.join(acts)}{replace_form}</td></tr>"
    )


def _create_form(cwd: str) -> str:
    return (
        '<h2>Add a secret</h2><form method="post" action="/api/vault/secrets" class="row">'
        f'<input type="hidden" name="cwd" value="{_e(cwd)}">'
        '<label>Name <input type="text" name="name" required pattern="[a-z0-9][a-z0-9._-]*" '
        'maxlength="64"></label>'
        '<label>Kept for <select name="tier"><option value="ws">this workspace</option>'
        '<option value="global">every workspace it is granted to</option></select></label>'
        '<label>Description <input type="text" name="description"></label>'
        f"<div>Used by {_checks('stage', vault.VAULT_STAGES, ('impl',))}</div>"
        f"<div>Passed as {_checks('mode', vault.MODES, ('env', 'file'))}</div>"
        '<label><input type="checkbox" name="broker" value="1"> Broker: passed as ssh only</label>'
        '<label>Value <input type="password" name="value" autocomplete="new-password" '
        "required></label>"
        '<button type="submit">Save</button></form>'
    )


def _grants(key: str, globals_: list[vault.Secret]) -> str:
    rows = "".join(
        f"<tr><td><b>{_e(s.name)}</b></td><td>{_e(s.description)}</td>"
        f"<td>{_button('grant', s, 'Grant')}</td></tr>"
        for s in globals_
        if key not in s.granted
    )
    if not rows:
        return ""
    return (
        "<h2>Global secrets not granted here</h2><table><thead><tr><th>Name</th>"
        f"<th>Description</th><th></th></tr></thead><tbody>{rows}</tbody></table>"
    )


def _notes(query: dict[str, str]) -> str:
    said = []
    if query.get("saved"):
        said.append(f"Saved {_e(query['saved'][:80])}.")
    if query.get("short"):
        said.append("That value is short, so masking it may hide other text.")
    if query.get("error"):
        said.append(_e(query["error"][:200]))
    return "".join(f"<p>{s}</p>" for s in said)


def _page(
    cwd: str,
    key: str,
    on: bool,
    mine: list[vault.Secret],
    globals_: list[vault.Secret],
    query: dict[str, str],
) -> str:
    where = f'<p class="muted">{_e(Path(key).name)}</p>'
    if mine:
        head = "<th>Name</th><th>Description</th><th>Used by</th><th>Passed as</th><th></th>"
        rows = "".join(_row(cwd, s, on) for s in mine)
        table = f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"
    else:
        table = "<p>No secrets here yet.</p>"
    if on:
        tail = _create_form(cwd) + _grants(key, globals_)
    else:
        tail = "<p>Vault is off for this workspace, so secrets can only be revoked or deleted.</p>"
    inner = f'{where}<p id="msg" role="status">{_notes(query)}</p>{table}{tail}'
    return _shell("Vault", inner + f"<script>{_PAGE_JS}</script>", cwd)


class Meta(TypedDict):
    """What a secret shows of itself: never a value, a length or a hash."""

    name: str
    tier: str
    description: str
    stages: list[str]
    modes: list[str]
    broker: bool
    has_value: bool
    granted: bool | None


class Secrets(TypedDict):
    workspace: str
    secrets: list[Meta]
    globals: list[Meta]


class Leaks(TypedDict):
    unit: str
    names: list[str]


class Deleted(TypedDict):
    deleted: str


def _meta(s: vault.Secret, key: str) -> Meta:
    return {
        "name": s.name,
        "tier": s.tier,
        "description": s.description,
        "stages": list(s.stages),
        "modes": list(s.modes),
        "broker": s.broker,
        "has_value": s.has_value,
        "granted": key in s.granted if s.tier == "global" else None,
    }


def _texts(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise Invalid("stages and modes are lists of names")
    return tuple(raw)


def _one(form: dict[str, list[str]], field: str) -> str:
    return form.get(field, [""])[0]


def _back(cwd: str, **fields: str) -> Response:
    query = "".join(f"&{k}={quote(v)}" for k, v in fields.items() if v)
    return RedirectResponse(f"/vault?cwd={quote(cwd)}{query}", 303)


class Door:
    """What a route does to the store, each a blocking call for `asyncio.to_thread`. A change is
    written to the run log as `human:owner`, with the names and never a value."""

    def __init__(self, ctx: Ctx, get: Callable[[], vault.Store]) -> None:
        self.ctx, self.get = ctx, get

    def key_of(self, cwd: str, *, must_be_on: bool = True) -> str:
        key = self.ctx.workspace_key(cwd)
        if must_be_on and not self.ctx.enabled(FEATURE, cwd):
            raise Invalid("the vault is off for this workspace")
        return key

    def keep(self, action: str, s: vault.Secret, key: str, **fields: Any) -> None:
        journal = self.ctx.journal()
        if journal is None:
            raise Invalid("there is no working folder, so there is no run log to record it in")
        vault.record(journal, action, s.name, key, HUMAN, tier=s.tier, **fields)

    def secret_of(self, sent: Mapping[str, object], key: str) -> vault.Secret:
        raw = sent.get("name")
        if not isinstance(raw, str):
            raise Invalid("name is text")
        name = _named(raw, str(sent.get("tier") or "ws"))
        found = self.get().get(name, key)
        if found is None:
            raise Invalid(f"no secret {name}")
        return found

    def rows(self, key: str) -> tuple[list[vault.Secret], list[vault.Secret]]:
        store = self.get()
        return store.visible(key), [s for s in store.all() if s.tier == "global"]

    def leaks(self, key: str, unit: str) -> list[str]:
        """The names the last scan of `unit` found: the hits of its newest scan, none after a
        clear one."""
        journal = self.ctx.journal()
        if journal is None or not unit:
            return []
        try:
            seen = journal.records(key, unit, kind="vault-leak")
        except Busy as e:
            raise Invalid("the run log is busy, try again") from e
        names: list[str] = []
        last = ""
        for r in seen:
            if r.get("scan") != last:
                names, last = [], str(r.get("scan"))
            if not r.get("clear") and r.get("name") not in names:
                names.append(str(r.get("name")))
        return names

    def save(self, key: str, form: dict[str, list[str]], value: bytes) -> str:
        """Make the secret, or put a new value in one that is there; `tier:name` when done."""
        name = _named(_one(form, "name"), _one(form, "tier") or "ws")
        ws = key if name.startswith("ws:") else ""
        broker = bool(_one(form, "broker"))
        modes = ("ssh",) if broker else tuple(form.get("mode", []))
        store = self.get()
        made = store.get(name, ws) is None
        if made:
            store.create(
                name,
                ws,
                description=_one(form, "description"),
                stages=tuple(form.get("stage", [])),
                modes=modes,
                broker=broker,
                actor=HUMAN,
            )
        stored = False
        try:
            store.put(name, ws, value)
            stored = True
        finally:
            if made and not stored:
                store.delete(name, ws)
        found = store.get(name, ws)
        if found is not None:
            self.keep("create" if made else "replace", found, key)
        return name

    def policy(self, key: str, sent: Mapping[str, object]) -> Meta:
        s = self.secret_of(sent, key)
        stages, modes = _texts(sent.get("stages")), _texts(sent.get("modes"))
        try:
            done = self.get().set_policy(s.name, s.workspace, stages, modes)
        except vault.BadSecret as e:
            raise Invalid(str(e)) from e
        self.keep("policy", done, key, stages=list(done.stages), modes=list(done.modes))
        return _meta(done, key)

    def grant(self, key: str, sent: Mapping[str, object]) -> Meta:
        return self._flip("grant", key, sent)

    def revoke(self, key: str, sent: Mapping[str, object]) -> Meta:
        return self._flip("revoke", key, sent)

    def _flip(self, act: str, key: str, sent: Mapping[str, object]) -> Meta:
        s = self.secret_of(sent, key)
        if s.tier != "global":
            raise Invalid("only a global secret is granted to a workspace")
        store = self.get()
        done = store.grant(s.name, key) if act == "grant" else store.revoke(s.name, key)
        self.keep(act, done, key)
        return _meta(done, key)

    def delete(self, key: str, sent: Mapping[str, object]) -> Deleted:
        s = self.secret_of(sent, key)
        self.get().delete(s.name, s.workspace)
        self.keep("delete", s, key)
        return {"deleted": s.name}


def routes(ctx: Ctx, store_of: StoreOf | None = None) -> Sequence[BaseRoute]:
    door = Door(ctx, _lazy(ctx, store_of))
    router = APIRouter()

    @router.get("/vault")
    async def page(request: Request) -> Response:
        query = dict(request.query_params)
        cwd = query.get("cwd", "")
        if not cwd:
            return HTMLResponse(_shell("Vault", "<p>Open the vault from a workspace.</p>"))
        key = door.key_of(cwd, must_be_on=False)
        mine, others = await asyncio.to_thread(door.rows, key)
        shown = _page(cwd, key, ctx.enabled(FEATURE, cwd), mine, others, query)
        return HTMLResponse(shown, headers={"Cache-Control": "no-store"})

    @router.get("/api/vault/secrets")
    async def secrets(request: Request) -> Secrets:
        """Metadata only: never a value, a length or a hash."""
        key = door.key_of(request.query_params.get("cwd", ""))
        mine, others = await asyncio.to_thread(door.rows, key)
        return {
            "workspace": key,
            "secrets": [_meta(s, key) for s in mine],
            "globals": [_meta(s, key) for s in others],
        }

    @router.get("/api/vault/leaks")
    async def leaks(request: Request) -> Leaks:
        """The secrets the last scan of a unit found in its work; names only."""
        key = door.key_of(request.query_params.get("cwd", ""))
        unit = request.query_params.get("unit", "")
        return {"unit": unit, "names": await asyncio.to_thread(door.leaks, key, unit)}

    @router.post("/api/vault/secrets")
    async def put(request: Request) -> Response:
        """The one door a value goes in by: a form post, answered with a redirect that carries
        the name and never the value. It makes the secret or replaces its value."""
        raw = await request.body()
        if len(raw) > MAX_FORM:
            raise Invalid("that is too big for a secret")
        form = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
        cwd = _one(form, "cwd")
        key = door.key_of(cwd)
        value = _one(form, "value").encode()
        if not value:
            raise Invalid("a secret needs a value")
        try:
            name = await asyncio.to_thread(door.save, key, form, value)
        except vault.BadSecret as e:
            return _back(cwd, error=str(e))
        return _back(cwd, saved=name, short="1" if len(value) < SHORT_BYTES else "")

    async def act[T](
        request: Request, do: Callable[[str, Mapping[str, object]], T], on: bool = True
    ) -> T:
        sent = await body(request)
        key = door.key_of(str(sent.get("cwd", "")), must_be_on=on)
        return await asyncio.to_thread(do, key, sent)

    @router.post("/api/vault/policy")
    async def policy_of(request: Request) -> Meta:
        return await act(request, door.policy)

    @router.post("/api/vault/grant")
    async def grant(request: Request) -> Meta:
        return await act(request, door.grant)

    # A revoke, like a delete, still works with the vault off.
    @router.post("/api/vault/revoke")
    async def revoke(request: Request) -> Meta:
        return await act(request, door.revoke, on=False)

    @router.post("/api/vault/delete")
    async def delete(request: Request) -> Deleted:
        return await act(request, door.delete, on=False)

    return router.routes


# A *Vault* link in the top bar, and on an open unit a line naming what the guard found in its
# work. The address's `ws` is a folder name; `/api/workspaces` says where it is.
_JS = """
(function () {
  if (window.__coscc_vault) return;
  window.__coscc_vault = true;
  var listed = null;
  function here() {
    listed = listed || window.coscc.api("/api/workspaces")
      .then(function (r) { return r.ok ? r.json() : {}; })
      .then(function (j) { return j.workspaces || []; })
      .catch(function () { return []; });
    var ws = new URLSearchParams(window.location.search).get("ws");
    return listed.then(function (list) {
      return list.filter(function (w) { return w.name === ws; })[0] || list[0] || null;
    });
  }
  window.coscc.slot("slot-topbar", function (el) {
    var a = document.createElement("a");
    a.href = "/vault";
    a.textContent = "Vault";
    a.style.cssText = "color:var(--gray-12);font-size:14px;text-decoration:none";
    a.onclick = function (e) {
      e.preventDefault();
      here().then(function (w) {
        window.location.href = w ? "/vault?cwd=" + encodeURIComponent(w.path) : "/vault";
      });
    };
    el.textContent = "";
    el.appendChild(a);
  });
  window.coscc.slot("slot-unit", function (el) {
    var id = new URLSearchParams(window.location.search).get("id");
    if (!id) return;
    here().then(function (w) {
      if (!w) return null;
      var url = "/api/vault/leaks?cwd=" + encodeURIComponent(w.path) + "&unit=" + encodeURIComponent(id);
      return window.coscc.api(url).then(function (r) { return r.ok ? r.json() : null; });
    }).then(function (j) {
      el.textContent = "";
      if (!j || !j.names || !j.names.length) return;
      var p = document.createElement("p");
      p.setAttribute("role", "status");
      p.style.cssText = "color:var(--red-11);margin:8px 0 0";
      p.textContent = "It cannot go out: " + j.names.join(", ") + " appears in its work.";
      el.appendChild(p);
    }).catch(function () {});
  });
})();
"""

PLUGIN = Plugin(
    "vault",
    routes=routes,
    scripts=(_JS,),
    tables=vault.TABLES,
    agent=agent,
)
