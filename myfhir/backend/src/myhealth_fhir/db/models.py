"""SQLAlchemy ORM models for FHIR EOB/Claim data."""

from datetime import date, datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Text,
    Float,
    func,
    Date,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all ORM models."""


# ── OAuth Tokens (multi-patient) ──────────────────────────────────


class OAuthTokenRecord(Base):
    """Persisted OAuth2 token row, keyed by (patient_id, provider)."""

    __tablename__ = "oauth_tokens"

    patient_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), primary_key=True)
    access_token: Mapped[str] = mapped_column(Text)
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


# ── EOB Tables (migrated from eob_db/models.py) ──────────────────


class EOB(Base):
    """ExplanationOfBenefit record with normalized fields and raw FHIR JSON."""

    __tablename__ = "eob"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    claim_number: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    claim_type: Mapped[str | None] = mapped_column(Text)
    sub_type: Mapped[str | None] = mapped_column(Text)
    use: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(Text)
    disposition: Mapped[str | None] = mapped_column(Text)
    created_date: Mapped[date | None] = mapped_column(Date)
    billable_period_start: Mapped[date | None] = mapped_column(Date)
    billable_period_end: Mapped[date | None] = mapped_column(Date)
    patient_ref: Mapped[str | None] = mapped_column(Text)
    provider_ref: Mapped[str | None] = mapped_column(Text)
    insurer_payer_id: Mapped[str | None] = mapped_column(Text)
    coverage_ref: Mapped[str | None] = mapped_column(Text)
    payee_type: Mapped[str | None] = mapped_column(Text)
    payee_ref: Mapped[str | None] = mapped_column(Text)
    claim_adjustment_key: Mapped[str | None] = mapped_column(Text)
    payment_amount: Mapped[float | None] = mapped_column(Float)
    payment_date: Mapped[date | None] = mapped_column(Date)
    payment_type: Mapped[str | None] = mapped_column(Text)
    last_updated: Mapped[datetime | None] = mapped_column(DateTime)
    raw_json: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    items = relationship("EOBItem", back_populates="eob", cascade="all, delete-orphan")
    diagnoses = relationship("EOBDiagnosis", back_populates="eob", cascade="all, delete-orphan")
    care_team = relationship("EOBCareTeam", back_populates="eob", cascade="all, delete-orphan")
    totals = relationship("EOBTotal", back_populates="eob", cascade="all, delete-orphan")


class EOBItem(Base):
    """Claim line item belonging to an EOB, with HCPCS code and adjudication."""

    __tablename__ = "eob_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    eob_id: Mapped[str] = mapped_column(ForeignKey("eob.id"))
    sequence: Mapped[int | None] = mapped_column(Integer)
    hcpcs_code: Mapped[str | None] = mapped_column(String(10))
    hcpcs_display: Mapped[str | None] = mapped_column(Text)
    modifier_codes: Mapped[str | None] = mapped_column(String(100))
    serviced_date: Mapped[date | None] = mapped_column(Date)
    serviced_period_start: Mapped[date | None] = mapped_column(Date)
    serviced_period_end: Mapped[date | None] = mapped_column(Date)
    location_code: Mapped[str | None] = mapped_column(String(10))
    location_display: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[int | None] = mapped_column(Integer)
    net_amount: Mapped[float | None] = mapped_column(Float)

    submitted_amount: Mapped[float | None] = mapped_column(Float)
    allowed_amount: Mapped[float | None] = mapped_column(Float)
    paid_provider: Mapped[float | None] = mapped_column(Float)
    paid_patient: Mapped[float | None] = mapped_column(Float)
    deductible: Mapped[float | None] = mapped_column(Float)
    coinsurance: Mapped[float | None] = mapped_column(Float)
    copay: Mapped[float | None] = mapped_column(Float)
    noncovered: Mapped[float | None] = mapped_column(Float)
    discount: Mapped[float | None] = mapped_column(Float)
    member_liability: Mapped[float | None] = mapped_column(Float)
    allowed_units: Mapped[int | None] = mapped_column(Integer)
    adjustment_reason: Mapped[str | None] = mapped_column(Text)
    payment_status: Mapped[str | None] = mapped_column(String(20))

    eob = relationship("EOB", back_populates="items")
    adjudications = relationship(
        "EOBItemAdjudication",
        back_populates="item",
        cascade="all, delete-orphan",
    )


class EOBItemAdjudication(Base):
    """Per-item adjudication entry (category, amount, reason)."""

    __tablename__ = "eob_item_adjudication"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    eob_item_id: Mapped[int] = mapped_column(ForeignKey("eob_item.id"))
    category: Mapped[str | None] = mapped_column(Text)
    category_code: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[float | None] = mapped_column(Float)
    value_units: Mapped[int | None] = mapped_column(Integer)
    reason_code: Mapped[str | None] = mapped_column(String(20))
    reason_display: Mapped[str | None] = mapped_column(Text)

    item = relationship("EOBItem", back_populates="adjudications")


class EOBDiagnosis(Base):
    """ICD diagnosis linked to an EOB by sequence."""

    __tablename__ = "eob_diagnosis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    eob_id: Mapped[str] = mapped_column(ForeignKey("eob.id"))
    sequence: Mapped[int | None] = mapped_column(Integer)
    icd_code: Mapped[str | None] = mapped_column(String(15))
    icd_display: Mapped[str | None] = mapped_column(Text)
    diagnosis_type: Mapped[str | None] = mapped_column(String(30))
    on_admission: Mapped[str | None] = mapped_column(String(10))

    eob = relationship("EOB", back_populates="diagnoses")


class EOBCareTeam(Base):
    """Provider reference and role for an EOB's care team member."""

    __tablename__ = "eob_care_team"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    eob_id: Mapped[str] = mapped_column(ForeignKey("eob.id"))
    sequence: Mapped[int | None] = mapped_column(Integer)
    provider_ref: Mapped[str | None] = mapped_column(Text)
    role_code: Mapped[str | None] = mapped_column(String(20))
    role_display: Mapped[str | None] = mapped_column(Text)

    eob = relationship("EOB", back_populates="care_team")


