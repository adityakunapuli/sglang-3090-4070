"""Integration tests for anthem view edge cases surfaced during claim/EOB debugging.

Covers:
- care_team_providers correlation (names must not leak across EOBs/claims)
- item-less EOBs/claims surviving the header+item merged vw_ views
- paid_to_member / paid_to_provider split (Anthem member reimbursement via
  payee.party = Patient)
- vw_ views expose no fhir_id; legacy views (eob_claims, eob_items,
  claim_submissions, claim_items) are dropped in favor of the vw_ views

Requires the shared Postgres (skips when unreachable). Fixture rows use
``TESTEDGE-``-prefixed ids and are removed before and after each test.
"""
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from myhealth_fhir.db.engine import get_anthem_engine
from myhealth_fhir.db.schema.views import _create_anthem_views
from myhealth_fhir.models.anthem import (
    EOB,
    ClaimCareTeam,
    ClaimItem,
    ClaimSubmission,
    EntityName,
    EOBCareTeam,
    EOBItem,
    EOBTotal,
    OAuthTokenRecord,
    PatientRecord,
)

P = "TESTEDGE-P1"
PATIENT_REF = f"anthem:Patient:{P}"
ORG_PROVIDER = "anthem:Organization:TESTEDGE-ORG-PROV"
ORG_CT_A = "anthem:Organization:TESTEDGE-ORG-CTA"
ORG_CT_B = "anthem:Organization:TESTEDGE-ORG-CTB"
PATIENT_NAME = "Test Edge Patient"


def _cleanup(session: Session):
    session.query(EOBCareTeam).filter(EOBCareTeam.eob_id.like("TESTEDGE-%")).delete(synchronize_session=False)
    session.query(EOBTotal).filter(EOBTotal.eob_id.like("TESTEDGE-%")).delete(synchronize_session=False)
    session.query(EOBItem).filter(EOBItem.eob_id.like("TESTEDGE-%")).delete(synchronize_session=False)
    session.query(EOB).filter(EOB.id.like("TESTEDGE-%")).delete(synchronize_session=False)
    session.query(ClaimCareTeam).filter(ClaimCareTeam.claim_id.like("TESTEDGE-%")).delete(synchronize_session=False)
    session.query(ClaimItem).filter(ClaimItem.claim_id.like("TESTEDGE-%")).delete(synchronize_session=False)
    session.query(ClaimSubmission).filter(ClaimSubmission.id.like("TESTEDGE-%")).delete(synchronize_session=False)
    session.query(EntityName).filter(EntityName.entity_ref.like("anthem:%:TESTEDGE-%")).delete(synchronize_session=False)
    session.query(PatientRecord).filter(PatientRecord.patient_id == P).delete(synchronize_session=False)
    session.query(OAuthTokenRecord).filter(OAuthTokenRecord.patient_id == P).delete(synchronize_session=False)
    session.commit()


def _mk_eob(eob_id, claim_number, *, payee_ref, payment_amount, care_team_ref, item_seq=None):
    eob = EOB(
        id=eob_id,
        claim_number=claim_number,
        status="active",
        outcome="complete",
        created_date="2026-03-21",
        billable_period_start="2026-03-21",
        billable_period_end="2026-03-21",
        patient_ref=PATIENT_REF,
        provider_ref=ORG_PROVIDER,
        payee_type="provider" if "Organization" in (payee_ref or "") else "subscriber",
        payee_ref=payee_ref,
        payment_amount=payment_amount,
        payment_date="2026-07-15",
    )
    if care_team_ref:
        eob.care_team = [EOBCareTeam(sequence=1, provider_ref=care_team_ref, role_code="purchasedservice", role_display="Purchased Service")]
    if item_seq is not None:
        eob.items = [EOBItem(sequence=item_seq, hcpcs_code="TST1", member_liability=1376.81)]
    return eob


def _mk_claim(claim_id, *, care_team_ref, item_seqs=()):
    claim = ClaimSubmission(
        id=claim_id,
        claim_number=claim_id,
        status="active",
        claim_type="professional",
        use="claim",
        created_date="2026-02-03",
        billable_period_start="2026-02-03",
        billable_period_end="2026-02-03",
        patient_ref=PATIENT_REF,
        provider_ref=ORG_PROVIDER,
        total_amount=500.0,
        total_currency="USD",
    )
    if care_team_ref:
        claim.claim_care_team = [ClaimCareTeam(sequence=1, provider_ref=care_team_ref, role_code="purchasedservice", role_display="Purchased Service")]
    claim.items = [ClaimItem(sequence=s, hcpcs_code="TST1") for s in item_seqs]
    return claim


