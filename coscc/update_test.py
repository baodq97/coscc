"""`coscc/update.py`: identity, candidates, checked downloads, and `finish`.

`.cos/0068_updating-the-app-is-a-manual-reinstall` plan step 4. Nothing here goes to the
network: `fetch_into` gets an opener that serves bytes from a dict, and `finish` runs a
fake `uv` — a shell script that writes a fake `coscc` printing the version it was
"installed" from.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from coscc import update
from coscc.config import from_env

SHA = "0123456789abcdef" * 2 + "01234567"
TAG = "v0.13.0"
PREFIX = f"{update.DOWNLOAD_PREFIX}{TAG}/"


def _release(tag=TAG, assets=None):
    version = tag[1:]
    if assets is None:
        assets = [
            {"name": f"coscc-{version}-py3-none-any.whl", "browser_download_url": f"{update.DOWNLOAD_PREFIX}{tag}/coscc-{version}-py3-none-any.whl"},
            {"name": "install.sh", "browser_download_url": f"{update.DOWNLOAD_PREFIX}{tag}/install.sh"},
            {"name": "SHA256SUMS", "browser_download_url": f"{update.DOWNLOAD_PREFIX}{tag}/SHA256SUMS"},
        ]
    return {"tag_name": tag, "assets": assets}


def _yes(_tag):
    return "release"


class _Opener:
    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.asked: list[str] = []

    def __call__(self, url):
        self.asked.append(url)
        if url not in self.files:
            raise OSError(f"404 {url}")
        return io.BytesIO(self.files[url])


def _executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class TheRunningBuild(unittest.TestCase):
    def test_a_release_with_a_stamp_shows_its_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            stamp = Path(tmp) / "_build.json"
            stamp.write_text(json.dumps({"commit": SHA}), encoding="utf-8")
            me = update.identity(from_env({}), True, stamp=stamp, version="0.12.0")
            self.assertEqual((me["version"], me["commit"]), ("0.12.0", SHA))
            self.assertEqual(me["build_id"], f"0.12.0+{SHA}")

    def test_a_wheel_built_before_the_stamp_says_it_does_not_know(self):
        with tempfile.TemporaryDirectory() as tmp:
            me = update.identity(from_env({}), True, stamp=Path(tmp) / "none.json", version="0.11.0")
            self.assertEqual((me["commit"], me["commit_label"]), ("", update.UNKNOWN_COMMIT))

    def test_a_checkout_reads_head_and_ignores_a_stamp_left_in_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            stamp = Path(tmp) / "_build.json"
            stamp.write_text(json.dumps({"commit": SHA}), encoding="utf-8")
            me = update.identity(from_env({}), False, stamp=stamp)
            head = subprocess.run(["git", "-C", str(update.REPO), "rev-parse", "HEAD"],
                                  capture_output=True, text=True).stdout.strip()
            self.assertEqual(me["commit"], head)
            self.assertEqual(me["install"], "checkout")

    def test_a_local_build_carries_its_commit_in_the_version(self):
        self.assertEqual(update.local_commit("0.12.0+gd02560a"), "d02560a")
        self.assertEqual(update.public("0.12.0+gd02560a"), (0, 12, 0))
        self.assertIsNone(update.public("0.12"))


class TheServiceShape(unittest.TestCase):
    """R2's six conditions, each false once, and the first false one is the one named."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.bin = root / "bin"
        self.bin.mkdir()
        self.exe = _executable(self.bin / "coscc", "#!/bin/sh\n")
        _executable(self.bin / "uv", "#!/bin/sh\n")
        self.prefix = root / "tools" / "coscc"
        self.prefix.mkdir(parents=True)
        (self.prefix / "uv-receipt.toml").write_text("", encoding="utf-8")
        self.cfg_home = root / "cfg"
        (self.cfg_home / "systemd" / "user").mkdir(parents=True)
        self.unit = self.cfg_home / "systemd" / "user" / "coscc.service"
        self.write_unit(f"ExecStart={self.exe}\nRestart=on-failure\n")

    def tearDown(self):
        self.tmp.cleanup()

    def write_unit(self, text):
        self.unit.write_text(f"[Service]\nType=simple\n{text}", encoding="utf-8")

    def env(self, **extra):
        return from_env({
            "INVOCATION_ID": "x", "XDG_CONFIG_HOME": str(self.cfg_home), "PATH": str(self.bin),
            "HOME": self.tmp.name, **extra,
        })

    def shape(self, config=None, packaged=True, prefix=None):
        return update.service_shape(
            config or self.env(), packaged, str(prefix or self.prefix), str(self.exe)
        )

    def test_all_six_hold(self):
        shape, reason, details = self.shape()
        self.assertEqual((shape, reason), ("service", ""))
        self.assertEqual(details["uv"], str(self.bin / "uv"))
        self.assertEqual(details["tool_dir"], str(self.prefix.parent))
        self.assertEqual(details["bin_dir"], str(self.bin))

    def test_each_one_false_names_itself(self):
        self.assertIn("checkout", self.shape(packaged=False)[1])
        no_id = from_env({"XDG_CONFIG_HOME": str(self.cfg_home), "PATH": str(self.bin)})
        self.assertIn("INVOCATION_ID", self.shape(config=no_id)[1])
        self.write_unit(f"ExecStart=/elsewhere/coscc\nRestart=on-failure\n")
        self.assertIn("ExecStart", self.shape()[1])
        self.write_unit(f"ExecStart={self.exe}\nRestart=always\n")
        self.assertIn("Restart=on-failure", self.shape()[1])
        self.write_unit(f"ExecStart={self.exe}\nRestart=on-failure\n")
        self.assertIn("uv-receipt", self.shape(prefix=Path(self.tmp.name))[1])
        no_uv = from_env({"INVOCATION_ID": "x", "XDG_CONFIG_HOME": str(self.cfg_home),
                          "PATH": "/nonexistent", "HOME": "/nonexistent"})
        self.assertIn("uv", self.shape(config=no_uv)[1])

    def test_the_first_false_one_wins(self):
        self.unit.unlink()
        no_id = from_env({"XDG_CONFIG_HOME": str(self.cfg_home), "PATH": str(self.bin)})
        self.assertIn("INVOCATION_ID", self.shape(config=no_id)[1])


