# LIXAFA Catalog Phase 1 — Database & Schema Report

Date label: 2026-10-04. Validation resumed and completed locally on 2026-10-05.

## 1. Scope

Phase 1 implements only the additive catalog schema, its explicit migration
runner, backup/preflight gates, legacy backfill, schema verification, and
disposable-database tests. It does not implement catalog sync, Preview/Apply,
ranking, checkout selection, provider calls, Railway changes, startup or
Pre-Deploy migration, UI changes, commits, pushes, or deployment.

Phase 0 remains isolated: its safety suite passed and no capability gate,
provider credential, legacy-sync behavior, transaction engine, checkout, or
storefront file was changed by this Phase 1 work.

## 2. New schema

New catalog-only tables are created by the dedicated runner:

- `provider_products`: independent provider/supplier candidate rows with a
  LIXAFA ID, provider/scope/SKU/seller evidence, nullable logical-product
  link, mapping provenance, raw allow-listed attributes, cost/status/stock/
  cut-off/availability facts, and observation/audit fields. Provider business
  fields intentionally have no uniqueness constraint.
- `catalog_sync_runs`: run metadata, immutable-snapshot identity, safe counts,
  status, source Preview relation, and sanitized error fields.
- `catalog_sync_run_items`: safe snapshot rows, source/identity fingerprints,
  proposed action/reasons, and review flag. It has `UNIQUE(run_id,
  sequence_no)`, `RESTRICT` foreign key protection, non-null ID, and
  append-only guards for UPDATE/DELETE.
- `catalog_mapping_reviews`: durable mapping/review evidence and decision
  metadata with `RESTRICT` references.
- `catalog_provider_sync_state`: one provider/scope state record for durable
  rate-limit and last-known-good information.
- `catalog_sync_locks`: provider/scope lease, owner/run, expiry, and fencing
  generation.

All new IDs are text UUID/ULID-compatible fields. New monetary candidate
fields are `NUMERIC`; catalog tables use `RESTRICT`, never cascade, for their
declared relationships. The schema is only storage and safety structure in
this phase—there is no network, Apply, ranking, or transaction behavior.

## 3. Modified schema

Only `products` is extended additively. `sku` remains its primary key and all
legacy fields remain untouched. New nullable fields are:

`catalog_product_id`, `logical_key`, `logical_key_version`,
`denomination_text`, `denomination_value`, `denomination_unit`,
`product_semantics`, and `store_enabled`.

`catalog_product_id` receives the one new unique index after backfill.
`logical_key` is explicitly non-unique. No `topup`, wallet, promotion history,
provider-attempt, payment, checkout, or transaction-engine table is changed.

## 4. Migration ID and checksum

- Migration ID: `20261004_catalog_schema_v1`
- SHA-256 checksum: `dd7523e6ab29c7180c660b942f9d219d83f0e7ad980a65a8de39d7faf461f0d8`

The marker is written last in `schema_migrations`. The runner rejects an
incompatible marker table, non-primary marker identity, duplicate marker rows,
or any checksum mismatch. Its checksum covers the declared catalog DDL,
indexes, immutable-run-item contract, and backfill plan version.

## 5. Preflight and backup requirements

`scripts/migrate_catalog_sync.py` supports only explicit owner commands:
`preflight`, `upgrade`, and `verify`. It imports neither application bootstrap
helpers nor provider services, never calls `init_db()`, and is not referenced
by startup or Pre-Deploy code.

The runner requires a target fingerprint returned by preflight. It never
prints the connection URL; `--database-url-env` is available for credentialed
URLs so they need not be placed in shell history. PostgreSQL identity is bound
to the connected database and effective schema as well as host/port/database.

SQLite behavior:

- rejects absent files, directories, and `:memory:` before opening a
  connection, so preflight cannot create an accidental target;
- requires an explicit `--sqlite-engine-stopped` acknowledgement and acquires
  `BEGIN IMMEDIATE` before mutation;
- creates an online backup only inside upgrade, before DDL/backfill, verifies
  `integrity_check`, SHA-256, size, target fingerprint, and baseline
  fingerprint, then writes a sidecar manifest;
- refuses a missing, invalid, mismatched, or partially existing backup.

PostgreSQL behavior:

- requires a transactional advisory lock;
- requires a pre-existing `pg_dump` artifact manifest before mutation;
- requires artifact hash/size, target and baseline fingerprints, dump tool,
  supported format, `pg_dump` version/time, and independent restore-verification
  time/ID. Missing evidence fails closed.

`preflight` exposes `can_upgrade` for schema suitability and `can_apply` for
the additional backup-ready state. A first SQLite upgrade safely creates and
verifies its requested backup under the migration lock; preflight itself stays
read-only.

## 6. Backfill result on disposable databases

Fresh SQLite fixtures containing the three required bootstrap products created
three stable `catalog_product_id` UUIDs. Rich legacy fixtures preserved all
four product records including `legacy-special`; their legacy SKU, name,
price, cost price, active/publication fields, display/media/promo metadata,
historical `topup`, promotion targets, and provider-attempt rows stayed
identical.

Backfill fills only null new fields:

