# LIXAFA — Staging Write-Quiescence Patch and Rollout Addendum

**Status:** prepared and tested locally only. No commit, push, deployment,
Railway-variable change, staging preflight, backup, restore, or migration was
performed by this work.

This addendum is deliberately separate from the Phase 1 report in the dirty
catalog worktree. It records an isolated patch based on the deployed staging
baseline and must be reviewed with that report before any operator action.

## 1. Isolated baseline and scope

- Isolated worktree base: `88c3b4e8146e1e7cbde05312005cfcc30af6d680`.
- Railway metadata had previously verified that this is the active `web`
  deployment revision for project `satisfied-miracle`, environment label
  `production` (owner-confirmed LIXAFA staging), not a production LIXAFA
  environment.
- Service in scope: `web`; public staging URL:
  `https://lixafa-staging.up.railway.app`.
- The isolated worktree began clean and is detached at that exact baseline.
  The dirty main worktree containing local Phase 0/1 catalog work was not
  modified.

This patch does not contain `catalog_schema.py`,
`scripts/migrate_catalog_sync.py`, a migration, catalog Preview/Apply work, or
any provider configuration change.

## 2. Implemented staging-only switch

`LIXAFA_WRITE_QUIESCENCE=0` is the documented default. A true value is
accepted only when both conditions hold:

1. `APP_ENV=staging`; and
2. `DATABASE_URL` uses the `postgresql+asyncpg` dialect.

Therefore the temporary mode cannot be accidentally enabled in development,
test, or production, and it cannot pair staging quiescence with SQLite.
The switch is process configuration only; it is never stored in the
application database.

When enabled, the process applies all of the following before route logic:

- `app.main.lifespan` completes its existing staging connection check but does
  not create `auto_engine_loop()`; it records `engine_stopped=true` in the
  process-local gate.
- Global HTTP middleware returns a deterministic `503` with
  `Retry-After: 60` and `Cache-Control: no-store` for every request except
  `GET /admin/health`. This precedes rate limiting, route matching,
  authentication, request parsing, lazy customer bootstrap, callback audit
  handling, and static mounts.
- `GET /admin/health` is handled by the middleware itself, so it does not call
  a route or database dependency. Its sanitized acknowledgement includes:

  ```json
  {
    "write_gate": "enabled",
    "engine": "stopped",
    "engine_stopped": true,
    "in_flight_writers": 0,
    "database_writes_allowed": false
  }
  ```

  The response also carries the non-secret readiness and single-process
  observations used by the operator check.
- A PostgreSQL connection opened in this mode receives the connection-local
  asyncpg server setting `default_transaction_read_only=on`. It is a defense
  in depth guard only; it does not alter a persistent database setting.
- Direct catalog sync, wallet mutation/lazy wallet creation, and promotion
  reservation/finalize/release writers acquire the same gate before provider
  or database access. This protects normal runtime paths that would otherwise
  bypass a route middleware.
- `auto_engine_loop()` also refuses to begin in this mode. If a future
  in-process transition races with an already-admitted cycle, that cycle drains
  under its existing lease, no new cycle is admitted, and the loop marks itself
  stopped.

The temporary `503` applies to Tripay and Digiflazz callbacks as well. It does
not write an audit/webhook row. Provider retry behavior remains provider-owned:
operators may expect a later retry after the gate is released, but must not
claim that a retry occurred without provider evidence.

## 3. In-flight writer fence and topology limit

`app.core.write_quiescence.WriteQuiescenceGate` is a process-local,
thread-safe admission fence. It uses a lock plus top-level writer count and
same-`asyncio.Task` re-entrant leases:

1. an admitted request/cycle increments the count before protected work;
2. enabling the gate atomically closes new admission;
3. already-admitted work can finish and decrements on exit;
4. readiness is false until the engine is stopped and the count is zero; and
5. a writer admitted after that acknowledgement is impossible because the
   gate remains closed.

