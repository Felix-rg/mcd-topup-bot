"""Prepare and run a hash-pinned, database-read-only catalog preflight probe.

The normal mode is deliberately small: it reads DATABASE_URL only from its own
process environment, calls the exact catalog_schema preflight implementation
with PostgreSQL read-only enforcement, and emits a strict sanitized projection.

The stream mode is for a future owner-reviewed Railway SSH invocation. It
bundles this wrapper and the exact local catalog_schema.py bytes in memory, so
the deployed web release need not contain local uncommitted catalog code. The
remote Python process receives source through stdin only; no remote file is
created by this script.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import importlib
import json
import os
import re
import sys
import types
from pathlib import Path
from typing import Any, Mapping, Optional


PROBE_NAME = "lixafa_catalog_full_preflight_v1"
ACCEPTED_STAGING_TARGET_FINGERPRINT = (
    "a9e28c4ae2f1d091acd04e7cf561c1035c56033553c54aa1e31c9ed3e20c3126"
)
ROOT = Path(__file__).resolve().parents[1]
CATALOG_SCHEMA_PATH = ROOT / "catalog_schema.py"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CATALOG_STATES = {"absent", "complete", "resumable", "partial_incompatible"}
_MARKER_STATES = {"absent", "matching", "incompatible", "checksum_mismatch"}
_BACKUP_REASONS = {
    "already_applied",
    "backup_manifest_not_supplied",
    "baseline_unavailable",
    "backup_path_not_supplied",
    "backup_required",
    "backup_verification_failed",
}


class ProbeError(RuntimeError):
    """A fixed, non-secret failure code for operator review."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def source_hashes() -> dict[str, str]:
    """Return hashes of source code only; no environment or connection data."""

    return {
        "catalog_schema_sha256": _sha256_file(CATALOG_SCHEMA_PATH),
        "probe_sha256": _sha256_file(Path(__file__).resolve()),
        "remote_bundle_sha256": _sha256_bytes(build_remote_source()),
    }


def _catalog_schema_module() -> types.ModuleType:
    """Resolve the local module or the exact injected remote module."""

    module = importlib.import_module("catalog_schema")
    if not isinstance(module, types.ModuleType):
        raise ProbeError("catalog_schema_module_invalid")
    return module


def _required_fingerprint(value: object, error_code: str) -> str:
    fingerprint = str(value or "").strip().casefold()
    if not _SHA256_RE.fullmatch(fingerprint):
        raise ProbeError(error_code)
    return fingerprint


def _safe_bool(value: object) -> bool:
    return bool(value) if isinstance(value, bool) else False


def _safe_count(value: object) -> Optional[int]:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _safe_state(value: object, allowed: set[str]) -> str:
    state = str(value or "").strip()
    return state if state in allowed else "unrecognized"


def _safe_backup_reason(value: object) -> str:
    reason = str(value or "").strip()
    return reason if reason in _BACKUP_REASONS else "unrecognized"