@pytest.fixture()
def anthem_env():
    try:
        engine = get_anthem_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("anthem Postgres not reachable")

    _create_anthem_views(engine)

    session = Session(engine)
    _cleanup(session)
    session.add_all([
        OAuthTokenRecord(patient_id=P, provider="anthem", access_token="test-token", obtained_at="2026-01-01"),
        PatientRecord(patient_id=P, provider="anthem", entity_ref=PATIENT_REF),
        EntityName(entity_ref=PATIENT_REF, provider="anthem", entity_type="Patient", entity_id=P, name=PATIENT_NAME),
        EntityName(entity_ref=ORG_PROVIDER, provider="anthem", entity_type="Organization", entity_id="TESTEDGE-ORG-PROV", name="Edge Care Provider"),
        EntityName(entity_ref=ORG_CT_A, provider="anthem", entity_type="Organization", entity_id="TESTEDGE-ORG-CTA", name="Alpha Care Team"),
        EntityName(entity_ref=ORG_CT_B, provider="anthem", entity_type="Organization", entity_id="TESTEDGE-ORG-CTB", name="Beta Care Team"),
        # EOB A: org payee, one item, own care team (Alpha)
        _mk_eob("TESTEDGE-EOB-A", "TESTEDGE-CLM-A", payee_ref=ORG_PROVIDER, payment_amount=10.0, care_team_ref=ORG_CT_A, item_seq=1),
        # EOB B: member payee (Patient), NO items, own care team (Beta)
        _mk_eob("TESTEDGE-EOB-B", "TESTEDGE-CLM-B", payee_ref=PATIENT_REF, payment_amount=623.19, care_team_ref=ORG_CT_B),
        EOBTotal(eob_id="TESTEDGE-EOB-A", category_code="paidtoprovider", amount=10.0),
        EOBTotal(eob_id="TESTEDGE-EOB-A", category_code="memberliability", amount=5.0),
        EOBTotal(eob_id="TESTEDGE-EOB-B", category_code="paidtoprovider", amount=623.19),
        EOBTotal(eob_id="TESTEDGE-EOB-B", category_code="memberliability", amount=1376.81),
        # Claims: C1 with two items + Alpha care team; C2 item-less + Beta care team
        _mk_claim("TESTEDGE-CLM-C1", care_team_ref=ORG_CT_A, item_seqs=(1, 2)),
        _mk_claim("TESTEDGE-CLM-C2", care_team_ref=ORG_CT_B),
    ])
    session.commit()
    yield engine
    _cleanup(session)
    session.close()


def _rows(engine, sql, **params):
    with engine.connect() as conn:
        result = conn.execute(text(sql), params)
        return [dict(r._mapping) for r in result]


def test_care_team_names_do_not_leak_across_eobs(anthem_env):
    """care_team_providers must be correlated per-EOB (the missing
    WHERE eob_id = e.id bug aggregated the whole table into every row)."""
    rows = _rows(
        anthem_env,
        "SELECT claim_number, care_team_providers FROM vw_eob WHERE claim_number LIKE 'TESTEDGE-%' ORDER BY claim_number",
    )
    by_claim = {r["claim_number"]: r["care_team_providers"] for r in rows}
    assert by_claim["TESTEDGE-CLM-A"] == "Alpha Care Team"
    assert by_claim["TESTEDGE-CLM-B"] == "Beta Care Team"


def test_itemless_eob_survives_vw_eob(anthem_env):
    """LEFT JOIN from header to items: an EOB with no items still appears
    (with NULL item columns) instead of vanishing."""
    rows = _rows(
        anthem_env,
        "SELECT item_seq, hcpcs_code FROM vw_eob WHERE claim_number = 'TESTEDGE-CLM-B'",
    )
    assert len(rows) == 1
    assert rows[0]["item_seq"] is None
    assert rows[0]["hcpcs_code"] is None


def test_paid_to_member_and_provider_split(anthem_env):
    """payee.party = Patient marks a subscriber reimbursement: paid_to_member
    carries the payment amount, paid_to_provider is NULL — and vice versa."""
    rows = _rows(
        anthem_env,
        """SELECT DISTINCT claim_number, paid_to_member, paid_to_provider
           FROM vw_eob WHERE claim_number LIKE 'TESTEDGE-%' ORDER BY claim_number""",
    )
    by_claim = {r["claim_number"]: r for r in rows}
    assert by_claim["TESTEDGE-CLM-B"]["paid_to_member"] == pytest.approx(623.19)
    assert by_claim["TESTEDGE-CLM-B"]["paid_to_provider"] is None
    assert by_claim["TESTEDGE-CLM-A"]["paid_to_provider"] == pytest.approx(10.0)
    assert by_claim["TESTEDGE-CLM-A"]["paid_to_member"] is None


