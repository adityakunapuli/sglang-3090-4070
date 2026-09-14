"""UCLA database models — clinical data (labs, imaging, encounters, notes)."""

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class UclaBase(DeclarativeBase):
    """SQLAlchemy declarative base for ucla database models."""


# ── OAuth Tokens (replica from auth DB for local queries) ─────────


class OAuthTokenRecord(UclaBase):
    """Replica of oauth_tokens for ucla-specific queries."""

    __tablename__ = "oauth_tokens"

    patient_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), primary_key=True)
    access_token: Mapped[str] = mapped_column(String(2000))
    token_type: Mapped[str] = mapped_column(String(20), default="Bearer")
    expires_in: Mapped[int] = mapped_column(Integer, default=3600)
    scope: Mapped[str | None] = mapped_column(Text)
    refresh_token: Mapped[str | None] = mapped_column(Text)
    id_token: Mapped[str | None] = mapped_column(Text)
    obtained_at: Mapped[datetime] = mapped_column(DateTime)
    refresh_token_expires_in: Mapped[int] = mapped_column(Integer, default=2592000)
    last_eob_fetch: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_claim_fetch: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


# ── Patient Identity (replica from auth DB for local queries) ───────


class PatientRecord(UclaBase):
    """Replica of patients table for ucla-specific queries."""

    __tablename__ = "patients"

    patient_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), primary_key=True)
    entity_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)


class EntityName(UclaBase):
    """Normalized identity registry for the UCLA database."""

    __tablename__ = "entity_names"

    entity_ref: Mapped[str] = mapped_column(String(255), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    npi: Mapped[str | None] = mapped_column(String(20))
    display: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ── Encounter (Visit anchor for analytics) ────────────────────────


class Encounter(UclaBase):
    """FHIR Encounter — a patient visit/encounter at the clinic.

    Serves as the anchor point for grouping clinical observations,
    diagnostic reports, and notes by visit.
    """

    __tablename__ = "encounter"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    patient_id: Mapped[str | None] = mapped_column(String(100))
    patient_ref: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str | None] = mapped_column(String(20))  # finished, in-progress, planned, etc.
    class_: Mapped[str | None] = mapped_column(String(50))  # ambulatory, emergency, inpatient, etc.
    subject_display: Mapped[str | None] = mapped_column(Text)
    period_start: Mapped[datetime | None] = mapped_column(DateTime)
    period_end: Mapped[datetime | None] = mapped_column(DateTime)
    date: Mapped[datetime | None] = mapped_column(DateTime)  # effective date for encounters without period
    reason_code: Mapped[str | None] = mapped_column(String(20))  # SNOMED/ICD code
    reason_display: Mapped[str | None] = mapped_column(Text)  # Human-readable reason
    location: Mapped[str | None] = mapped_column(Text)  # Location name
    source: Mapped[str | None] = mapped_column(String(10), server_default="fhir")  # fhir | ehi
    source_id: Mapped[str | None] = mapped_column(Text)  # EHI PAT_ENC_CSN_ID / FHIR encounter id
    type_code: Mapped[str | None] = mapped_column(String(100))
    type_display: Mapped[str | None] = mapped_column(Text)
    class_display: Mapped[str | None] = mapped_column(Text)
    admit_source_code: Mapped[str | None] = mapped_column(String(100))
    admit_source_display: Mapped[str | None] = mapped_column(Text)
    discharge_disposition_code: Mapped[str | None] = mapped_column(String(100))
    discharge_disposition_display: Mapped[str | None] = mapped_column(Text)
    service_type_display: Mapped[str | None] = mapped_column(Text)
    part_of_ref: Mapped[str | None] = mapped_column(Text)
    account_ids: Mapped[str | None] = mapped_column(Text)
    accident_related: Mapped[bool | None] = mapped_column(Boolean)
    raw_json: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    participants = relationship("EncounterParticipant", back_populates="encounter", cascade="all, delete-orphan")
    reports = relationship("DiagnosticReport", back_populates="encounter")
    observations = relationship("ClinicalObservation", back_populates="encounter")
    notes = relationship("ClinicalNote", back_populates="encounter")
    identifiers = relationship("EncounterIdentifier", back_populates="encounter", cascade="all, delete-orphan")


class EncounterIdentifier(UclaBase):
    """Encounter-level FHIR identifier[] entry."""

    __tablename__ = "encounter_identifier"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    encounter_id: Mapped[str] = mapped_column(ForeignKey("encounter.id"))
    seq: Mapped[int | None] = mapped_column(Integer)
    system: Mapped[str | None] = mapped_column(Text)
    value: Mapped[str | None] = mapped_column(Text)
    use: Mapped[str | None] = mapped_column(String(20))

    encounter = relationship("Encounter", back_populates="identifiers")


class EncounterParticipant(UclaBase):
    """Provider/staff member participating in an encounter."""

    __tablename__ = "encounter_participant"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    encounter_id: Mapped[str] = mapped_column(ForeignKey("encounter.id"))
    individual_ref: Mapped[str | None] = mapped_column(Text)
    role_code: Mapped[str | None] = mapped_column(String(20))
    role_display: Mapped[str | None] = mapped_column(Text)
    type_code: Mapped[str | None] = mapped_column(String(100))
    period_start: Mapped[datetime | None] = mapped_column(DateTime)
    period_end: Mapped[datetime | None] = mapped_column(DateTime)

    encounter = relationship("Encounter", back_populates="participants")