This is intentionally valid only for the current staging topology: **one web
replica, one process, and no separate worker/writer service**. It fails the
in-process readiness signal when `WEB_CONCURRENCY`, `UVICORN_WORKERS`, or
`GUNICORN_WORKERS` is explicitly anything other than `1` (or malformed).
An application process cannot discover a different Railway replica, so the
deployment procedure below separately requires Railway inventory evidence of
one `web` instance and no worker service. Do not treat a local counter as proof
about another replica or an external script.

External migration/reset scripts are not made safe by this process-local fence
and remain prohibited during the window.

## 4. Test evidence

The isolated worktree used the existing Python 3.11.9 interpreter without
altering the older environment. No provider API, Railway service, or real
staging database was contacted.

Focused coverage added in `tests/test_write_quiescence.py` proves:

- drain behavior, post-enable admission rejection, concurrent post-enable
  rejection, and explicit multi-worker readiness failure;
- the only enabled HTTP acknowledgement is read-only health;
- checkout, lazy `GET /api/customer/wallet`, cancel, wallet payment,
  malformed Tripay/Digiflazz callbacks, admin login/product/promo/refund,
  catalog sync, and incorrect-method health requests return the maintenance
  response before their local writer/provider helpers;
- enabled lifespan does not start the engine; direct engine invocation does
  not invoke any reconciler, provider attempt, outbox, or catalog-sync cycle;
- direct wallet, promotion, and catalog-sync calls are rejected before their
  database/provider primitives; and
- the normal disabled health route and embedded-engine startup behavior remain
  intact.

`tests/test_settings.py` also proves the switch defaults to disabled and is
limited to staging asyncpg configuration. The existing SQLite eligibility
fixture now closes its raw setup connection explicitly; this is a test-only
Windows file-handle cleanup, not a production behavior change. It fixed a
teardown `PermissionError` without weakening any migration assertion.

Commands actually run in this isolated worktree:

```text
Python 3.11.9
pytest tests/test_write_quiescence.py tests/test_settings.py -q
23 passed, 1 warning

pytest -q
217 passed, 0 failed, 0 skipped, 1 warning in 148.91s
```

The sole warning is the pre-existing Starlette/AnyIO `BlockingPortal` alias
deprecation. The count is intentionally not compared with the owner-reported
245-test Phase 1 catalog worktree run: that later local catalog code is not in
this isolated deployed-baseline worktree.

## 5. Prepared deployment plan — do not execute as part of this task

### 5.1 Isolate the deployable change

Before any future approval, review the isolated worktree only. The candidate
allowlist is:

```text
.env.example
app/core/database.py
app/core/settings.py
app/core/write_quiescence.py
app/engine.py
app/main.py
app/promotions/service.py
app/services/catalog_sync_service.py
app/services/wallet_service.py
tests/test_promo_eligibility_schema.py
tests/test_settings.py
tests/test_write_quiescence.py
docs/LIXAFA_STAGING_WRITE_QUIESCENCE_PATCH_2026-10-06.md
```

The owner must compare this allowlist against the baseline (`git diff --check`
and `git diff --name-only <baseline>`) before creating a dedicated reviewed
commit from the isolated worktree. Do not stage, cherry-pick, or deploy any
file from the dirty catalog worktree. In particular, do not include catalog
migration source, Phase 0/1 schema work, or provider feature work.

After separate owner approval, deploy only that reviewed patch to Railway
project `satisfied-miracle`, environment `production` (the confirmed staging
label), service `web`. First verify the deployed revision and normal default
mode; then enable the switch on that same service. A future operator command
has this shape and must not be run from this task:

```powershell
railway variable set LIXAFA_WRITE_QUIESCENCE=1 `
  --project satisfied-miracle --environment production --service web
```

Do not use `railway variable list --json` or `--kv` in shared logs because
those forms can expose raw variable values. The variable change is expected to
restart/deploy the service; wait for its new deployment to become healthy.

### 5.2 Required quiescence proof

With the gate enabled, obtain all of the following before any preflight:

1. `GET https://lixafa-staging.up.railway.app/admin/health` reports
   `write_gate=enabled`, `engine=stopped`, `engine_stopped=true`,
   `in_flight_writers=0`, `database_writes_allowed=false`, and `ready=true`.
