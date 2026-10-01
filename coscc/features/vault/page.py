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
body { margin: 0 auto; max-width: 960px; padding: 24px 16px 48px; background: var(--s1);
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
ul.list { list-style: none; margin: 0; padding: 0; display: grid; gap: 12px; }
.card { background: var(--s2); border: 1px solid var(--s6); border-radius: 14px; padding: 16px; }
.name { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-weight: 600;
  overflow-wrap: anywhere; }
.tag { font-size: 12px; background: var(--s3); color: var(--s11); border-radius: 999px;
  padding: 1px 8px; margin-left: 6px; white-space: nowrap; }
.groups { display: grid; gap: 12px; margin-top: 12px; }
@media (min-width: 641px) { .groups { grid-template-columns: 1.5fr 1fr auto; align-items: start; } }
fieldset, details.group { border: 1px solid var(--s6); border-radius: 10px; padding: 10px 12px;
  margin: 0; min-width: 0; }
legend, summary, .label { font-size: 13px; font-weight: 500; color: var(--s11); }
summary { cursor: pointer; }
.danger { display: flex; gap: 8px; flex-wrap: wrap; align-items: start; }
.checks { display: flex; flex-wrap: wrap; gap: 4px 14px; margin: 2px 0 8px; }
.checks label { white-space: nowrap; }
form.add { max-width: 640px; display: grid; gap: 14px; }
form.add label.field { display: grid; gap: 4px; }
input[type=text], select, textarea { width: 100%; font: inherit; color: var(--s12);
  background: var(--s1); border: 1px solid var(--s7); border-radius: 8px; padding: 7px 10px; }
textarea { font-family: ui-monospace, monospace; -webkit-text-security: disc; }
input:focus-visible, select:focus-visible, textarea:focus-visible, button:focus-visible,
summary:focus-visible { outline: 2px solid var(--i8); outline-offset: 1px; }
button { font: inherit; font-size: 14px; font-weight: 500; border: 0; border-radius: 8px;
  padding: 6px 14px; cursor: pointer; background: var(--i3); color: var(--i11); }
button:hover { background: var(--i4); }
button.primary { background: var(--i9); color: white; }
button.primary:hover { background: var(--i10); }
button.danger-btn { background: var(--r3); color: var(--r11); }
button:disabled { opacity: 0.5; cursor: not-allowed; }
form.replace { display: grid; gap: 8px; margin-top: 8px; }
"""

# Before the first paint: the studio's choice (`localStorage.theme`, `light`/`dark`/`system`) as
# the class Radix reads, followed again when the studio changes it.
_MODE_JS = """
(function () {
  function apply() {
    var t = null;
    try { t = localStorage.getItem("theme"); } catch (e) {}
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
  if (window.top === window) document.getElementById("back").hidden = false;
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
    var b = e.target.closest ? e.target.closest("button[data-act]") : null;
    if (!b) return;
    var sent = {cwd: document.body.getAttribute("data-cwd"),
                name: b.getAttribute("data-name"), tier: b.getAttribute("data-tier")};
    var ask = b.getAttribute("data-ask");
    if (ask && !window.confirm(ask)) return;
    if (b.getAttribute("data-act") === "policy") {
      sent.stages = boxes(b.closest("li"), "stage");
      sent.modes = boxes(b.closest("li"), "mode");
    }
    fetch("/api/vault/" + b.getAttribute("data-act"), {
      method: "POST", credentials: "same-origin",
      headers: {"content-type": "application/json"}, body: JSON.stringify(sent)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (r.ok) window.location.reload();
        else say(j.error || "That did not work.");
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


def shell(inner: str, cwd: str = "") -> str:
    data = f' data-cwd="{_e(cwd)}"' if cwd else ""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Vault</title><script>{_MODE_JS}</script><style>{_CSS}</style></head>"
        f'<body{data}><p><a id="back" href="/" hidden>Back to the studio</a></p>'
        f"<h1>Vault</h1>{inner}<script>{_PAGE_JS}</script></body></html>"
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


def _replace_form(cwd: str, s: vault.Secret, age: bool) -> str:
    return (
        '<details class="group"><summary>Replace value</summary>'
        '<form method="post" action="/api/vault/secrets" class="replace">'
        f'<input type="hidden" name="cwd" value="{_e(cwd)}">'
        f'<input type="hidden" name="tier" value="{_e(s.tier)}">'
        f'<input type="hidden" name="name" value="{_e(s.name)}">'
        + _VALUE_BOX.format(f' aria-label="New value of {_e(s.name)}"')
        + _submit("Replace value", age)
        + "</form></details>"
    )


def _button(act: str, s: vault.Secret, label: str, ask: str = "", kind: str = "") -> str:
    said = f' data-ask="{_e(ask)}"' if ask else ""
    css = f' class="{kind}"' if kind else ""
    return (
        f'<button type="button"{css} data-act="{act}" data-name="{_e(s.name)}" '
        f'data-tier="{_e(s.tier)}"{said}>{_e(label)}</button>'
    )


def _row(cwd: str, s: vault.Secret, on: bool, age: bool) -> str:
    tags = ""
    if not s.has_value:
        tags += '<span class="tag">no value</span>'
    if s.broker:
        tags += '<span class="tag">ssh only</span>'
    modes = (
        '<p class="muted">ssh only</p>'
        if s.broker
        else _checks("mode", vault.MODES, s.modes, not on)
    )
    access = (
        '<fieldset class="group"><legend>Access</legend><span class="label">Used by</span>'
        f"{_checks('stage', vault.VAULT_STAGES, s.stages, not on)}"
        f'<span class="label">Passed as</span>{modes}'
        f"{_button('policy', s, 'Save access') if on else ''}</fieldset>"
    )
    danger = []
    if s.tier == "global":
        danger.append(_button("revoke", s, "Revoke", kind="danger-btn"))
    ask = "Delete this secret for every workspace?" if s.tier == "global" else "Delete this secret?"
    danger.append(_button("delete", s, "Delete", ask, "danger-btn"))
    desc = f'<p class="muted">{_e(s.description)}</p>' if s.description else ""
    return (
        f'<li class="card"><span class="name">{_e(s.name)}</span>{tags}{desc}'
        f'<div class="groups">{access}{_replace_form(cwd, s, age) if on else ""}'
        f'<div class="danger">{"".join(danger)}</div></div></li>'
    )


def _create_form(cwd: str, age: bool) -> str:
    return (
        '<h2>Add a secret</h2><form method="post" action="/api/vault/secrets" class="add">'
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
        f"<div>{_submit('Add secret', age)}</div></form>"
    )


def _grants(key: str, globals_: list[vault.Secret]) -> str:
    rows = "".join(
        f'<li class="card"><span class="name">{_e(s.name)}</span>'
        f'<p class="muted">{_e(s.description)}</p>{_button("grant", s, "Grant")}</li>'
        for s in globals_
        if key not in s.granted
    )
    if not rows:
        return ""
    return f'<h2>Global secrets not granted here</h2><ul class="list">{rows}</ul>'


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
    if mine:
        rows = "".join(_row(cwd, s, on, age) for s in mine)
        table = f'<h2>Secrets</h2><ul class="list">{rows}</ul>'
    else:
        table = '<p class="muted">No secrets here yet.</p>'
    if on:
        tail = _create_form(cwd, age) + _grants(key, globals_)
    else:
        tail = (
            '<p class="note warn">Vault is off for this workspace, so secrets can only be '
            "revoked or deleted.</p>"
        )
    inner = f'{where}<div id="msg" role="status">{_notes(query, age)}</div>{table}{tail}'
    return shell(inner, cwd)