# ── Diagnostic Report (panel/container for lab observations) ──────


class DiagnosticReport(UclaBase):
    """Lab/imaging report (DiagnosticReport) with normalized fields and raw FHIR JSON."""

    __tablename__ = "diagnostic_report"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    patient_id: Mapped[str | None] = mapped_column(String(100))
    provider: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str | None] = mapped_column(String(20))
    category: Mapped[str | None] = mapped_column(String(100))
    code_display: Mapped[str | None] = mapped_column(Text)
    code_text: Mapped[str | None] = mapped_column(Text)
    code_loinc: Mapped[str | None] = mapped_column(String(20))
    effective_datetime: Mapped[datetime | None] = mapped_column(DateTime)
    issued: Mapped[datetime | None] = mapped_column(DateTime)
    performer_ref: Mapped[str | None] = mapped_column(String(255))
    conclusion: Mapped[str | None] = mapped_column(Text)
    body_site: Mapped[str | None] = mapped_column(Text)
    method: Mapped[str | None] = mapped_column(Text)
    encounter_id: Mapped[str | None] = mapped_column(ForeignKey("encounter.id"))
    specimen_ref: Mapped[str | None] = mapped_column(Text)
    has_images: Mapped[int | None] = mapped_column(Integer, default=0)
    conclusion_code: Mapped[str | None] = mapped_column(String(100))
    conclusion_code_display: Mapped[str | None] = mapped_column(Text)
    interpreter_ref: Mapped[str | None] = mapped_column(Text)
    interpreter_display: Mapped[str | None] = mapped_column(Text)
    presented_form_url: Mapped[str | None] = mapped_column(Text)
    presented_form_title: Mapped[str | None] = mapped_column(Text)
    presented_form_type: Mapped[str | None] = mapped_column(Text)
    category_code: Mapped[str | None] = mapped_column(String(100))
    raw_json: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    results = relationship("LabResult", back_populates="report", cascade="all, delete-orphan")
    encounter = relationship("Encounter", back_populates="reports")
    identifiers = relationship("DiagnosticReportIdentifier", back_populates="report", cascade="all, delete-orphan")


class DiagnosticReportIdentifier(UclaBase):
    """DiagnosticReport-level FHIR identifier[] entry."""

    __tablename__ = "diagnostic_report_identifier"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("diagnostic_report.id"))
    seq: Mapped[int | None] = mapped_column(Integer)
    system: Mapped[str | None] = mapped_column(Text)
    value: Mapped[str | None] = mapped_column(Text)
    use: Mapped[str | None] = mapped_column(String(20))

    report = relationship("DiagnosticReport", back_populates="identifiers")


class LabResult(UclaBase):
    """Individual lab test result (Observation) within a DiagnosticReport panel."""

    __tablename__ = "lab_result"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fhir_id: Mapped[str] = mapped_column(Text)
    report_id: Mapped[str] = mapped_column(ForeignKey("diagnostic_report.id"))
    code_display: Mapped[str | None] = mapped_column(Text)
    code_text: Mapped[str | None] = mapped_column(Text)
    code_loinc: Mapped[str | None] = mapped_column(String(20))
    value: Mapped[str | None] = mapped_column(Text)
    value_float: Mapped[float | None] = mapped_column(Float)
    value_unit: Mapped[str | None] = mapped_column(String(20))
    reference_range: Mapped[str | None] = mapped_column(Text)
    interpretation_code: Mapped[str | None] = mapped_column(String(10))
    interpretation_display: Mapped[str | None] = mapped_column(String(30))
    effective_datetime: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str | None] = mapped_column(String(20))
    component_value: Mapped[str | None] = mapped_column(Text)  # JSON: parsed Observation.component[]
    raw_json: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    category_code: Mapped[str | None] = mapped_column(String(100))
    category_display: Mapped[str | None] = mapped_column(Text)
    based_on_ref: Mapped[str | None] = mapped_column(Text)
    specimen_ref: Mapped[str | None] = mapped_column(Text)
    encounter_ref: Mapped[str | None] = mapped_column(Text)
    issued: Mapped[datetime | None] = mapped_column(DateTime)
    note_text: Mapped[str | None] = mapped_column(Text)
    method_display: Mapped[str | None] = mapped_column(Text)
    body_site: Mapped[str | None] = mapped_column(Text)
    data_absent_reason_code: Mapped[str | None] = mapped_column(String(100))
    data_absent_reason_display: Mapped[str | None] = mapped_column(Text)
    value_code: Mapped[str | None] = mapped_column(String(100))
    value_display: Mapped[str | None] = mapped_column(Text)
    value_comparator: Mapped[str | None] = mapped_column(String(10))

    report = relationship("DiagnosticReport", back_populates="results")
    components = relationship("LabResultComponent", back_populates="lab", cascade="all, delete-orphan")


