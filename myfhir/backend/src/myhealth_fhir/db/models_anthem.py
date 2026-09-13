"""Anthem database models — insurance data (EOB, claims, members, entities)."""

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


class AnthemBase(DeclarativeBase):
    """SQLAlchemy declarative base for anthem database models."""


# ── OAuth Tokens (replica from auth DB for view joins) ────────────


class OAuthTokenRecord(AnthemBase):
    """Replica of oauth_tokens for anthem-specific view joins."""

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


# ── Patient Identity (replica from auth DB for view joins) ─────────


class PatientRecord(AnthemBase):
    """Replica of patients table for anthem-specific view joins."""

    __tablename__ = "patients"

    patient_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), primary_key=True)
    entity_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)


# ── EOB Tables ────────────────────────────────────────────────────


class EOB(AnthemBase):
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
    claim_received_date: Mapped[date | None] = mapped_column(Date)
    billable_period_start: Mapped[date | None] = mapped_column(Date)
    billable_period_end: Mapped[date | None] = mapped_column(Date)
    patient_ref: Mapped[str | None] = mapped_column(Text)
    provider_ref: Mapped[str | None] = mapped_column(Text)
    insurer_payer_id: Mapped[str | None] = mapped_column(Text)
    coverage_ref: Mapped[str | None] = mapped_column(Text)
    payee_type: Mapped[str | None] = mapped_column(Text)
    payee_ref: Mapped[str | None] = mapped_column(Text)
    submission_origin: Mapped[str] = mapped_column(String(20), default="provider")
    is_out_of_network: Mapped[bool] = mapped_column(Boolean, default=False)
    claim_adjustment_key: Mapped[str | None] = mapped_column(Text)
    payment_amount: Mapped[float | None] = mapped_column(Float)
    payment_date: Mapped[date | None] = mapped_column(Date)
    payment_type: Mapped[str | None] = mapped_column(Text)
    last_updated: Mapped[datetime | None] = mapped_column(DateTime)
    insurer_ref: Mapped[str | None] = mapped_column(Text)
    payment_currency: Mapped[str | None] = mapped_column(String(3))
    payment_adjustment_code: Mapped[str | None] = mapped_column(String(100))
    payment_adjustment_display: Mapped[str | None] = mapped_column(Text)
    preauth_refs: Mapped[str | None] = mapped_column(Text)
    payer_display: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    items = relationship("EOBItem", back_populates="eob", cascade="all, delete-orphan")
    diagnoses = relationship("EOBDiagnosis", back_populates="eob", cascade="all, delete-orphan")
    care_team = relationship("EOBCareTeam", back_populates="eob", cascade="all, delete-orphan")
    totals = relationship("EOBTotal", back_populates="eob", cascade="all, delete-orphan")
    identifiers = relationship("EOBIdentifier", back_populates="eob", cascade="all, delete-orphan")
    adjudications = relationship("EOBAdjudication", back_populates="eob", cascade="all, delete-orphan")
    supporting_infos = relationship("EOBSupportingInfo", back_populates="eob", cascade="all, delete-orphan")
    procedures = relationship("EOBProcedure", back_populates="eob", cascade="all, delete-orphan")


class EOBItem(AnthemBase):
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
    adjudications = relationship("EOBItemAdjudication", back_populates="item", cascade="all, delete-orphan")


class EOBItemAdjudication(AnthemBase):
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


class EOBDiagnosis(AnthemBase):
    __tablename__ = "eob_diagnosis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    eob_id: Mapped[str] = mapped_column(ForeignKey("eob.id"))
    sequence: Mapped[int | None] = mapped_column(Integer)
    icd_code: Mapped[str | None] = mapped_column(String(15))
    icd_display: Mapped[str | None] = mapped_column(Text)
    diagnosis_type: Mapped[str | None] = mapped_column(String(200))
    on_admission: Mapped[str | None] = mapped_column(String(200))

    eob = relationship("EOB", back_populates="diagnoses")


class EOBCareTeam(AnthemBase):
    __tablename__ = "eob_care_team"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    eob_id: Mapped[str] = mapped_column(ForeignKey("eob.id"))
    sequence: Mapped[int | None] = mapped_column(Integer)
    provider_ref: Mapped[str | None] = mapped_column(Text)
    role_code: Mapped[str | None] = mapped_column(String(20))
    role_display: Mapped[str | None] = mapped_column(Text)

    eob = relationship("EOB", back_populates="care_team")


class EOBTotal(AnthemBase):
    __tablename__ = "eob_total"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    eob_id: Mapped[str] = mapped_column(ForeignKey("eob.id"))
    category: Mapped[str | None] = mapped_column(Text)
    category_code: Mapped[str | None] = mapped_column(String(20))
    amount: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(3))

    eob = relationship("EOB", back_populates="totals")


