"""The review of flagged claims against a real tenant database (#290 step 3).

The unit tests fake the connection and so pin nothing about the SQL: a renamed
column in `_SELECT_FLAGGED_SQL` or a wrong ON CONFLICT target passes every one
of them. This module creates a throwaway tenant database on the local Postgres,
migrates it to head through the production path, writes one run's faithfulness
rows and samples the way `write_eval_results` and `write_eval_samples` do, and
runs the service's three statements for real:

  - the flagged listing joins the sample and lists only the unsupported claims
  - an answer lands, a second answer on the same claim replaces it, and the
    `position` column name is accepted by Postgres
  - an answer about a claim the run did not flag is refused before any row lands

Needs the local cluster (`TEST_ADMIN_DB_URL`, default wchats:wchats@localhost).
Spends nothing.
"""

from __future__ import annotations

import json
import os
import uuid

import psycopg2
import pytest

from app.domain.claim_review import ClaimReview, InvalidClaimReview
from app.services import claim_review_service as svc
from app.services.migrations import run_tenant_migrations
from tests.integration._tenant_db import create_tenant_database, drop_tenant_database

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("INTEGRATION_TESTS_ENABLED", "") != "1",
                       reason="INTEGRATION_TESTS_ENABLED=1 runs this against the local cluster"),
]

RUN = str(uuid.uuid4())
CLAIMS = [
    {"statement": "Delivery costs R30 in Tembisa.", "supported": True, "reason": "in the policy"},
    {"statement": "Orders over R500 ship free.", "supported": False, "reason": "absent"},
    {"statement": "The fee is R60 outside Tembisa.", "supported": False, "reason": "absent"},
]


@pytest.fixture(scope="module")
def tenant_dsn():
    name = f"wchats_claim_reviews_{uuid.uuid4().hex[:12]}"
    dsn = create_tenant_database(name)
    try:
        run_tenant_migrations(dsn)
        conn = psycopg2.connect(dsn)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO eval_runs (id, kind, started_at, status) VALUES (%s::uuid, %s, NOW(), 'complete')",
                    (RUN, "m6:test"),
                )
                cur.execute(
                    """INSERT INTO eval_results (id, eval_run_id, scenario_id, metric, score, claims)
                       VALUES (%s::uuid, %s::uuid, 's1', 'faithfulness', 0.3333, %s::jsonb),
                              (%s::uuid, %s::uuid, 's2', 'faithfulness', 1.0, %s::jsonb),
                              (%s::uuid, %s::uuid, 's1', 'context_recall', 0.5, NULL)""",
                    (str(uuid.uuid4()), RUN, json.dumps(CLAIMS),
                     str(uuid.uuid4()), RUN, json.dumps([{"statement": "Returns take 30 days.", "supported": True, "reason": "x"}]),
                     str(uuid.uuid4()), RUN),
                )
                cur.execute(
                    """INSERT INTO eval_samples (id, eval_run_id, scenario_id, user_input, response, retrieved_contexts, reference)
                       VALUES (%s::uuid, %s::uuid, 's1', 'what does delivery cost?', 'Delivery costs R30.', %s::jsonb, 'R30')""",
                    (str(uuid.uuid4()), RUN, json.dumps(["Delivery is R30 in Tembisa."])),
                )
            conn.commit()
        finally:
            conn.close()
        yield dsn
    finally:
        drop_tenant_database(name)


def test_the_listing_joins_the_sample_and_lists_only_the_unsupported_claims(tenant_dsn):
    result = svc.read_flagged_claims(RUN, tenant_dsn)
    assert result.reviews_available is True
    [s1] = result.scenarios
    assert s1.scenario_id == "s1"
    assert [(c.position, c.statement, c.review) for c in s1.claims] == [
        (1, "Orders over R500 ship free.", None),
        (2, "The fee is R60 outside Tembisa.", None),
    ]
    assert s1.question == "what does delivery cost?"
    assert s1.retrieved_contexts == ("Delivery is R30 in Tembisa.",)


def test_an_answer_lands_and_a_second_answer_on_the_same_claim_replaces_it(tenant_dsn):
    first = ClaimReview(RUN, "s1", 1, "Orders over R500 ship free.", False)
    assert svc.write_claim_reviews(RUN, [first], tenant_dsn) == 1
    assert [c.review for c in svc.read_flagged_claims(RUN, tenant_dsn).scenarios[0].claims] == [False, None]

    second = ClaimReview(RUN, "s1", 1, "Orders over R500 ship free.", True)
    assert svc.write_claim_reviews(RUN, [second], tenant_dsn) == 1
    assert [c.review for c in svc.read_flagged_claims(RUN, tenant_dsn).scenarios[0].claims] == [True, None]

    conn = psycopg2.connect(tenant_dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*), bool_and(supported) FROM claim_reviews WHERE eval_run_id = %s::uuid", (RUN,))
            assert cur.fetchone() == (1, True), "one row, replaced, not two"
    finally:
        conn.close()


def test_an_answer_about_a_claim_the_run_did_not_flag_is_refused_and_nothing_lands(tenant_dsn):
    stray = ClaimReview(RUN, "s2", 0, "Returns take 30 days.", False)
    with pytest.raises(InvalidClaimReview, match="flagged no claim"):
        svc.write_claim_reviews(RUN, [stray], tenant_dsn)
    conn = psycopg2.connect(tenant_dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM claim_reviews WHERE eval_run_id = %s::uuid AND scenario_id = 's2'", (RUN,))
            assert cur.fetchone() == (0,)
    finally:
        conn.close()
