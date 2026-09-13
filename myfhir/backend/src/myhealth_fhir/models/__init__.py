"""All ORM models, re-exported by domain.

- ``myhealth_fhir.models.auth``    — auth DB (OAuth tokens, PKCE, job runs)
- ``myhealth_fhir.models.anthem``  — myhealth_anthem (EOB, claims, members)
- ``myhealth_fhir.models.ucla``    — myhealth_ucla (clinical data)

The ``EntityName`` / ``OAuthTokenRecord`` / ``PatientRecord`` classes exist in
all three databases under the same name; they are exported here ONLY in
aliased form (``AuthEntityName``, ``AnthemOAuthTokenRecord``, ...) — import
the bare names from the qualified domain module instead.
"""

from myhealth_fhir.models.anthem import (
    EOB,
    AnthemBase,
    ClaimCareTeam,
    ClaimDiagnosis,
    ClaimIdentifier,
    ClaimItem,
    ClaimSubmission,
    EOBAdjudication,
    EOBCareTeam,
    EOBDiagnosis,
    EOBIdentifier,
    EOBItem,
    EOBItemAdjudication,
    EOBProcedure,
    EOBSupportingInfo,
    EOBTotal,
    MemberClaimSubmission,
)
from myhealth_fhir.models.anthem import (
    EntityName as AnthemEntityName,
)
from myhealth_fhir.models.anthem import (
    OAuthTokenRecord as AnthemOAuthTokenRecord,
)
from myhealth_fhir.models.anthem import (
    PatientRecord as AnthemPatientRecord,
)
from myhealth_fhir.models.auth import (
    AuthBase,
    JobRun,
    PKCEVerifier,
)
from myhealth_fhir.models.auth import (
    EntityName as AuthEntityName,
)
from myhealth_fhir.models.auth import (
    OAuthTokenRecord as AuthOAuthTokenRecord,
)
from myhealth_fhir.models.auth import (
    PatientRecord as AuthPatientRecord,
)
from myhealth_fhir.models.ucla import (
    AllergyIntolerance,
    CarePlan,
    CareTeam,
    CareTeamParticipant,
    ClinicalNote,
    ClinicalNoteIdentifier,
    ClinicalObservation,
    ClinicalObservationComponent,
    Communication,
    Condition,
    DiagnosticReport,
    DiagnosticReportIdentifier,
    DocumentReference,
    DocumentReferenceContent,
    DocumentReferenceIdentifier,
    Encounter,
    EncounterIdentifier,
    EncounterParticipant,
    FamilyMemberHistory,
    ImagingObservation,
    Immunization,
    ImmunizationIdentifier,
    LabResult,
    LabResultComponent,
    MedicationAdministration,
    MedicationRequest,
    MedicationRequestDosage,
    MedicationRequestIdentifier,
    MedicationStatement,
    ProcedureRecord,
    ServiceRequest,
    Specimen,
    UclaBase,
)
from myhealth_fhir.models.ucla import (
    EntityName as UclaEntityName,
)
from myhealth_fhir.models.ucla import (
    OAuthTokenRecord as UclaOAuthTokenRecord,
)
from myhealth_fhir.models.ucla import (
    PatientRecord as UclaPatientRecord,
)

__all__ = [
    "AllergyIntolerance",
    "AnthemBase",
    "AnthemEntityName",
    "AnthemOAuthTokenRecord",
    "AnthemPatientRecord",
    "AuthBase",
    "AuthEntityName",
    "AuthOAuthTokenRecord",
    "AuthPatientRecord",
    "CarePlan",
    "CareTeam",
    "CareTeamParticipant",
    "ClaimCareTeam",
    "ClaimDiagnosis",
    "ClaimIdentifier",
    "ClaimItem",
    "ClaimSubmission",
    "ClinicalNote",
    "ClinicalNoteIdentifier",
    "ClinicalObservation",
    "ClinicalObservationComponent",
    "Communication",
    "Condition",
    "DiagnosticReport",
    "DiagnosticReportIdentifier",
    "DocumentReference",
    "DocumentReferenceContent",
    "DocumentReferenceIdentifier",
    "EOB",
    "EOBAdjudication",
    "EOBCareTeam",
    "EOBDiagnosis",
    "EOBIdentifier",
    "EOBItem",
    "EOBItemAdjudication",
    "EOBProcedure",
    "EOBSupportingInfo",
    "EOBTotal",
    "Encounter",
    "EncounterIdentifier",
    "EncounterParticipant",
    "FamilyMemberHistory",
    "ImagingObservation",
    "Immunization",
    "ImmunizationIdentifier",
    "JobRun",
    "LabResult",
    "LabResultComponent",
    "MedicationAdministration",
    "MedicationRequest",
    "MedicationRequestDosage",
    "MedicationRequestIdentifier",
    "MedicationStatement",
    "MemberClaimSubmission",
    "PKCEVerifier",
    "ProcedureRecord",
    "ServiceRequest",
    "Specimen",
    "UclaBase",
    "UclaEntityName",
    "UclaOAuthTokenRecord",
    "UclaPatientRecord",
]