def test_total_paid_and_member_liability_from_totals(anthem_env):
    """total_paid / total_member_liability come from the paidtoprovider /
    memberliability eob_total categories."""
    rows = _rows(
        anthem_env,
        """SELECT DISTINCT claim_number, total_paid, total_member_liability
           FROM vw_eob WHERE claim_number = 'TESTEDGE-CLM-A'""",
    )
    assert rows[0]["total_paid"] == pytest.approx(10.0)
    assert rows[0]["total_member_liability"] == pytest.approx(5.0)


def test_vw_views_have_no_fhir_id(anthem_env):
    """The vw_ analytics views deliberately drop fhir_id (group by claim_number)."""
    n = _rows(
        anthem_env,
        """SELECT count(*) AS n FROM information_schema.columns
           WHERE table_name IN ('vw_eob', 'vw_claims') AND column_name = 'fhir_id'""",
    )[0]["n"]
    assert n == 0


def test_vw_claims_header_item_merge(anthem_env):
    """vw_claims: one row per claim item with header context; item-less
    claims still appear; care team correlated per claim."""
    rows = _rows(
        anthem_env,
        """SELECT claim_number, item_seq, care_team_providers
           FROM vw_claims WHERE claim_number LIKE 'TESTEDGE-%'
           ORDER BY claim_number, item_seq NULLS FIRST""",
    )
    c1 = [r for r in rows if r["claim_number"] == "TESTEDGE-CLM-C1"]
    c2 = [r for r in rows if r["claim_number"] == "TESTEDGE-CLM-C2"]
    assert sorted(r["item_seq"] for r in c1) == [1, 2]
    assert all(r["care_team_providers"] == "Alpha Care Team" for r in c1)
    assert len(c2) == 1
    assert c2[0]["item_seq"] is None
    assert c2[0]["care_team_providers"] == "Beta Care Team"


def test_legacy_views_are_dropped(anthem_env):
    """The header/item legacy views are replaced by vw_eob / vw_claims."""
    n = _rows(
        anthem_env,
        """SELECT count(*) AS n FROM information_schema.views
           WHERE table_name IN ('eob_claims', 'eob_items', 'claim_submissions', 'claim_items')""",
    )[0]["n"]
    assert n == 0


# ── live-data invariants (catch stale-parser writes from running containers) ──


def test_no_claims_missing_number_when_raw_json_has_one(anthem_env):
    """Every claim whose raw_json carries a .../clm_nbr identifier must have a
    claim_number. Catches writes from containers running a parser without the
    system-URL fallback (identifiers with no type coding)."""
    n = _rows(
        anthem_env,
        """SELECT count(*) AS n FROM claim_submission
           WHERE claim_number IS NULL
             AND raw_json::jsonb #>'{identifier}' @> '[{"system":"https://elevancehealth.com/CDL/EDW/clm_nbr"}]'""",
    )[0]["n"]
    assert n == 0, f"{n} claim rows lost their claim_number (stale parser write?)"


def test_no_corrupted_patient_payee_refs(anthem_env):
    """A subscriber payee (payee.party = Patient/<id> in raw_json) must be
    stored with a anthem:Patient: ref — never anthem:Organization:."""
    n = _rows(
        anthem_env,
        """SELECT count(*) AS n FROM eob
           WHERE raw_json::jsonb #>>'{payee,party,reference}' LIKE 'Patient/%'
             AND payee_ref NOT LIKE 'anthem:Patient:%'""",
    )[0]["n"]
    assert n == 0, f"{n} eob rows have a Patient payee stored as Organization (stale parser write?)"


def test_no_corrupted_care_team_refs(anthem_env):
    """careTeam provider references typed Organization/<id> in raw_json must
    not be stored as anthem:Practitioner:<id>."""
    n = _rows(
        anthem_env,
        """SELECT count(*) AS n
           FROM eob_care_team ct
           JOIN eob e ON e.id = ct.eob_id
           CROSS JOIN LATERAL (
             SELECT c->'provider'->>'reference' AS ref
             FROM jsonb_array_elements(e.raw_json::jsonb -> 'careTeam') c
             WHERE c->>'sequence' = ct.sequence::text LIMIT 1
           ) x
           WHERE x.ref LIKE 'Organization/%'
             AND ct.provider_ref LIKE 'anthem:Practitioner:%'""",
    )[0]["n"]
    assert n == 0, f"{n} care-team rows typed Practitioner but referenced Organization (stale parser write?)"
