---
paths:
  - "coscc/auth.py"
  - "coscc/run.py"
---

# The login door

- **`coscc/auth.py` is the only door, and it opens on one password.** uvicorn serves
  `coscc.coscc:served`, the composed app wrapped by `auth.Guard` — the one position
  `.cos/0070_*/spike.md ## U1` measured to see every scope, CORS preflight included; moving
  the guard into `api_transformer` lets Reflex answer `OPTIONS` without it. `auth.EXEMPT`
  (`coscc/auth.py:58`) is the whole list of what answers without a session (`/api/health`,
  `/login`, and `/setup` while no password is stored); anything else, a route added later
  included, is refused, and `coscc/auth_test.py` plus `scripts/verify_0070.py` count that.
- **What no test sees.**
  - `proxy_headers=False` in `coscc/run.py`, so behind a proxy every client shares one
    failure count and a stranger can lock the owner out for up to an hour
    (`.cos/0070_*/spec.md` C2).
  - A state-changing request or websocket handshake whose `Origin` does not match `Host`
    is `403`, so a proxy that rewrites `Host` breaks the page (C3).
  - `HASH_CONCURRENCY` (`coscc/auth.py:82`) argon2 hashes run at once, about 64 MiB each
    (`.cos/0070_*/spike.md ## U2`); another waits `HASH_WAIT` (`coscc/auth.py:85`) and gets
    `429` — many addresses trying at once can refuse the owner too.
  - The failure count and the setup token live in memory: a restart clears the one and
    mints the other. A setup token sits in the journal until the password is set.
  - The guard opens a SQLite connection per request (unmeasured cost); a `Busy` there is a
    `500`, still a refusal.
  - A page it lets through goes out `Cache-Control: no-cache`: without it chromium reused a
    cached `index.html` after logout, a board whose socket the guard refused, and never
    reached `/login`.
- `verify_0070.py --browser` is the one proof that opens chromium behind the guard, on
  loopback and off it — not behind a proxy, and not on the installed service.
- The default bind is `0.0.0.0` and the app serves plain HTTP; `COS_HOST=127.0.0.1` is the
  loopback posture. `coscc reset-password` is the only way back from a forgotten password.