2. Read-only Railway status evidence confirms exactly one running `web`
   instance and no worker/service capable of writing. Check the service
   inventory, not merely the process-local health response:

   ```powershell
   railway status --project satisfied-miracle --environment production --json
   ```

3. Through owner-authenticated read-only PostgreSQL access, run only this
   corroborating activity query and require zero:

   ```sql
   SELECT count(*) AS active_non_inspection_client_backends
   FROM pg_stat_activity
   WHERE datname = current_database()
     AND pid <> pg_backend_pid()
     AND backend_type = 'client backend'
     AND state <> 'idle';
   ```

If any value differs, any callback reaches a database, any writer is present,
or the topology cannot be proven, abort the window. Do not start a preflight or
dump.

## 6. Correct staging-review order after the gate exists

The accepted staging target fingerprint remains:

```text
a9e28c4ae2f1d091acd04e7cf561c1035c56033553c54aa1e31c9ed3e20c3126
```

The remaining operational gates are now ordered as follows:

1. Deploy the reviewed isolated patch and enable verified write-quiescence.
2. Prove engine stopped and writer drain/zero active writers as in Section 5.2.
3. Execute the separately owner-reviewed authoritative **read-only** full
   staging preflight while that same gate remains active.
4. Immediately create the full custom-format `pg_dump` artifact while the
   same gate remains active.
5. Release the staging write gate only after the dump command has exited zero,
   the named artifact exists with positive size, and its SHA-256/size/timestamp
   evidence is recorded.
6. Restore and verify independently on a new isolated restore-only target.
7. Generate the valid backup manifest bound to the accepted target fingerprint.
8. Obtain explicit owner approval before any staging migration.

The quiescence patch deliberately does not deploy the local catalog migration
source. The later full-preflight execution must use the already-reviewed,
hash-verified operator procedure and must not silently deploy dirty code.

## 7. Backup/restore prerequisites retained for the next gate

This patch creates no backup. For the later approved operator window:

- Use **PowerShell Core (`pwsh`) 7.4 or newer** for binary archive handling.
  Windows PowerShell 5.1 remains rejected.
- Use Railway staging PostgreSQL 18.6 tooling where available. Local 18.4 is
  same-major validation evidence only, not exact patch parity; record actual
  `pg_dump` and `pg_restore` versions privately.
- Keep the custom-format artifact in encrypted, owner-only storage outside the
  repository, OneDrive, chat, and general temporary folders. Do not put a
  database URL or credentials in command history or operator evidence.
- The source dump must be a full custom archive with the reviewed
  `pg_dump --no-password --format=custom --no-owner --no-privileges
  --serializable-deferrable` flags, using inherited Railway/libpq credentials
  only. Preserve stdout as archive bytes and stderr separately; never combine
  them.
- Restore only into a newly created, never-used, local isolated database named
  `lixafa_staging_restore_<UTC-YYYYMMDDTHHMMSSZ>_<8-lowercase-hex>`. It must
  never be an application target, `catalog_test`, `postgres`, or `railway`.
- Use the reviewed direct restore form:
  `pg_restore --clean --if-exists --no-owner --no-privileges --exit-on-error
  --dbname=<generated-restore-db> <protected-dump-path>`.
- Require archive TOC, source/restore identity, schema and index digest,
  aggregate critical-table checks, and restored-database preflight/baseline
  comparison before accepting the artifact.
- Only after that independent restore passes, create the manifest with
  `manifest_version=lixafa-catalog-backup-v1`, `dialect=postgresql`, the
  accepted `target_fingerprint`, fresh `baseline_fingerprint`, relative
  artifact filename, SHA-256, positive size, `dump_tool=pg_dump`,
  `backup_format=custom`, `pg_dump_version`, `dump_completed_at`,
  `restore_verified_at`, and nonempty `restore_verification_id`.

## 8. Abort and rollback plan