class WhichReleaseCounts(unittest.TestCase):
    def test_a_good_release_is_a_candidate_even_with_install_sh_beside_it(self):
        c = update.candidate(_release(), "0.12.0", _yes)
        self.assertEqual(c["version"], "0.13.0")
        self.assertTrue(c["wheel_url"].startswith(PREFIX))

    def test_check_tag_decides_the_grammar(self):
        self.assertIsNone(update.candidate(_release(), "0.12.0", lambda t: "prerelease"))

        def boom(_t):
            raise OSError("no node")

        self.assertIsNone(update.candidate(_release(), "0.12.0", boom))

    def test_not_higher_is_not_a_candidate(self):
        self.assertIsNone(update.candidate(_release("v0.12.0"), "0.12.0", _yes))
        self.assertIsNone(update.candidate(_release("v0.11.9"), "0.12.0", _yes))
        # A local build of the same number is still behind the next release.
        self.assertIsNotNone(update.candidate(_release(), "0.12.0+gd02560a", _yes))

    def test_a_missing_or_doubled_asset_refuses(self):
        good = _release()["assets"]
        missing = [a for a in good if a["name"] != "SHA256SUMS"]
        self.assertIsNone(update.candidate(_release(assets=missing), "0.12.0", _yes))
        doubled = good + [good[0]]
        self.assertIsNone(update.candidate(_release(assets=doubled), "0.12.0", _yes))

    def test_a_url_outside_the_source_refuses(self):
        assets = _release()["assets"]
        assets[0] = {**assets[0], "browser_download_url": "https://evil.example/coscc-0.13.0-py3-none-any.whl"}
        self.assertIsNone(update.candidate(_release(assets=assets), "0.12.0", _yes))
        assets = _release()["assets"]
        assets[2] = {**assets[2], "browser_download_url": f"{update.DOWNLOAD_PREFIX}v0.12.0/SHA256SUMS"}
        self.assertIsNone(update.candidate(_release(assets=assets), "0.12.0", _yes))