class LabResultComponent(UclaBase):
    """Individual component measurement within a panel Observation."""

    __tablename__ = "lab_result_component"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lab_id: Mapped[int] = mapped_column(ForeignKey("lab_result.id"))
    seq: Mapped[int | None] = mapped_column(Integer)
    code_loinc: Mapped[str | None] = mapped_column(String(20))
    code_display: Mapped[str | None] = mapped_column(Text)
    component_value: Mapped[str | None] = mapped_column(Text)
    value_float: Mapped[float | None] = mapped_column(Float)
    value_unit: Mapped[str | None] = mapped_column(String(30))
    reference_range: Mapped[str | None] = mapped_column(Text)
    interpretation_code: Mapped[str | None] = mapped_column(String(10))
    interpretation_display: Mapped[str | None] = mapped_column(Text)

    lab = relationship("LabResult", back_populates="components")


class ImagingObservation(UclaBase):
    """Standalone imaging observation (POCUS ultrasound measurements, etc.)."""

    __tablename__ = "imaging_observation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fhir_id: Mapped[str] = mapped_column(Text)
    code_display: Mapped[str | None] = mapped_column(Text)
    code_text: Mapped[str | None] = mapped_column(Text)
    code_loinc: Mapped[str | None] = mapped_column(String(20))
    value: Mapped[str | None] = mapped_column(Text)
    value_float: Mapped[float | None] = mapped_column(Float)
    value_unit: Mapped[str | None] = mapped_column(String(20))
    reference_range: Mapped[str | None] = mapped_column(Text)
    interpretation_code: Mapped[str | None] = mapped_column(String(10))
    interpretation_display: Mapped[str | None] = mapped_column(String(30))
    effective_datetime: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str | None] = mapped_column(String(20))
    component_value: Mapped[str | None] = mapped_column(Text)  # JSON: parsed Observation.component[]
    raw_json: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ── Clinical Observation (normalized, extensible findings table) ──


class ClinicalObservation(UclaBase):
    """Generic clinical finding/measurement — extensible for labs, vitals, POCUS, etc.

    Unlike lab_result (tied to DiagnosticReport), this table can capture
    ANY Observation resource, linked to an encounter for analytics.
    """

    __tablename__ = "clinical_observation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fhir_id: Mapped[str] = mapped_column(Text, unique=True)
    encounter_id: Mapped[str | None] = mapped_column(ForeignKey("encounter.id"))
    category: Mapped[str | None] = mapped_column(String(50))  # laboratory, vital-signs, imaging, etc.
    code_display: Mapped[str | None] = mapped_column(Text)
    code_text: Mapped[str | None] = mapped_column(Text)
    code_system: Mapped[str | None] = mapped_column(Text)  # e.g. http://loinc.org
    code_loinc: Mapped[str | None] = mapped_column(String(20))
    value_text: Mapped[str | None] = mapped_column(Text)  # Human-readable value
    value_float: Mapped[float | None] = mapped_column(Float)  # Numeric value
    value_unit: Mapped[str | None] = mapped_column(String(30))
    reference_range: Mapped[str | None] = mapped_column(Text)
    interpretation_code: Mapped[str | None] = mapped_column(String(10))
    interpretation_display: Mapped[str | None] = mapped_column(String(30))
    effective_datetime: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str | None] = mapped_column(String(20))
    component_value: Mapped[str | None] = mapped_column(Text)  # JSON: parsed Observation.component[]
    source: Mapped[str | None] = mapped_column(String(10), server_default="fhir")  # fhir | ehi
    source_id: Mapped[str | None] = mapped_column(Text)  # EHI measurement id / FHIR Observation id
    raw_json: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    category_display: Mapped[str | None] = mapped_column(Text)
    issued: Mapped[datetime | None] = mapped_column(DateTime)
    note_text: Mapped[str | None] = mapped_column(Text)
    value_code: Mapped[str | None] = mapped_column(String(100))
    value_display: Mapped[str | None] = mapped_column(Text)
    value_comparator: Mapped[str | None] = mapped_column(String(10))

    encounter = relationship("Encounter", back_populates="observations")
    components = relationship("ClinicalObservationComponent", back_populates="observation", cascade="all, delete-orphan")


class ClinicalObservationComponent(UclaBase):
    """Individual component measurement within a panel Observation."""

    __tablename__ = "clinical_observation_component"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    observation_id: Mapped[int] = mapped_column(ForeignKey("clinical_observation.id"))
    seq: Mapped[int | None] = mapped_column(Integer)
    code_loinc: Mapped[str | None] = mapped_column(String(20))
    code_display: Mapped[str | None] = mapped_column(Text)
    component_value: Mapped[str | None] = mapped_column(Text)
    value_float: Mapped[float | None] = mapped_column(Float)
    value_unit: Mapped[str | None] = mapped_column(String(30))
    reference_range: Mapped[str | None] = mapped_column(Text)
    interpretation_code: Mapped[str | None] = mapped_column(String(10))
    interpretation_display: Mapped[str | None] = mapped_column(Text)

    observation = relationship("ClinicalObservation", back_populates="components")


# ── Clinical Note (free-form text: discharge summaries, visit notes) ──


