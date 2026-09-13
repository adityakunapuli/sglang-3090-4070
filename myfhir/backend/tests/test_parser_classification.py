"""Unit tests for member-submitted claim classification helpers."""
from myhealth_fhir.db.parser import (
    claim_number_of,
    classify_submission_origin,
    is_out_of_network,
    load_care_team,
    load_claim,
    load_claim_care_team,
    load_eob,
)


def _payee(code, reference):
    """Build a minimal FHIR payee object with the given type code and party reference."""
    return {"type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/payeetype", "code": code}]},
            "party": {"reference": reference}}


def test_member_submitted_by_beneficiary_payee():
    """A claim paid to the patient (FHIR-standard beneficiary type) is member-submitted."""
    payee = _payee("beneficiary", "Patient/0001234567")
    assert classify_submission_origin(payee, []) == "member"


def test_member_submitted_by_subscriber_payee():
    """Anthem codes member reimbursement payee type as 'subscriber' (not the
    FHIR-standard 'beneficiary') — it must still classify as member-submitted."""
    payee = _payee("subscriber", "Patient/0001234567")
    assert classify_submission_origin(payee, []) == "member"


def test_provider_submitted_by_payee_org():
    """A claim paid to an organization is provider-submitted."""
    payee = _payee("provider", "Organization/4553b98784464ca52ec65d5e57efafe4")
    assert classify_submission_origin(payee, []) == "provider"


def test_member_submitted_by_vendor_prefix():
    """A VSP-prefixed claim number marks a member-submitted claim."""
    payee = _payee("provider", "Organization/abc123")
    idents = [{"system": "https://elevancehealth.com/CDL/clm_nbr", "value": "VSP000000000260020004501"}]
    assert classify_submission_origin(payee, idents) == "member"


def test_member_submitted_by_delta_dental_prefix():
    """A DELTADENTAL-prefixed claim number marks a member-submitted claim."""
    payee = _payee("provider", "Organization/abc123")
    idents = [{"system": "https://elevancehealth.com/CDL/clm_nbr", "value": "DELTADENTALD250280013101"}]
    assert classify_submission_origin(payee, idents) == "member"


def test_member_submitted_by_medco_prefix():
    """A MEDCO-prefixed claim number marks a member-submitted claim."""
    payee = _payee("provider", "Organization/abc123")
    idents = [{"system": "https://elevancehealth.com/CDL/clm_nbr", "value": "MEDCO000000001"}]
    assert classify_submission_origin(payee, idents) == "member"


def test_provider_default():
    """Claims without beneficiary payee or vendor prefix default to provider."""
    payee = _payee("provider", "Organization/xyz789")
    idents = [{"system": "https://elevancehealth.com/CDL/clm_nbr", "value": "26190CL1697"}]
    assert classify_submission_origin(payee, idents) == "provider"


def test_out_of_network_true():
    """Header adjudication with billingnetworkstatus/outofnetwork is out of network."""
    adj = [{"category": {"coding": [{"code": "billingnetworkstatus"}]},
            "reason": {"coding": [{"code": "outofnetwork"}]}}]
    assert is_out_of_network(adj) is True


def test_out_of_network_false_in_network():
    """Header adjudication with an in-network reason is not out of network."""
    adj = [{"category": {"coding": [{"code": "billingnetworkstatus"}]},
            "reason": {"coding": [{"code": "innetwork"}]}}]
    assert is_out_of_network(adj) is False


def test_out_of_network_false_empty():
    """Empty adjudication is never out of network."""
    assert is_out_of_network([]) is False


def test_claim_number_of():
    """Extracts the identifier with type code ``uc`` as the claim number."""
    res = {"identifier": [
        {"use": "secondary", "type": {"coding": [{"code": "ck"}]}, "value": "5F8308FE"},
        {"use": "usual", "type": {"coding": [{"code": "uc"}]}, "value": "TST-20260215-B9"},
    ]}
    assert claim_number_of(res) == "TST-20260215-B9"


def test_claim_number_of_missing():
    """Returns None when no ``uc`` identifier exists."""
    assert claim_number_of({"identifier": []}) is None


def test_claim_number_of_untyped_identifier():
    """Some Anthem Claim resources carry identifiers with NO type coding —
    only a system URL (.../EDW/clm_nbr). The claim number must still parse."""
    res = {"identifier": [
        {"use": "usual", "value": "2619TEST001", "system": "https://elevancehealth.com/CDL/EDW/clm_nbr"},
    ]}
    assert claim_number_of(res) == "2619TEST001"