class CheckedDownloads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "release"
        self.cand = update.candidate(_release(), "0.12.0", _yes)
        self.wheel = b"wheel bytes"
        self.sums = f"{hashlib.sha256(self.wheel).hexdigest()}  coscc-0.13.0-py3-none-any.whl\n".encode()

    def tearDown(self):
        self.tmp.cleanup()

    def opener(self, wheel=None, sums=None):
        return _Opener({
            self.cand["wheel_url"]: self.wheel if wheel is None else wheel,
            self.cand["sums_url"]: self.sums if sums is None else sums,
        })

    def test_a_matching_checksum_is_renamed_in(self):
        got = update.fetch_into(self.dir, self.cand, self.opener())
        self.assertEqual(got["state"], "ready")
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()),
                         ["SHA256SUMS", "coscc-0.13.0-py3-none-any.whl"])
        self.assertIsNotNone(update.verified_wheel(self.dir))

    def test_a_mismatch_deletes_the_file(self):
        got = update.fetch_into(self.dir, self.cand, self.opener(wheel=b"swapped"))
        self.assertEqual(got["state"], "checksum")
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_over_the_limit_is_cut_and_not_kept(self):
        got = update.fetch_into(self.dir, self.cand, self.opener(), limit=5)
        self.assertEqual(got["state"], "error")
        self.assertIn("over", got["reason"])
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_the_old_wheel_goes_only_after_the_new_one_matched(self):
        self.dir.mkdir()
        old = self.dir / "coscc-0.12.5-py3-none-any.whl"
        old.write_bytes(b"old")
        update.fetch_into(self.dir, self.cand, self.opener(wheel=b"swapped"))
        self.assertTrue(old.exists())
        update.fetch_into(self.dir, self.cand, self.opener())
        self.assertFalse(old.exists())

    def test_offline_is_an_error_and_leaves_nothing(self):
        got = update.fetch_into(self.dir, self.cand, _Opener({}))
        self.assertEqual(got["state"], "error")
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_a_local_builds_manifest_goes_with_its_wheel(self):
        # Review round 2, F4: a local build promoted into `current/`, then a release fetched over it.
        self.dir.mkdir()
        old = self.dir / "coscc-0.12.0+gabcdef0-py3-none-any.whl"
        old.write_bytes(b"local")
        update.write_json(self.dir / update.MANIFEST, {"version": "0.12.0+gabcdef0", "commit": "c" * 40,
                                                       "sha256": hashlib.sha256(b"local").hexdigest()})
        update.fetch_into(self.dir, self.cand, self.opener())
        self.assertFalse((self.dir / update.MANIFEST).exists())
        self.assertEqual(update.verified_wheel(self.dir)["version"], "0.13.0")

    def test_a_manifest_for_another_wheel_is_not_read(self):
        update.fetch_into(self.dir, self.cand, self.opener())
        update.write_json(self.dir / update.MANIFEST, {"version": "0.12.0+gabcdef0", "commit": "c" * 40,
                                                       "sha256": hashlib.sha256(b"local").hexdigest()})
        self.assertEqual(update.verified_wheel(self.dir)["version"], "0.13.0")


FAKE_UV = """#!/bin/sh
# A stand-in for `uv tool install --force <wheel>`: records how it was called, then
# "installs" by writing a `coscc` that prints the version in the wheel's name. A wheel
# whose name carries `broken` fails; one carrying `garbled` installs a `coscc` that
# prints nothing useful.
printf '%s|%s|%s|%s\\n' "$*" "$UV_OFFLINE" "$UV_TOOL_DIR" "$UV_TOOL_BIN_DIR" >> "$UV_CALLS"
wheel="$4"
case "$wheel" in *broken*) echo "install failed" >&2; exit 1 ;; esac
v=$(basename "$wheel" | sed -e 's/^coscc-//' -e 's/-py3-none-any.whl$//')
case "$wheel" in *garbled*) v="garbled" ;; esac
mkdir -p "$UV_TOOL_BIN_DIR"
printf '#!/bin/sh\\necho "coscc %s"\\n' "$v" > "$UV_TOOL_BIN_DIR/coscc"
chmod +x "$UV_TOOL_BIN_DIR/coscc"
"""