Abort before preflight/dump/migration if health is not ready, topology differs,
an active writer or unexpected client backend exists, the patch behaves
unexpectedly, or any protected operation reaches database/provider code.

The patch performs no schema/data mutation. Therefore the operational rollback
is:

1. set `LIXAFA_WRITE_QUIESCENCE=0` on `satisfied-miracle` / `production` /
   `web` using the same reviewed service targeting;
2. wait for the resulting normal deployment;
3. verify `/admin/health` returns the ordinary health response and the normal
   embedded engine starts; and
4. if the patch release itself is defective, roll the web service source back
   to the verified baseline revision
   `88c3b4e8146e1e7cbde05312005cfcc30af6d680`, with the switch disabled.

Do not run a migration as a rollback action. If a later preflight or dump
fails, discard it as migration evidence and begin again from a fresh approved
quiescent window.

## 9. Historical pre-final-audit conclusion

The patch is ready for code/owner review only. The required staging actions
remain unexecuted.

<!-- Historical pre-final-audit label; it is superseded by Section 10:

**Final verdict: `PHASE 1 VALIDATED — READY FOR STAGING MIGRATION REVIEW`**
-->

## 10. Final executed deployment-readiness audit — 2026-10-06

This audit was executed only in the isolated worktree. It did not commit,
push, deploy, contact Railway, change variables, run a staging preflight,
create a backup/restore target, or run a migration.

### 10.1 Exact baseline and Git evidence

Verified isolated-worktree HEAD:

```text
88c3b4e8146e1e7cbde05312005cfcc30af6d680
```

Executed `git status --short`:

```text
 M .env.example
 M app/core/database.py
 M app/core/settings.py
 M app/engine.py
 M app/main.py
 M app/promotions/service.py
 M app/services/catalog_sync_service.py
 M app/services/wallet_service.py
 M tests/test_promo_eligibility_schema.py
 M tests/test_settings.py
?? app/core/write_quiescence.py
?? docs/LIXAFA_STAGING_WRITE_QUIESCENCE_PATCH_2026-10-06.md
?? tests/test_write_quiescence.py
```

Both `git diff --check` and `git diff --check
88c3b4e8146e1e7cbde05312005cfcc30af6d680` completed with empty output.
The three untracked candidates also passed `git diff --no-index --check`
against `NUL`.

Executed `git diff --name-status
88c3b4e8146e1e7cbde05312005cfcc30af6d680`:

```text
M  .env.example
M  app/core/database.py
M  app/core/settings.py
M  app/engine.py
M  app/main.py
M  app/promotions/service.py
M  app/services/catalog_sync_service.py
M  app/services/wallet_service.py
M  tests/test_promo_eligibility_schema.py
M  tests/test_settings.py
```

Executed `git diff --stat 88c3b4e8146e1e7cbde05312005cfcc30af6d680`:

```text
 .env.example                           |  3 ++
 app/core/database.py                   |  8 ++-
 app/core/settings.py                   | 11 +++-
 app/engine.py                          | 38 ++++++++++---
 app/main.py                            | 98 ++++++++++++++++++++++++----------
 app/promotions/service.py              | 52 ++++++++++++++++++
 app/services/catalog_sync_service.py   |  8 +++
 app/services/wallet_service.py         | 36 +++++++++++++
 tests/test_promo_eligibility_schema.py | 20 ++++++-
 tests/test_settings.py                 | 39 ++++++++++++++
 10 files changed, 276 insertions(+), 37 deletions(-)
```

The Git diff commands do not include untracked files. The full candidate list,
including those three files, is exactly:

```text
.env.example
app/core/database.py
app/core/settings.py
app/core/write_quiescence.py
app/engine.py
app/main.py
app/promotions/service.py
app/services/catalog_sync_service.py
app/services/wallet_service.py
docs/LIXAFA_STAGING_WRITE_QUIESCENCE_PATCH_2026-10-06.md
tests/test_promo_eligibility_schema.py
tests/test_settings.py
tests/test_write_quiescence.py
```