def test_load_eob_classifies_out_of_network():
    """load_eob populates classification columns from the raw resource."""
    rec = {
        "id": "eob-1",
        "identifier": [{"type": {"coding": [{"code": "uc"}]}, "value": "TST-20260215-B9"}],
        "payee": {"type": {"coding": [{"code": "provider"}]},
                  "party": {"reference": "Organization/4553b98784464ca52ec65d5e57efafe4"}},
        "adjudication": [{"category": {"coding": [{"code": "billingnetworkstatus"}]},
                          "reason": {"coding": [{"code": "outofnetwork"}]}}],
    }
    eob = load_eob(rec)
    assert eob["submission_origin"] == "provider"
    assert eob["is_out_of_network"] is True


def test_load_eob_classifies_member_submitted():
    """load_eob marks beneficiary payees as member-submitted."""
    rec = {
        "id": "eob-2",
        "identifier": [],
        "payee": {"type": {"coding": [{"code": "beneficiary"}]},
                  "party": {"reference": "Patient/0001234567"}},
        "adjudication": [],
    }
    eob = load_eob(rec)
    assert eob["submission_origin"] == "member"
    assert eob["is_out_of_network"] is False


def test_load_claim_classifies_submission_origin():
    """load_claim populates submission_origin (member via vendor prefix)."""
    rec = {
        "id": "claim-1",
        "identifier": [{"type": {"coding": [{"code": "uc"}]}, "value": "VSP000000000260020004501"}],
        "payee": {"type": {"coding": [{"code": "provider"}]},
                  "party": {"reference": "Organization/abc123"}},
    }
    claim = load_claim(rec)
    assert claim["submission_origin"] == "member"
    assert claim["is_out_of_network"] is False


# ── payee_ref entity typing ────────────────────────────────────────────
# Anthem sets payee.party = Patient/<id> when the reimbursement check goes
# to the subscriber; the parser must infer the entity type from the
# reference instead of hardcoding Organization.


def test_load_eob_payee_patient_typed_as_patient():
    """payee.party = Patient/<id> is stored as anthem:Patient:<id>, not Organization."""
    rec = {
        "id": "eob-payee-pt",
        "identifier": [],
        "payee": {"type": {"coding": [{"code": "subscriber"}]},
                  "party": {"reference": "Patient/fb832f7a"}},
    }
    eob = load_eob(rec)
    assert eob["payee_ref"] == "anthem:Patient:fb832f7a"
    assert eob["submission_origin"] == "member"


def test_load_eob_payee_org_typed_as_org():
    """A provider payee keeps its Organization typing (regression guard)."""
    rec = {
        "id": "eob-payee-org",
        "identifier": [],
        "payee": {"type": {"coding": [{"code": "provider"}]},
                  "party": {"reference": "Organization/abc123"}},
    }
    eob = load_eob(rec)
    assert eob["payee_ref"] == "anthem:Organization:abc123"


def test_load_claim_payee_patient_typed_as_patient():
    """Claim parser applies the same entity-type inference for payees."""
    rec = {
        "id": "claim-payee-pt",
        "identifier": [],
        "payee": {"type": {"coding": [{"code": "subscriber"}]},
                  "party": {"reference": "Patient/fb832f7a"}},
    }
    claim = load_claim(rec)
    assert claim["payee_ref"] == "anthem:Patient:fb832f7a"
    assert claim["submission_origin"] == "member"


# ── care-team entity typing ────────────────────────────────────────────
# careTeam[].provider may reference an Organization (e.g. pharmacies as
# 'Purchased Service') or a Practitioner; the type must come from the
# reference itself.


def _care_team_rec(provider_ref):
    return {"careTeam": [
        {"sequence": 1,
         "role": {"coding": [{"code": "purchasedservice", "display": "Purchased Service"}]},
         "provider": {"reference": provider_ref}},
    ]}


def test_load_care_team_organization_ref_typed_as_org():
    """careTeam provider Organization/<id> is stored as anthem:Organization:<id>."""
    rows = load_care_team(_care_team_rec("Organization/b2ac9ef9"), "eob-ct-1")
    assert rows[0]["provider_ref"] == "anthem:Organization:b2ac9ef9"


def test_load_care_team_practitioner_ref_stays_practitioner():
    """careTeam provider Practitioner/<id> keeps its typing (regression guard)."""
    rows = load_care_team(_care_team_rec("Practitioner/5a3e6180"), "eob-ct-2")
    assert rows[0]["provider_ref"] == "anthem:Practitioner:5a3e6180"


def test_load_claim_care_team_organization_ref_typed_as_org():
    """Claim careTeam entries apply the same entity-type inference."""
    rows = load_claim_care_team(_care_team_rec("Organization/b2ac9ef9"), "claim-ct-1")
    assert rows[0]["provider_ref"] == "anthem:Organization:b2ac9ef9"


def test_load_care_team_bare_ref_defaults_to_practitioner():
    """A reference without a resource-type prefix falls back to Practitioner."""
    rows = load_care_team(_care_team_rec("5a3e6180"), "eob-ct-3")
    assert rows[0]["provider_ref"] == "anthem:Practitioner:5a3e6180"
