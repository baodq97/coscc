
(function () {
  if (window.__coscc_scan) return;
  window.__coscc_scan = true;
  var C = window.coscc, filter = "pending";
  var CHIP = {pending: "amber", accepted: "grass", dismissed: "gray"};
  var KIND = {"refused": "Refused", "ci-red": "CI red", "rerun": "Rerun",
    "review-round": "Review round", "impl-draft": "Impl draft", "integrate": "Integrate"};
  // The workspace the slot is drawn for, from the slot element the page gives.
  function here() {
    var cwd = slotEl && slotEl.dataset.cwd;
    return Promise.resolve(cwd ? {path: cwd, name: slotEl.dataset.workspace || ""} : null);
  }
  function el(tag, css, text) {
    var e = document.createElement(tag);
    if (css) e.style.cssText = css;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function chip(state) {
    var c = CHIP[state] || "gray";
    return el("span", "display:inline-block;border-radius:999px;padding:1px 8px;font-size:12px;" +
      "background:var(--" + c + "-3);color:var(--" + c + "-11);flex-shrink:0;min-width:88px;" +
      "white-space:nowrap;text-align:center", state);
  }
  function button(text, soft) {
    var b = el("button", "border-radius:6px;padding:4px 10px;font-size:13px;cursor:pointer;" +
      "border:1px solid var(--" + (soft ? "gray-6" : "accent-9") + ");background:var(--" +
      (soft ? "gray-2" : "accent-9") + ");color:var(--" + (soft ? "gray-12" : "accent-contrast") + ")", text);
    b.type = "button";
    return b;
  }
  function input(value, label) {
    var i = el("input", "flex:1;min-width:160px;border:1px solid var(--gray-6);border-radius:6px;" +
      "padding:4px 8px;font-size:13px;background:var(--gray-1);color:var(--gray-12)");
    i.value = value;
    i.setAttribute("aria-label", label);
    return i;
  }
  function muted(text) { return el("div", "color:var(--gray-11);font-size:13px", text); }
  function act(w, p, sent, err) {
    sent.cwd = w.path;
    return C.api("/api/scan/proposals/" + p.id, {method: "POST",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify(sent)})
      .then(function (r) { return r.json().then(function (j) { return [r.ok, j]; }); })
      .then(function (got) {
        if (got[0]) { window.location.hash = "proposal-" + p.id; return draw(); }
        err.textContent = got[1].detail || got[1].error || "That did not work.";
      });
  }
  function sources(p) {
    var t = el("table", "width:100%;border-collapse:collapse;font-size:13px;margin:8px 0");
    var head = el("tr");
    ["Kind", "Unit", "When"].forEach(function (h) {
      head.appendChild(el("th", "text-align:left;color:var(--gray-11);font-weight:500;" +
        "padding:4px 8px 4px 0;border-bottom:1px solid var(--gray-5)", h));
    });
    t.appendChild(head);
    p.sources.forEach(function (s) {
      var r = el("tr");
      [KIND[s.kind] || s.kind, s.unit || "-", C.ago(s.at)].forEach(function (v) {
        r.appendChild(el("td", "padding:4px 8px 4px 0;border-bottom:1px solid var(--gray-4)", v));
      });
      t.appendChild(r);
    });
    return t;
  }
  function actions(w, p) {
    var box = el("div", "display:flex;flex-direction:column;gap:8px;margin-top:8px");
    var err = el("div", "color:var(--red-11);font-size:13px");
    var one = el("div", "display:flex;gap:8px;align-items:center;flex-wrap:wrap");
    var slug = input(p.slug, "Slug of the new unit");
    var ok = button("Accept");
    ok.onclick = function () { act(w, p, {action: "accept", slug: slug.value}, err); };
    one.appendChild(slug); one.appendChild(ok);
    var two = el("div", "display:flex;gap:8px;align-items:center;flex-wrap:wrap");
    var why = input("", "Why it is dismissed");
    why.placeholder = "Why it is dismissed";
    var no = button("Dismiss", true);
    var hint = muted("Dismiss needs a reason.");
    function check() {
      var empty = !why.value.trim();
      no.disabled = empty; no.style.opacity = empty ? "0.5" : "1";
      hint.style.display = empty ? "block" : "none";
    }
    why.oninput = check; check();
    no.onclick = function () { act(w, p, {action: "dismiss", reason: why.value}, err); };
    two.appendChild(why); two.appendChild(no);
    box.appendChild(one); box.appendChild(two); box.appendChild(hint); box.appendChild(err);
    return box;
  }
  function row(w, p) {
    var d = el("details", "border-bottom:1px solid var(--gray-5)");
    d.id = "proposal-" + p.id;
    var s = el("summary", "display:flex;gap:12px;align-items:center;padding:10px 0;cursor:pointer;" +
      "flex-wrap:wrap");
    s.appendChild(chip(p.state));
    s.appendChild(el("span", "flex:1;min-width:160px;color:var(--gray-12)", p.title));
    s.appendChild(el("span", "color:var(--gray-11);font-size:13px;width:64px", p.type));
    s.appendChild(el("span", "color:var(--gray-11);font-size:13px;width:84px",
      p.sources.length + (p.sources.length === 1 ? " source" : " sources")));
    s.appendChild(el("span", "color:var(--gray-11);font-size:13px;width:110px", C.ago(p.at)));
    d.appendChild(s);
    var body = el("div", "padding:0 0 14px");
    body.appendChild(el("p", "margin:4px 0;white-space:pre-wrap;color:var(--gray-12);font-size:14px",
      p.problem));
    body.appendChild(sources(p));
    if (p.state === "pending") body.appendChild(actions(w, p));
    if (p.state === "accepted") body.appendChild(muted("Accepted as " + p.unit + ", " + C.ago(p.decided) + "."));
    if (p.state === "dismissed") body.appendChild(muted("Dismissed " + C.ago(p.decided) + ": " + p.reason));
    d.appendChild(body);
    return d;
  }
  function last(runs) {
    if (!runs.length) return "No scan yet.";
    var r = runs[0], cost = "$" + r.cost_usd.toFixed(2);
    if (r.outcome === "skipped") return "Last scan " + C.ago(r.at) + ": nothing new, $0.00.";
    if (r.outcome === "failed") return "Last scan " + C.ago(r.at) + " failed, " + cost + ".";
    return "Last scan " + C.ago(r.at) + ": " + r.taken + " interventions read, " + cost + ".";
  }
  var slotEl = null;
  function draw() {
    var target = slotEl;
    return here().then(function (w) {
      if (!w || !target) return;
      return C.api("/api/scan/proposals?cwd=" + encodeURIComponent(w.path))
        .then(function (r) { return r.ok ? r.json() : {on: false}; })
        .then(function (j) { paint(target, w, j); });
    }).catch(function () {});
  }
  function paint(target, w, j) {
    target.textContent = "";
    if (!j.on) return;
    var panel = el("section", "background:var(--gray-2);border:1px solid var(--gray-5);" +
      "border-radius:14px;padding:22px;width:100%;box-sizing:border-box");
    panel.id = "scan-proposals";
    var head = el("div", "display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px");
    head.appendChild(el("h3", "margin:0;font-size:18px;font-weight:500;flex:1", "Proposals"));
    var go = button(j.scanning ? "Scanning" : "Scan now", true);
    go.id = "scan-now";
    go.disabled = !!j.scanning;
    var said = muted(j.consequence);
    var err = el("div", "color:var(--red-11);font-size:13px");
    go.onclick = function () {
      go.disabled = true; go.textContent = "Scanning";
      C.api("/api/scan?cwd=" + encodeURIComponent(w.path), {method: "POST"})
        .then(function (r) { return r.json().then(function (b) { return [r.ok, b]; }); })
        .then(function (got) { if (!got[0]) err.textContent = got[1].detail || "The scan did not run."; })
        .catch(function () {}).then(draw);
    };
    head.appendChild(go);
    panel.appendChild(head);
    var line = el("div", "display:flex;gap:16px;flex-wrap:wrap;margin-bottom:6px");
    line.appendChild(said);
    line.appendChild(muted(last(j.runs)));
    panel.appendChild(line);
    if (j.note) panel.appendChild(el("div", "color:var(--amber-11);font-size:13px;margin-bottom:6px", j.note));
    panel.appendChild(err);
    var tabs = el("div", "display:flex;gap:6px;margin:10px 0;flex-wrap:wrap");
    tabs.setAttribute("role", "group");
    tabs.setAttribute("aria-label", "Filter proposals by state");
    ["pending", "accepted", "dismissed", "all"].forEach(function (f) {
      var n = j.proposals.filter(function (p) { return f === "all" || p.state === f; }).length;
      var t = button(f.charAt(0).toUpperCase() + f.slice(1) + " " + n, f !== filter);
      t.setAttribute("aria-pressed", f === filter ? "true" : "false");
      t.onclick = function () { filter = f; paint(target, w, j); };
      tabs.appendChild(t);
    });
    panel.appendChild(tabs);
    var shown = j.proposals.filter(function (p) { return filter === "all" || p.state === filter; });
    shown.forEach(function (p) { panel.appendChild(row(w, p)); });
    if (!shown.length) panel.appendChild(muted(j.proposals.length ? "No proposal in this state." :
      "No proposal yet: a scan makes them from the run log."));
    target.appendChild(panel);
    var want = window.location.hash.slice(1);
    var open = want && document.getElementById(want);
    if (open && open.tagName === "DETAILS") { open.open = true; open.scrollIntoView({block: "start"}); }
  }
  C.slot("slot-backlog", function (target) {
    slotEl = target;
    var want = window.location.hash.match(/^#proposal-(\d+)$/);
    if (want) filter = "all";
    draw();
  });
})();
