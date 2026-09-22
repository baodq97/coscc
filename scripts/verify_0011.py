#!/usr/bin/env python3
"""Proof for .cos/0011_no-install-path-on-a-clean-machine.

The claim in `intent.md`'s "Proposed outcome": on a clean Linux machine — no checkout, no
`uv`, no Python 3.14, no Node — a person reaches the rendered page in <= 3 commands, the
page survives a reboot with 0 further commands, and moving to the next release costs 1
command, after which a second reboot still holds.

This cannot run against the machine that runs it. The target is supplied by the operator
through `COS_PROOF_TARGET`, an SSH destination such as `user@host` (`plan.md` OQ1). Nothing
here may substitute a weaker stand-in for the two reboots: `plan.md` OQ1 rejects
`systemctl --user restart coscc` by name, because a restart never calls
`loginctl enable-linger` — the mechanism `spec.md` R10 depends on to bring the service up
before anyone has logged in. A restart would read green on exactly the failure
`plan.md` Risk 6 describes ("quên enable-linger, và nó trông như đã xong").

    0  every claim held, on the target, across both reboots
    1  at least one did not
    2  the environment could not answer: no COS_PROOF_TARGET, no `ssh`, no chromium
       (`scripts/proof_harness.py:require_browser`), or the target has no systemd

`scripts/verify_0003.py:8-14` explains why exit 2 is kept apart from exit 1, and it matters
more here than anywhere else in this repository: this unit is brand new and every one of
its claims is expected to fail today (nothing it tests has been built yet), so exit 1 is the
correct, informative result of a real run. Exit 2 is reserved for "no clean machine was
handed to this proof", which is a different fact and must never be reported as "the release
does not install."

Two things this proof insists on because `plan.md`'s risk list names the alternative as the
way to go quietly wrong:

- **Every `.gz` file is decompressed before it is searched** (`spec.md` R3, `plan.md`
  Risk 1). `intent.md`'s own measurement is that the address lives in two files — a `.js`
  and its `.gz` sibling — and every real browser reads the `.gz`. A scan that skips it
  passes on exactly the broken case, which is what happened on 2026-09-21. This proof does
  not reimplement that scan: `coscc.frontend.event_addresses` (commit 5cf4809) does the
  decompression, and this file imports it from the checkout rather than duplicating it
  (`scripts/verify_0003.py:29` inserts the repo root on `sys.path` the same way).
- **The check is "exactly the event socket," not "no address but ours."**
  `frontend.addresses()` returns every absolute URL authority the bundle carries — measured
  on the real bundle 2026-09-22 at 13, eleven of which belong to other people (`react.dev`,
  `github.com`, `reactrouter.com`, `socket.io`, `w3.org`, and a `http://localhost:3000` left
  over from Reflex's dev default). A check phrased as "no address but ours" cannot be
  asserted over that set. `frontend.event_addresses()` narrows to `ws://`/`wss://`
  authorities: the only socket the page opens is its own event socket, so after a correct
  rewrite that set holds exactly one entry, and this proof asserts it equals
  `{f"ws://{host}:{port}"}` — both that the destination is there and that nothing else is.
- **The bundle-and-address check runs against files pulled off the target**, never against
  a local build. There is no repository on the target by definition (`intent.md`'s
  "Problem" section), so nothing here may read `.web/` or `coscc/_web/` from this checkout
  as a stand-in.

`docs/install.md` is being written by a different track in parallel with this file. If it
already exists, the install and update commands are read out of it, under `## Install` and
`## Update` respectively — scoped the way `scripts/verify_0009.py:300-313` scopes a search
to one section, so prose elsewhere in the document is never mistaken for an instruction. If
the file, or either section, is not there yet, this falls back to the pinned command surface
below, which is the same contract `scripts/install.sh` and `.github/workflows/release.yml`
are being built against.
"""

from __future__ import annotations

import os
import posixpath
import re
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coscc import frontend
from scripts.proof_harness import EXIT_BROKEN, EXIT_ENV, EXIT_PASS, require_browser, say

