"""DB layer compatibility shim — migrate imports to the new homes.

- engines/sessions:     ``myhealth_fhir.db.engine``
- schema bootstrap:     ``myhealth_fhir.db.schema.bootstrap``
- migrations/views:     ``myhealth_fhir.db.schema.migrations`` / ``.views``
- OAuth replica sync:   ``myhealth_fhir.services.oauth_sync``
"""

from myhealth_fhir.db.engine import (
    PROVIDER_DB,
    get_anthem_engine,
    get_anthem_session,
    get_auth_engine,
    get_auth_session,
    get_session_for,
    get_ucla_engine,
    get_ucla_session,
    is_postgres,
)
from myhealth_fhir.db.schema.bootstrap import init_db
from myhealth_fhir.db.schema.migrations import (
    _backfill_auth_patient_registry,
    _backfill_fhir_identity_registry,
    _migrate_anthem_schema,
    _migrate_auth_schema,
    _migrate_identity_schema,
    _migrate_raw_json_unpack_anthem,
    _migrate_raw_json_unpack_ucla,
    _migrate_ucla_extended_schema,
    _migrate_ucla_schema,
    _validate_identity_backfill,
)
from myhealth_fhir.db.schema.views import _create_anthem_views, _create_ucla_views
from myhealth_fhir.services.oauth_sync import sync_oauth_tokens_replica

__all__ = [
    "PROVIDER_DB",
    "_backfill_auth_patient_registry",
    "_backfill_fhir_identity_registry",
    "_create_anthem_views",
    "_create_ucla_views",
    "_migrate_anthem_schema",
    "_migrate_auth_schema",
    "_migrate_identity_schema",
    "_migrate_raw_json_unpack_anthem",
    "_migrate_raw_json_unpack_ucla",
    "_migrate_ucla_extended_schema",
    "_migrate_ucla_schema",
    "_validate_identity_backfill",
    "get_anthem_engine",
    "get_anthem_session",
    "get_auth_engine",
    "get_auth_session",
    "get_session_for",
    "get_ucla_engine",
    "get_ucla_session",
    "init_db",
    "is_postgres",
    "sync_oauth_tokens_replica",
]
