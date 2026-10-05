"""`python -m coscc.loop`, answered in this process: what a child would print and exit with.

A child's start costs more than most answers, and the tests ask thousands. A child starts with
none of what a question before it learned, so neither does this; one runs at a time, because
the streams, the env and the cwd it swaps are the process's.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from unittest import mock

from coscc.loop import checkout, model
from coscc.loop.__main__ import main

_one = threading.Lock()


def in_process(
    argv: list[str], stdin: bytes | None, cwd: str | Path | None, environ: dict[str, str]
) -> tuple[int, str, str]:
    out, err = io.BytesIO(), io.BytesIO()
    streams = {
        "stdout": io.TextIOWrapper(out, encoding="utf-8"),
        "stderr": io.TextIOWrapper(err, encoding="utf-8"),
        "stdin": io.TextIOWrapper(io.BytesIO(stdin or b""), encoding="utf-8"),
    }
    with _one, contextlib.ExitStack() as stack:
        here = os.getcwd()
        stack.enter_context(mock.patch.dict(os.environ, environ, clear=True))
        for name, stream in streams.items():
            stack.enter_context(mock.patch.object(sys, name, stream))
        stack.callback(time.tzset)
        stack.callback(os.chdir, here)
        time.tzset()
        if cwd is not None:
            os.chdir(cwd)
        model.LINKS.clear()
        checkout.cache_clear()
        try:
            code = main(list(argv))
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else int(e.code is not None)
        except Exception:  # noqa: BLE001 - a child prints the traceback and exits 1
            traceback.print_exc()
            code = 1
        streams["stdout"].flush()
        streams["stderr"].flush()
    return code, out.getvalue().decode(), err.getvalue().decode()