REPO = Path(__file__).resolve().parent.parent
INSTALL_DOC = REPO / "docs" / "install.md"

# The pinned interface contract this file was handed. `docs/install.md` is the primary
# source for the commands actually run (see `install_commands`/`update_commands`); this is
# only the fallback for when that document, or the section this proof needs, is not there
# yet.
PINNED_INSTALL_COMMANDS = [
    "curl -LsSf https://github.com/baodq97/coscc/releases/latest/download/install.sh | sh",
]
# "update command: exactly the same line" — the pinned contract, verbatim.
PINNED_UPDATE_COMMANDS = list(PINNED_INSTALL_COMMANDS)

DEFAULT_PORT = 8790
DEFAULT_HOST = "0.0.0.0"  # spec.md R5's new default; install.sh is required to pin it explicitly

# `coscc/state.py:33-38`, the six screens `.claude/rules/coscc-app.md` names in `## Shape`.
# `#nav-{key}` is the click target `scripts/verify_0006.py:127` already uses against the
# same page.
SCREENS = ("overview", "workspaces", "board", "sessions", "activity", "settings")

MAX_INSTALL_COMMANDS = 3  # spec.md R15
MAX_UPDATE_COMMANDS = 1  # spec.md R15

SSH_TIMEOUT_S = 20
BOOT_TIMEOUT_S = 300  # generous: a real VM reboot, not a process restart
PAGE_TIMEOUT_MS = 20_000

_CODE_BLOCK = re.compile(r"```(?:sh|shell|bash)?\n(.*?)```", re.DOTALL)


# --------------------------------------------------------------------------
# docs/install.md, or the pinned fallback
# --------------------------------------------------------------------------


def _section(markdown: str, heading: str) -> str:
    """The prose under a `## heading`, up to the next one.

    `scripts/verify_0009.py:311-313` scopes a search over `.claude/CLAUDE.md` the same
    way, for the same reason: a plain substring search would also match the word showing up
    in running prose somewhere else in the document.
    """
    start = re.search(rf"^##\s+{re.escape(heading)}\s*$", markdown, re.MULTILINE)
    if not start:
        return ""
    return markdown[start.end():].partition("\n## ")[0]


def _commands_from(markdown: str) -> list[str]:
    cmds: list[str] = []
    for block in _CODE_BLOCK.findall(markdown):
        for line in block.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                cmds.append(line)
    return cmds


def install_commands() -> list[str]:
    """The commands a person actually types, from `docs/install.md`'s `## Install`.

    Falls back to `PINNED_INSTALL_COMMANDS` when the document, or that section, is not
    there yet — `docs/install.md` is `plan.md` step 8, written by a different track in
    parallel with this file.
    """
    if INSTALL_DOC.is_file():
        text = INSTALL_DOC.read_text(encoding="utf-8")
        cmds = _commands_from(_section(text, "Install") or text)
        if cmds:
            return cmds
    return list(PINNED_INSTALL_COMMANDS)


def update_commands() -> list[str]:
    if INSTALL_DOC.is_file():
        text = INSTALL_DOC.read_text(encoding="utf-8")
        cmds = _commands_from(_section(text, "Update"))
        if cmds:
            return cmds
    return list(PINNED_UPDATE_COMMANDS)


# --------------------------------------------------------------------------
# the target, over SSH — everything the proof knows about it goes through here
# --------------------------------------------------------------------------