class ClinicalNote(UclaBase):
    """Free-form clinical note (discharge summary, visit note, procedure note, etc.).

    Captures text-heavy resources like ProcedureReport, ClinicalImpression,
    or any text-heavy Observation that doesn't fit the structured observation model.
    """

    __tablename__ = "clinical_note"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fhir_id: Mapped[str] = mapped_column(Text, unique=True)
    encounter_id: Mapped[str | None] = mapped_column(ForeignKey("encounter.id"))
    resource_type: Mapped[str | None] = mapped_column(String(30))  # ProcedureReport, ClinicalImpression, etc.
    category: Mapped[str | None] = mapped_column(String(50))  # procedure-note, discharge-summary, etc.
    title: Mapped[str | None] = mapped_column(Text)  # Short title/summary
    text_body: Mapped[str | None] = mapped_column(Text)  # Main note text (conclusion, description, etc.)
    raw_html: Mapped[str | None] = mapped_column(Text)  # Raw HTML Binary blob
    raw_rtf: Mapped[str | None] = mapped_column(Text)  # Raw RTF Binary blob
    author_ref: Mapped[str | None] = mapped_column(Text)  # Authoring provider name (entity ref)
    authored_datetime: Mapped[datetime | None] = mapped_column(DateTime)
    effective_datetime: Mapped[datetime | None] = mapped_column(DateTime)
    source: Mapped[str | None] = mapped_column(String(10), server_default="fhir")  # fhir | ehi
    source_id: Mapped[str | None] = mapped_column(Text)  # EHI NOTE_ID / FHIR resource id
    raw_json: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    type_code: Mapped[str | None] = mapped_column(String(100))
    doc_status: Mapped[str | None] = mapped_column(String(50))
    custodian_display: Mapped[str | None] = mapped_column(Text)
    context_period_start: Mapped[datetime | None] = mapped_column(DateTime)
    context_period_end: Mapped[datetime | None] = mapped_column(DateTime)
    subject_display: Mapped[str | None] = mapped_column(Text)
    author_display: Mapped[str | None] = mapped_column(Text)

    encounter = relationship("Encounter", back_populates="notes")
    identifiers = relationship("ClinicalNoteIdentifier", back_populates="note", cascade="all, delete-orphan")


class ClinicalNoteIdentifier(UclaBase):
    """ClinicalNote-level FHIR identifier[] entry."""

    __tablename__ = "clinical_note_identifier"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    note_id: Mapped[str] = mapped_column(ForeignKey("clinical_note.fhir_id"))
    seq: Mapped[int | None] = mapped_column(Integer)
    system: Mapped[str | None] = mapped_column(Text)
    value: Mapped[str | None] = mapped_column(Text)
    use: Mapped[str | None] = mapped_column(String(20))

    note = relationship("ClinicalNote", back_populates="identifiers")


# ── MedicationAdministration (MAR — actually given doses) ─────────


class MedicationAdministration(UclaBase):
    """Medication actually administered to the patient (Epic MAR / FHIR MedicationAdministration)."""

    __tablename__ = "medication_administration"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fhir_id: Mapped[str] = mapped_column(Text, unique=True)  # FHIR id or EHI synthetic
    source: Mapped[str | None] = mapped_column(String(10))  # fhir | ehi
    source_id: Mapped[str | None] = mapped_column(Text)  # EHI MAR RECORD_ID / FHIR id
    order_id: Mapped[str | None] = mapped_column(Text)  # EHI ORDER_MED_ID link
    patient_id: Mapped[str | None] = mapped_column(String(100))
    encounter_id: Mapped[str | None] = mapped_column(ForeignKey("encounter.id"))
    status: Mapped[str | None] = mapped_column(String(30))  # completed, stopped, etc.
    medication_display: Mapped[str | None] = mapped_column(Text)
    administered_datetime: Mapped[datetime | None] = mapped_column(DateTime)  # TAKEN_TIME
    scheduled_datetime: Mapped[datetime | None] = mapped_column(DateTime)
    route_display: Mapped[str | None] = mapped_column(String(50))
    dose_display: Mapped[str | None] = mapped_column(Text)
    dose_quantity: Mapped[float | None] = mapped_column(Float)
    dose_unit: Mapped[str | None] = mapped_column(String(30))
    morphone_mg: Mapped[float | None] = mapped_column(Float)  # MORPHINE_EQUIV_MG_DOSE
    performer_ref: Mapped[str | None] = mapped_column(String(255))  # MAR_DOC_USER_ID_NAME
    reason_display: Mapped[str | None] = mapped_column(Text)  # MAR_ACTION_C_NAME
    note_text: Mapped[str | None] = mapped_column(Text)  # COMMENTS
    raw_json: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ── ServiceRequest (orders: procedures, labs) ─────────────────────


class ServiceRequest(UclaBase):
    """FHIR ServiceRequest — a diagnostic/procedure/therapy order.

    Maps Epic ORDER_PROC / ORDER_MED to an order timeline.
    """

    __tablename__ = "service_request"

    fhir_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str | None] = mapped_column(String(10))  # fhir | ehi
    source_id: Mapped[str | None] = mapped_column(Text)  # EHI ORDER_PROC_ID / FHIR id
    patient_id: Mapped[str | None] = mapped_column(String(100))
    encounter_id: Mapped[str | None] = mapped_column(ForeignKey("encounter.id"))
    status: Mapped[str | None] = mapped_column(String(30))  # active, completed, on-hold, etc.
    intent: Mapped[str | None] = mapped_column(String(30))
    category: Mapped[str | None] = mapped_column(String(50))  # procedure, laboratory, etc.
    code_display: Mapped[str | None] = mapped_column(Text)
    code_system: Mapped[str | None] = mapped_column(Text)
    code_value: Mapped[str | None] = mapped_column(String(30))
    authored_on: Mapped[datetime | None] = mapped_column(DateTime)
    requester_ref: Mapped[str | None] = mapped_column(String(255))
    reason_display: Mapped[str | None] = mapped_column(Text)
    order_detail: Mapped[str | None] = mapped_column(Text)  # EHI ORDER_INST / instruction text
    note_text: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    priority: Mapped[str | None] = mapped_column(String(10))
    based_on_ref: Mapped[str | None] = mapped_column(Text)
    code_text: Mapped[str | None] = mapped_column(Text)


