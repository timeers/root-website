# Bot Latency Fix — Phase 2 (Apache worker isolation + tuning)

> **Do this AFTER the server upgrade, not before.** These changes add worker processes and
> only make sense on the resized box. Applying them on the current 1‑core / 1.9 GB box
> would deepen swapping and make things worse.

Phase 1 (in‑repo code fixes: the `SetLanguageMiddleware` session‑write fix and
`CONN_MAX_AGE` on the DB) is already done and deployed separately. This document is only
the production/server half.

---

## ⛔ Gate — complete the server upgrade first

The root constraint on prod is capacity, not code:

- **1 CPU core, 1.9 GB RAM, ~200 MB already in swap**, with Apache/Django **+ Postgres +
  Redis + Celery worker + celerybeat all on the same box** (localhost).
- Swapping stalls WSGI processes for hundreds of ms–seconds → blows the 3s Discord budget.
- 1 core + the Python GIL means the bot can't run while a website request holds the core.

**Required action: resize to ~2 vCPU / 8 GB before applying anything below.** This ends the
swapping and gives a second core so a dedicated bot process group can actually run in
parallel with the website.

**Confirm the upgrade landed before starting:**
```bash
nproc          # should return 2
free -h        # swap ~unused under normal load
```

---

## B1 — Dedicated WSGI daemon group for the bot endpoint (highest impact; do first)

Isolate `/discord/interactions/` in its own mod_wsgi process group so Discord interactions
never queue behind slow website requests.

Edit the active vhost: `/etc/apache2/sites-available/django_project-le-ssl.conf`
(the `sites-enabled` entry symlinks to it; there's also a stale `.bak-20260510` — ignore it).

**Declare the more‑specific `/discord/interactions` alias BEFORE the catch‑all `/`**
(mod_wsgi matches `WSGIScriptAlias` by declaration/prefix; putting the specific one first
avoids ambiguity):

```apache
# Two daemon groups
WSGIDaemonProcess django_app processes=2 threads=10
WSGIDaemonProcess django_bot processes=1 threads=4   # small, fast, latency-critical

# Specific alias FIRST: Discord interactions -> dedicated bot group
WSGIScriptAlias /discord/interactions /home/mrmirz/django_project/django_project/wsgi.py \
    process-group=django_bot application-group=%{GLOBAL}
<Location /discord/interactions>
    WSGIProcessGroup django_bot
</Location>

# Catch-all alias AFTER: everything else -> website group
WSGIScriptAlias / /home/mrmirz/django_project/django_project/wsgi.py
WSGIProcessGroup django_app
```

Start the bot group at `processes=1 threads=4` and tune to interaction volume. This
guarantees free slots for the 3s‑critical path regardless of site load.

Reload after editing:
```bash
sudo systemctl reload apache2      # or: sudo apache2ctl graceful
```

---

## B2 — Lower `MaxRequestWorkers` to match real WSGI capacity

Currently `MaxRequestWorkers 150` (in `/etc/apache2/mods-enabled/mpm_event.conf`) vs only
~20–24 WSGI slots. Apache accepts far more than Django can serve, so excess requests queue.

Set **`MaxRequestWorkers 50`** (a clean multiple of `ThreadsPerChild 25`, i.e. 2 child
processes) so backpressure happens at the connection layer instead of stalling in‑flight
requests.

Keep the website group modest (≈ `processes=2 threads=10` on 2C/8GB). With only 2 cores +
the GIL, **do not over‑provision** — more processes past ~2–3 mostly wastes RAM. Watch
memory (each WSGI process ≈ 190 MB).

---

## B3 — Connection math (sanity check for the Phase 1 `CONN_MAX_AGE`)

With these worker counts, persistent connections hold at most ≈ (website slots + bot slots)
≈ **24–30 Postgres connections**, well under `max_connections` (confirmed **100** in prod):
```sql
SHOW max_connections;   -- 100
SELECT count(*) FROM pg_stat_activity WHERE datname='rootdbpostgres';  -- under load
```
So the Phase 1 `CONN_MAX_AGE` (currently set to 10s) is safe. **pgbouncer is optional** —
only worth it if you later scale worker counts much higher. (If you ever add pgbouncer in
transaction‑pooling mode, set `CONN_MAX_AGE = 0` and let pgbouncer own pooling.)

---

## B4 — Already satisfied

`DEBUG_VALUE=False` and `POSTGRES_VALUE=True` are confirmed in the prod `.env`, so
debug_toolbar is inert and Postgres is active. Nothing to do.

---

## B5 — `WSGIApplicationGroup %{GLOBAL}` (optional)

Set on the daemon groups to avoid sub‑interpreter issues with some C extensions and
slightly reduce per‑request overhead. (Already shown on the bot alias in B1.)

---

## Post‑upgrade verification

1. **Capacity:** `nproc` = 2 and `free -h` shows swap ~unused under load.
2. **Contention gone:** interaction latency no longer correlates with concurrent
   heavy‑page requests; Discord interaction failure rate drops sharply.
3. **DB connections:** `SELECT count(*) FROM pg_stat_activity WHERE datname='rootdbpostgres';`
   under load stays well under `max_connections`.
4. **Bot group is live:** confirm requests to `/discord/interactions/` are served by the
   `django_bot` group (check Apache error log for the daemon group name, or that the bot
   stays responsive while the site is under load).

---

## Optional follow‑ups (not required for the timeout fix)

- **Widen Celery.** The worker runs `--concurrency=1 --pool=solo`, a throughput bottleneck
  for offloaded bot tasks (thread creation, command re‑registration, DMs). After the
  resize there's headroom to raise concurrency. Not an interaction‑timeout cause.
- **Move Postgres/Redis off the web host** — only if traffic grows well beyond what
  2C/8GB handles. Not urgent.
- **Redis‑backed sessions (site‑wide).** Deliberately skipped in Phase 1 (would log out all
  users and pressure the shared Redis, which runs `allkeys-lru` at 143/200 MB). If ever
  wanted, do it post‑upgrade in a **dedicated Redis DB**, and expect a one‑time forced
  logout.
