"""Unit tests for the member-submission registry matching logic."""
from datetime import date

from myhealth_fhir.services.member_submissions import find_matches


def _submission(**kw):
    base = {"id": 1, "claim_number": None, "provider_name": "Test Surgery Center",
            "provider_npi": None, "service_date": date(2026, 2, 13),
            "total_amount": 1100.11, "status": "registered"}
    base.update(kw)
    return base


def _eob(id="e1", claim_number="TST-20260215-B9", provider="Organization/org1",
         bp="2026-02-13", total=1100.11):
    return {"id": id, "identifier": [
        {"type": {"coding": [{"code": "uc"}]}, "value": claim_number}],
        "provider": {"reference": provider},
        "billablePeriod": {"start": bp, "end": bp},
        "item": [], "total": [{"category": {"coding": [{"code": "submitted"}]},
                               "amount": {"value": total, "currency": "USD"}}]}


def _entity(**kw):
    return {"name": kw.get("name", "Test Surgery Center"), "npi": kw.get("npi")}


def test_match_by_claim_number():
    """A registry claim number matching the EOB identifier matches."""
    ents = {"anthem:Organization:org1": _entity()}
    subs = [_submission(claim_number="TST-20260215-B9", service_date=None, total_amount=None)]
    assert find_matches(subs, [_eob()], [], ents) == [(1, "e1", None)]


def test_match_by_provider_date_amount():
    """Provider name, service date, and amount all matching."""
    ents = {"anthem:Organization:org1": _entity()}
    subs = [_submission()]
    assert find_matches(subs, [_eob()], [], ents) == [(1, "e1", None)]


def test_match_by_npi():
    """NPI match wins over name matching."""
    ents = {"anthem:Organization:org1": _entity(npi="1234567890")}
    subs = [_submission(provider_npi="1234567890")]
    assert find_matches(subs, [_eob()], [], ents) == [(1, "e1", None)]


def test_no_match_wrong_provider():
    """Different provider name yields no match."""
    ents = {"anthem:Organization:org1": _entity(name="Different Clinic")}
    subs = [_submission()]
    assert find_matches(subs, [_eob()], [], ents) == []


def test_no_match_wrong_amount():
    """Amount outside tolerance yields no match."""
    ents = {"anthem:Organization:org1": _entity()}
    subs = [_submission(total_amount=500.00)]
    assert find_matches(subs, [_eob()], [], ents) == []


def test_no_match_wrong_date():
    """Service date beyond tolerance yields no match."""
    ents = {"anthem:Organization:org1": _entity()}
    subs = [_submission(service_date=date(2026, 3, 1))]
    assert find_matches(subs, [_eob()], [], ents) == []


def test_amount_within_tolerance_matches():
    """Amount within ±$0.50 tolerance matches."""
    ents = {"anthem:Organization:org1": _entity()}
    subs = [_submission(total_amount=1100.40)]
    assert find_matches(subs, [_eob(total=1100.11)], [], ents) == [(1, "e1", None)]


def test_skips_adjudicated_submissions():
    """Already-adjudicated registry rows are skipped."""
    ents = {"anthem:Organization:org1": _entity()}
    subs = [_submission(status="adjudicated")]
    assert find_matches(subs, [_eob()], [], ents) == []