The allowlist comparison was exact: no file exists outside it. No Catalog
Phase 0/1 application source, migration/schema source or runner, provider
credential/configuration source, or local secret file is present. The
schema-named test file is the approved SQLite test-fixture cleanup only. The
only ignored artifacts found were pytest/Python cache directories.

### 10.2 Startup-write conclusion

The enabled setting permits only staging plus a PostgreSQL asyncpg URL. A
controlled import probe denied socket connections and completed, so module
import and FastAPI app construction do not enter lifespan or open a network
connection. The executed lifecycle probe recorded:

```text
startup_path=staging_verify_only
init_db_calls=0
lifespan_engine_task_calls=0
```

Automatic bootstrap is limited to development/test. Therefore staging startup
uses only `verify_database_connection()` and its `SELECT 1`; it does not
call `init_db()`. The enabled engine uses connection-local
`default_transaction_read_only=on`. Lifespan marks the engine stopped and
does not create the embedded engine task. No DDL, DML, bootstrap writer,
provider call, catalog/promotion initialization, or database audit write is
admitted before maintenance readiness.

### 10.3 Middleware conclusion

The write gate runs before rate limiting, `call_next`, routing,
authentication, dependency injection, request parsing, audit helpers, and
static mounts. `GET /admin/health` returns the maintenance payload directly
without database access. CORS is the only outer middleware; a valid
`OPTIONS` preflight can be answered there, but it is non-routing and
non-writing. Every actual route request reaches the gate first.

The executed in-process probe returned maintenance health successfully and
returned `503 WRITE_QUIESCENCE_ACTIVE` for six representative request
classes: top-up, lazy wallet read, malformed callback, provider webhook,
admin login, and admin catalog sync. Patched route writers, admin bootstrap,
embedded engine, and catalog provider client recorded zero calls.

### 10.4 Engine and writer-fence conclusion

`auto_engine_loop()` exits before any reconciler, retry/provider attempt,
outbox/notification work, wallet work, promotion work, or catalog sync can
start. The normal engine cycle has one top-level writer lease; direct catalog,
wallet, and promotion writers have re-entrant gates before database/provider
access.

The executed concurrency probe allowed one existing writer to drain, rejected
all 32 later admissions after quiescence began, and made readiness true only
after the writer exited with zero active writers and engine stopped. It also
confirmed zero embedded reconciler starts and zero catalog provider/database
calls while enabled.

### 10.5 Topology conclusion

The patch explicitly requires one web replica, one process, and no separate
worker. `WEB_CONCURRENCY`, `UVICORN_WORKERS`, and `GUNICORN_WORKERS`
must be absent or exactly `1`; malformed or other explicit values fail
readiness. The executed probe verified failure for `2`, `3`, and a
malformed value.

Process-local code cannot discover another Railway replica or external worker.
The deployment operator must still verify one running web instance and no
separate writer service before relying on the health acknowledgement.

### 10.6 Secret and error-exposure conclusion

The health payload contains only gate/engine state, a writer count, and
booleans. The blocked response is fixed and carries no exception detail. No
raw `DATABASE_URL`, password, Railway variable, provider secret, or
environment-variable enumeration was added. Only the three named worker-count
variables are read. Database startup logging uses the existing masked-host
summary; no Digiflazz/Tripay credential handling changed.

The enabled maintenance health/block paths do not render a traceback or an
exception to clients. The pre-existing normal-startup exception logger remains
outside the enabled quiescence branch.

### 10.7 Re-executed tests

Tests ran in this isolated worktree with Python 3.11.9 and a fresh local
disposable SQLite URL. No Railway connection or provider API call was used.

```text
python -m pytest tests/test_write_quiescence.py tests/test_settings.py -q
23 passed, 1 warning in 5.30s

python -m pytest -q
217 passed, 1 warning in 126.73s
```

There were zero failures and zero skips. The only warning is the existing
Starlette/AnyIO `anyio.abc.BlockingPortal` deprecation.

**WRITE-QUIESCENCE PATCH READY FOR OWNER COMMIT/DEPLOY APPROVAL**
