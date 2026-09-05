"""ENVIRONMENT refuses unknown words at boot, and production hides the schema.

Four fail-open guards key off the exact string "production" (the storage
endpoint allowlist, /docs and /redoc, the snippet loopback refusal, the
token-error redaction). Before the validator, ENVIRONMENT=Production was a
production process with every one of them silently off.

The second half of this file pins the doc surface itself. #143: hiding the two
doc UIs while serving /openapi.json leaves every path, parameter and response
model publicly enumerable, because anyone can point their own Swagger UI at the
JSON. All three go together or none of them do.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings, settings

MAIN_PATH = Path(__file__).resolve().parents[2] / "app" / "main.py"

DOC_ROUTES = ["/docs", "/redoc", "/openapi.json"]

#: Every setting a known word makes mandatory, so this file keeps asserting the
#: ENVIRONMENT validator rather than the newest production-only requirement.
#: S3_EXPECTED_ENDPOINT_HOST (#133) refuses an empty value under production;
#: tests/unit/test_storage_endpoint_seam.py is where that refusal is pinned.
_PRODUCTION_REQUIREMENTS = {
    "S3_EXPECTED_ENDPOINT_HOST": "ourownaccountid.r2.cloudflarestorage.com",
}


@pytest.mark.parametrize("word", ["development", "test", "staging", "production"])
def test_the_known_words_boot(word):
    extra = _PRODUCTION_REQUIREMENTS if word == "production" else {}
    assert Settings(ENVIRONMENT=word, **extra).ENVIRONMENT == word


@pytest.mark.parametrize("typo", ["Production", "prod", "production ", ""])
def test_a_typo_refuses_to_boot_rather_than_failing_open(typo):
    with pytest.raises(ValidationError) as exc_info:
        Settings(ENVIRONMENT=typo)
    assert "ENVIRONMENT" in str(exc_info.value)


# ---------------------------------------------------------------------------
# The doc surface, built under a given ENVIRONMENT (#143)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def app_under():
    """Build a private copy of app.main per ENVIRONMENT, once each.

    app.main reads settings.ENVIRONMENT once, at import, to decide the three
    doc URLs. Loading the file under its own module name gives a second app
    built under the patched value and leaves sys.modules["app.main"] alone, so
    the app every other test imports is untouched. Building it costs seconds,
    hence the cache.
    """
    cache: dict[str, object] = {}

    def build(environment: str) -> object:
        if environment not in cache:
            original = settings.ENVIRONMENT
            settings.ENVIRONMENT = environment
            try:
                spec = importlib.util.spec_from_file_location(
                    f"main_under_{environment}", MAIN_PATH
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                cache[environment] = module.app
            finally:
                settings.ENVIRONMENT = original
        return cache[environment]

    return build


@pytest.mark.parametrize("route", DOC_ROUTES)
def test_production_serves_neither_the_doc_uis_nor_the_schema(route, app_under):
    """Under production all three 404, the schema included."""
    # No context manager: lifespan runs a JWKS network probe, and routing does
    # not need it.
    client = TestClient(app_under("production"))

    assert client.get(route).status_code == 404


@pytest.mark.parametrize("route", DOC_ROUTES)
def test_every_other_environment_still_serves_all_three(route, app_under):
    """Staging and development keep the docs and the schema they render."""
    client = TestClient(app_under("staging"))

    assert client.get(route).status_code == 200