class EOBTotal(Base):
    """Financial total for one category of an EOB."""

    __tablename__ = "eob_total"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    eob_id: Mapped[str] = mapped_column(ForeignKey("eob.id"))
    category: Mapped[str | None] = mapped_column(Text)
    category_code: Mapped[str | None] = mapped_column(String(20))
    amount: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(3))

    eob = relationship("EOB", back_populates="totals")


# ── Entity Name Lookup (for resolving FHIR references to names) ─


class EntityName(Base):
    """Resolved Practitioner/Organization name cached by entity id."""

    __tablename__ = "entity_names"

    entity_ref: Mapped[str] = mapped_column(String(255), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    npi: Mapped[str | None] = mapped_column(String(20))
    display: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ── Claim Submission Tables (FHIR Claim resource) ───────────────


class ClaimSubmission(Base):
    """FHIR Claim submission record (electronic in-network)."""

    __tablename__ = "claim_submission"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str | None] = mapped_column(Text)
    claim_type: Mapped[str | None] = mapped_column(Text)
    use: Mapped[str | None] = mapped_column(Text)
    created_date: Mapped[date | None] = mapped_column(Date)
    billable_period_start: Mapped[date | None] = mapped_column(Date)
    billable_period_end: Mapped[date | None] = mapped_column(Date)
    patient_ref: Mapped[str | None] = mapped_column(Text)
    provider_ref: Mapped[str | None] = mapped_column(Text)
    insurer_ref: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str | None] = mapped_column(Text)
    total_amount: Mapped[float | None] = mapped_column(Float)
    total_currency: Mapped[str | None] = mapped_column(String(3), default="USD")
    last_updated: Mapped[datetime | None] = mapped_column(DateTime)
    raw_json: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    items = relationship("ClaimItem", back_populates="claim", cascade="all, delete-orphan")
    claim_diagnoses = relationship("ClaimDiagnosis", back_populates="claim", cascade="all, delete-orphan")
    claim_care_team = relationship("ClaimCareTeam", back_populates="claim", cascade="all, delete-orphan")


