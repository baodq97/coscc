"""The vault's page: plain HTML a person keeps the secrets on, framed by the studio.

Only metadata goes out here; a value comes in only through the form `vault.py`'s
`POST /api/vault/secrets` reads. `coscc/features/vault.md` says what the page is not.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from pathlib import Path

from coscc import vault


# The studio's tokens (`coscc/ui.py`: slate, iris, radius large), copied from the Radix CSS the
# page build ships, light and dark. A feature cannot link that CSS (its name carries a hash), so
# a theme change in `ui.py` is made here too.
_CSS = """
:root { --s1:#fcfcfd; --s2:#f9f9fb; --s3:#f0f0f3; --s4:#e8e8ec; --s6:#d9d9e0; --s7:#cdced6;
  --s11:#60646c; --s12:#1c2024; --i3:#f0f1fe; --i4:#e6e7ff; --i8:#9b9ef0; --i9:#5b5bd6;
  --i10:#5151cd; --i11:#5753c6; --r3:#feebec; --r6:#fdbdbe; --r11:#ce2c31; --g3:#e6f6eb;
  --g6:#adddc0; --g11:#218358; --a3:#fff7c2; --a6:#f3d673; --a11:#ab6400; color-scheme: light; }
.dark { --s1:#111113; --s2:#18191b; --s3:#212225; --s4:#272a2d; --s6:#363a3f; --s7:#43484e;
  --s11:#b0b4ba; --s12:#edeef0; --i3:#202248; --i4:#262a65; --i8:#5958b1; --i9:#5b5bd6;
  --i10:#6e6ade; --i11:#b1a9ff; --r3:#3b1219; --r6:#72232d; --r11:#ff9592; --g3:#132d21;
  --g6:#20573e; --g11:#3dd68c; --a3:#302008; --a6:#5c3d05; --a11:#ffca16; color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; padding: 0 0 48px; background: var(--s1);
  color: var(--s12); font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI",
  Roboto, "Helvetica Neue", Arial, sans-serif; }
h1 { font-size: 28px; font-weight: 500; letter-spacing: -0.02em; margin: 0; }
h2 { font-size: 17px; font-weight: 500; margin: 32px 0 12px; }
a { color: var(--i11); }
.muted { color: var(--s11); font-size: 14px; margin: 4px 0 0; }
.note { border: 1px solid; border-radius: 10px; padding: 10px 14px; margin: 16px 0 0; }
.ok { background: var(--g3); border-color: var(--g6); color: var(--g11); }
.warn { background: var(--a3); border-color: var(--a6); color: var(--a11); }
.bad { background: var(--r3); border-color: var(--r6); color: var(--r11); }
.head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.name { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-weight: 600;
  overflow-wrap: anywhere; }
td .muted { display: block; margin: 2px 0 0; }
.tag { font-size: 12px; background: var(--s3); color: var(--s11); border-radius: 999px;
  padding: 1px 8px; margin-left: 6px; white-space: nowrap; }
.warn-text { color: var(--a11); }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
table { width: 100%; border-collapse: collapse; margin-top: 20px; font-size: 14px; }
th { text-align: left; white-space: nowrap; font-weight: 500; color: var(--s11); font-size: 13px;
  border-bottom: 1px solid var(--s6); padding: 8px 10px; }
td { border-bottom: 1px solid var(--s4); padding: 10px; vertical-align: top; }
td.acts, td.short { white-space: nowrap; }
td.acts { text-align: right; }
td.acts button { margin-left: 6px; padding: 4px 10px; font-size: 13px; }
body.alone { padding: 24px 16px 48px; }
@media (min-width: 641px) { body.alone { padding: 32px 32px 48px; } }
@media (max-width: 640px) {
  thead { display: none; }
  table, tbody, tr, td { display: block; }
  tr { background: var(--s2); border: 1px solid var(--s6); border-radius: 14px; padding: 6px 12px;
    margin-bottom: 12px; }
  td { border: 0; padding: 6px 0; }
  td[data-th]:not([data-th=Name])::before { content: attr(data-th) ": "; color: var(--s11); }
  td.acts, td.short { white-space: normal; }
  td.acts { text-align: left; display: flex; flex-wrap: wrap; gap: 6px; }
  td.acts button { margin: 0; }
}
dialog { border: 1px solid var(--s6); border-radius: 14px; padding: 20px 22px; width: min(560px, 94vw);
  max-height: calc(100vh - 32px); overflow: auto;
  background: var(--s1); color: var(--s12); }
dialog::backdrop { background: rgb(0 0 0 / 0.35); }
dialog h2 { margin: 0 0 14px; }
.form { display: grid; gap: 14px; }
.form label.field { display: grid; gap: 4px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; align-items: center; flex-wrap: wrap; }
.actions .muted { width: 100%; text-align: right; }
fieldset { border: 1px solid var(--s6); border-radius: 10px; padding: 10px 12px; margin: 0;
  min-width: 0; display: grid; gap: 6px; }
legend, .label { font-size: 13px; font-weight: 500; color: var(--s11); }
.checks { display: flex; flex-wrap: wrap; gap: 4px 14px; margin: 0 0 4px; }
.checks label { white-space: nowrap; }
input[type=text], select, textarea { width: 100%; font: inherit; color: var(--s12);
  background: var(--s1); border: 1px solid var(--s7); border-radius: 8px; padding: 7px 10px; }
textarea { font-family: ui-monospace, monospace; -webkit-text-security: disc; }
input:focus-visible, select:focus-visible, textarea:focus-visible, button:focus-visible { outline: 2px solid var(--i8); outline-offset: 1px; }
button { font: inherit; font-size: 14px; font-weight: 500; border: 0; border-radius: 8px;
  padding: 6px 14px; cursor: pointer; background: var(--i3); color: var(--i11); }
button:hover { background: var(--i4); }
button.primary { background: var(--i9); color: white; }
button.primary:hover { background: var(--i10); }
button.danger-btn { background: var(--r3); color: var(--r11); }
button:disabled { opacity: 0.5; cursor: not-allowed; }
"""

# Before the first paint: the studio's choice (`localStorage["cos-theme"]`, `light` or `dark`) as
# the class Radix reads, followed again when the studio changes it.
_MODE_JS = """
(function () {
  function apply() {
    var t = null;
    try { t = localStorage.getItem("cos-theme"); } catch (e) {}
    if (t !== "light" && t !== "dark")
      t = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    document.documentElement.classList.remove("light", "dark");
    document.documentElement.classList.add(t);
  }
  apply();
  window.addEventListener("storage", apply);
})();
"""

_PAGE_JS = """
(function () {
  // The studio frame already pads the page; alone, it pads itself.
  if (window.top === window) {
    document.getElementById("back").hidden = false;
    document.body.classList.add("alone");
  }
  function boxes(row, name) {
    var all = row.querySelectorAll("input[name=" + name + "]:checked");
    return Array.prototype.map.call(all, function (x) { return x.value; });
  }
  function say(text) {
    var p = document.createElement("p");
    p.className = "note bad";
    p.textContent = text;
    var msg = document.getElementById("msg");
    msg.textContent = "";
    msg.appendChild(p);
  }
  document.addEventListener("click", function (e) {
    if (!e.target.closest) return;
    var open = e.target.closest("button[data-open]");
    if (open) return document.getElementById(open.getAttribute("data-open")).showModal();
    var shut = e.target.closest("button[data-close]");
    if (shut) return shut.closest("dialog").close();
    var b = e.target.closest("button[data-act]");
    if (!b) return;
    var sent = {cwd: document.body.getAttribute("data-cwd"),
                name: b.getAttribute("data-name"), tier: b.getAttribute("data-tier")};
    var ask = b.getAttribute("data-ask");
    if (ask && !window.confirm(ask)) return;
    if (b.getAttribute("data-act") === "policy") {
      sent.stages = boxes(b.closest("[data-row]"), "stage");
      sent.modes = boxes(b.closest("[data-row]"), "mode");
    }
    fetch("/api/vault/" + b.getAttribute("data-act"), {
      method: "POST", credentials: "same-origin",
      headers: {"content-type": "application/json"}, body: JSON.stringify(sent)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (r.ok) window.location.reload();
        else {
          var d = b.closest("dialog");
          if (d) d.close();
          say(j.error || "That did not work.");
        }
      });
    });
  });
})();
"""

# The store's `NAME` without its tier, escaped so the pattern holds under the `v` flag too.
NAME_PATTERN = r"[a-z0-9][a-z0-9._\-]*"
NO_AGE = "age is not installed on this machine, so a value cannot be saved: install age."


def _e(text: object) -> str:
    return html.escape(str(text), quote=True)


def shell(inner: str, cwd: str = "", action: str = "") -> str:
    data = f' data-cwd="{_e(cwd)}"' if cwd else ""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Vault</title><script>{_MODE_JS}</script><style>{_CSS}</style></head>"
        f'<body{data}><p><a id="back" href="/" hidden>Back to the studio</a></p>'
        f'<div class="head"><h1>Vault</h1>{action}</div>{inner}'
        f"<script>{_PAGE_JS}</script></body></html>"
    )


def _checks(name: str, options: Sequence[str], on: Sequence[str], disabled: bool = False) -> str:
    dis = " disabled" if disabled else ""
    boxes = "".join(
        f'<label><input type="checkbox" name="{name}" value="{_e(o)}"'
        f"{' checked' if o in on else ''}{dis}> {_e(o)}</label>"
        for o in options
    )
    return f'<div class="checks">{boxes}</div>'


# Not a password input: a browser strips the line breaks out of one, and a key is many lines.
_VALUE_BOX = (
    '<textarea name="value" rows="3" required autocomplete="off" spellcheck="false" '
    'autocapitalize="off" autocorrect="off"{}></textarea>'
)


def _submit(label: str, age: bool) -> str:
    if age:
        return f'<button type="submit" class="primary">{label}</button>'
    return (
        f'<button type="submit" class="primary" disabled>{label}</button>'
        '<p class="muted">Install age to save a value.</p>'
    )


def _dialog(id_: str, title: str, body: str) -> str:
    """A native modal: `data-open` shows it, `data-close` and Esc close it."""
    return (
        f'<dialog id="{id_}" aria-labelledby="{id_}-t"><h2 id="{id_}-t">{title}</h2>{body}</dialog>'
    )


def _opener(id_: str, label: str, kind: str = "", disabled: bool = False) -> str:
    css = f' class="{kind}"' if kind else ""
    return (
        f'<button type="button"{css} data-open="{id_}"{" disabled" if disabled else ""}>'
        f"{_e(label)}</button>"
    )


_CANCEL = '<button type="button" data-close>Cancel</button>'


def _button(act: str, s: vault.Secret, label: str, ask: str = "", kind: str = "") -> str:
    said = f' data-ask="{_e(ask)}"' if ask else ""
    css = f' class="{kind}"' if kind else ""
    return (
        f'<button type="button"{css} data-act="{act}" data-name="{_e(s.name)}" '
        f'data-tier="{_e(s.tier)}"{said}>{_e(label)}</button>'
    )


def _access_dialog(i: int, s: vault.Secret) -> str:
    modes = '<p class="muted">ssh only</p>' if s.broker else _checks("mode", vault.MODES, s.modes)
    return _dialog(
        f"access-{i}",
        f"Access of {_e(s.name)}",
        '<div class="form" data-row>'
        f'<span class="label">Used by</span>{_checks("stage", vault.VAULT_STAGES, s.stages)}'
        f'<span class="label">Passed as</span>{modes}'
        f'<div class="actions">{_CANCEL}{_button("policy", s, "Save access", kind="primary")}'
        "</div></div>",
    )


def _value_dialog(i: int, cwd: str, s: vault.Secret, age: bool) -> str:
    return _dialog(
        f"value-{i}",
        f"Replace the value of {_e(s.name)}",
        '<form method="post" action="/api/vault/secrets" class="form">'
        f'<input type="hidden" name="cwd" value="{_e(cwd)}">'
        f'<input type="hidden" name="tier" value="{_e(s.tier)}">'
        f'<input type="hidden" name="name" value="{_e(s.name)}">'
        + _VALUE_BOX.format(f' aria-label="New value of {_e(s.name)}"')
        + f'<div class="actions">{_CANCEL}{_submit("Replace value", age)}</div></form>',
    )


def _row(i: int, s: vault.Secret, on: bool) -> str:
    tags = '<span class="tag">ssh only</span>' if s.broker else ""
    desc = f'<span class="muted">{_e(s.description)}</span>' if s.description else ""
    modes = "ssh" if s.broker else ", ".join(s.modes)
    acts = []
    if on:
        acts.append(_opener(f"access-{i}", "Edit access"))
        acts.append(_opener(f"value-{i}", "Replace value"))
    if s.tier == "global":
        acts.append(_button("revoke", s, "Revoke", kind="danger-btn"))
    ask = "Delete this secret for every workspace?" if s.tier == "global" else "Delete this secret?"
    acts.append(_button("delete", s, "Delete", ask, "danger-btn"))
    return (
        f'<tr><td data-th="Name"><span class="name">{_e(s.name)}</span>{tags}{desc}</td>'
        f'<td class="short" data-th="Kept for">{"every workspace" if s.tier == "global" else "this workspace"}</td>'
        f'<td class="short" data-th="Used by">{_e(", ".join(s.stages)) or "—"}</td>'
        f'<td class="short" data-th="Passed as">{_e(modes) or "—"}</td>'
        f'<td class="short" data-th="Value">{"set" if s.has_value else "<span class=warn-text>not set</span>"}</td>'
        f'<td class="acts">{"".join(acts)}</td></tr>'
    )


def _table(head: Sequence[str], rows: str) -> str:
    th = "".join(f"<th>{h}</th>" for h in head)
    return f'<table><thead><tr>{th}<th><span class="sr">Actions</span></th></tr></thead><tbody>{rows}</tbody></table>'


def _add_dialog(cwd: str, age: bool) -> str:
    return _dialog(
        "add",
        "Add a secret",
        '<form method="post" action="/api/vault/secrets" class="form add">'
        f'<input type="hidden" name="cwd" value="{_e(cwd)}">'
        "<fieldset><legend>Secret</legend>"
        '<label class="field"><span class="label">Name</span><input type="text" name="name" '
        f'required pattern="{NAME_PATTERN}" maxlength="64" '
        'title="Lower-case letters, digits, dots, dashes and underscores, up to 64."></label>'
        '<label class="field"><span class="label">Kept for</span><select name="tier">'
        '<option value="ws">this workspace</option>'
        '<option value="global">every workspace it is granted to</option></select></label>'
        '<label class="field"><span class="label">Description</span>'
        '<input type="text" name="description"></label></fieldset>'
        '<fieldset><legend>Access</legend><span class="label">Used by</span>'
        f"{_checks('stage', vault.VAULT_STAGES, ('impl',))}"
        f'<span class="label">Passed as</span>{_checks("mode", vault.MODES, ("env", "file"))}'
        '<label><input type="checkbox" name="broker" value="1"> Broker: passed as ssh only'
        "</label></fieldset>"
        '<fieldset><legend>Value</legend><label class="field"><span class="label">Value</span>'
        f"{_VALUE_BOX.format('')}</label></fieldset>"
        f'<div class="actions">{_CANCEL}{_submit("Add secret", age)}</div></form>',
    )


def _grants(key: str, globals_: list[vault.Secret]) -> str:
    rows = "".join(
        f'<tr><td data-th="Name"><span class="name">{_e(s.name)}</span>'
        f'<span class="muted">{_e(s.description)}</span></td>'
        f'<td class="acts">{_button("grant", s, "Grant")}</td></tr>'
        for s in globals_
        if key not in s.granted
    )
    if not rows:
        return ""
    return f"<h2>Global secrets not granted here</h2>{_table(('Name',), rows)}"


def _notes(query: dict[str, str], age: bool) -> str:
    said = []
    if not age:
        said.append(("bad", NO_AGE))
    if query.get("saved"):
        said.append(("ok", f"Saved {_e(query['saved'][:80])}."))
    if query.get("short"):
        said.append(("warn", "That value is short, so masking it may hide other text."))
    if query.get("error"):
        said.append(("bad", _e(query["error"][:200])))
    return "".join(f'<p class="note {kind}">{s}</p>' for kind, s in said)


def page(
    cwd: str,
    key: str,
    on: bool,
    mine: list[vault.Secret],
    globals_: list[vault.Secret],
    query: dict[str, str],
    age: bool = True,
) -> str:
    where = f'<p class="muted">{_e(Path(key).name)}</p>'
    head = ("Name", "Kept for", "Used by", "Passed as", "Value")
    if mine:
        table = _table(head, "".join(_row(i, s, on) for i, s in enumerate(mine)))
    else:
        table = '<p class="muted">No secrets here yet.</p>'
    dialogs = ""
    action = ""
    if on:
        action = _opener("add", "Add secret", "primary")
        dialogs = _add_dialog(cwd, age) + "".join(
            _access_dialog(i, s) + _value_dialog(i, cwd, s, age) for i, s in enumerate(mine)
        )
        tail = _grants(key, globals_)
    else:
        tail = (
            '<p class="note warn">Vault is off for this workspace, so secrets can only be '
            "revoked or deleted.</p>"
        )
    inner = f'{where}<div id="msg" role="status">{_notes(query, age)}</div>{table}{tail}{dialogs}'
    return shell(inner, cwd, action)
