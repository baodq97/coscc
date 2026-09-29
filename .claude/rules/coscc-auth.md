---
paths:
  - "coscc/auth.py"
  - "coscc/run.py"
---

# Things that break here

- `auth.Guard` wraps the served app and is the only door; `auth.EXEMPT` (`coscc/auth.py:49`)
  lists what answers without a session, and any new route is refused unless added there.
  Moving the guard into `api_transformer` lets Reflex answer `OPTIONS` without it.
- `proxy_headers=False` in `coscc/run.py`: behind a proxy every client shares one failure count,
  so a stranger can lock the owner out for up to an hour.
- A state-changing request or websocket handshake whose `Origin` does not match `Host` is `403`;
  a proxy that rewrites `Host` breaks the page.
- `HASH_CONCURRENCY` (`coscc/auth.py:71`) argon2 hashes run at once, about 64 MiB each;
  another waits `HASH_WAIT` (`coscc/auth.py:73`) and gets `429`.
- The failure count and setup token live in memory; a restart clears the one and mints the other.
- The guard opens a SQLite connection per request; a `Busy` there is a `500`.
- A page it lets through goes out `Cache-Control: no-cache`, or chromium reuses a cached
  `index.html` after logout.
- `scripts/e2e.py` opens chromium on loopback only, not behind a proxy or on the installed service.