def sanitize_preflight_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Project only non-secret evidence from canonical preflight output."""

    target = report.get("target")
    baseline = report.get("baseline")
    catalog_schema = report.get("catalog_schema")
    marker = report.get("marker")
    backup = report.get("backup")
    base_schema = report.get("base_schema")
    backfill = report.get("backfill_precheck")
    if not all(
        isinstance(value, Mapping)
        for value in (target, catalog_schema, marker, backup, base_schema)
    ):
        raise ProbeError("preflight_report_invalid")
    if baseline is not None and not isinstance(baseline, Mapping):
        raise ProbeError("preflight_report_invalid")
    if backfill is not None and not isinstance(backfill, Mapping):
        raise ProbeError("preflight_report_invalid")

    target_fingerprint = _required_fingerprint(
        target.get("fingerprint"),
        "target_fingerprint_invalid",
    )
    baseline_fingerprint: Optional[str] = None
    counts: dict[str, Optional[int]] = {
        "products": None,
        "topup": None,
        "promos": None,
        "promotion_targets": None,
    }
    baseline_readiness: dict[str, Optional[bool] | Optional[int]] = {
        "bootstrap_products_complete": None,
        "raw_sku_duplicates": None,
        "casefold_sku_collision_count": None,
    }
    if baseline is not None:
        baseline_fingerprint = _required_fingerprint(
            baseline.get("fingerprint"),
            "baseline_fingerprint_invalid",
        )
        counts = {
            "products": _safe_count(baseline.get("product_count")),
            "topup": _safe_count(baseline.get("topup_count")),
            "promos": _safe_count(baseline.get("promo_count")),
            "promotion_targets": _safe_count(baseline.get("promotion_target_count")),
        }
        bootstrap = baseline.get("bootstrap_presence")
        if not isinstance(bootstrap, Mapping):
            raise ProbeError("baseline_readiness_invalid")
        baseline_readiness = {
            "bootstrap_products_complete": all(value is True for value in bootstrap.values()),
            "raw_sku_duplicates": (
                baseline.get("raw_sku_duplicates")
                if isinstance(baseline.get("raw_sku_duplicates"), bool)
                else None
            ),
            "casefold_sku_collision_count": (
                len(baseline.get("casefold_sku_collisions"))
                if isinstance(baseline.get("casefold_sku_collisions"), list)
                else None
            ),
        }

    backfill_valid: Optional[bool]
    if backfill is None:
        backfill_valid = None
    else:
        backfill_valid = backfill.get("valid") if isinstance(backfill.get("valid"), bool) else None

    return {
        "probe": PROBE_NAME,
        "transaction_read_only": True,
        "target_fingerprint": target_fingerprint,
        "baseline_fingerprint": baseline_fingerprint,
        "can_upgrade": _safe_bool(report.get("can_upgrade")),
        "can_apply": _safe_bool(report.get("can_apply")),
        "catalog_schema_state": _safe_state(catalog_schema.get("state"), _CATALOG_STATES),
        "backup": {
            "required": _safe_bool(backup.get("required")),
            "valid": _safe_bool(backup.get("valid")),
            "reason": _safe_backup_reason(backup.get("reason")),
        },
        "readiness": {
            "base_schema_valid": _safe_bool(base_schema.get("valid")),
            "marker_state": _safe_state(marker.get("state"), _MARKER_STATES),
            "backfill_precheck_valid": backfill_valid,
            "issues_present": bool(report.get("issues")),
            **baseline_readiness,
        },
        "counts": counts,
        "success": True,
    }


async def probe_database_url(
    database_url: str,
    *,
    expected_target_fingerprint: Optional[str] = None,
    catalog_module: Optional[types.ModuleType] = None,
) -> dict[str, Any]:
    """Run the exact catalog preflight in PostgreSQL server-enforced RO mode."""

    module = catalog_module or _catalog_schema_module()
    preflight = getattr(module, "preflight_catalog_schema", None)
    if not callable(preflight):
        raise ProbeError("catalog_preflight_unavailable")

    report = await preflight(
        str(database_url or "").strip(),
        postgres_read_only=True,
    )
    if not isinstance(report, Mapping):
        raise ProbeError("preflight_report_invalid")
    payload = sanitize_preflight_report(report)
    expected = expected_target_fingerprint
    if expected is not None:
        expected = _required_fingerprint(expected, "expected_target_fingerprint_invalid")
        if payload["target_fingerprint"] != expected:
            raise ProbeError("target_fingerprint_mismatch")
    return payload


async def probe_from_environment() -> dict[str, Any]:
    """Use only the current process DATABASE_URL and the accepted staging target."""

    database_url = str(os.environ.get("DATABASE_URL") or "").strip()
    if not database_url:
        raise ProbeError("database_url_not_set")
    return await probe_database_url(
        database_url,
        expected_target_fingerprint=ACCEPTED_STAGING_TARGET_FINGERPRINT,
    )


def build_remote_source(
    *,
    catalog_source: Optional[bytes] = None,
    probe_source: Optional[bytes] = None,
) -> bytes:
    """Return an in-memory-only remote bootstrap using exact hash-pinned bytes."""

    catalog_bytes = catalog_source if catalog_source is not None else CATALOG_SCHEMA_PATH.read_bytes()
    probe_bytes = probe_source if probe_source is not None else Path(__file__).resolve().read_bytes()
    catalog_hash = _sha256_bytes(catalog_bytes)
    probe_hash = _sha256_bytes(probe_bytes)
    catalog_b64 = base64.b64encode(catalog_bytes).decode("ascii")
    probe_b64 = base64.b64encode(probe_bytes).decode("ascii")
    source = (
        "import base64, hashlib, sys, types\n"
        f"_catalog_source = base64.b64decode({catalog_b64!r})\n"
        f"_catalog_sha256 = {catalog_hash!r}\n"
        "if hashlib.sha256(_catalog_source).hexdigest() != _catalog_sha256:\n"
        "    raise SystemExit(70)\n"
        "_catalog_module = types.ModuleType('catalog_schema')\n"
        "_catalog_module.__file__ = '<in-memory:catalog_schema.py>'\n"
        "sys.modules['catalog_schema'] = _catalog_module\n"
        "exec(compile(_catalog_source, _catalog_module.__file__, 'exec'), _catalog_module.__dict__)\n"
        f"_probe_source = base64.b64decode({probe_b64!r})\n"
        f"_probe_sha256 = {probe_hash!r}\n"
        "if hashlib.sha256(_probe_source).hexdigest() != _probe_sha256:\n"
        "    raise SystemExit(71)\n"
        "_probe_globals = {\n"
        "    '__name__': '__main__',\n"
        "    '__file__': '<in-memory:probe_catalog_full_preflight.py>',\n"
        "    '__package__': None,\n"
        "}\n"
        "exec(compile(_probe_source, _probe_globals['__file__'], 'exec'), _probe_globals)\n"
    )
    return source.encode("utf-8")


def _failure_payload(error_code: str) -> dict[str, Any]:
    return {
        "probe": PROBE_NAME,
        "success": False,
        "error_code": error_code,
    }


def _hashes_payload() -> dict[str, Any]:
    return {
        "probe": PROBE_NAME,
        "success": True,
        **source_hashes(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="LIXAFA hash-pinned database-read-only catalog preflight probe",
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--emit-remote-source", action="store_true")
    actions.add_argument("--print-source-hashes", action="store_true")
    actions.add_argument("--stream-remote-source", action="store_true")
    parser.add_argument("--expected-bundle-sha256")
    return parser


def _stream_remote_source(expected_hash: str) -> int:
    expected = _required_fingerprint(expected_hash, "expected_bundle_sha256_invalid")
    payload = build_remote_source()
    if _sha256_bytes(payload) != expected:
        raise ProbeError("remote_bundle_sha256_mismatch")
    sys.stdout.buffer.write(payload)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.expected_bundle_sha256 and not args.stream_remote_source:
        print(json.dumps(_failure_payload("expected_bundle_sha256_not_applicable"), sort_keys=True))
        return 2
    try:
        if args.emit_remote_source:
            sys.stdout.buffer.write(build_remote_source())
            return 0
        if args.print_source_hashes:
            print(json.dumps(_hashes_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            return 0
        if args.stream_remote_source:
            return _stream_remote_source(str(args.expected_bundle_sha256 or ""))
        payload = asyncio.run(probe_from_environment())
        exit_code = 0
    except ProbeError as error:
        payload = _failure_payload(error.code)
        exit_code = 2
    except Exception:
        # Driver, SQLAlchemy, and catalog exceptions can carry a URL or detail.
        # The probe never serializes them.
        payload = _failure_payload("preflight_probe_failed")
        exit_code = 1

    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