# ── Specimen ──────────────────────────────────────────────────────


class Specimen(UclaBase):
    """FHIR Specimen — laboratory specimen collected from the patient."""

    __tablename__ = "specimen"

    fhir_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str | None] = mapped_column(String(10))  # fhir | ehi
    patient_id: Mapped[str | None] = mapped_column(String(100))
    encounter_id: Mapped[str | None] = mapped_column(ForeignKey("encounter.id"))
    status: Mapped[str | None] = mapped_column(String(30))
    type_display: Mapped[str | None] = mapped_column(Text)
    type_system: Mapped[str | None] = mapped_column(Text)
    type_code: Mapped[str | None] = mapped_column(String(30))
    collected_datetime: Mapped[datetime | None] = mapped_column(DateTime)
    received_datetime: Mapped[datetime | None] = mapped_column(DateTime)
    body_site: Mapped[str | None] = mapped_column(Text)
    note_text: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    identifier_value: Mapped[str | None] = mapped_column(Text)


# ── Communication (phone encounters, messages) ────────────────────


class Communication(UclaBase):
    """FHIR Communication — a record of a message between patient and provider.

    Mirrors Epic COMM_TRACE_INFO / phone encounter notes.
    """

    __tablename__ = "communication"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fhir_id: Mapped[str] = mapped_column(Text, unique=True)
    source: Mapped[str | None] = mapped_column(String(10))  # fhir | ehi
    source_id: Mapped[str | None] = mapped_column(Text)
    patient_id: Mapped[str | None] = mapped_column(String(100))
    encounter_id: Mapped[str | None] = mapped_column(ForeignKey("encounter.id"))
    status: Mapped[str | None] = mapped_column(String(30))  # completed, in-progress, etc.
    category: Mapped[str | None] = mapped_column(String(50))  # notification, phone-call, etc.
    subject: Mapped[str | None] = mapped_column(Text)
    sent_datetime: Mapped[datetime | None] = mapped_column(DateTime)
    received_datetime: Mapped[datetime | None] = mapped_column(DateTime)
    sender_ref: Mapped[str | None] = mapped_column(String(255))
    recipient_ref: Mapped[str | None] = mapped_column(String(255))
    medium: Mapped[str | None] = mapped_column(String(30))
    payload_text: Mapped[str | None] = mapped_column(Text)
    note_text: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    category_code: Mapped[str | None] = mapped_column(String(100))


# ── CareTeam (care team participants per episode) ─────────────────


class CareTeam(UclaBase):
    """FHIR CareTeam — the care team responsible for a patient's care."""

    __tablename__ = "care_team"

    fhir_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str | None] = mapped_column(String(10))  # fhir | ehi
    patient_id: Mapped[str | None] = mapped_column(String(100))
    encounter_id: Mapped[str | None] = mapped_column(ForeignKey("encounter.id"))
    status: Mapped[str | None] = mapped_column(String(30))
    category: Mapped[str | None] = mapped_column(String(50))
    name: Mapped[str | None] = mapped_column(Text)
    period_start: Mapped[datetime | None] = mapped_column(DateTime)
    period_end: Mapped[datetime | None] = mapped_column(DateTime)
    participants_display: Mapped[str | None] = mapped_column(Text)
    note_text: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    category_code: Mapped[str | None] = mapped_column(String(100))

    members = relationship("CareTeamParticipant", back_populates="care_team", cascade="all, delete-orphan")


class CareTeamParticipant(UclaBase):
    """CareTeam participant[] entry (member + role per participant)."""

    __tablename__ = "care_team_participant"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    care_team_id: Mapped[str] = mapped_column(ForeignKey("care_team.fhir_id"))
    seq: Mapped[int | None] = mapped_column(Integer)
    member_ref: Mapped[str | None] = mapped_column(Text)
    member_display: Mapped[str | None] = mapped_column(Text)
    role_code: Mapped[str | None] = mapped_column(String(100))
    role_display: Mapped[str | None] = mapped_column(Text)

    care_team = relationship("CareTeam", back_populates="members")


# ── Condition (diagnoses) ──────────────────────────────────────────