class Finishing(unittest.TestCase):
    """R12 steps 8 to 10 with a fake `uv`: four results, `last.json`, and exit 75."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.uv = _executable(self.root / "uv", FAKE_UV)
        self.calls = self.root / "calls"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        _executable(self.bin / "coscc", '#!/bin/sh\necho "coscc 0.12.0"\n')

    def tearDown(self):
        self.tmp.cleanup()

    def handoff(self, target="coscc-0.13.0-py3-none-any.whl", current="coscc-0.12.0-py3-none-any.whl",
                target_version="0.13.0"):
        return update.Handoff(
            target_wheel=str(self.root / target), target_version=target_version,
            current_wheel=str(self.root / current), current_version="0.12.0",
            from_version="0.12.0", uv=str(self.uv), tool_dir=str(self.root / "tools"),
            bin_dir=str(self.bin), log=str(self.root / "update.log"), last=str(self.root / "last.json"),
            env={"PATH": os.environ.get("PATH", ""), "UV_CALLS": str(self.calls)},
        )

    def last(self):
        return json.loads((self.root / "last.json").read_text(encoding="utf-8"))

    def test_applied(self):
        self.assertEqual(update.finish(self.handoff()), 75)
        last = self.last()
        self.assertEqual(last["result"], "applied")
        self.assertEqual(set(last), {"from", "to", "result", "log", "finished_at"})
        args, offline, tool_dir, bin_dir = self.calls.read_text().splitlines()[0].split("|")
        self.assertTrue(args.startswith("tool install --force "))
        self.assertEqual((offline, tool_dir, bin_dir), ("1", str(self.root / "tools"), str(self.bin)))

    def test_failed_leaves_the_old_one(self):
        update.finish(self.handoff(target="coscc-broken-py3-none-any.whl"))
        self.assertEqual(self.last()["result"], "failed")
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)

    def test_a_wrong_version_rolls_back(self):
        update.finish(self.handoff(target="coscc-garbled-py3-none-any.whl"))
        self.assertEqual(self.last()["result"], "rolled-back")
        self.assertEqual(len(self.calls.read_text().splitlines()), 2)

    def test_a_rollback_that_fails_too_is_broken(self):
        update.finish(self.handoff(target="coscc-garbled-py3-none-any.whl",
                                   current="coscc-broken-py3-none-any.whl"))
        self.assertEqual(self.last()["result"], "broken")
        self.assertIn("result: broken", (self.root / "update.log").read_text())

    def test_the_install_timeout_is_the_chosen_one(self):
        self.assertEqual(update.INSTALL_TIMEOUT, 120)

    def test_nothing_is_imported_after_the_install_begins(self):
        """R12: the venv is replaced under this process at step 8, so `finish` may import
        nothing. Measured in a clean interpreter that has imported only `coscc.update`."""
        h = self.handoff()
        child = (
            "import json, sys\n"
            "from coscc import update\n"
            f"h = update.Handoff(**json.loads({json.dumps(json.dumps(h.__dict__))}))\n"
            "before = set(sys.modules)\n"
            "code = update.finish(h)\n"
            "print(json.dumps({'code': code, 'new': sorted(set(sys.modules) - before)}))\n"
        )
        out = subprocess.run([sys.executable, "-c", child], capture_output=True, text=True,
                             cwd=str(update.REPO), check=True)
        got = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(got, {"code": 75, "new": []})


class TheSlot(unittest.TestCase):
    def test_no_server_refuses_the_hand_off(self):
        slot = update._Slot()
        self.assertFalse(slot.hand_off(object()))  # type: ignore[arg-type]

    def test_a_hand_off_stops_the_server_and_is_taken_once(self):
        class S:
            should_exit = False

        slot, server = update._Slot(), S()
        slot.register(server)
        self.assertTrue(slot.hand_off("h"))  # type: ignore[arg-type]
        self.assertTrue(server.should_exit)
        self.assertEqual(slot.take(), "h")
        self.assertIsNone(slot.take())


class TheRollbackCommand(unittest.TestCase):
    def test_it_names_all_five_steps(self):
        text = update.rollback_command("/u/uv", "/t", "/b", "/w.whl", "/d/cos.db", "/d/updates/cos.db.bak")
        self.assertIn("systemctl --user stop coscc", text)
        self.assertIn("cp /d/updates/cos.db.bak /d/cos.db", text)
        self.assertIn("rm -f /d/cos.db-wal /d/cos.db-shm", text)
        self.assertIn("UV_OFFLINE=1", text)
        self.assertIn("systemctl --user start coscc", text)


if __name__ == "__main__":
    unittest.main()
