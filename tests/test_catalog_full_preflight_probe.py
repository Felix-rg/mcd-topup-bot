"""Regression coverage for the hash-pinned full catalog preflight probe."""

from __future__ import annotations

import asyncio
import ast
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

import catalog_schema
import scripts.migrate_catalog_sync as catalog_runner
from catalog_schema import DatabaseTarget, DatabaseTargetError


ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = ROOT / "scripts" / "probe_catalog_full_preflight.py"
_MODULE_NAME = "_lixafa_catalog_full_preflight_probe_test"
_TARGET_FINGERPRINT = "a" * 64
_BASELINE_FINGERPRINT = "b" * 64


def _load_probe() -> Any:
    existing = sys.modules.get(_MODULE_NAME)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, PROBE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


probe = _load_probe()


def test_probe_and_migration_runner_share_the_canonical_preflight_entry_point() -> None:
    """The staging wrapper may add a guard, but never a second algorithm."""

    assert catalog_runner.preflight_catalog_schema is catalog_schema.preflight_catalog_schema
    assert "await preflight_catalog_schema(" in (
        ROOT / "scripts" / "migrate_catalog_sync.py"
    ).read_text(encoding="utf-8")
    assert "await preflight(" in PROBE_PATH.read_text(encoding="utf-8")


def _canonical_report() -> dict[str, Any]:
    return {
        "target": {
            "fingerprint": _TARGET_FINGERPRINT,
            "host": "sensitive-host.example",
            "database": "sensitive_database",
        },
        "baseline": {
            "fingerprint": _BASELINE_FINGERPRINT,
            "product_count": 3,
            "topup_count": 5,
            "promo_count": 2,
            "promotion_target_count": 1,
            "bootstrap_presence": {
                "ml_10k": True,
                "ff_12k": True,
                "pubg_20k": True,
            },
            "raw_sku_duplicates": False,
            "casefold_sku_collisions": [],
            "untrusted_rows": ["must-not-leak"],
        },
        "base_schema": {"valid": True, "missing": {"sensitive": ["column"]}},
        "catalog_schema": {"state": "absent", "errors": ["untrusted-detail"]},
        "marker": {"state": "absent", "checksum": "untrusted-checksum"},
        "backfill_precheck": None,
        "issues": [],
        "can_upgrade": True,
        "backup": {
            "required": True,
            "valid": False,
            "reason": "backup_manifest_not_supplied",
            "untrusted": "must-not-leak",
        },
        "can_apply": False,
    }