class ClaimItem(Base):
    """Claim line item with HCPCS code, unit price, and net amount."""

    __tablename__ = "claim_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claim_submission.id"))
    sequence: Mapped[int | None] = mapped_column(Integer)
    hcpcs_code: Mapped[str | None] = mapped_column(String(10))
    hcpcs_display: Mapped[str | None] = mapped_column(Text)
    modifier_codes: Mapped[str | None] = mapped_column(String(100))
    serviced_date: Mapped[date | None] = mapped_column(Date)
    serviced_period_start: Mapped[date | None] = mapped_column(Date)
    serviced_period_end: Mapped[date | None] = mapped_column(Date)
    location_code: Mapped[str | None] = mapped_column(String(10))
    location_display: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[int | None] = mapped_column(Integer)
    unit_price: Mapped[float | None] = mapped_column(Float)
    net_amount: Mapped[float | None] = mapped_column(Float)

    claim = relationship("ClaimSubmission", back_populates="items")


class ClaimDiagnosis(Base):
    """ICD diagnosis linked to a Claim submission."""

    __tablename__ = "claim_diagnosis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claim_submission.id"))
    sequence: Mapped[int | None] = mapped_column(Integer)
    icd_code: Mapped[str | None] = mapped_column(String(15))
    icd_display: Mapped[str | None] = mapped_column(Text)
    diagnosis_type: Mapped[str | None] = mapped_column(String(30))

    claim = relationship("ClaimSubmission", back_populates="claim_diagnoses")


class ClaimCareTeam(Base):
    """Provider role for a Claim submission care team member."""

    __tablename__ = "claim_care_team"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claim_submission.id"))
    sequence: Mapped[int | None] = mapped_column(Integer)
    provider_ref: Mapped[str | None] = mapped_column(Text)
    role_code: Mapped[str | None] = mapped_column(String(20))
    role_display: Mapped[str | None] = mapped_column(Text)

    claim = relationship("ClaimSubmission", back_populates="claim_care_team")


# ── Member-Submitted Claims (paper / PDF intake) ────────────────


class MemberClaim(Base):
    """A claim form the member submitted themselves (paper Medical Claim Form + invoice).

    Patient-submitted, out-of-network, separate from FHIR Claim resources.
    Sources: PDF medical claim forms, provider invoices. Populated manually by member.
    """

    __tablename__ = "member_claim"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Submit info (Section 4 of Medical Claim Form)
    submitter_ref: Mapped[str | None] = mapped_column(String(255))
    submitted_date: Mapped[date | None] = mapped_column(Date)
    # Patient info (Section 1)
    patient_ref: Mapped[str | None] = mapped_column(String(255))
    patient_dob: Mapped[date | None] = mapped_column(Date)
    patient_relationship: Mapped[str | None] = mapped_column(String(50))
    patient_gender: Mapped[str | None] = mapped_column(String(10))
    has_other_insurance: Mapped[bool | None] = mapped_column()
    # Subscriber info (Section 2)
    subscriber_ref: Mapped[str | None] = mapped_column(String(255))
    subscriber_id: Mapped[str | None] = mapped_column(String(50))
    subscriber_group: Mapped[str | None] = mapped_column(String(50))
    subscriber_dob: Mapped[date | None] = mapped_column(Date)
    # Provider info (Section 3)
    provider_ref: Mapped[str | None] = mapped_column(String(255))
    provider_tax_id: Mapped[str | None] = mapped_column(String(20))
    provider_npi: Mapped[str | None] = mapped_column(String(20))
    place_of_service: Mapped[str | None] = mapped_column(String(10))
    job_related: Mapped[bool | None] = mapped_column()
    # Invoice / service info (from provider invoice)
    invoice_number: Mapped[str | None] = mapped_column(String(50))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    date_of_service: Mapped[date | None] = mapped_column(Date)
    referring_provider_ref: Mapped[str | None] = mapped_column(String(255))
    primary_icd_code: Mapped[str | None] = mapped_column(String(15))
    primary_icd_display: Mapped[str | None] = mapped_column(Text)
    invoice_total: Mapped[float | None] = mapped_column(Float)
    payments_credits: Mapped[float | None] = mapped_column(Float)
    balance_due: Mapped[float | None] = mapped_column(Float)
    # Source tracking
    source_pdf: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    items = relationship("MemberClaimItem", back_populates="claim", cascade="all, delete-orphan")


