# Epic OAuth2 Auto-Download Setup Guide

Complete reference for registering a patient-facing app on [Epic's Open EHR Developer Portal](https://open.epic.com/)
that automatically distributes to all qualifying Epic healthcare systems (including UCLA, Kaiser, Mayo Clinic, etc.)
**without manual provider approval**.

## The Magic Setting: Automatic Client Distribution

The single most important field is **Automatic Client Distribution**. If you set this correctly, Epic will automatically
push your client ID to every healthcare system that supports your chosen distribution standard. You'll never need to
manually contact providers to add your client to their allowlist.

| Option                     | What It Does                                                        | Auto-Push?         |
|----------------------------|---------------------------------------------------------------------|--------------------|
| **None**                   | No automatic distribution. You must manually contact each provider. | ❌                 |
| **USCDI v1**               | Push to systems supporting the 2020 US CDH Implementation Guide     | Limited            |
| **USCDI v3**               | Push to systems supporting the 2024 US CDH Implementation Guide     | ✅ **Best choice** |
| **CMS Patient Access API** | Push to systems registered for the CMS Patient Access API program   | ✅                 |

**Select `USCDI v3`** for maximum automatic distribution. When marked "Ready for production," Epic displays: *"This app
will be automatically downloaded to all qualifying customers."*

## Complete Registration Checklist

Below are the **exact field values** that enable auto-download to providers like UCLA, Kaiser, etc. Every field matters.

### 1. Application Basics

| Field                        | Value                                    | Notes                                                                                            |
|------------------------------|------------------------------------------|--------------------------------------------------------------------------------------------------|
| **Application Name**         | Your app name (e.g., "myhealth2")        |                                                                                                  |
| **Application Audience**     | `Patients`                               | **Critical** — selecting "Clinicians" or "Backend Systems" changes the entire distribution model |
| **Public Documentation URL** | `https://your-domain.example` or your docs URL | Should be publicly accessible                                                                    |

### 2. Automatic Client Distribution ⭐

| Field                             | Value      | Why                                                                                                         |
|-----------------------------------|------------|-------------------------------------------------------------------------------------------------------------|
| **Automatic Client Distribution** | `USCDI v3` | This is THE field that makes auto-download work. Without it, you must manually reach out to every provider. |

### 3. Endpoint URI

| Field            | Value                            | Notes                                                       |
|------------------|----------------------------------|-------------------------------------------------------------|
| **Endpoint URI** | `https://your-domain.example/callback` | Your OAuth2 redirect URI. Must be publicly reachable HTTPS. |

### 4. Client Settings

| Field                                       | Value      | Why                                            |
|---------------------------------------------|------------|------------------------------------------------|
| **Is this app a confidential client?**      | ✅ **Yes** | Required for PKCE + client secret auth flow    |
| **Will this app register dynamic clients?** | ❌ No      | Not needed for our use case                    |
| **Does the app require persistent access?** | ❌ No      | Leave unchecked unless you need >1 hour tokens |

### 5. JWK Set URLs

| Field                          | Value                   | Notes               |
|--------------------------------|-------------------------|---------------------|
| **Non-Production JWK Set URL** | `https://fhir.epic.com` | For sandbox testing |
| **Production JWK Set URL**     | `https://fhir.epic.com` | For production      |

### 6. SMART/FHIR Configuration

| Field                         | Value                        | Why                                                                                                                                                                               |
|-------------------------------|------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **SMART on FHIR Version**     | `R4`                         | Latest stable FHIR version                                                                                                                                                        |
| **SMART Scope Version**       | `SMART v1`                   | Standard SMART scopes                                                                                                                                                             |
| **FHIR ID Generation Scheme** | `Use Unconstrained FHIR IDs` | **Critical** — the yellow warning says some systems may not support your scheme, but Epic will auto-switch to unconstrained for those systems. This gives you full compatibility. |

### 7. Intended Users

| Field              | Value                  | Why                         |
|--------------------|------------------------|-----------------------------|
| **Intended Users** | `Individual/Caregiver` | Matches "Patients" audience |

### 8. Summary & Description

| Field           | Value                                                                                                                                                                                                                                                                                             |
|-----------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Summary**     | "Personal health data viewer for patients to access their own medical records via FHIR APIs."                                                                                                                                                                                                     |
| **Description** | "A patient-facing application that allows individuals to securely access their own health records from Epic-based healthcare organizations. Supports reading lab results, medications, conditions, allergies, immunizations, encounters, and care plans using SMART on FHIR OAuth 2.0 with PKCE." |

### 9. Incoming APIs ⭐⭐⭐

**This is the most critical section.** You must select **every scope you might ever need**. Epic provides 162 R4 scopes
organized by resource type. The easiest method is to select **all Read and Search scopes for R4** across all resource
types.

Below are all 162 scopes that were selected, grouped by resource type:

#### AllergyIntolerance (4)

- `AllergyIntolerance.Read (Outside Record) (R4)`
- `AllergyIntolerance.Read (Patient Chart) (R4)`
- `AllergyIntolerance.Search (Outside Record) (R4)`
- `AllergyIntolerance.Search (Patient Chart) (R4)`

#### Binary (10)

- `Binary.Read (Clinical Notes) (R4)`
- `Binary.Read (Generated CDAs) (R4)`
- `Binary.Read (Labs) (R4)`
- `Binary.Read (Outside Record - Clinical Notes) (R4)`
- `Binary.Read (Study) (R4)`
- `Binary.Search (Clinical Notes) (R4)`
- `Binary.Search (Generated CDAs) (R4)`
- `Binary.Search (Labs) (R4)`
- `Binary.Search (Outside Record - Clinical Notes) (R4)`
- `Binary.Search (Study) (R4)`

#### CarePlan (6)

- `CarePlan.Read (Encounter) (R4)`
- `CarePlan.Read (Longitudinal) (R4)`
- `CarePlan.Read (Outside Record) (R4)`
- `CarePlan.Search (Encounter) (R4)`
- `CarePlan.Search (Longitudinal) (R4)`
- `CarePlan.Search (Outside Record) (R4)`

#### CareTeam (4)

- `CareTeam.Read (Longitudinal CareTeam) (R4)`
- `CareTeam.Read (Outside Record) (R4)`
- `CareTeam.Search (Longitudinal CareTeam) (R4)`
- `CareTeam.Search (Outside Record) (R4)`

#### Condition (14)

- `Condition.Read (Care Plan Problem) (R4)`
- `Condition.Read (Encounter Diagnosis) (R4)`
- `Condition.Read (Health Concerns) (R4)`
- `Condition.Read (Outside Record Encounter Diagnosis) (R4)`
- `Condition.Read (Outside Record Health Concern) (R4)`
- `Condition.Read (Outside Record Problems) (R4)`
- `Condition.Read (Problems) (R4)`
- `Condition.Search (Care Plan Problem) (R4)`
- `Condition.Search (Encounter Diagnosis) (R4)`
- `Condition.Search (Health Concerns) (R4)`
- `Condition.Search (Outside Record Encounter Diagnosis) (R4)`
- `Condition.Search (Outside Record Health Concern) (R4)`
- `Condition.Search (Outside Record Problems) (R4)`
- `Condition.Search (Problems) (R4)`

#### Coverage (4)

- `Coverage.Read (Outside Record) (R4)`
- `Coverage.Read (Patient Insurance Information) (R4)`
- `Coverage.Search (Outside Record) (R4)`
- `Coverage.Search (Patient Insurance Information) (R4)`

#### Device (4)

- `Device.Read (Implants) (R4)`
- `Device.Read (Outside Record) (R4)`
- `Device.Search (Implants) (R4)`
- `Device.Search (Outside Record) (R4)`

#### DiagnosticReport (4)

- `DiagnosticReport.Read (Outside Record Results) (R4)`
- `DiagnosticReport.Read (Results) (R4)`
- `DiagnosticReport.Search (Outside Record Results) (R4)`
- `DiagnosticReport.Search (Results) (R4)`

#### DocumentReference (8)

- `DocumentReference.Read (Clinical Notes) (R4)`
- `DocumentReference.Read (Generated CDAs) (R4)`
- `DocumentReference.Read (Labs) (R4)`
- `DocumentReference.Read (Outside Record - Clinical Notes) (R4)`
- `DocumentReference.Search (Clinical Notes) (R4)`
- `DocumentReference.Search (Generated CDAs) (R4)`
- `DocumentReference.Search (Labs) (R4)`
- `DocumentReference.Search (Outside Record - Clinical Notes) (R4)`

#### Encounter (4)

- `Encounter.Read (Outside Record) (R4)`
- `Encounter.Read (Patient Chart) (R4)`
- `Encounter.Search (Outside Record) (R4)`
- `Encounter.Search (Patient Chart) (R4)`

#### Goal (6)

- `Goal.Read (Care Plan Goal) (R4)`
- `Goal.Read (Outside Record) (R4)`
- `Goal.Read (Patient) (R4)`
- `Goal.Search (Care Plan Goal) (R4)`
- `Goal.Search (Outside Record) (R4)`
- `Goal.Search (Patient) (R4)`

#### Immunization (4)

- `Immunization.Read (Outside Record) (R4)`
- `Immunization.Read (Patient Chart) (R4)`
- `Immunization.Search (Outside Record) (R4)`
- `Immunization.Search (Patient Chart) (R4)`

#### Location (4)

- `Location.Read (Organizational Directory) (R4)`
- `Location.Read (Outside Record) (R4)`
- `Location.Search (Organizational Directory) (R4)`
- `Location.Search (Outside Record) (R4)`

#### Media (2)

- `Media.Read (Study) (R4)`
- `Media.Search (Study) (R4)`

#### Medication (4)

- `Medication.Read (Organization Med List) (R4)`
- `Medication.Read (Outside Record) (R4)`
- `Medication.Search (Organization Med List) (R4)`
- `Medication.Search (Outside Record) (R4)`

#### MedicationDispense (4)

- `MedicationDispense.Read (Fill Status) (R4)`
- `MedicationDispense.Read (Outside Record) (R4)`
- `MedicationDispense.Search (Fill Status) (R4)`
- `MedicationDispense.Search (Outside Record) (R4)`

#### MedicationRequest (4)

- `MedicationRequest.Read (Outside Record) (R4)`
- `MedicationRequest.Read (Signed Medication Order) (R4)`
- `MedicationRequest.Search (Outside Record) (R4)`
- `MedicationRequest.Search (Signed Medication Order) (R4)`

#### Observation (31)

- `Observation.Read (Assessments) (R4)`
- `Observation.Read (Labs) (R4)`
- `Observation.Read (Outside Record Activities of Daily Living) (R4)`
- `Observation.Read (Outside Record Occupation) (R4)`
- `Observation.Read (Outside Record Pregnancy Status) (R4)`
- `Observation.Read (Outside Record SDOH Assessment) (R4)`
- `Observation.Read (Outside Record Screening Assessment) (R4)`
- `Observation.Read (Outside Record Sexual Orientation) (R4)`
- `Observation.Read (Outside Record Smoking Status) (R4)`
- `Observation.Read (Outside Record Vital Signs) (R4)`
- `Observation.Read (SDOH Assessments) (R4)`
- `Observation.Read (SmartData Elements) (R4)`
- `Observation.Read (Social History) (R4)`
- `Observation.Read (Study Finding) (R4)`
- `Observation.Read (Vital Signs) (R4)`
- `Observation.Search (Assessments) (R4)`
- `Observation.Search (Labs) (R4)`
- `Observation.Search (Outside Record Activities of Daily Living) (R4)`
- `Observation.Search (Outside Record Occupation) (R4)`
- `Observation.Search (Outside Record Pregnancy Status) (R4)`
- `Observation.Search (Outside Record Results) (R4)`
- `Observation.Search (Outside Record SDOH Assessment) (R4)`
- `Observation.Search (Outside Record Screening Assessment) (R4)`
- `Observation.Search (Outside Record Sexual Orientation) (R4)`
- `Observation.Search (Outside Record Smoking Status) (R4)`
- `Observation.Search (Outside Record Vital Signs) (R4)`
- `Observation.Search (SDOH Assessments) (R4)`
- `Observation.Search (SmartData Elements) (R4)`
- `Observation.Search (Social History) (R4)`
- `Observation.Search (Study Finding) (R4)`
- `Observation.Search (Vital Signs) (R4)`

#### Organization (4)

- `Organization.Read (Organizational Directory) (R4)`
- `Organization.Read (Outside Record) (R4)`
- `Organization.Search (Organizational Directory) (R4)`
- `Organization.Search (Outside Record) (R4)`

#### Patient (2)

- `Patient.Read (Demographics) (R4)`
- `Patient.Search (Demographics) (R4)`

#### Practitioner (4)

- `Practitioner.Read (Organizational Directory) (R4)`
- `Practitioner.Read (Outside Record) (R4)`
- `Practitioner.Search (Organizational Directory) (R4)`
- `Practitioner.Search (Outside Record) (R4)`

#### PractitionerRole (4)

- `PractitionerRole.Read (Organizational Directory) (R4)`
- `PractitionerRole.Read (Outside Record) (R4)`
- `PractitionerRole.Search (Organizational Directory) (R4)`
- `PractitionerRole.Search (Outside Record) (R4)`

#### Procedure (8)

- `Procedure.Read (Orders) (R4)`
- `Procedure.Read (Outside Record) (R4)`
- `Procedure.Read (SDOH Intervention) (R4)`
- `Procedure.Read (Surgeries) (R4)`
- `Procedure.Search (Orders) (R4)`
- `Procedure.Search (Outside Record) (R4)`
- `Procedure.Search (SDOH Intervention) (R4)`
- `Procedure.Search (Surgeries) (R4)`

#### Provenance (1)

- `Provenance.Read (R4)`

#### QuestionnaireResponse (2)

- `QuestionnaireResponse.Read (Outside Record) (R4)`
- `QuestionnaireResponse.Search (Outside Record) (R4)`

#### RelatedPerson (6)

- `RelatedPerson.Read (Friends and Family) (R4)`
- `RelatedPerson.Read (Outside Record) (R4)`
- `RelatedPerson.Read (Proxy) (R4)`
- `RelatedPerson.Search (Friends and Family) (R4)`
- `RelatedPerson.Search (Outside Record) (R4)`
- `RelatedPerson.Search (Proxy) (R4)`

#### ServiceRequest (6)

- `ServiceRequest.Read (Community Resource ServiceRequest) (R4)`
- `ServiceRequest.Read (Orders) (R4)`
- `ServiceRequest.Read (Outside Record) (R4)`
- `ServiceRequest.Search (Community Resource ServiceRequest) (R4)`
- `ServiceRequest.Search (Orders) (R4)`
- `ServiceRequest.Search (Outside Record) (R4)`

#### Specimen (4)

- `Specimen.Read (Outside Record) (R4)`
- `Specimen.Read (Patient Chart) (R4)`
- `Specimen.Search (Outside Record) (R4)`
- `Specimen.Search (Patient Chart) (R4)`

---

**Total: 162 scopes across 28 resource types**

## OAuth2 Flow (Patient-Facing App)

Once registered, the OAuth2 flow for each provider is:

1. **User opens** your app's authorize URL:
   ```
   https://<provider>.com/oauth2/authorize?
     client_id=YOUR_CLIENT_ID&
     scope=openid%20fhirUser%20offline_access%20patient/%2A.read&
     response_type=code&
     redirect_uri=https://your-domain.example/callback&
     code_challenge_method=S256&
     code_challenge=YOUR_PKCE_CHALLENGE&
     aud=https://<provider>.com/fhir/r4
   ```

2. **Patient logs in** to their provider's MyChart/portal and consents

3. **Provider redirects** to `https://your-domain.example/callback?code=XXX&scope=YYY`

4. **Your app exchanges** the code for tokens at the provider's token endpoint

5. **Your app queries** the provider's FHIR R4 endpoint with the access token

## Provider-Specific Endpoints

Each provider has unique URLs. The auto-download setting ensures your client ID is pre-approved, but you still need to
discover the correct endpoints:

| Provider         | Auth Base URL                                                    | FHIR Base URL                                               | Token URL                                                    |
|------------------|------------------------------------------------------------------|-------------------------------------------------------------|--------------------------------------------------------------|
| **UCLA**         | `https://arrprox.mednet.ucla.edu/FHIRPRD/oauth2/authorize`       | `https://arrprox.mednet.ucla.edu/FHIRPRD/api/FHIR/R4`       | `https://arrprox.mednet.ucla.edu/FHIRPRD/oauth2/token`       |
| **Epic Sandbox** | `https://fhir.epic.com/interconnect-fhir-oauth/oauth2/authorize` | `https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4` | `https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token` |

For other providers, find their FHIR base URL
at [open.epic.com/MyApps/Endpoints](https://open.epic.com/MyApps/Endpoints), then derive the auth/token URLs from the
capability statement.

## Common Pitfalls

| Pitfall                        | Symptom                         | Fix                                                                                                     |
|--------------------------------|---------------------------------|---------------------------------------------------------------------------------------------------------|
| **Wrong distribution setting** | Client ID not found at provider | Must be `USCDI v3` — `None` requires manual provider approval                                           |
| **Wrong audience**             | OAuth returns invalid_client    | Must be `Patients` for patient data access                                                              |
| **Missing scopes**             | FHIR search returns 403         | Re-register and add missing scopes. Epic won't let you add scopes post-registration without re-approval |
| **FHIR ID scheme**             | Some providers can't parse IDs  | Use `Use Unconstrained FHIR IDs`                                                                        |
| **Not confidential**           | PKCE + client secret fails      | Check `Is this app a confidential client?`                                                              |
| **HTTPS redirect**             | OAuth fails to redirect         | Redirect URI must be HTTPS and publicly reachable                                                       |
| **SMART v1 vs v2**             | Scopes don't match              | Use `SMART v1` for `patient/Resource.read` format                                                       |

## After Registration: Production Steps

1. **Click "Save"** — your app moves to "Tested" stage
2. **Generate sandbox client secret** — copy to your dev environment
3. **Test against Epic sandbox** — use non-production client ID
4. **Mark as "Ready for production"** — triggers auto-download to qualifying Epic customers
5. **Generate production client secret** — copy to production environment
6. **Wait** — Epic automatically distributes your client ID to all USCDI v3-supporting providers (typically within 24-72
   hours)

## Known FHIR Limitations by Provider

- **ImagingStudy**: Not included in USCDI v3. Most providers (including UCLA) return 403 for ImagingStudy even with all
  scopes selected. Requires `patient/ImagingStudy.read` scope which is not auto-granted.
- **Binary/DICOM**: The `Binary.Read (Study)` scope gives you Binary metadata, but actual DICOM pixel data requires
  WADO-RS endpoints that most patient portals don't expose.
- **DiagnosticReport narrative text**: Epic FHIR returns minimal DiagnosticReport data (no conclusion text, no bodySite,
  no method). The narrative findings exist in the patient portal but not via FHIR.
- **Media**: Available via scope but typically requires `_id` search (can't search without knowing the resource ID).

## References

- [Epic OAuth2 Tutorial](https://fhir.epic.com/Documentation?docId=oauth2tutorial)
- [open.epic.com Developer Portal](https://open.epic.com/)
- [Epic Community Member Endpoints](https://open.epic.com/MyApps/Endpoints)
- [USCDI v3 Implementation Guide](https://confluence.hl7.org/display/USCDIv3)
- [Full extracted scope list (HTML)](./scopes-for-auto-download.html)
- [Registration screenshot reference](correct-setup.png)
