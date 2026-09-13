"""Canonical identity references and registry persistence helpers."""



def entity_ref(provider: str, entity_type: str, entity_id: str) -> str:
    """Return the stable, provider-scoped identity key."""
    return f"{provider}:{entity_type}:{entity_id}"


def ref_from_fhir(reference: str | None, provider: str, entity_type: str | None = None) -> str | None:
    """Convert a FHIR ``ResourceType/id`` reference into an entity reference."""
    if not reference:
        return None
    value = reference.split("|", 1)[0].split("/", 1)
    if len(value) == 2:
        entity_type, entity_id = value
    elif entity_type:
        entity_id = value[0]
    else:
        return None
    return entity_ref(provider, entity_type, entity_id) if entity_id else None


def display_from_fhir(obj: dict | None) -> str | None:
    """Extract a display value without interpreting clinical descriptions."""
    if not isinstance(obj, dict):
        return None
    if obj.get("display"):
        return str(obj["display"])
    if obj.get("text"):
        return str(obj["text"])
    name = obj.get("name")
    if isinstance(name, list):
        name = name[0] if name else None
    if isinstance(name, dict):
        parts = [*(name.get("prefix") or []), *(name.get("given") or [])]
        if name.get("family"):
            parts.append(name["family"])
        if name.get("suffix"):
            parts.extend(name["suffix"] or [])
        return " ".join(str(part) for part in parts if part).strip() or None
    return None


def _registry_model(session):
    """Select the registry ORM model matching the session's database."""
    url = str(session.get_bind().url)
    if "myhealth_ucla" in url:
        from myhealth_fhir.models.ucla import EntityName
    elif "myhealth_auth" in url:
        from myhealth_fhir.models.auth import EntityName
    else:
        from myhealth_fhir.models.anthem import EntityName
    return EntityName


def upsert_entity_name(
    session,
    *,
    provider: str,
    entity_type: str,
    entity_id: str,
    name: str | None,
    npi: str | None = None,
    display: str | None = None,
) -> str | None:
    """Upsert a non-empty identity name and return its canonical reference."""
    if not entity_id:
        return None
    ref = entity_ref(provider, entity_type, entity_id)
    EntityName = _registry_model(session)
    row = session.get(EntityName, ref)
    if row is None:
        row = EntityName(entity_ref=ref, provider=provider, entity_type=entity_type, entity_id=entity_id)
        session.add(row)
    if name:
        row.name = name
    if npi:
        row.npi = npi
    if display:
        row.display = display
    return ref


def upsert_patient_name(session, *, provider: str, patient_id: str, name: str | None) -> str | None:
    """Persist a patient display name in the registry."""
    return upsert_entity_name(
        session,
        provider=provider,
        entity_type="Patient",
        entity_id=patient_id,
        name=name,
    )