class EOBIdentifier(AnthemBase):
    """EOB-level FHIR identifier[] entry (claim number / adjustment key)."""

    __tablename__ = "eob_identifier"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    eob_id: Mapped[str] = mapped_column(ForeignKey("eob.id"))
    seq: Mapped[int | None] = mapped_column(Integer)
    system: Mapped[str | None] = mapped_column(Text)
    value: Mapped[str | None] = mapped_column(Text)
    use: Mapped[str | None] = mapped_column(String(20))
    type_code: Mapped[str | None] = mapped_column(String(100))
    type_display: Mapped[str | None] = mapped_column(Text)

    eob = relationship("EOB", back_populates="identifiers")


class EOBAdjudication(AnthemBase):
    """EOB-level (header) adjudication entry."""

    __tablename__ = "eob_adjudication"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    eob_id: Mapped[str] = mapped_column(ForeignKey("eob.id"))
    seq: Mapped[int | None] = mapped_column(Integer)
    category_code: Mapped[str | None] = mapped_column(String(100))
    category_display: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(3))
    value_units: Mapped[int | None] = mapped_column(Integer)
    reason_code: Mapped[str | None] = mapped_column(String(100))
    reason_display: Mapped[str | None] = mapped_column(Text)

    eob = relationship("EOB", back_populates="adjudications")


class EOBSupportingInfo(AnthemBase):
    """EOB-level supportingInformation[] entry (e.g. billing period)."""

    __tablename__ = "eob_supporting_info"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    eob_id: Mapped[str] = mapped_column(ForeignKey("eob.id"))
    seq: Mapped[int | None] = mapped_column(Integer)
    category_code: Mapped[str | None] = mapped_column(String(100))
    category_display: Mapped[str | None] = mapped_column(Text)
    code_code: Mapped[str | None] = mapped_column(String(100))
    code_display: Mapped[str | None] = mapped_column(Text)
    timing_date: Mapped[date | None] = mapped_column(Date)
    timing_period_start: Mapped[date | None] = mapped_column(Date)
    timing_period_end: Mapped[date | None] = mapped_column(Date)
    value_string: Mapped[str | None] = mapped_column(Text)

    eob = relationship("EOB", back_populates="supporting_infos")


class EOBProcedure(AnthemBase):
    """EOB-level procedure[] entry (procedure code + type per line)."""

    __tablename__ = "eob_procedure"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    eob_id: Mapped[str] = mapped_column(ForeignKey("eob.id"))
    seq: Mapped[int | None] = mapped_column(Integer)
    service_sequence: Mapped[int | None] = mapped_column(Integer)
    service_date: Mapped[date | None] = mapped_column(Date)
    code_code: Mapped[str | None] = mapped_column(String(100))
    code_display: Mapped[str | None] = mapped_column(Text)
    type_code: Mapped[str | None] = mapped_column(String(100))
    type_display: Mapped[str | None] = mapped_column(Text)

    eob = relationship("EOB", back_populates="procedures")


# ── Entity Name Lookup ────────────────────────────────────────────


