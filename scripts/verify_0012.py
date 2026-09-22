#!/usr/bin/env python3
"""Proof for .cos/0012_installed-copy-runs-no-stage.

The claim in `intent.md`'s "Proposed outcome": on a machine with no checkout, an installed
copy answers the same as one run from the checkout. This file measures **the installed
copy** — its interpreter and its running service — and never this tree's `.venv`. A proof
that imported `coscc` from the checkout would pass on `v0.2.2`, which is the release this
unit exists because of.

    0  every claim held against the installed copy
    1  at least one did not
    2  the environment could not answer: no installed copy, no service, no `node`,
       no workspace

`scripts/verify_0003.py:8-14` explains why 2 is kept apart from 1, and it matters here for
the usual reason: "no coscc is installed on this machine" and "the installed coscc is
broken" are different facts, and the second is the only one this file is entitled to
report.

**What this proof cannot prove.** `spec.md` C5: the machine running it has `node`, because
this repository's `npm test` needs one — a machine that followed `docs/install.md` and
nothing else does not. So claim 5 checks that the prerequisite is *written down*, not that
the app works without it. The full version of that claim needs a separate target over SSH,
the way `scripts/verify_0011.py` takes `COS_PROOF_TARGET`. This file says so on stdout
rather than letting exit 0 imply more than it measured.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.proof_harness import EXIT_BROKEN, EXIT_ENV, EXIT_PASS, say  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
URL = os.environ.get("COS_URL", "http://127.0.0.1:8790").rstrip("/")

# The stage measured, and the floor it has to clear.
#
# **The floor is on the rules, not on the prompt, and that changed on 2026-09-23.**
# `spec.md` R3 set it at 18.000 characters of finished prompt, measured against a unit
# carrying a full `intent.md`. `0014` moved units into the product's store, so the unit
# this claim reaches is whatever that store holds -- on a fresh one, a prompt of 4.897
# characters that contained its rules in full still failed a floor built for a different
# input. The floor was measuring the unit's artifacts as much as the rules.
#
# What it measures now is the thing `0012` exists for: on `v0.2.2` every skill resolved to
# nothing and a step ran 4.569 characters short of its rules with no sign anything was
# missing (`coscc/runner.py:43-52`). So the claim asks the installed copy for its own
# rules and requires **every character of them** to be inside the prompt. That catches the
# original failure exactly, and it does not depend on which unit is at hand.
STAGE = "spec"
# A skill that resolved to almost nothing would pass the "is it all in there" test
# trivially, so the rules have to be a real size. 1.500 is below every skill in
# `.claude/skills/` -- the smallest is `write-idea/SKILL.md` at 1.974 bytes, measured
# 2026-09-23 with `wc -c` -- and far above the nothing that `v0.2.2` shipped.
RULES_FLOOR = 1_500


def installed_python() -> Path | None:
    """The interpreter of the installed copy, never this checkout's."""
    if env := os.environ.get("COSCC_PYTHON"):
        return Path(env) if Path(env).is_file() else None
    try:
        tools = subprocess.run(
            ["uv", "tool", "dir"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if tools.returncode != 0:
        return None
    candidate = Path(tools.stdout.strip()) / "coscc" / "bin" / "python"
    return candidate if candidate.is_file() else None


def in_installed(python: Path, program: str) -> tuple[bool, str]:
    """Run `program` under the installed interpreter, from a directory that is not the
    checkout.

    The `cwd` matters and is the whole reason this helper exists rather than a bare
    `subprocess.run`: Python puts the working directory on `sys.path` for `-c`, so running
    this from the repository root would import the *checkout's* `coscc` and measure the
    thing that was never broken. Measured 2026-09-22, that is exactly what happened on the
    first attempt to check this by hand.
    """
    try:
        done = subprocess.run(
            [str(python), "-c", program],
            capture_output=True, text=True, timeout=120, cwd="/",
        )
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    if done.returncode != 0:
        return False, (done.stderr or done.stdout).strip()[-600:]
    return True, done.stdout.strip()


def http_json(path: str) -> tuple[bool, object]:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(URL + path, timeout=30) as response:
            return True, json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        return False, e.read().decode(errors="replace")[:400]
    except (OSError, ValueError) as e:
        return False, str(e)


def gate_rows(store_root: str) -> tuple[bool, set]:
    """`(unit, stage, status)` as the harness reports it for a store root.

    **It is the store root, not the workspace, since `0014`.** Units moved out of the
    repository's tree and into the product's own directory so that nothing of coscc's
    lands in a repository a team shares (`0014 spec.md` R2). Pointed at the workspace,
    this claim compared the board's units against a *different* set of units and failed
    on every row — found 2026-09-23 by running this proof against the installed `0.4.0`.

    The root itself is taken from the board's own reply. That much is the app's answer and
    is not independent; what this claim measures is whether the two sides agree on the
    **statuses** of the units they both see, and for that the gate runs on its own.

    One derivation is applied here, and only one: an artifact the gate does not mention
    is `not started`. That is `coscc/board.py:76`, and `board.py:65-68` records it as a
    reading of the absence rather than something stored. Copying it is what keeps the
    comparison about the two sides agreeing instead of about who spells an empty cell how;
    copying anything *else* the app does would make this claim compare the app with itself.
    """
    script = REPO / ".claude" / "scripts" / "cos.mjs"
    try:
        done = subprocess.run(
            ["node", str(script), "--root", store_root, "status", "--json"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return False, {str(e)}
    if done.returncode != 0:
        return False, {(done.stderr or done.stdout).strip()[:300]}
    data = json.loads(done.stdout)
    return True, {
        (unit["name"], stage["file"].removesuffix(".md"), entry.get("status") or "not started")
        for unit in data.get("units", [])
        for stage in data.get("stages", [])
        for entry in [(unit.get("artifacts") or {}).get(stage["file"]) or {}]
    }


def board_rows(payload: dict) -> set:
    return {
        (unit["name"], row["stage"], row.get("status") or "")
        for unit in payload.get("units", [])
        for row in unit.get("stages", [])
    }


def main() -> int:
    python = installed_python()
    if python is None:
        print("no installed copy of coscc found — set COSCC_PYTHON or install it", file=sys.stderr)
        return EXIT_ENV
    if shutil.which("node") is None:
        print("no node on PATH — the comparison side of claim 2 cannot run", file=sys.stderr)
        return EXIT_ENV
    ok, health = http_json("/api/health")
    if not ok:
        print(f"no service answering at {URL}: {health}", file=sys.stderr)
        return EXIT_ENV
    ok, spaces = http_json("/api/workspaces")
    if not ok or not (spaces or {}).get("paths"):
        print(f"the service at {URL} has no workspace to measure", file=sys.stderr)
        return EXIT_ENV
    workspace = spaces["paths"][0]

    # `node` on *this* shell's PATH is not the question. The Board is read by the service,
    # and the service is a systemd user unit whose PATH is the systemd default. Measured
    # 2026-09-22: node was on the machine, under `~/.nvm/`, and the service could not see
    # it. That is a machine this proof cannot measure, not a release that is broken --
    # `scripts/verify_0003.py:8-14`, and the whole reason exit 2 exists.
    probed_ok, probed = http_json(f"/api/board?cwd={workspace}")
    if not probed_ok and "could not run node" in str(probed):
        print(
            f"the service at {URL} cannot run node — its PATH has none.\n"
            f"  {probed}\n"
            "  docs/install.md, under ## Prerequisites, says how to put one there.",
            file=sys.stderr,
        )
        return EXIT_ENV

    print(f"measuring {python}\n      against {URL}, workspace {workspace}\n")

    results = []

    # --- claim 1: the installed copy finds its own rules, outside this checkout ---
    ran, out = in_installed(python, (
        "import json;from coscc import harness;"
        "print(json.dumps({"
        "'root': str(harness.root()), 'script': str(harness.script()),"
        "'script_there': harness.script().is_file(),"
        "'skills': len(list(harness.skills_dir().glob('*/' + harness.SKILL_FILE))),"
        "'packaged': harness.is_packaged()}))"
    ))
    if not ran:
        results.append(say(False, "the installed copy resolves cos.mjs and its skills", out))
    else:
        found = json.loads(out)
        inside_checkout = Path(found["root"]) == REPO / ".claude" or str(REPO) in found["root"]
        good = found["script_there"] and found["skills"] > 0 and not inside_checkout
        results.append(say(
            good,
            f"the installed copy resolves cos.mjs and {found['skills']} skill(s), outside this checkout",
            f"root={found['root']} script_there={found['script_there']} "
            f"skills={found['skills']} inside_checkout={inside_checkout}",
        ))

    # --- claim 2: its board is the workspace's own gate ---
    got_board, payload = probed_ok, probed
    # `0014` moved the units; the board is what knows where they went.
    store_root = payload.get("workspace") or workspace
    got_gate, expected = gate_rows(store_root)
    if not got_board:
        results.append(say(False, "the board agrees with the gate in the workspace", str(payload)))
    elif not got_gate:
        results.append(say(False, "the board agrees with the gate in the workspace", str(expected)))
    elif not expected:
        # Nothing to compare is not the same as the two sides agreeing. A workspace with
        # no `.cos/` would make this claim pass without measuring anything, which is the
        # shape of green evidence about nothing that `coscc/build.py:3-6` warns about.
        print(
            f"the workspace {workspace} holds no work units — nothing to compare",
            file=sys.stderr,
        )
        return EXIT_ENV
    else:
        actual = board_rows(payload)
        results.append(say(
            actual == expected,
            f"the board agrees with the gate on all {len(expected)} (unit, stage, status) rows",
            f"only in the board: {sorted(actual - expected)[:4]} | "
            f"only in the gate: {sorted(expected - actual)[:4]}",
        ))

    # --- claim 3: a step's prompt carries its rules ---
    # The unit is whichever one the board lists first, not a name written here. Until
    # `0014` this named `0011_no-install-path-on-a-clean-machine`, a unit that lived in
    # the workspace's own `.cos/`; units now live in the product's store, so a hardcoded
    # name is a name that may not be there. `build_prompt` also took a `directory` in
    # `0014`, and this call was still passing the old five arguments -- it raised
    # `TypeError` rather than measuring anything, found 2026-09-23.
    subject = sorted(u["name"] for u in payload.get("units", []))[0]
    ran, out = in_installed(python, (
        "import json;from coscc import units;"
        "from coscc.runner import build_prompt, skill_for;"
        f"w={workspace!r};u={subject!r};s=skill_for({STAGE!r});"
        "d=units.unit_dir(w,u);"
        f"p,_=build_prompt(w,d,u,{STAGE!r},['intent','spec'],'spec.md');"
        "print(json.dumps({'chars': len(p), 'rules': len(s), 'whole': bool(s) and s in p,"
        " 'has_rules': '# The rules for this stage' in p}))"
    ))
    if not ran:
        results.append(say(False, f"a {STAGE} step is built with its rules in the prompt", out))
    else:
        built = json.loads(out)
        results.append(say(
            built["whole"] and built["has_rules"] and built["rules"] >= RULES_FLOOR,
            f"a {STAGE} step's prompt carries every character of its {built['rules']:,}-character rules",
            f"{built['chars']:,} characters of prompt, rules {built['rules']:,}, "
            f"whole: {built['whole']}, section header present: {built['has_rules']}",
        ))

    # --- claim 4: rules that cannot be found stop the step ---
    # `RunError` is what is asserted, not `MissingRules`: `coscc/service.py` maps this one
    # exception type to a 400 and anything else reaches the route as a 500. Review caught
    # that gap open on 2026-09-22 -- this claim was asserting the type that escaped.
    ran, out = in_installed(python, (
        "import json;from coscc import harness;"
        "from coscc.runner import RunError, skill_for;"
        "harness.PACKAGE_HARNESS=harness.Path('/nonexistent/packaged');"
        "harness.CHECKOUT_HARNESS=harness.Path('/nonexistent/checkout');"
        "\ntry:\n skill_for('spec');print(json.dumps({'raised': False, 'message': ''}))"
        "\nexcept RunError as e:\n print(json.dumps({'raised': True, 'message': str(e)}))"
    ))
    if not ran:
        results.append(say(False, "rules that cannot be found stop the step", out))
    else:
        refusal = json.loads(out)
        named = "/nonexistent/checkout" in refusal["message"]
        results.append(say(
            refusal["raised"] and named,
            "rules that cannot be found stop the step, and the refusal names the paths tried",
            f"raised={refusal['raised']} message={refusal['message'][:200]!r}",
        ))

    # --- claim 5: node is written down as a prerequisite ---
    install_doc = (REPO / "docs" / "install.md").read_text(encoding="utf-8")
    prerequisites = install_doc.split("## Prerequisites", 1)[-1].split("## Install", 1)[0]
    results.append(say(
        "`node`" in prerequisites,
        "docs/install.md declares node under ## Prerequisites",
        "the Board spawns node on every read (coscc/board.py) and the page said it was not needed",
    ))

    print(
        "\nnote: claim 5 checks that the prerequisite is written down, not that a machine\n"
        "      without node behaves. This host has node because npm test needs one. Proving\n"
        "      the clean-machine half needs a target over SSH, as verify_0011.py does.\n"
        "      spec.md C5."
    )
    return EXIT_PASS if all(results) else EXIT_BROKEN


if __name__ == "__main__":
    raise SystemExit(main())