- one UUID per legacy product;
- `store_enabled` from its explicit binary `active` value;
- `logical_key=legacy-sku:<case-folded sku>` and
  `logical_key_version=legacy-sku-v1`;
- no denomination or semantic value inferred;
- no `provider_products` candidate inferred or created.

Malformed prefilled values, blank/invalid IDs, duplicate IDs, invalid logical
keys/versions, invalid publication flags, bootstrap absence, and case-folded
SKU collisions are rejected rather than repaired speculatively.

## 7. SQLite tests

`tests/test_catalog_schema_migration.py` completed with **19 passed, 1
skipped** on disposable temporary SQLite files. Coverage includes fresh and
legacy schema, no accidental target creation, additive fields/defaults,
schema/index/PK/FK shape rejection, marker checksums, backup failures,
fingerprint and stopped-engine acknowledgement, lock contention, real
transaction rollback/retry, resumable backfill validation, immutable snapshot
guards, candidate duplication/many-to-one mapping, legacy compatibility, CLI
redaction, and no HTTP provider request.

## 8. PostgreSQL tests

**NOT TESTED.** No explicitly marked disposable PostgreSQL URL was available;
there were no `CATALOG_TEST_POSTGRES_*`, `POSTGRES*`, or `DATABASE_URL` test
environment variables, and local `psql`, `pg_dump`, `pg_restore`, and Docker
were unavailable. The PostgreSQL test lane was skipped rather than pointed at
Railway or any other database.

PostgreSQL DDL, advisory locking, current-schema target binding, and verified
backup-manifest rules are implemented but need fresh/legacy disposable
PostgreSQL integration coverage before any staging invocation.

## 9. Idempotency and recovery tests

Rerunning a completed migration is verification-only and preserves existing
IDs. A resumable pre-marker state fills only null fields and keeps IDs already
created. A simulated exception during a real backfill transaction rolled back
the DDL/backfill; the verified backup remained available and a retry completed
successfully. Concurrent SQLite migration attempts are rejected by the
immediate lock.

## 10. Legacy compatibility

The test fixture snapshots prove preservation of legacy product values,
historical order snapshots (`nominal`, `price`, `product_cost`), promotion
references/targets, and `SENT_UNKNOWN` provider-attempt evidence. The full
existing wallet, promotion, checkout, and engine regression suites also pass.
No legacy bootstrap product was removed or auto-mapped to a supplier.

## 11. Full regression results

Full collection: **239 tests**. It was executed as two non-overlapping batches
to retain complete terminal status:

- 114 passed, 1 skipped, 1 deprecation warning;
- 124 passed.

Total: **238 passed, 1 skipped, 1 warning**. The skipped test is the explicit
PostgreSQL disposable lane above. The warning is an existing Python 3.9
`asyncio` loop-argument deprecation from the async stack; it is not a test
failure. Phase 0 safety tests: **8 passed**.

## 12. Python runtime verification

`runtime.txt` declares `python-3.11`, but the local virtual environment is
Python **3.9.7** and the Windows launcher reported no installed system Python.
Therefore Python 3.11 validation is **NOT VERIFIED**. This is a deployment
blocker that must be closed in a Python 3.11 environment before staging
migration approval.

## 13. Changed files

- `catalog_schema.py` — standalone, direct database migration primitives and
  contract verification.
- `scripts/migrate_catalog_sync.py` — explicit owner-runner.
- `tests/test_catalog_schema_migration.py` — disposable schema/migration
  coverage.
- `docs/LIXAFA_CATALOG_PHASE1_SCHEMA_REPORT_2026-10-04.md` — this report.

Existing uncommitted Phase 0 and historical worktree changes were preserved;
they were neither reverted nor committed.

## 14. Remaining risks

- PostgreSQL fresh/legacy migration, backup, advisory-lock, and schema
  assertions are not yet exercised against a disposable PostgreSQL database.
- Python 3.11 has not been locally exercised despite the deployment runtime
  declaration.
- A PostgreSQL backup manifest is an owner-supplied operational artifact; the
  runner validates its evidence and fails closed, but cannot itself perform an
  independent restore in this workspace.
- No migration has been run on staging, by design. No provider credential,
  request, supplier candidate, transaction, or real catalog data was used.

## 15. Staging migration readiness

**NOT READY FOR STAGING INVOCATION.** The source runner is safe to review, but
owner approval must wait for (1) disposable PostgreSQL test coverage under
Python 3.11, (2) a reviewed PostgreSQL `pg_dump` + restore-verification
artifact, and (3) an explicit owner migration window/target-fingerprint
confirmation. Do not add this runner to Railway Pre-Deploy or application
startup.

## 16. Phase 2 readiness

After the staging gates above and owner review are complete, Phase 2 may start
only with pure model/normalization/validation/fixture work. It must remain
mocked and must not add provider calls, Preview/Apply, catalog scheduling,
ranking, checkout routing, or credential changes without separate approval.

## 17. Final verdict

**PHASE 1 COMPLETE — READY FOR REVIEW**

This verdict means the requested local implementation and disposable SQLite
validation are complete and ready for owner review. It does **not** authorize
staging migration, commit, push, deployment, provider activation, or Phase 2
implementation.
