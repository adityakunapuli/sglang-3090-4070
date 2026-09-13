"""Regression tests for the UCLA FHIR extractors (db/ucla_unpack.py)."""
from myhealth_fhir.db.ucla_unpack import extract_medication_request


def test_medication_request_without_dispense_request():
    """A MedicationRequest with no dispenseRequest (no substitution) must not
    raise — this crashed the ucla run with 'NoneType' object has no attribute 'get'."""
    res = {"id": "mr-1", "status": "active", "medicationCodeableConcept": {"text": "Ibuprofen"}}
    header, children = extract_medication_request(res)
    assert header["substitution_allowed"] is None


def test_medication_request_with_empty_dispense_request():
    """dispenseRequest present but without a substitution key."""
    res = {"id": "mr-2", "status": "active", "dispenseRequest": {"numberOfRepeatsAllowed": 0}}
    header, _ = extract_medication_request(res)
    assert header["substitution_allowed"] is None


def test_medication_request_substitution_allowed_true():
    """substitution.allowed is a real boolean — it is preserved."""
    res = {"id": "mr-3", "status": "active", "dispenseRequest": {"substitution": {"allowed": True}}}
    header, _ = extract_medication_request(res)
    assert header["substitution_allowed"] is True


def test_medication_request_substitution_allowed_non_bool():
    """A non-boolean substitution.allowed is stored as None."""
    res = {"id": "mr-4", "status": "active", "dispenseRequest": {"substitution": {"allowed": "yes"}}}
    header, _ = extract_medication_request(res)
    assert header["substitution_allowed"] is None