class Condition(UclaBase):
    __tablename__ = "condition"

    fhir_id: Mapped[str] = mapped_column(Text, primary_key=True)
    patient_id: Mapped[str | None] = mapped_column(String(100))
    encounter_id: Mapped[str | None] = mapped_column(ForeignKey("encounter.id"))
    clinical_status: Mapped[str | None] = mapped_column(String(30))
    verification_status: Mapped[str | None] = mapped_column(String(30))
    category: Mapped[str | None] = mapped_column(String(50))
    code_system: Mapped[str | None] = mapped_column(Text)
    code_value: Mapped[str | None] = mapped_column(String(30))
    code_display: Mapped[str | None] = mapped_column(Text)
    body_site: Mapped[str | None] = mapped_column(Text)
    severity_text: Mapped[str | None] = mapped_column(String(30))
    onset_datetime: Mapped[datetime | None] = mapped_column(DateTime)
    abatement_datetime: Mapped[datetime | None] = mapped_column(DateTime)
    recorded_date: Mapped[datetime | None] = mapped_column(DateTime)
    asserter_ref: Mapped[str | None] = mapped_column(String(255))
    note_text: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(10), server_default="fhir")  # fhir | ehi
    source_id: Mapped[str | None] = mapped_column(Text)  # EHI PROBLEM_ID
    raw_json: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    category_display: Mapped[str | None] = mapped_column(Text)
    clinical_status_display: Mapped[str | None] = mapped_column(Text)
    verification_status_display: Mapped[str | None] = mapped_column(Text)
    code_text: Mapped[str | None] = mapped_column(Text)
    evidence_refs: Mapped[str | None] = mapped_column(Text)


# ── Procedure (surgical history, IVF procedures) ───────────────────


class ProcedureRecord(UclaBase):
    __tablename__ = "procedure_record"

    fhir_id: Mapped[str] = mapped_column(Text, primary_key=True)
    patient_id: Mapped[str | None] = mapped_column(String(100))
    encounter_id: Mapped[str | None] = mapped_column(ForeignKey("encounter.id"))
    status: Mapped[str | None] = mapped_column(String(30))
    category: Mapped[str | None] = mapped_column(String(50))
    code_system: Mapped[str | None] = mapped_column(Text)
    code_value: Mapped[str | None] = mapped_column(String(30))
    code_display: Mapped[str | None] = mapped_column(Text)
    performed_datetime: Mapped[datetime | None] = mapped_column(DateTime)
    performer_ref: Mapped[str | None] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(Text)
    reason_display: Mapped[str | None] = mapped_column(Text)
    outcome_text: Mapped[str | None] = mapped_column(Text)
    body_site: Mapped[str | None] = mapped_column(Text)
    note_text: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ── MedicationStatement (patient-reported medications) ─────────────


class MedicationStatement(UclaBase):
    __tablename__ = "medication_statement"

    fhir_id: Mapped[str] = mapped_column(Text, primary_key=True)
    patient_id: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str | None] = mapped_column(String(30))
    category: Mapped[str | None] = mapped_column(String(50))
    medication_display: Mapped[str | None] = mapped_column(Text)
    medication_code: Mapped[str | None] = mapped_column(String(30))
    medication_system: Mapped[str | None] = mapped_column(Text)
    effective_start: Mapped[datetime | None] = mapped_column(DateTime)
    effective_end: Mapped[datetime | None] = mapped_column(DateTime)
    date_asserted: Mapped[datetime | None] = mapped_column(DateTime)
    information_source: Mapped[str | None] = mapped_column(Text)
    reason_display: Mapped[str | None] = mapped_column(Text)
    dosage_text: Mapped[str | None] = mapped_column(Text)
    route_display: Mapped[str | None] = mapped_column(String(50))
    note_text: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    medication_ref: Mapped[str | None] = mapped_column(Text)
    reported: Mapped[bool | None] = mapped_column(Boolean)
    information_source_ref: Mapped[str | None] = mapped_column(Text)
    category_code: Mapped[str | None] = mapped_column(String(100))


# ── MedicationRequest (prescriptions) ──────────────────────────────


class MedicationRequest(UclaBase):
    __tablename__ = "medication_request"

    fhir_id: Mapped[str] = mapped_column(Text, primary_key=True)
    patient_id: Mapped[str | None] = mapped_column(String(100))
    encounter_id: Mapped[str | None] = mapped_column(ForeignKey("encounter.id"))
    status: Mapped[str | None] = mapped_column(String(30))
    intent: Mapped[str | None] = mapped_column(String(30))
    medication_display: Mapped[str | None] = mapped_column(Text)
    medication_code: Mapped[str | None] = mapped_column(String(100))
    medication_system: Mapped[str | None] = mapped_column(Text)
    authored_on: Mapped[datetime | None] = mapped_column(DateTime)
    requester_ref: Mapped[str | None] = mapped_column(String(255))
    dosage_instruction: Mapped[str | None] = mapped_column(Text)
    quantity_dispensed: Mapped[int | None] = mapped_column(Integer)
    refills: Mapped[int | None] = mapped_column(Integer)
    validity_start: Mapped[datetime | None] = mapped_column(DateTime)
    validity_end: Mapped[datetime | None] = mapped_column(DateTime)
    reason_display: Mapped[str | None] = mapped_column(Text)
    note_text: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(10), server_default="fhir")  # fhir | ehi
    source_id: Mapped[str | None] = mapped_column(Text)  # EHI ORDER_MED_ID
    raw_json: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    category_display: Mapped[str | None] = mapped_column(Text)
    course_of_therapy_code: Mapped[str | None] = mapped_column(String(100))
    course_of_therapy_display: Mapped[str | None] = mapped_column(Text)
    expected_supply_value: Mapped[float | None] = mapped_column(Float)
    expected_supply_unit: Mapped[str | None] = mapped_column(String(30))
    quantity_unit: Mapped[str | None] = mapped_column(String(30))
    medication_ref: Mapped[str | None] = mapped_column(Text)
    recorder_ref: Mapped[str | None] = mapped_column(Text)
    recorder_display: Mapped[str | None] = mapped_column(Text)
    reported: Mapped[bool | None] = mapped_column(Boolean)
    prior_prescription_ref: Mapped[str | None] = mapped_column(Text)
    group_identifier_value: Mapped[str | None] = mapped_column(Text)
    substitution_allowed: Mapped[bool | None] = mapped_column(Boolean)

    identifiers = relationship("MedicationRequestIdentifier", back_populates="medreq", cascade="all, delete-orphan")
    dosages = relationship("MedicationRequestDosage", back_populates="medreq", cascade="all, delete-orphan")


