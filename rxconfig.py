"""Reflex configuration. Flat layout: the package sits beside this file.

`spec.md` R1 fixes the name; Reflex resolves `app_name` to a directory of the same name at
the repo root, which is why there is no `src/`.

`backend_host` is set explicitly because Reflex 0.9.11 defaults it to `0.0.0.0` — verified
on 2026-09-21 by reading `Config.__dataclass_fields__["backend_host"].default`, and by
`ss -ltn` showing `0.0.0.0:8000` before this line existed. `0002` defaulted the other way
(`cos_baodo/config.py:60`), so adopting Reflex silently reverses that posture. `spec.md` R5 is
the requirement this line answers; deleting it must be a visible edit.
"""

import reflex as rx

config = rx.Config(
    app_name="cos_baodo",
    backend_host="127.0.0.1",
)
