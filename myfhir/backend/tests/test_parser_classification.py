"""Unit tests for member-submitted claim classification helpers."""
from myhealth_fhir.db.parser import (
    classify_submission_origin,
    is_out_of_network,
    claim_number_of,
    load_eob,
    load_claim,
)


def _payee(code, reference):
    """Build a minimal FHIR payee object with the given type code and party reference."""
    return {"type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/payeetype", "code": code}]},
            "party": {"reference": reference}}


def test_member_submitted_by_payee_patient():
    """A claim paid to the patient (beneficiary) is member-submitted."""
    payee = _payee("beneficiary", "Patient/0001234567")
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
