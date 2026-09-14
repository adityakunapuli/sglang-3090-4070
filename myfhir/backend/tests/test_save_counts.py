"""B1 regression: the upsert save_* functions must count UPDATEs, not just INSERTs.

Before the fix, a re-sync (every row already present) reported ``(0, ...)`` even
though every row was refreshed, so the job summary misleadingly showed "0 new".
Now the 16 upsert save_* functions in ``fhir/ucla_save.py`` return
``(inserted, updated)``. This integration test proves the contract on a real
Postgres (skips when unreachable) using ``TESTB1-``-prefixed fixtures that are
removed before and after the test.
"""
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from myhealth_fhir.db.engine import get_ucla_engine, get_session_for
from myhealth_fhir.fhir.ucla_save import save_encounters_to_db
from myhealth_fhir.models.ucla import (
    Encounter,
    EncounterIdentifier,
    EncounterParticipant,
)

P = "TESTB1-ENC-1"


def _encounter(res_id: str, status: str = "finished") -> dict:
    return {
        "id": res_id,
        "status": status,
        "class": {"code": "AMB"},
        "period": {"start": "2026-01-01T00:00:00Z", "end": "2026-01-01T01:00:00Z"},
        "reasonCode": [],
        "location": [],
        "participant": [],
    }


def _cleanup(session: Session) -> None:
    session.query(EncounterParticipant).filter(EncounterParticipant.encounter_id.like("TESTB1-%")).delete(synchronize_session=False)
    session.query(EncounterIdentifier).filter(EncounterIdentifier.encounter_id.like("TESTB1-%")).delete(synchronize_session=False)
    session.query(Encounter).filter(Encounter.id.like("TESTB1-%")).delete(synchronize_session=False)
    session.commit()


@pytest.fixture()
def ucla_env():
    try:
        engine = get_ucla_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("ucla Postgres not reachable")

    with get_session_for("ucla") as session:
        _cleanup(session)  # pre-test cleanup (idempotent)

    yield

    with get_session_for("ucla") as session:
        _cleanup(session)  # post-test cleanup


def test_first_save_is_insert(ucla_env):
    """A brand-new encounter is counted as an insert, not an update."""
    inserted, updated = save_encounters_to_db([_encounter(P)], provider="ucla")
    assert (inserted, updated) == (1, 0)


def test_resync_is_update_not_insert(ucla_env):
    """Re-saving the same encounter counts as an update (the B1 fix)."""
    save_encounters_to_db([_encounter(P)], provider="ucla")  # seed
    inserted, updated = save_encounters_to_db([_encounter(P, status="in-progress")], provider="ucla")
    assert (inserted, updated) == (0, 1)


def test_mixed_batch_splits_inserts_and_updates(ucla_env):
    """A batch with one new + one existing splits the counts correctly."""
    save_encounters_to_db([_encounter("TESTB1-ENC-A")], provider="ucla")  # seed one
    inserted, updated = save_encounters_to_db(
        [_encounter("TESTB1-ENC-A"), _encounter("TESTB1-ENC-B")],
        provider="ucla",
    )
    assert (inserted, updated) == (1, 1)
