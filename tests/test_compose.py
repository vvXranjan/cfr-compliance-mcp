"""P7.3 compose configuration sanity tests.

Static validation of ``compose.yaml`` -- it does NOT require Docker or
PostgreSQL, only that the file is well-formed and defines the expected
local stack.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_COMPOSE = _ROOT / "compose.yaml"


def _load() -> dict:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))


def test_compose_exists() -> None:
    assert _COMPOSE.exists()


def test_expected_services() -> None:
    data = _load()
    assert set(data["services"]) == {"db", "migrate", "app"}


def test_postgres_image_and_volume() -> None:
    data = _load()
    db = data["services"]["db"]
    assert db["image"] == "postgres:16"
    assert "pgdata" in data["volumes"]
    assert any("pgdata" in v for v in db["volumes"])


def test_app_uses_postgres_backend_and_waits_for_migration() -> None:
    data = _load()
    app = data["services"]["app"]
    assert app["environment"]["CFR_PERSISTENCE_BACKEND"] == "postgres"
    deps = app["depends_on"]
    assert deps["db"]["condition"] == "service_healthy"
    assert deps["migrate"]["condition"] == "service_completed_successfully"


def test_migrate_has_no_restart_loop() -> None:
    data = _load()
    assert data["services"]["migrate"]["restart"] == "no"


def test_healthchecks_present() -> None:
    data = _load()
    assert "healthcheck" in data["services"]["db"]
    assert "healthcheck" in data["services"]["app"]


def test_no_hardcoded_secrets() -> None:
    text = _COMPOSE.read_text(encoding="utf-8")
    for secretish in ("POSTGRES_PASSWORD: cfrpass", "password=cfrpass"):
        assert secretish not in text
    # Password values come from env vars only.
    assert "POSTGRES_PASSWORD:-cfr}" in text