class MedicationRequestIdentifier(UclaBase):
    """MedicationRequest-level FHIR identifier[] entry."""

    __tablename__ = "medication_request_identifier"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    medreq_id: Mapped[str] = mapped_column(ForeignKey("medication_request.fhir_id"))
    seq: Mapped[int | None] = mapped_column(Integer)
    system: Mapped[str | None] = mapped_column(Text)
    value: Mapped[str | None] = mapped_column(Text)
    use: Mapped[str | None] = mapped_column(String(20))

    medreq = relationship("MedicationRequest", back_populates="identifiers")


class MedicationRequestDosage(UclaBase):
    """MedicationRequest dosageInstruction[] entry."""

    __tablename__ = "medication_request_dosage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    medreq_id: Mapped[str] = mapped_column(ForeignKey("medication_request.fhir_id"))
    seq: Mapped[int | None] = mapped_column(Integer)
    dosage_text: Mapped[str | None] = mapped_column(Text)
    route_code: Mapped[str | None] = mapped_column(String(100))
    route_display: Mapped[str | None] = mapped_column(Text)
    method_code: Mapped[str | None] = mapped_column(String(100))
    method_display: Mapped[str | None] = mapped_column(Text)
    patient_instruction: Mapped[str | None] = mapped_column(Text)
    as_needed: Mapped[bool | None] = mapped_column(Boolean)
    timing_text: Mapped[str | None] = mapped_column(Text)
    dose_value: Mapped[float | None] = mapped_column(Float)
    dose_unit: Mapped[str | None] = mapped_column(String(30))

    medreq = relationship("MedicationRequest", back_populates="dosages")


# ── AllergyIntolerance ─────────────────────────────────────────────


class AllergyIntolerance(UclaBase):
    __tablename__ = "allergy_intolerance"

    fhir_id: Mapped[str] = mapped_column(Text, primary_key=True)
    patient_id: Mapped[str | None] = mapped_column(String(100))
    clinical_status: Mapped[str | None] = mapped_column(String(30))
    verification_status: Mapped[str | None] = mapped_column(String(30))
    category: Mapped[str | None] = mapped_column(String(30))
    criticality: Mapped[str | None] = mapped_column(String(30))
    code_display: Mapped[str | None] = mapped_column(Text)
    code_system: Mapped[str | None] = mapped_column(Text)
    code_value: Mapped[str | None] = mapped_column(String(30))
    reaction_manifestation: Mapped[str | None] = mapped_column(Text)
    reaction_severity: Mapped[str | None] = mapped_column(String(30))
    recorded_date: Mapped[datetime | None] = mapped_column(DateTime)
    recorder_ref: Mapped[str | None] = mapped_column(String(255))
    note_text: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    type: Mapped[str | None] = mapped_column(String(50))
    onset_datetime: Mapped[datetime | None] = mapped_column(DateTime)
    code_text: Mapped[str | None] = mapped_column(Text)
    reaction_description: Mapped[str | None] = mapped_column(Text)


# ── Immunization (vaccine history) ─────────────────────────────────


class Immunization(UclaBase):
    __tablename__ = "immunization"

    fhir_id: Mapped[str] = mapped_column(Text, primary_key=True)
    patient_id: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str | None] = mapped_column(String(30))
    vaccine_display: Mapped[str | None] = mapped_column(Text)
    vaccine_code: Mapped[str | None] = mapped_column(String(30))
    vaccine_system: Mapped[str | None] = mapped_column(Text)
    occurrence_datetime: Mapped[datetime | None] = mapped_column(DateTime)
    manufacturer: Mapped[str | None] = mapped_column(Text)
    lot_number: Mapped[str | None] = mapped_column(String(50))
    dose_quantity: Mapped[int | None] = mapped_column(Integer)
    dose_unit: Mapped[str | None] = mapped_column(String(20))
    route_display: Mapped[str | None] = mapped_column(String(50))
    site_display: Mapped[str | None] = mapped_column(String(50))
    performer_ref: Mapped[str | None] = mapped_column(String(255))
    reason_code: Mapped[str | None] = mapped_column(Text)
    note_text: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(10), server_default="fhir")  # fhir | ehi
    source_id: Mapped[str | None] = mapped_column(Text)  # EHI IMM_ADMIN row id
    raw_json: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    encounter_id: Mapped[str | None] = mapped_column(ForeignKey("encounter.id"))
    expiration_date: Mapped[date | None] = mapped_column(Date)
    location_display: Mapped[str | None] = mapped_column(Text)
    primary_source: Mapped[bool | None] = mapped_column(Boolean)
    report_origin_display: Mapped[str | None] = mapped_column(Text)

    identifiers = relationship("ImmunizationIdentifier", back_populates="immunization", cascade="all, delete-orphan")


