"""Updating the app from the board: the pure half, and what `main` runs after uvicorn stops.

`.cos/0068_updating-the-app-is-a-manual-reinstall`. The state machine that decides when to
apply lives in `coscc/updater.py`; this module holds only what that machine and `run.main`
both need, and what can be tested without a server:

- `identity` — which version and commit this process runs, and whether this install is the
  one shape an update can be applied to (R1, R2);
- `candidate` and `fetch_into` — which release counts, and getting it onto disk checked
  (R4, R5);
- `finish` — R12 steps 8 to 10, run by `run.main` after `uvicorn.Server.run()` returns.

**Standard library only, and every import at the top.** `finish` runs after `uv tool
install --force` has replaced the venv under this very process (R12 step 8): a module
imported lazily from then on would be read from the new version, or from a half-written
tree. `coscc/update_test.py` pins that nothing is imported while `finish` runs.

**The source is a constant.** No environment variable and no request reaches `SOURCE`, and
`fetch_into` takes its opener as a Python argument so a test does not go to the network
(R3, R15). A checksum from the same release as the wheel catches a torn or swapped file; it
does not catch a compromised repository (spec C5).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version
from pathlib import Path
from typing import Any, Callable

SOURCE = "https://github.com/baodq97/coscc"
LATEST_API = "https://api.github.com/repos/baodq97/coscc/releases/latest"
DOWNLOAD_PREFIX = SOURCE + "/releases/download/"
SUMS = "SHA256SUMS"
MANIFEST = "manifest.json"

# Chosen by the spec (C11), not measured: a wheel is a few MB.
MAX_BYTES = 50 * 1024 * 1024
INSTALL_TIMEOUT = 120
# `EX_TEMPFAIL`. Any non-zero code brings the service back under `Restart=on-failure`;
# this one says "on purpose" to a person reading `journalctl`. Spec C7: systemd still
# counts it as a failure.
EXIT_CODE = 75

_HERE = Path(__file__).resolve().parent
REPO = _HERE.parent
# The same name `coscc.harness.BUILD_STAMP` checks for in a wheel.
BUILD_STAMP = _HERE / "_build.json"

_FULL_SHA = re.compile(r"[0-9a-f]{40}")
_PUBLIC = re.compile(r"(\d+)\.(\d+)\.(\d+)")
UNKNOWN_COMMIT = "commit không rõ"
UNAVAILABLE = "cập nhật không khả dụng ở cách cài này"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def running_version() -> str:
    try:
        return _dist_version("coscc")
    except PackageNotFoundError:  # pragma: no cover - coscc is always installed to run
        return "unknown"


def public(version: str) -> tuple[int, int, int] | None:
    """`X.Y.Z` of a version, local part dropped, as numbers. `None` when not that shape.

    A tag has already passed `cos.mjs check-tag` before it gets here, so every release is
    `X.Y.Z`; comparing three integers is the whole of PEP 440 this needs (plan, "Không dùng
    `packaging`").
    """
    m = _PUBLIC.fullmatch(version.split("+", 1)[0])
    return tuple(int(p) for p in m.groups()) if m else None  # type: ignore[return-value]


def local_commit(version: str) -> str:
    """The `g<sha7>` of a local build's version, without the `g`, or ``""``."""
    _, _, tail = version.partition("+")
    return tail[1:] if tail.startswith("g") else ""


# --- which build is running (R1, R2) -------------------------------------------------


def build_commit(stamp: Path = BUILD_STAMP) -> str | None:
    try:
        commit = json.loads(stamp.read_text(encoding="utf-8")).get("commit")
    except (OSError, ValueError, AttributeError):
        return None
    return commit if isinstance(commit, str) and _FULL_SHA.fullmatch(commit) else None


def checkout_commit(repo: Path = REPO) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = out.stdout.strip()
    return commit if out.returncode == 0 and _FULL_SHA.fullmatch(commit) else None


def find_uv(candidates: tuple[str, ...]) -> str | None:
    for d in candidates:
        path = Path(d) / "uv"
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def unit_file(config: Any) -> Path | None:
    return Path(config.config_home) / "systemd" / "user" / "coscc.service" if config.config_home else None


def _unit_lines(text: str, key: str) -> list[str]:
    return [line.split("=", 1)[1].strip() for line in text.splitlines() if line.strip().startswith(key + "=")]


def service_shape(
    config: Any, packaged: bool, prefix: str | None = None, executable: str | None = None
) -> tuple[str, str, dict[str, str]]:
    """R2, in order: `("service", "", details)`, or `("unavailable", <first false one>, {})`.

    `details` carries what the install step needs and must not take from a request: the
    `uv` to run, the tool and bin directories this process was installed into.
    """
    prefix = sys.prefix if prefix is None else prefix
    executable = os.path.abspath(sys.argv[0]) if executable is None else executable
    if not packaged:
        return "unavailable", "đây không phải bản cài đóng gói (chạy từ checkout)", {}
    if not config.invocation_id:
        return "unavailable", "tiến trình không chạy như một systemd service (không có INVOCATION_ID)", {}
    unit = unit_file(config)
    try:
        text = unit.read_text(encoding="utf-8") if unit else ""
    except OSError:
        text = ""
    if not text:
        return "unavailable", f"không có unit file {unit or 'coscc.service'}", {}
    starts = _unit_lines(text, "ExecStart")
    if not starts or os.path.realpath(starts[-1].split()[0]) != os.path.realpath(executable):
        return "unavailable", f"ExecStart= của {unit} không trỏ tới {executable}", {}
    if "on-failure" not in _unit_lines(text, "Restart"):
        return "unavailable", f"{unit} không có Restart=on-failure, nên thoát để cập nhật sẽ không lên lại", {}
    if not (Path(prefix) / "uv-receipt.toml").is_file():
        return "unavailable", f"venv {prefix} không phải một uv tool (không có uv-receipt.toml)", {}
    uv = find_uv(config.uv_candidates)
    if uv is None:
        return "unavailable", "không tìm thấy uv (PATH, UV_INSTALL_DIR, XDG_BIN_HOME, ~/.local/bin, ~/.cargo/bin)", {}
    return "service", "", {
        "uv": uv,
        "tool_dir": str(Path(prefix).parent),
        "bin_dir": str(Path(starts[-1].split()[0]).parent),
        "executable": starts[-1].split()[0],
    }


def identity(
    config: Any,
    packaged: bool,
    *,
    prefix: str | None = None,
    executable: str | None = None,
    stamp: Path = BUILD_STAMP,
    repo: Path = REPO,
    version: str | None = None,
) -> dict[str, Any]:
    """R1 and R2. Computed once at startup; no network."""
    version = running_version() if version is None else version
    commit = build_commit(stamp) if packaged else checkout_commit(repo)
    shape, reason, details = service_shape(config, packaged, prefix, executable)
    return {
        "version": version,
        "commit": commit or "",
        "commit_label": commit or UNKNOWN_COMMIT,
        "install": "package" if packaged else "checkout",
        "shape": shape,
        "reason": reason,
        "build_id": f"{version}+{commit or 'unknown'}",
        **details,
    }


# --- which release counts, and getting it onto disk (R4, R5) -------------------------


def wheel_name(version: str) -> str:
    return f"coscc-{version}-py3-none-any.whl"


def release_urls(tag: str) -> tuple[str, str]:
    """The wheel and `SHA256SUMS` of one tag, built from `SOURCE` and nothing else."""
    version = tag[1:] if tag.startswith("v") else tag
    base = f"{DOWNLOAD_PREFIX}{tag}/"
    return base + wheel_name(version), base + SUMS


def candidate(
    release: Any, running: str, check_tag: Callable[[str], str]
) -> dict[str, str] | None:
    """R4: a release worth downloading, or `None` — never an error.

    `release` is the JSON `releases/latest` returned. All four must hold: `check-tag` says
    `release`; its version is higher than the one running; it carries the wheel of that
    version and `SHA256SUMS`, each exactly once; both download URLs are under
    `SOURCE/releases/download/<tag>/`. A release carries `install.sh` too
    (`.github/workflows/release.yml`), and that is not a reason to refuse it.
    """
    if not isinstance(release, dict):
        return None
    tag = release.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith("v"):
        return None
    try:
        if check_tag(tag).strip() != "release":
            return None
    except Exception:  # noqa: BLE001 - a check that could not run is not a yes
        return None
    version = tag[1:]
    mine, theirs = public(running), public(version)
    if theirs is None or mine is None or theirs <= mine:
        return None
    assets = release.get("assets")
    if not isinstance(assets, list):
        return None
    wanted = {wheel_name(version): "", SUMS: ""}
    prefix = f"{DOWNLOAD_PREFIX}{tag}/"
    for asset in assets:
        name = asset.get("name") if isinstance(asset, dict) else None
        if name not in wanted:
            continue
        url = asset.get("browser_download_url")
        if wanted[name] or not isinstance(url, str) or not url.startswith(prefix):
            return None
        wanted[name] = url
    if not all(wanted.values()):
        return None
    return {
        "tag": tag, "version": version, "wheel_name": wheel_name(version),
        "wheel_url": wanted[wheel_name(version)], "sums_url": wanted[SUMS],
    }


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def sums_entry(text: str, name: str) -> str | None:
    """The sha256 `sha256sum` wrote for `name`, or `None`."""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name and re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            return parts[0]
    return None


class TooLarge(Exception):
    pass


def _download(opener: Callable[[str], Any], url: str, to: Path, limit: int) -> None:
    got = 0
    with opener(url) as src, open(to, "wb") as out:
        while True:
            block = src.read(1 << 16)
            if not block:
                break
            got += len(block)
            if got > limit:
                raise TooLarge(f"{url} is over {limit} bytes")
            out.write(block)


def fetch_into(
    channel_dir: Path, cand: dict[str, str], opener: Callable[[str], Any], limit: int = MAX_BYTES
) -> dict[str, Any]:
    """R5. Download a release's wheel and `SHA256SUMS` into `channel_dir`, checked.

    Written under temporary names, renamed in only after the wheel's sha256 matches its
    line in `SHA256SUMS`, and only then is the channel's previous wheel removed. Returns
    `{"state": "ready"|"checksum"|"error", ...}`; a mismatch leaves nothing behind.
    """
    channel_dir.mkdir(parents=True, exist_ok=True)
    name = cand["wheel_name"]
    part_wheel = channel_dir / f".{name}.part"
    part_sums = channel_dir / f".{SUMS}.part"
    try:
        _download(opener, cand["sums_url"], part_sums, limit)
        _download(opener, cand["wheel_url"], part_wheel, limit)
        want = sums_entry(part_sums.read_text(encoding="utf-8", errors="replace"), name)
        got = sha256_of(part_wheel)
    except TooLarge as e:
        _unlink(part_wheel, part_sums)
        return {"state": "error", "reason": str(e)}
    except Exception as e:  # noqa: BLE001 - offline, a 404, a torn read: try again next check
        _unlink(part_wheel, part_sums)
        return {"state": "error", "reason": f"{type(e).__name__}: {e}"}
    if want is None or want != got:
        _unlink(part_wheel, part_sums)
        return {"state": "checksum", "reason": f"sha256 của {name} không khớp SHA256SUMS", "sha256": got}
    os.replace(part_sums, channel_dir / SUMS)
    os.replace(part_wheel, channel_dir / name)
    for old in channel_dir.glob("*.whl"):
        if old.name != name:
            _unlink(old)
    # A local build promoted into `current/` left its manifest; it now describes a wheel
    # that is gone, and `verified_wheel` reads a manifest before `SHA256SUMS`.
    _unlink(channel_dir / MANIFEST)
    return {"state": "ready", "wheel": str(channel_dir / name), "version": cand["version"], "sha256": got}


def _unlink(*paths: Path) -> None:
    for p in paths:
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def verified_wheel(directory: Path) -> dict[str, Any] | None:
    """The one wheel in `directory`, if its sha256 matches what was recorded beside it.

    Recorded means `manifest.json` (a local build, R6) or `SHA256SUMS` (a release, R5). R12
    step 1 calls this on the target and on `current/` right before anything changes.
    """
    wheels = sorted(directory.glob("*.whl")) if directory.is_dir() else []
    if len(wheels) != 1:
        return None
    wheel = wheels[0]
    m = re.fullmatch(r"coscc-(.+)-py3-none-any\.whl", wheel.name)
    manifest = read_json(directory / MANIFEST)
    if manifest is not None and not (m and manifest.get("version") == m.group(1)):
        manifest = None  # it describes some other wheel, not this one
    if manifest is not None:
        want = manifest.get("sha256")
        version = manifest.get("version")
        commit = manifest.get("commit")
    else:
        try:
            want = sums_entry((directory / SUMS).read_text(encoding="utf-8"), wheel.name)
        except OSError:
            return None
        version, commit = None, None
    if not isinstance(want, str) or sha256_of(wheel) != want:
        return None
    if not isinstance(version, str):
        version = m.group(1) if m else ""
    return {"wheel": str(wheel), "version": version, "commit": commit or "", "sha256": want}


# --- files under `<data>/updates/` ----------------------------------------------------


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def write_json(path: Path, value: dict[str, Any]) -> None:
    """Whole or not at all: written beside, then renamed over."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.part")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def tail(path: Path | str, lines: int = 40) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def rollback_command(
    uv: str, tool_dir: str, bin_dir: str, old_wheel: str, db: str, backup: str
) -> str:
    """R13: how a person goes back by hand when nothing on the board can (spec C1)."""
    return "\n".join([
        "systemctl --user stop coscc",
        f"cp {backup} {db}",
        f"rm -f {db}-wal {db}-shm",
        f"UV_OFFLINE=1 UV_TOOL_DIR={tool_dir} UV_TOOL_BIN_DIR={bin_dir} {uv} tool install --force {old_wheel}",
        "systemctl --user start coscc",
    ])


# --- the hand-off to `main` (R12 steps 7 to 10) -----------------------------------


@dataclass(frozen=True)
class Handoff:
    """What the updater leaves for `main`: every path is derived from this process and the
    data root, never from a request (R15)."""

    target_wheel: str
    target_version: str
    current_wheel: str
    current_version: str
    from_version: str
    uv: str
    tool_dir: str
    bin_dir: str
    log: str
    last: str
    env: dict[str, str] = field(default_factory=dict)


class _Slot:
    """The `uvicorn.Server` `run.main` registered, and the hand-off waiting for it."""

    def __init__(self) -> None:
        self.server: Any = None
        self.handoff: Handoff | None = None

    def register(self, server: Any) -> None:
        self.server = server

    def hand_off(self, handoff: Handoff) -> bool:
        """Leave `handoff` and ask the server to stop. `False` when there is no server."""
        if self.server is None:
            return False
        self.handoff = handoff
        self.server.should_exit = True
        return True

    def take(self) -> Handoff | None:
        h, self.handoff = self.handoff, None
        return h


SERVER = _Slot()


def take_handoff() -> Handoff | None:
    return SERVER.take()


def finish(h: Handoff) -> int:
    """R12 steps 8 to 10, after uvicorn has returned. Always returns `EXIT_CODE`.

    `applied` — the target installed and reports its version. `failed` — the install
    failed and the old version still answers (`spike.md ## U4` step 4: a failed install
    leaves the old one standing). Anything else reinstalls the current wheel: `rolled-back`
    when that reports the old version, `broken` when it does not either, with the command
    to repair it already in the log.
    """
    env = {**h.env, "UV_OFFLINE": "1", "UV_TOOL_DIR": h.tool_dir, "UV_TOOL_BIN_DIR": h.bin_dir}
    coscc = str(Path(h.bin_dir) / "coscc")
    with open(h.log, "a", encoding="utf-8") as log:

        def run(cmd: list[str]) -> tuple[int, str]:
            log.write(f"\n$ {' '.join(cmd)}\n")
            try:
                done = subprocess.run(
                    cmd, env=env, capture_output=True, text=True, timeout=INSTALL_TIMEOUT
                )
                out, code = (done.stdout or "") + (done.stderr or ""), done.returncode
            except subprocess.TimeoutExpired:
                out, code = f"timed out after {INSTALL_TIMEOUT}s", -1
            except OSError as e:
                out, code = str(e), -1
            log.write(out + f"\n[exit {code}]\n")
            log.flush()
            return code, out

        def reports() -> str:
            code, out = run([coscc, "--version"])
            first = out.strip().splitlines()[0] if out.strip() else ""
            return first.split(" ", 1)[1].strip() if code == 0 and first.startswith("coscc ") else ""

        code, _ = run([h.uv, "tool", "install", "--force", h.target_wheel])
        seen = reports()
        if code == 0 and seen == h.target_version:
            result = "applied"
        elif code != 0 and seen == h.from_version:
            result = "failed"
        else:
            back, _ = run([h.uv, "tool", "install", "--force", h.current_wheel])
            result = "rolled-back" if back == 0 and reports() == h.current_version else "broken"
        log.write(f"\nresult: {result}\n")
    write_json(Path(h.last), {
        "from": h.from_version, "to": h.target_version, "result": result,
        "log": h.log, "finished_at": now(),
    })
    return EXIT_CODE


def free_copy(src: Path, dst: Path) -> None:
    """Copy a file under a temporary name, then rename it in."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(f".{dst.name}.part")
    shutil.copyfile(src, part)
    os.replace(part, dst)