def _module_with_report(report: dict[str, Any], calls: list[tuple[str, dict[str, Any]]]) -> types.ModuleType:
    module = types.ModuleType("catalog_schema")

    async def preflight_catalog_schema(database_url: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((database_url, kwargs))
        return report

    module.preflight_catalog_schema = preflight_catalog_schema
    return module


def test_probe_delegates_to_exact_canonical_preflight_and_strictly_sanitizes() -> None:
    secret_url = "postgresql+asyncpg://probe_user:probe-secret@db.example.test/railway"
    calls: list[tuple[str, dict[str, Any]]] = []
    payload = asyncio.run(
        probe.probe_database_url(
            secret_url,
            expected_target_fingerprint=_TARGET_FINGERPRINT,
            catalog_module=_module_with_report(_canonical_report(), calls),
        )
    )

    assert calls == [(secret_url, {"postgres_read_only": True})]
    assert payload == {
        "probe": "lixafa_catalog_full_preflight_v1",
        "transaction_read_only": True,
        "target_fingerprint": _TARGET_FINGERPRINT,
        "baseline_fingerprint": _BASELINE_FINGERPRINT,
        "can_upgrade": True,
        "can_apply": False,
        "catalog_schema_state": "absent",
        "backup": {
            "required": True,
            "valid": False,
            "reason": "backup_manifest_not_supplied",
        },
        "readiness": {
            "base_schema_valid": True,
            "marker_state": "absent",
            "backfill_precheck_valid": None,
            "issues_present": False,
            "bootstrap_products_complete": True,
            "raw_sku_duplicates": False,
            "casefold_sku_collision_count": 0,
        },
        "counts": {
            "products": 3,
            "topup": 5,
            "promos": 2,
            "promotion_targets": 1,
        },
        "success": True,
    }
    rendered = json.dumps(payload, sort_keys=True)
    for forbidden in (
        secret_url,
        "probe-secret",
        "sensitive-host.example",
        "sensitive_database",
        "must-not-leak",
        "untrusted-detail",
    ):
        assert forbidden not in rendered


def test_probe_rejects_a_target_fingerprint_mismatch_before_output() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    with pytest.raises(probe.ProbeError, match="target_fingerprint_mismatch"):
        asyncio.run(
            probe.probe_database_url(
                "postgresql+asyncpg://user:secret@db.example.test/railway",
                expected_target_fingerprint="c" * 64,
                catalog_module=_module_with_report(_canonical_report(), calls),
            )
        )
    assert calls and calls[0][1] == {"postgres_read_only": True}


def test_probe_main_redacts_unexpected_preflight_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret_url = "postgresql+asyncpg://probe_user:probe-secret@db.example.test/railway"
    module = types.ModuleType("catalog_schema")

    async def failing_preflight(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError(f"database failure for {secret_url}")

    module.preflight_catalog_schema = failing_preflight
    monkeypatch.setenv("DATABASE_URL", secret_url)
    monkeypatch.setattr(probe, "_catalog_schema_module", lambda: module)

    assert probe.main([]) == 1
    output = capsys.readouterr().out.strip()
    assert json.loads(output) == {
        "error_code": "preflight_probe_failed",
        "probe": "lixafa_catalog_full_preflight_v1",
        "success": False,
    }
    assert secret_url not in output
    assert "probe-secret" not in output
    assert "RuntimeError" not in output


def test_bundle_hashes_bind_exact_local_sources_without_connection_data() -> None:
    hashes = probe.source_hashes()
    payload = probe.build_remote_source()

    assert hashes["catalog_schema_sha256"] == hashlib.sha256(
        (ROOT / "catalog_schema.py").read_bytes()
    ).hexdigest()
    assert hashes["probe_sha256"] == hashlib.sha256(PROBE_PATH.read_bytes()).hexdigest()
    assert hashes["remote_bundle_sha256"] == hashlib.sha256(payload).hexdigest()
    assert b"sys.modules['catalog_schema'] = _catalog_module" in payload
    assert payload.find(b"sys.modules['catalog_schema'] = _catalog_module") < payload.find(
        b"exec(compile(_catalog_source"
    )
    assert b"postgresql+asyncpg://probe_user:probe-secret@" not in payload


def test_remote_bundle_loads_exact_catalog_source_in_memory_without_database_access() -> None:
    environment = dict(os.environ)
    environment.pop("DATABASE_URL", None)
    result = subprocess.run(
        [sys.executable, "-"],
        input=probe.build_remote_source(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=environment,
        cwd=ROOT,
    )

    assert result.returncode == 2
    assert result.stderr == b""
    assert json.loads(result.stdout) == {
        "error_code": "database_url_not_set",
        "probe": "lixafa_catalog_full_preflight_v1",
        "success": False,
    }


def test_stream_mode_rejects_wrong_hash_before_writing_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written = bytearray()

    class Buffer:
        def write(self, value: bytes) -> None:
            written.extend(value)

    class Stdout:
        buffer = Buffer()

    monkeypatch.setattr(probe.sys, "stdout", Stdout())
    with pytest.raises(probe.ProbeError, match="remote_bundle_sha256_mismatch"):
        probe._stream_remote_source("0" * 64)
    assert not written


def test_probe_wrapper_has_no_application_lifecycle_or_second_preflight_algorithm() -> None:
    source = PROBE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert not any(
        module == "app"
        or module.startswith("app.")
        or module in {"database", "config", "engine"}
        or module.startswith(("routes", "services", "scripts.migrate_catalog_sync"))
        for module in imported_modules
    )
    defined_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    forbidden_definitions = {
        "database_target",
        "_resolved_target_summary",
        "_capture_legacy_baseline",
        "_preflight_on_connection",
        "_backup_preflight",
        "preflight_catalog_schema",
        "upgrade_catalog_schema",
        "verify_catalog_schema",
        "init_db",
    }
    assert not (defined_names & forbidden_definitions)
    assert "postgres_read_only=True" in source
    assert "upgrade_catalog_schema" not in source
    assert "verify_catalog_schema" not in source
    assert "init_db" not in source


class _FakeConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def exec_driver_sql(self, statement: str) -> None:
        self.statements.append(statement)


class _FakeConnectContext:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _FakeConnection:
        return self.connection

    async def __aexit__(self, *_args: Any) -> None:
        return None


class _FakeEngine:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection
        self.disposed = False

    def connect(self) -> _FakeConnectContext:
        return _FakeConnectContext(self.connection)

    async def dispose(self) -> None:
        self.disposed = True


def _postgres_target() -> DatabaseTarget:
    return DatabaseTarget(
        database_url="postgresql+asyncpg://user:secret@db.example.test/railway",
        dialect="postgresql",
        fingerprint="d" * 64,
        summary={"dialect": "postgresql", "host": "db***", "database": "railway"},
    )


def _core_preflight_report() -> dict[str, Any]:
    return {
        "target": {"fingerprint": _TARGET_FINGERPRINT},
        "marker": {"state": "absent"},
        "baseline": {"fingerprint": _BASELINE_FINGERPRINT},
        "can_upgrade": True,
    }


def test_postgres_read_only_preflight_wraps_the_same_core_in_begin_and_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _postgres_target()
    connection = _FakeConnection()
    engine = _FakeEngine(connection)
    create_calls: list[bool] = []
    core_calls: list[tuple[object, object]] = []

    def fake_create_migration_engine(
        actual_target: DatabaseTarget,
        *,
        postgres_read_only: bool = False,
    ) -> _FakeEngine:
        assert actual_target is target
        create_calls.append(postgres_read_only)
        return engine

    async def fake_core(actual_connection: object, actual_target: object) -> dict[str, Any]:
        core_calls.append((actual_connection, actual_target))
        return _core_preflight_report()

    monkeypatch.setattr(catalog_schema, "database_target", lambda _url: target)
    monkeypatch.setattr(catalog_schema, "create_migration_engine", fake_create_migration_engine)
    monkeypatch.setattr(catalog_schema, "_preflight_on_connection", fake_core)

    report = asyncio.run(
        catalog_schema.preflight_catalog_schema(
            "postgresql+asyncpg://user:secret@db.example.test/railway",
            postgres_read_only=True,
        )
    )

    assert create_calls == [True]
    assert core_calls == [(connection, target)]
    assert connection.statements == ["BEGIN READ ONLY", "ROLLBACK"]
    assert engine.disposed
    assert report["target"]["fingerprint"] == _TARGET_FINGERPRINT
    assert report["baseline"]["fingerprint"] == _BASELINE_FINGERPRINT
    assert report["backup"] == {
        "required": True,
        "valid": False,
        "reason": "backup_manifest_not_supplied",
    }
    assert report["can_upgrade"] is True
    assert report["can_apply"] is False


def test_postgres_read_only_preflight_rolls_back_when_the_canonical_core_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _postgres_target()
    connection = _FakeConnection()
    engine = _FakeEngine(connection)

    async def failing_core(*_args: Any) -> None:
        raise RuntimeError("sentinel canonical failure")

    monkeypatch.setattr(catalog_schema, "database_target", lambda _url: target)
    monkeypatch.setattr(catalog_schema, "create_migration_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(catalog_schema, "_preflight_on_connection", failing_core)

    with pytest.raises(RuntimeError, match="sentinel canonical failure"):
        asyncio.run(
            catalog_schema.preflight_catalog_schema(
                "postgresql+asyncpg://user:secret@db.example.test/railway",
                postgres_read_only=True,
            )
        )
    assert connection.statements == ["BEGIN READ ONLY", "ROLLBACK"]
    assert engine.disposed


def test_default_preflight_mode_preserves_the_existing_connection_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _postgres_target()
    connection = _FakeConnection()
    engine = _FakeEngine(connection)
    create_calls: list[bool] = []

    def fake_create_migration_engine(
        _target: DatabaseTarget,
        *,
        postgres_read_only: bool = False,
    ) -> _FakeEngine:
        create_calls.append(postgres_read_only)
        return engine

    async def fake_core(*_args: Any) -> dict[str, Any]:
        return _core_preflight_report()

    monkeypatch.setattr(catalog_schema, "database_target", lambda _url: target)
    monkeypatch.setattr(catalog_schema, "create_migration_engine", fake_create_migration_engine)
    monkeypatch.setattr(catalog_schema, "_preflight_on_connection", fake_core)

    report = asyncio.run(
        catalog_schema.preflight_catalog_schema(
            "postgresql+asyncpg://user:secret@db.example.test/railway",
        )
    )

    assert create_calls == [False]
    assert connection.statements == []
    assert engine.disposed
    assert report["can_apply"] is False


def test_postgres_read_only_engine_uses_server_default_and_no_pre_ping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_create_async_engine(*args: Any, **kwargs: Any) -> str:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return "engine"

    monkeypatch.setattr(catalog_schema, "create_async_engine", fake_create_async_engine)

    assert catalog_schema.create_migration_engine(
        _postgres_target(),
        postgres_read_only=True,
    ) == "engine"
    assert captured["kwargs"]["pool_pre_ping"] is False
    assert captured["kwargs"]["isolation_level"] == "AUTOCOMMIT"
    assert captured["kwargs"]["connect_args"] == {
        "server_settings": {"default_transaction_read_only": "on"}
    }


def test_postgres_read_only_mode_rejects_a_non_postgres_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = DatabaseTarget(
        database_url="sqlite+aiosqlite:///safe.db",
        dialect="sqlite",
        fingerprint="e" * 64,
        summary={"dialect": "sqlite", "host": "local", "database": "safe.db"},
        sqlite_path=Path("safe.db"),
    )
    monkeypatch.setattr(catalog_schema, "database_target", lambda _url: target)

    with pytest.raises(DatabaseTargetError, match="PostgreSQL read-only mode"):
        asyncio.run(catalog_schema.preflight_catalog_schema("sqlite+aiosqlite:///safe.db", postgres_read_only=True))