def run_remote(
    target: str, command: str, timeout: float = SSH_TIMEOUT_S
) -> subprocess.CompletedProcess[str]:
    """One line, run on the target exactly as a person typing it over SSH would.

    Never raises: a timed-out or unreachable target comes back as a `CompletedProcess`
    with a nonzero code, so every caller can treat "the target did not answer" as just
    another failed command instead of an exception this proof has to remember to catch.
    """
    try:
        return subprocess.run(
            [
                "ssh",
                "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ConnectTimeout=10",
                target,
                command,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            ["ssh", target, command], 124, stdout="", stderr=f"ssh timed out after {timeout}s"
        )
    except OSError as e:
        return subprocess.CompletedProcess(["ssh", target, command], 255, stdout="", stderr=str(e))


def target_host(target: str) -> str:
    """`user@host` -> `host`, for building the URL a browser on this machine opens."""
    return target.rsplit("@", 1)[-1]


# --------------------------------------------------------------------------
# environment gates — exit 2, never a claim
# --------------------------------------------------------------------------


def require_target() -> str:
    target = os.environ.get("COS_PROOF_TARGET", "").strip()
    if not target:
        print(
            "COS_PROOF_TARGET is not set. This proof needs a clean Linux machine reached "
            "over SSH, e.g. COS_PROOF_TARGET=user@host — it cannot answer whether the "
            "install path or the reboot survival hold without one.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_ENV)
    return target


def require_ssh() -> None:
    import shutil

    if not shutil.which("ssh"):
        print("ssh is not installed; this proof cannot answer.", file=sys.stderr)
        raise SystemExit(EXIT_ENV)


def require_systemd(target: str) -> None:
    """`spec.md` R10 is a systemd user unit; without systemd on the target there is
    nothing for the rest of this proof to measure."""
    result = run_remote(
        target, "test -d /run/systemd/system && command -v systemctl >/dev/null", timeout=15
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        print(
            f"{target} did not confirm systemd "
            "(test -d /run/systemd/system && command -v systemctl "
            f"exited {result.returncode}{': ' + detail if detail else ''}). "
            "This proof cannot answer without a systemd user unit on the target (spec.md R10).",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_ENV)


# --------------------------------------------------------------------------
# step 2 — a real browser, never an HTTP request (spec.md C4/C5, spec.md R6)
# --------------------------------------------------------------------------


def page_renders_all_screens_and_websocket(browser, url: str) -> tuple[bool, str]:
    """Return ("", True)-shaped as (ok, reason): all six screens render, and the `/_event`
    WebSocket is open and never closes.

    Only a real browser sees either failure mode. `plan.md` Risk 3 records that the
    frontend directory can point at nothing while `/api/health` still answers, and
    `spec.md` C4 records that the runtime hostname substitution the whole rewrite depends
    on (R6) is a Reflex internal an HTTP client never exercises.
    """
    page = browser.new_page()
    ws_seen = {"opened": False, "closed": False}

    def on_ws(ws) -> None:
        if "/_event" in ws.url:
            ws_seen["opened"] = True
            ws.on("close", lambda: ws_seen.__setitem__("closed", True))

    page.on("websocket", on_ws)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        try:
            page.wait_for_selector("#studio-shell", timeout=PAGE_TIMEOUT_MS)
        except Exception:
            body = (page.text_content("body") or "")[:200].replace("\n", " ")
            return False, f"#studio-shell never appeared; saw: {body!r}"

        for screen in SCREENS:
            nav = page.locator(f"#nav-{screen}")
            if nav.count() == 0:
                return False, f"no #nav-{screen} control on the page"
            nav.first.click()
            try:
                page.wait_for_selector(
                    f"#nav-{screen}[aria-current='page']", timeout=PAGE_TIMEOUT_MS
                )
            except Exception:
                return False, f"the {screen!r} screen never became active after its nav click"

        page.wait_for_timeout(1000)
        if not ws_seen["opened"]:
            return False, "no WebSocket to /_event was ever opened"
        if ws_seen["closed"]:
            return False, "the /_event WebSocket opened and then closed"
        return True, ""
    finally:
        page.close()


# --------------------------------------------------------------------------
# step 3 — the served bundle's event socket, read off the target
# --------------------------------------------------------------------------


def remote_bundle_dir(target: str) -> str | None:
    """The directory Reflex mounts as static files on the target, read off the live
    process rather than guessed.

    `plan.md`'s design part 3 has `coscc/run.py` set `REFLEX_WEB_WORKDIR` before
    importing the app; `scripts/proof_harness.py:72` reads the served tree the same way
    this proof does, as `<that>/build/client`. Reading the variable back out of the running
    unit's own environment is the only way to find it that does not hardcode a layout this
    file was written before the packaging code existed to have one.
    """
    pid = run_remote(target, "systemctl --user show coscc -p MainPID --value").stdout.strip()
    if not pid or pid == "0":
        return None
    environ = run_remote(target, f"tr '\\0' '\\n' < /proc/{pid}/environ 2>/dev/null")
    if environ.returncode != 0:
        return None
    for line in environ.stdout.splitlines():
        if line.startswith("REFLEX_WEB_WORKDIR="):
            return line.split("=", 1)[1]
    return None


def fetch_bundle(target: str, remote_dir: str, local_dir: Path) -> tuple[bool, str]:
    """Stream the served directory off the target with one `tar`, not `scp -r`.

    `intent.md`'s own measurement is ~3.8k files under the bundle root; one stream over
    the SSH connection this proof already opened is one round trip instead of thousands.
    """
    remote_parent = posixpath.dirname(remote_dir) or "."
    remote_name = posixpath.basename(remote_dir)
    try:
        ssh_proc = subprocess.Popen(
            [
                "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", target,
                f"tar -C {shlex.quote(remote_parent)} -cf - {shlex.quote(remote_name)}",
            ],
            stdout=subprocess.PIPE,
        )
        assert ssh_proc.stdout is not None
        tar_proc = subprocess.run(
            ["tar", "-xf", "-", "-C", str(local_dir)],
            stdin=ssh_proc.stdout,
            capture_output=True,
            timeout=180,
        )
        ssh_proc.stdout.close()
        ssh_proc.wait(timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"could not stream {remote_dir} off {target}: {e}"
    if ssh_proc.returncode != 0 or tar_proc.returncode != 0:
        stderr = tar_proc.stderr.decode(errors="replace")[:300] if tar_proc.stderr else ""
        return False, (
            f"tar over ssh failed (ssh exit {ssh_proc.returncode}, tar exit "
            f"{tar_proc.returncode}): {stderr}"
        )
    return True, ""


def event_address_holds(target: str, expected: str) -> tuple[bool, str]:
    """`spec.md` R3/R4, via `coscc.frontend.event_addresses` (commit 5cf4809).

    `frontend.addresses()` is too blunt to assert against: it returns every absolute URL
    authority the bundle carries, and eleven of the thirteen measured on the real bundle
    belong to other people (`react.dev`, `github.com`, a `http://localhost:3000` left over
    from Reflex's dev default…). `event_addresses()` narrows to `ws://`/`wss://`
    authorities, and the only socket the page opens is its own event socket — so after a
    correct rewrite that set holds exactly `{expected}`, no more and no fewer. Both
    `addresses()` and `event_addresses()` decompress every `.gz` file before matching
    (`plan.md` Risk 1); this proof does not reimplement that, it only fetches the files for
    them to read.
    """
    remote_dir = remote_bundle_dir(target)
    if not remote_dir:
        return False, (
            "could not read REFLEX_WEB_WORKDIR off the running coscc unit "
            "(systemctl --user show coscc -p MainPID --value, then /proc/<pid>/environ)"
        )
    with tempfile.TemporaryDirectory(prefix="cos0011-bundle-") as tmp:
        local_dir = Path(tmp)
        bundle_dir = posixpath.join(remote_dir, "build", "client")
        ok, reason = fetch_bundle(target, bundle_dir, local_dir)
        if not ok:
            return False, reason

        if not any(local_dir.rglob("*")):
            return False, f"nothing was fetched from {bundle_dir} on {target} — wrong directory?"

        found = frontend.event_addresses(local_dir)
        if expected not in found:
            return False, (
                f"the destination address {expected!r} was never found among the event "
                f"socket authorities {sorted(found)} — is this the right served directory?"
            )
        if found != {expected}:
            return False, (
                f"event socket authorities are {sorted(found)}, wanted exactly {{{expected!r}}}"
            )
        return True, ""


# --------------------------------------------------------------------------
# steps 4 and 6 — a real reboot, never `systemctl --user restart` (plan.md OQ1)
# --------------------------------------------------------------------------


def reboot_and_wait(target: str) -> tuple[bool, str]:
    """Reboot the target for real, and wait for it to come back on its own — 0 commands
    typed by anyone.

    `plan.md` OQ1 rejects `systemctl --user restart coscc` as a stand-in: a restart never
    calls `loginctl enable-linger`, the mechanism `spec.md` R10 depends on to bring the
    service up before anyone has logged in, so a restart-based check would read green on
    exactly the failure `plan.md` Risk 6 names. Only a full reboot, confirmed by a new boot
    id, exercises that path.

    Assumes the target allows the invoking user to reboot without an interactive password
    — a property of the clean machine handed to this proof, not something this proof can
    supply.
    """
    before = run_remote(target, "cat /proc/sys/kernel/random/boot_id").stdout.strip()
    if not before:
        return False, "could not read the target's boot id before rebooting"

    # Fire-and-forget: the SSH connection carrying this command is expected to die with
    # the machine mid-flight, so a nonzero or timed-out result here is not itself a failure.
    run_remote(target, "sudo systemctl reboot || sudo reboot", timeout=10)

    deadline = time.monotonic() + BOOT_TIMEOUT_S
    went_down = False
    while time.monotonic() < deadline:
        if run_remote(target, "true", timeout=5).returncode != 0:
            went_down = True
            break
        time.sleep(2)
    if not went_down:
        return False, f"{target} never went unreachable within {BOOT_TIMEOUT_S}s of the reboot"

    while time.monotonic() < deadline:
        result = run_remote(target, "cat /proc/sys/kernel/random/boot_id", timeout=5)
        got = result.stdout.strip()
        if result.returncode == 0 and got and got != before:
            return True, ""
        time.sleep(3)
    return False, f"{target} did not come back with a new boot id within {BOOT_TIMEOUT_S}s"


# --------------------------------------------------------------------------
# step 5 — update, version, and the env file untouched
# --------------------------------------------------------------------------


def remote_env_path(target: str) -> str:
    """`plan.md` OQ2: `${XDG_CONFIG_HOME:-$HOME/.config}/coscc/env`, expanded on the
    target rather than assumed here, since `$HOME` is the target's, not this machine's."""
    return run_remote(target, 'echo "${XDG_CONFIG_HOME:-$HOME/.config}/coscc/env"').stdout.strip()


def read_remote_file(target: str, path: str) -> str | None:
    out = run_remote(target, f"cat {shlex.quote(path)}")
    return out.stdout if out.returncode == 0 else None


def remote_host_port(env_text: str | None) -> tuple[str, int]:
    """`COS_HOST`/`COS_PORT`, read from the env file `plan.md` Risk 2 requires
    `install.sh` to write explicitly on first install. Falls back to the pinned defaults
    when a line is absent, never to a value this proof invents."""
    host, port = DEFAULT_HOST, DEFAULT_PORT
    for line in (env_text or "").splitlines():
        line = line.strip()
        if line.startswith("COS_HOST="):
            host = line.split("=", 1)[1].strip() or host
        elif line.startswith("COS_PORT="):
            raw = line.split("=", 1)[1].strip()
            if raw.isdigit():
                port = int(raw)
    return host, port


def remote_version(target: str) -> str | None:
    """The pinned contract: `coscc --version` prints exactly one line, `"coscc <ver>"`."""
    out = run_remote(target, "coscc --version")
    if out.returncode != 0:
        return None
    line = out.stdout.strip()
    return line if re.fullmatch(r"coscc \S+", line) else None


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------


def run_commands(target: str, cmds: list[str], label: str) -> bool:
    for cmd in cmds:
        result = run_remote(target, cmd, timeout=300)
        if result.returncode != 0:
            tail = (result.stderr or result.stdout).strip()[-500:]
            say(False, f"the {label} command succeeds on the target", f"`{cmd}` exited {result.returncode}: {tail}")
            return False
    return True


def run() -> int:
    # Order matches the task's own: no target, no ssh, no chromium, then no systemd —
    # each one answers "this proof cannot answer" rather than "the outcome does not hold".
    target = require_target()
    require_ssh()
    playwright, browser = require_browser()
    try:
        require_systemd(target)

        host = target_host(target)

        # --- step 1: install, <= 3 commands, taken from docs/install.md or the pinned line ---
        cmds = install_commands()
        if not say(
            len(cmds) <= MAX_INSTALL_COMMANDS,
            f"the install fits in <= {MAX_INSTALL_COMMANDS} commands",
            f"counted {len(cmds)}: {cmds}",
        ):
            return EXIT_BROKEN
        if not run_commands(target, cmds, "install"):
            return EXIT_BROKEN
        say(True, f"installed with {len(cmds)} command(s)")

        env_path = remote_env_path(target)
        env_before_update = read_remote_file(target, env_path)
        expected_host, expected_port = remote_host_port(env_before_update)
        expected_event_address = f"ws://{expected_host}:{expected_port}"
        url = f"http://{host}:{expected_port}/"

        # --- step 2: the page, six screens, /_event — real browser only ---
        ok, reason = page_renders_all_screens_and_websocket(browser, url)
        if not say(ok, "the page renders all six screens and /_event connects", reason):
            return EXIT_BROKEN

        # --- step 3: the whole served bundle, .gz included, via coscc.frontend ---
        ok, reason = event_address_holds(target, expected_event_address)
        if not say(
            ok,
            f"the served bundle's event socket is exactly {expected_event_address!r}",
            reason,
        ):
            return EXIT_BROKEN

        # --- step 4: reboot, 0 commands, page renders again ---
        ok, reason = reboot_and_wait(target)
        if not say(ok, "the target reboots and the service comes back on its own", reason):
            return EXIT_BROKEN
        ok, reason = page_renders_all_screens_and_websocket(browser, url)
        if not say(ok, "the page renders again after the reboot, with nobody typing anything", reason):
            return EXIT_BROKEN

        # --- step 5: update, 1 command, version changes, env file untouched ---
        version_before = remote_version(target)
        upd_cmds = update_commands()
        if not say(
            len(upd_cmds) <= MAX_UPDATE_COMMANDS,
            f"the update fits in <= {MAX_UPDATE_COMMANDS} command",
            f"counted {len(upd_cmds)}: {upd_cmds}",
        ):
            return EXIT_BROKEN
        if not run_commands(target, upd_cmds, "update"):
            return EXIT_BROKEN
        say(True, f"updated with {len(upd_cmds)} command(s)")

        version_after = remote_version(target)
        if not say(
            bool(version_before) and bool(version_after) and version_before != version_after,
            "`coscc --version` reports a new number after the update",
            f"before={version_before!r} after={version_after!r}",
        ):
            return EXIT_BROKEN

        env_after_update = read_remote_file(target, env_path)
        if not say(
            env_before_update is not None and env_after_update == env_before_update,
            "the env file is unchanged, line for line",
            "could not re-read it, or its content changed",
        ):
            return EXIT_BROKEN

        # --- step 6: reboot again, page still renders ---
        ok, reason = reboot_and_wait(target)
        if not say(ok, "the target reboots a second time and comes back on its own", reason):
            return EXIT_BROKEN
        ok, reason = page_renders_all_screens_and_websocket(browser, url)
        if not say(ok, "the page still renders after the second reboot", reason):
            return EXIT_BROKEN

        print(f"\ncommands run: {len(cmds)} to install, {len(upd_cmds)} to update")
        print("PASS — install, render, survive a reboot, update, survive a second reboot.")
        return EXIT_PASS
    finally:
        browser.close()
        playwright.stop()


if __name__ == "__main__":
    sys.exit(run())