class MemberClaimItem(Base):
    """Line item on a member-submitted claim (one per CPT code)."""

    __tablename__ = "member_claim_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("member_claim.id"))
    cpt_code: Mapped[str | None] = mapped_column(String(10))
    cpt_description: Mapped[str | None] = mapped_column(Text)
    icd_code: Mapped[str | None] = mapped_column(String(15))
    modifier: Mapped[str | None] = mapped_column(String(20))
    units: Mapped[int | None] = mapped_column(Integer)
    amount: Mapped[float | None] = mapped_column(Float)

    claim = relationship("MemberClaim", back_populates="items")


# ── Lab Result Tables (DiagnosticReport + Observation) ──────────


class DiagnosticReport(Base):
    """Lab/imaging report (DiagnosticReport) with normalized fields and raw FHIR JSON."""

    __tablename__ = "diagnostic_report"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # FHIR resource ID
    patient_id: Mapped[str | None] = mapped_column(String(100))
    provider: Mapped[str | None] = mapped_column(String(20))  # e.g. "ucla", "anthem"
    status: Mapped[str | None] = mapped_column(String(20))
    category: Mapped[str | None] = mapped_column(String(100))  # "laboratory", "imaging", etc.
    code_display: Mapped[str | None] = mapped_column(Text)  # Panel/report name
    code_text: Mapped[str | None] = mapped_column(Text)  # Short name from code.text
    code_loinc: Mapped[str | None] = mapped_column(String(20))  # LOINC code
    effective_datetime: Mapped[datetime | None] = mapped_column(DateTime)
    issued: Mapped[datetime | None] = mapped_column(DateTime)
    performer_ref: Mapped[str | None] = mapped_column(String(255))
    conclusion: Mapped[str | None] = mapped_column(Text)  # Report conclusion/findings
    body_site: Mapped[str | None] = mapped_column(Text)  # Body site examined
    method: Mapped[str | None] = mapped_column(Text)  # Method of study (e.g. "Ultrasound")
    encounter_ref: Mapped[str | None] = mapped_column(Text)  # Encounter reference
    specimen_ref: Mapped[str | None] = mapped_column(Text)  # Specimen reference
    has_images: Mapped[bool] = mapped_column()  # True if imagingResults present
    raw_json: Mapped[str | None] = mapped_column(Text)  # Full FHIR JSON
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    results = relationship("LabResult", back_populates="report", cascade="all, delete-orphan")


class LabResult(Base):
    """Individual lab test result (Observation) within a DiagnosticReport panel."""

    __tablename__ = "lab_result"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fhir_id: Mapped[str] = mapped_column(Text)  # FHIR Observation ID (unique for dedup)
    report_id: Mapped[str] = mapped_column(ForeignKey("diagnostic_report.id"))
    code_display: Mapped[str | None] = mapped_column(Text)  # Full test name
    code_text: Mapped[str | None] = mapped_column(Text)  # Short name (e.g. "Glucose")
    code_loinc: Mapped[str | None] = mapped_column(String(20))
    # Value (polymorphic — store as string for display, float+unit for numeric)
    value: Mapped[str | None] = mapped_column(Text)  # Human-readable (e.g. "88 mg/dL")
    value_float: Mapped[float | None] = mapped_column(Float)  # Numeric value
    value_unit: Mapped[str | None] = mapped_column(String(20))
    # Reference range
    reference_range: Mapped[str | None] = mapped_column(Text)  # "135 - 146 mmol/L"
    # Interpretation / flag
    interpretation_code: Mapped[str | None] = mapped_column(String(10))  # "H", "L", "N", "A"
    interpretation_display: Mapped[str | None] = mapped_column(String(30))  # "High", "Low"
    # Timestamps
    effective_datetime: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str | None] = mapped_column(String(20))
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    report = relationship("DiagnosticReport", back_populates="results")


class ImagingObservation(Base):
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
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
