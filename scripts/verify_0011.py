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

- **The bundle is read the way a browser reads it: over HTTP, with `Accept-Encoding: gzip`
  sent on purpose.** `spec.md` R3 and `plan.md` Risk 1 are about the same failure — the
  address lives in two files, a `.js` and its `.gz` sibling, Reflex's
  `frontend_compression_formats` defaults to `['gzip']`, and `PrecompressedStaticFiles`
  serves the sidecar whenever the request carries that header, which every real browser
  sends and a bare `httpx.get()` does not. An earlier draft of this file instead read
  `REFLEX_WEB_WORKDIR` off `/proc/<pid>/environ` on the target and pulled the tree with
  `tar` over SSH; that cannot work, because `coscc/run.py` sets that variable with
  `os.environ[...] = ...` *inside* `main()`, after the process has already called
  `execve()`, and `/proc/<pid>/environ` only ever reflects the environment at that moment —
  measured 2026-09-22, a variable set after start never shows up there. So this proof
  measures the bytes the server actually sends instead of guessing a layout on disk:
  `GET /` off the target, read the env chunk's own URL out of the returned HTML (its name
  is content-hashed, `coscc/frontend.py:58`, so it is never constructed), then `GET` that
  URL with `Accept-Encoding: gzip` and assert the response carries `content-encoding:
  gzip` back — the load-bearing assertion, because its absence means the plain file
  answered and the sidecar, exactly where a stale address would survive, was never read.
