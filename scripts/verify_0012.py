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

# The stage measured, and the floor it has to clear. Both from `spec.md` R3: the same
# stage on the same unit built 18.882 characters from the checkout and 14.313 from the
# installed copy on 2026-09-22, and 18.000 sits between them by more than the noise a
# changed skill would add.
STAGE = "spec"
PROMPT_FLOOR = 18_000


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


def gate_rows(workspace: str) -> tuple[bool, set]:
    """`(unit, stage, status)` as the harness in the workspace reports it.

    One derivation is applied here, and only one: an artifact the gate does not mention
    is `not started`. That is `coscc/board.py:76`, and `board.py:65-68` records it as a
    reading of the absence rather than something stored. Copying it is what keeps the
    comparison about the two sides agreeing instead of about who spells an empty cell how;
    copying anything *else* the app does would make this claim compare the app with itself.
    """
    script = REPO / ".claude" / "scripts" / "cos.mjs"
    try:
        done = subprocess.run(
            ["node", str(script), "--root", workspace, "status", "--json"],
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
    got_gate, expected = gate_rows(workspace)
    if not got_board:
        results.append(say(False, "the board agrees with the gate in the workspace", str(payload)))
    elif not got_gate:
        results.append(say(False, "the board agrees with the gate in the workspace", str(expected)))
    else:
        actual = board_rows(payload)
        results.append(say(
            actual == expected,
            f"the board agrees with the gate on all {len(expected)} (unit, stage, status) rows",
            f"only in the board: {sorted(actual - expected)[:4]} | "
            f"only in the gate: {sorted(expected - actual)[:4]}",
        ))

    # --- claim 3: a step's prompt carries its rules ---
    ran, out = in_installed(python, (
        "import json;from coscc.runner import build_prompt;"
        f"p,_=build_prompt({workspace!r},{'0011_no-install-path-on-a-clean-machine'!r},"
        f"{STAGE!r},['intent','spec'],'spec.md');"
        "print(json.dumps({'chars': len(p), 'has_rules': '# The rules for this stage' in p}))"
    ))
    if not ran:
        results.append(say(False, f"a {STAGE} step is built with its rules in the prompt", out))
    else:
        built = json.loads(out)
        results.append(say(
            built["chars"] >= PROMPT_FLOOR and built["has_rules"],
            f"a {STAGE} step's prompt carries its rules and clears {PROMPT_FLOOR:,} characters",
            f"{built['chars']:,} characters, rules section present: {built['has_rules']}",
        ))

    # --- claim 4: rules that cannot be found stop the step ---
    ran, out = in_installed(python, (
        "import json;from coscc import harness;from coscc.runner import skill_for;"
        "harness.PACKAGE_HARNESS=harness.Path('/nonexistent/packaged');"
        "harness.CHECKOUT_HARNESS=harness.Path('/nonexistent/checkout');"
        "\ntry:\n skill_for('spec');print(json.dumps({'raised': False, 'message': ''}))"
        "\nexcept harness.MissingRules as e:\n print(json.dumps({'raised': True, 'message': str(e)}))"
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