class ImmunizationIdentifier(UclaBase):
    """Immunization-level FHIR identifier[] entry."""

    __tablename__ = "immunization_identifier"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    immunization_id: Mapped[str] = mapped_column(ForeignKey("immunization.fhir_id"))
    seq: Mapped[int | None] = mapped_column(Integer)
    system: Mapped[str | None] = mapped_column(Text)
    value: Mapped[str | None] = mapped_column(Text)
    use: Mapped[str | None] = mapped_column(String(20))

    immunization = relationship("Immunization", back_populates="identifiers")


# ── CarePlan (treatment plans) ─────────────────────────────────────


class CarePlan(UclaBase):
    __tablename__ = "care_plan"

    fhir_id: Mapped[str] = mapped_column(Text, primary_key=True)
    patient_id: Mapped[str | None] = mapped_column(String(100))
    encounter_id: Mapped[str | None] = mapped_column(ForeignKey("encounter.id"))
    status: Mapped[str | None] = mapped_column(String(30))
    intent: Mapped[str | None] = mapped_column(String(30))
    category: Mapped[str | None] = mapped_column(String(50))
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    period_start: Mapped[datetime | None] = mapped_column(DateTime)
    period_end: Mapped[datetime | None] = mapped_column(DateTime)
    author_ref: Mapped[str | None] = mapped_column(String(255))
    goal_descriptions: Mapped[str | None] = mapped_column(Text)
    activity_text: Mapped[str | None] = mapped_column(Text)
    note_text: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ── DocumentReference (clinical documents, scanned notes) ───────────


class DocumentReference(UclaBase):
    __tablename__ = "document_reference"

    fhir_id: Mapped[str] = mapped_column(Text, primary_key=True)
    patient_id: Mapped[str | None] = mapped_column(String(100))
    encounter_id: Mapped[str | None] = mapped_column(ForeignKey("encounter.id"))
    status: Mapped[str | None] = mapped_column(String(30))
    type_display: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(50))
    date_created: Mapped[datetime | None] = mapped_column(DateTime)
    author_ref: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    content_url: Mapped[str | None] = mapped_column(Text)
    content_title: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    facility: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    type_code: Mapped[str | None] = mapped_column(String(100))
    doc_status: Mapped[str | None] = mapped_column(String(50))
    authenticator_ref: Mapped[str | None] = mapped_column(Text)
    authenticator_display: Mapped[str | None] = mapped_column(Text)
    custodian_display: Mapped[str | None] = mapped_column(Text)
    context_period_start: Mapped[datetime | None] = mapped_column(DateTime)
    context_period_end: Mapped[datetime | None] = mapped_column(DateTime)
    subject_display: Mapped[str | None] = mapped_column(Text)
    author_display: Mapped[str | None] = mapped_column(Text)
    category_code: Mapped[str | None] = mapped_column(String(100))

    contents = relationship("DocumentReferenceContent", back_populates="doc", cascade="all, delete-orphan")
    identifiers = relationship("DocumentReferenceIdentifier", back_populates="doc", cascade="all, delete-orphan")


class DocumentReferenceContent(UclaBase):
    """DocumentReference content[] entry (attachment per format)."""

    __tablename__ = "document_reference_content"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_id: Mapped[str] = mapped_column(ForeignKey("document_reference.fhir_id"))
    seq: Mapped[int | None] = mapped_column(Integer)
    format_code: Mapped[str | None] = mapped_column(String(100))
    format_display: Mapped[str | None] = mapped_column(Text)
    attachment_url: Mapped[str | None] = mapped_column(Text)
    attachment_title: Mapped[str | None] = mapped_column(Text)
    attachment_type: Mapped[str | None] = mapped_column(String(100))
    size: Mapped[int | None] = mapped_column(Integer)

    doc = relationship("DocumentReference", back_populates="contents")


class DocumentReferenceIdentifier(UclaBase):
    """DocumentReference-level FHIR identifier[] entry."""

    __tablename__ = "document_reference_identifier"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_id: Mapped[str] = mapped_column(ForeignKey("document_reference.fhir_id"))
    seq: Mapped[int | None] = mapped_column(Integer)
    system: Mapped[str | None] = mapped_column(Text)
    value: Mapped[str | None] = mapped_column(Text)
    use: Mapped[str | None] = mapped_column(String(20))

    doc = relationship("DocumentReference", back_populates="identifiers")


# ── FamilyMemberHistory ─────────────────────────────────────────────


class FamilyMemberHistory(UclaBase):
    __tablename__ = "family_member_history"

    fhir_id: Mapped[str] = mapped_column(Text, primary_key=True)
    patient_id: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str | None] = mapped_column(String(30))
    relationship: Mapped[str | None] = mapped_column(String(30))
    family_member_ref: Mapped[str | None] = mapped_column(String(255))
    born_date: Mapped[str | None] = mapped_column(String(50))
    deceased_age: Mapped[str | None] = mapped_column(String(50))
    condition_display: Mapped[str | None] = mapped_column(Text)
    condition_code: Mapped[str | None] = mapped_column(String(30))
    condition_system: Mapped[str | None] = mapped_column(Text)
    note_text: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    relationship_code: Mapped[str | None] = mapped_column(String(100))