- **The check is "exactly the event socket," not "no address but ours."**
  `frontend.addresses()` returns every absolute URL authority the bundle carries — measured
  on the real bundle 2026-09-22 at 13, eleven of which belong to other people (`react.dev`,
  `github.com`, `reactrouter.com`, `socket.io`, `w3.org`, and a `http://localhost:3000` left
  over from Reflex's dev default). A check phrased as "no address but ours" cannot be
  asserted over that set. `frontend.event_addresses()` narrows to `ws://`/`wss://`
  authorities: the only socket the page opens is its own event socket, so after a correct
  rewrite that set holds exactly one entry, and this proof asserts it equals
  `{f"ws://{host}:{port}"}` — both that the destination is there and that nothing else is.
  This file reuses that function rather than duplicating its authority regex
  (`scripts/verify_0003.py:29` inserts the repo root on `sys.path` the same way), by
  writing the decompressed chunk into a temporary directory and pointing it there.

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
import re
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin

import httpx

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

# Reading which release `/releases/latest/` currently resolves to. The redirect target
# carries the tag, so this needs no API token and no JSON: GitHub answers
# `/releases/latest` with a 302 to `/releases/tag/vX.Y.Z`.
RELEASES_LATEST = "https://github.com/baodq97/coscc/releases/latest"

# How long step 5 will wait for the operator to publish the release it is about to update
# to. Generous: the release workflow builds a frontend from scratch.
NEXT_RELEASE_TIMEOUT_S = 1800

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

# The env chunk's own name is content-hashed (`coscc/frontend.py:58`, `_ENV_GLOB`), so it
# changes on every build; this reads its URL out of the page rather than constructing it.
_ENV_CHUNK_HREF = re.compile(r"[\"'](?P<path>/?assets/reflex-env-[^\"'>\s]+\.js)[\"']")


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
# step 3 — the served bundle's event socket, measured over HTTP
# --------------------------------------------------------------------------


def find_env_chunk_url(base_url: str) -> tuple[str | None, str]:
    """`GET /` off the target and read the env chunk's own URL out of the returned HTML.

    Its filename is content-hashed (`coscc/frontend.py:58`, `_ENV_GLOB`), so it changes on
    every build; constructing it would silently start matching nothing the day the hash
    changes — the same failure `coscc.frontend.NoEnvChunk` exists to stop on the write side.
    """
    try:
        resp = httpx.get(base_url, timeout=15, follow_redirects=True)
    except httpx.HTTPError as e:
        return None, f"GET {base_url} failed: {e}"
    if resp.status_code != 200:
        return None, f"GET {base_url} returned {resp.status_code}"
    match = _ENV_CHUNK_HREF.search(resp.text)
    if not match:
        return None, f"no assets/reflex-env-*.js reference found in the page at {base_url}"
    return urljoin(base_url, match.group("path")), ""


def event_address_holds(base_url: str, expected: str) -> tuple[bool, str]:
    """`spec.md` R3/R4, measured the way a browser measures them: over HTTP, with the
    header that makes Reflex answer with the `.gz` sidecar rather than the plain file.

    `PrecompressedStaticFiles` serves the plain chunk unless the request carries
    `Accept-Encoding: gzip`; every real browser sends it, and `httpx.get()` does not unless
    told to. So `content-encoding: gzip` coming back is the load-bearing assertion here —
    its absence means this check just read the plain file, which is exactly where the
    stale address from `plan.md` Risk 1 would still be sitting while everything looked
    fine. Once that holds, the decompressed body is handed to
    `coscc.frontend.event_addresses()` (commit 5cf4809) rather than matched by a second
    regex this file would have to keep in sync with `coscc/frontend.py` by hand.
    """
    chunk_url, reason = find_env_chunk_url(base_url)
    if not chunk_url:
        return False, reason

    try:
        resp = httpx.get(chunk_url, headers={"Accept-Encoding": "gzip"}, timeout=15)
    except httpx.HTTPError as e:
        return False, f"GET {chunk_url} failed: {e}"
    if resp.status_code != 200:
        return False, f"GET {chunk_url} returned {resp.status_code}"

    encoding = resp.headers.get("content-encoding", "")
    if encoding.lower() != "gzip":
        return False, (
            f"{chunk_url} answered with content-encoding={encoding!r}, not gzip — this "
            "measured the plain file, not the .gz sidecar a real browser is served "
            "(plan.md Risk 1)"
        )

    with tempfile.TemporaryDirectory(prefix="cos0011-chunk-") as tmp:
        # httpx already decoded the body per this content-encoding, so `resp.content` here
        # is the plain text the sidecar decompresses to. The file just needs a name that
        # does not end in `.gz`, or `frontend.addresses()` would try to gunzip it again.
        chunk_path = Path(tmp) / "reflex-env.js"
        chunk_path.write_bytes(resp.content)
        found = frontend.event_addresses(Path(tmp))

    if expected not in found:
        return False, (
            f"the destination address {expected!r} was never found among the event "
            f"socket authorities {sorted(found)} served from {chunk_url}"
        )
    if found != {expected}:
        return False, (
            f"event socket authorities served from {chunk_url} are {sorted(found)}, "
            f"wanted exactly {{{expected!r}}}"
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


def latest_published_version() -> str | None:
    """The version `/releases/latest/download/...` would serve right now, or None."""
    try:
        resp = httpx.get(RELEASES_LATEST, timeout=15, follow_redirects=True)
    except httpx.HTTPError:
        return None
    found = re.search(r"/releases/tag/v(\d+\.\d+\.\d+[^/\s\"']*)", str(resp.url))
    return found.group(1) if found else None


def wait_for_a_newer_release(installed: str | None) -> tuple[bool, str]:
    """Step 5 measures moving to the *next* release, so a next one has to exist.

    This precondition went unwritten until the proof was first run for real, 2026-09-22,
    and it is not a detail of the harness -- it is forced by what the outcome claims.
    `docs/install.md` gives one line for both install and update, and that line resolves
    through `/releases/latest/`. Run with a single release published, it installs X and
    then "updates" to X: `coscc --version` cannot change, and the step fails while
    nothing about the update path is actually broken.

    So the proof stops here and says what it is waiting for. A timeout is `EXIT_ENV`, not
    `EXIT_BROKEN` -- "nobody published the next release" is an unready environment, the
    same distinction the module docstring draws for a missing target.
    """
    current = (installed or "").removeprefix("coscc ").strip()
    deadline = time.monotonic() + NEXT_RELEASE_TIMEOUT_S
    told = False
    while time.monotonic() < deadline:
        latest = latest_published_version()
        if latest and latest != current:
            return True, f"{RELEASES_LATEST} now resolves to v{latest}"
        if not told:
            print(
                f"\n  waiting: the target has coscc {current or '(unknown)'} installed, and "
                f"{RELEASES_LATEST}\n  still resolves to v{latest or '(unreadable)'}. Step 5 "
                "measures the move to the *next* release,\n  so publish it now -- push the "
                f"next tag. Waiting up to {NEXT_RELEASE_TIMEOUT_S // 60} minutes.",
                flush=True,
            )
            told = True
        time.sleep(15)
    return False, f"no release newer than {current!r} appeared within {NEXT_RELEASE_TIMEOUT_S}s"


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

        # --- step 3: the served bundle's event socket, over HTTP, Accept-Encoding: gzip ---
        ok, reason = event_address_holds(url, expected_event_address)
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
        ready, why = wait_for_a_newer_release(version_before)
        if not ready:
            print(f"\n{why}", file=sys.stderr)
            return EXIT_ENV
        say(True, f"a newer release is published ({why})")
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