class EntityName(AnthemBase):
    __tablename__ = "entity_names"

    entity_ref: Mapped[str] = mapped_column(String(255), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    npi: Mapped[str | None] = mapped_column(String(20))
    display: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ── Claim Submission Tables ──────────────────────────────────────


class ClaimSubmission(AnthemBase):
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
    submission_origin: Mapped[str] = mapped_column(String(20), default="provider")
    is_out_of_network: Mapped[bool] = mapped_column(Boolean, default=False)
    last_updated: Mapped[datetime | None] = mapped_column(DateTime)
    claim_number: Mapped[str | None] = mapped_column(Text)
    claim_adjustment_key: Mapped[str | None] = mapped_column(Text)
    sub_type_code: Mapped[str | None] = mapped_column(String(100))
    sub_type_display: Mapped[str | None] = mapped_column(Text)
    payee_type: Mapped[str | None] = mapped_column(String(50))
    payee_ref: Mapped[str | None] = mapped_column(Text)
    coverage_ref: Mapped[str | None] = mapped_column(Text)
    preauth_refs: Mapped[str | None] = mapped_column(Text)
    prescription_ref: Mapped[str | None] = mapped_column(Text)
    priority_display: Mapped[str | None] = mapped_column(Text)
    adjudication_date: Mapped[date | None] = mapped_column(Date)
    adjudication_status_code: Mapped[str | None] = mapped_column(String(50))
    action_date: Mapped[date | None] = mapped_column(Date)
    action_type_code: Mapped[str | None] = mapped_column(String(50))
    adjustment_number: Mapped[str | None] = mapped_column(Text)
    claim_class_code: Mapped[str | None] = mapped_column(String(50))
    denial_reason_code: Mapped[str | None] = mapped_column(String(50))
    line_status_code: Mapped[str | None] = mapped_column(String(50))
    line_status_display: Mapped[str | None] = mapped_column(Text)
    paid_date: Mapped[date | None] = mapped_column(Date)
    system_of_record_code: Mapped[str | None] = mapped_column(String(50))
    discharge_status_code: Mapped[str | None] = mapped_column(String(50))
    document_control_number: Mapped[str | None] = mapped_column(Text)
    external_load_code: Mapped[str | None] = mapped_column(String(50))
    in_patient: Mapped[str | None] = mapped_column(String(10))
    length_of_stay: Mapped[int | None] = mapped_column(Integer)
    network_identifier_code: Mapped[str | None] = mapped_column(String(50))
    place_of_service_code: Mapped[str | None] = mapped_column(String(50))
    place_of_service_display: Mapped[str | None] = mapped_column(Text)
    pps_code: Mapped[str | None] = mapped_column(String(50))
    source_billing_provider_id: Mapped[str | None] = mapped_column(Text)
    source_npi: Mapped[str | None] = mapped_column(String(20))
    total_diag_code_count: Mapped[int | None] = mapped_column(Integer)
    total_paid_amount: Mapped[float | None] = mapped_column(Float)
    master_consumer_id: Mapped[str | None] = mapped_column(Text)
    mbr_key: Mapped[str | None] = mapped_column(Text)
    ipt_facility_number: Mapped[str | None] = mapped_column(Text)
    ipt_home_code: Mapped[str | None] = mapped_column(String(50))
    point_of_origin_code: Mapped[str | None] = mapped_column(String(50))
    priority_admit_type_code: Mapped[str | None] = mapped_column(String(50))
    dispensed_brand_generic_code: Mapped[str | None] = mapped_column(String(50))
    raw_json: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    items = relationship("ClaimItem", back_populates="claim", cascade="all, delete-orphan")
    claim_diagnoses = relationship("ClaimDiagnosis", back_populates="claim", cascade="all, delete-orphan")
    claim_care_team = relationship("ClaimCareTeam", back_populates="claim", cascade="all, delete-orphan")
    identifiers = relationship("ClaimIdentifier", back_populates="claim", cascade="all, delete-orphan")


class ClaimItem(AnthemBase):
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


class MemberClaimSubmission(AnthemBase):
    """Manual registry of member-submitted (paper/portal) claims.

    Portal submission IDs (e.g. ``3f6dd0c598ab80928``) do not exist in FHIR;
    they are registered here manually and auto-matched to EOBs/Claims on fetch.
    """

    __tablename__ = "member_claim_submission"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portal_submission_id: Mapped[str | None] = mapped_column(String(200))
    patient_id: Mapped[str | None] = mapped_column(String(200))
    claim_number: Mapped[str | None] = mapped_column(Text)
    provider_ref: Mapped[str | None] = mapped_column(String(255))
    provider_npi: Mapped[str | None] = mapped_column(String(20))
    service_date: Mapped[date | None] = mapped_column(Date)
    cpt_codes: Mapped[str | None] = mapped_column(Text)
    total_amount: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="registered")
    matched_eob_id: Mapped[str | None] = mapped_column(Text)
    matched_claim_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ClaimDiagnosis(AnthemBase):
    __tablename__ = "claim_diagnosis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claim_submission.id"))
    sequence: Mapped[int | None] = mapped_column(Integer)
    icd_code: Mapped[str | None] = mapped_column(String(15))
    icd_display: Mapped[str | None] = mapped_column(Text)
    diagnosis_type: Mapped[str | None] = mapped_column(String(200))

    claim = relationship("ClaimSubmission", back_populates="claim_diagnoses")


class ClaimCareTeam(AnthemBase):
    __tablename__ = "claim_care_team"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claim_submission.id"))
    sequence: Mapped[int | None] = mapped_column(Integer)
    provider_ref: Mapped[str | None] = mapped_column(Text)
    role_code: Mapped[str | None] = mapped_column(String(20))
    role_display: Mapped[str | None] = mapped_column(Text)

    claim = relationship("ClaimSubmission", back_populates="claim_care_team")


class ClaimIdentifier(AnthemBase):
    """Claim-level FHIR identifier[] entry (clm_nbr / claimAdjustmentKey)."""

    __tablename__ = "claim_identifier"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claim_submission.id"))
    seq: Mapped[int | None] = mapped_column(Integer)
    system: Mapped[str | None] = mapped_column(Text)
    value: Mapped[str | None] = mapped_column(Text)
    use: Mapped[str | None] = mapped_column(String(20))
    type_code: Mapped[str | None] = mapped_column(String(100))
    type_display: Mapped[str | None] = mapped_column(Text)

    claim = relationship("ClaimSubmission", back_populates="identifiers")



