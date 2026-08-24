# Anthem TotalView HealthOS - FHIR APIs

## Overview

This document describes the details needed to access and connect **Patient Access Approver Direct API** and **Developer Portal** endpoints via the FHIR standard.

- **Document Version:** November Status — Active
- **Scope:** Provides the details needed to access and connect the Patient Access Approver, Direct API, Developer Portal endpoints per the FHIR standard.
- **Data Element Standard (DES):** Elevance Enterprise Data Standard (EDS)
- **Purpose:** Interoperability Endpoints documentation

### Patient Access

Patient data with full FHIR specification:

- US Core R4 Component
- CARIN BCMS v1 Certified
- C-CDA Direct
- Pdex Data Exchange
- CARIN BB Static
- SMART on FHIR App Launch workflow
- CDS Hooks Static
- Questionnaire and Token Notification per the patient

Documentation can be found here: [FHIR Documentation](https://totalview.healthos.elevancehealth.com/fhir/documentation)

> **Note:** No authorization data required. Sandbox with additional test functionality and aesthetic data is available for application testing (sandbox).

---

## Table of Contents

1. [Authorization Flow](#authorization-flow)
2. [FHIR Base URLs](#fhir-base-urls)
3. [SMART on FHIR](#smart-on-fhir)
4. [API Endpoints](#api-endpoints)
5. [Patient Resources](#patient-resources)
6. [Unregistered Endpoints](#unregistered-endpoints)
7. [CMS Mandate Data](#cms-mandate-data)

---

## Authorization Flow

OAuth2 Authorization Code flow (SMART on FHIR STU2.2):

### Authorization Endpoint (Registered APIs)
```
https://totalview.healthos.elevancehealth.com/oauth2.code/registered/api/v1/authorize
```

### Token Endpoint (Registered APIs)
```
https://totalview.healthos.elevancehealth.com/client.oauth2/registered/api/v1/token
```

### Registration
- OAuth client registration: https://fhir.careevolution.com/Master.Adapter1.WebClient/oauthclients
- Sandbox registration: https://sbx.totalview.healthos.elevancehealth.com/registration/sandbox/login

### OAuth Parameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| `client_id` | Your registered client ID | Required |
| `client_secret` | Your registered client secret | Required for token exchange |
| `redirect_uri` | Your redirect URI | Must match registered URI |
| `response_type` | `code` | Authorization code flow |
| `scope` | SMART on FHIR scopes | See below for valid scopes |

---

## FHIR Base URLs

### Registered API Endpoints by Plan/Law

| Plan / Brand | FHIR Base URL |
|---|---|
| Amerigroup | `https://totalview.healthos.elevancehealth.com/resources/registered/Amerigroup/api/v1/fhir` |
| Anthem Blue Cross | `https://totalview.healthos.elevancehealth.com/resources/registered/AnthemBlueCross/api/v1/fhir` |
| Anthem Blue Cross Blue Shield | `https://totalview.healthos.elevancehealth.com/resources/registered/AnthemBlueCrossBlueShield/api/v1/fhir` |
| Blue Medicare Advantage | `https://totalview.healthos.elevancehealth.com/resources/registered/BlueMedicareAdvantage/api/v1/fhir` |
| Clear Health Alliance | `https://totalview.healthos.elevancehealth.com/resources/registered/ClearHealthAlliance/api/v1/fhir` |
| Dell Children Health Plan | `https://totalview.healthos.elevancehealth.com/resources/registered/DellChildrenHealthPlan/api/v1/fhir` |
| Healthy Blue | `https://totalview.healthos.elevancehealth.com/resources/registered/HealthyBlue/api/v1/fhir` |
| Healthy Blue Blue Choice | `https://totalview.healthos.elevancehealth.com/resources/registered/HealthyBlueBlueChoice/api/v1/fhir` |
| Healthy Blue NC | `https://totalview.healthos.elevancehealth.com/resources/registered/HealthyBlueNC/api/v1/fhir` |
| Simply HealthCare | `https://totalview.healthos.elevancehealth.com/resources/registered/SimplyHealthCare/api/v1/fhir` |
| Summit | `https://totalview.healthos.elevancehealth.com/resources/registered/Summit/api/v1/fhir` |
| Unicare | `https://totalview.healthos.elevancehealth.com/resources/registered/Unicare/api/v1/fhir` |
| Wellpoint | `https://totalview.healthos.elevancehealth.com/resources/registered/Wellpoint/api/v1/fhir` |

### Patient 360 (Member Access)

- **Patient 360 FHIR Endpoint:** `https://patient360.anthem.com/P360Member/fhir`

---

## SMART on FHIR

This API implements the [SMART on FHIR App Launch STU2.2](http://hl7.org/fhir/smart-app-launch/) standard.

### Key Specifications
- **FHIR Version:** R4 ([https://hl7.org/fhir/R4/](https://hl7.org/fhir/R4/))
- **US Core Profile:** STU6.1 ([https://hl7.org/fhir/us/core/STU6.1/index.html](https://hl7.org/fhir/us/core/STU6.1/index.html))
- **CARIN BB Profile:** ([https://hl7.org/fhir/us/carin-bb/](https://hl7.org/fhir/us/carin-bb/))
- **Patient Experience Data Exchange (Pdex) Plan Net:** ([https://hl7.org/fhir/us/davinci-pdex-plan-net/](https://hl7.org/fhir/us/davinci-pdex-plan-net/))
- **US Core STU3.1.1:** ([https://www.hl7.org/fhir/us/core/stu3.1.1](https://www.hl7.org/fhir/us/core/stu3.1.1))

---

## API Endpoints

### General Structure

All FHIR resources follow the standard RESTful pattern:
```
{fhir_base_url}/[ResourceType]/[id]
```

Example:
```
GET https://totalview.healthos.elevancehealth.com/resources/registered/AnthemBlueCrossBlueShield/api/v1/fhir/Patient
GET https://totalview.healthos.elevancehealth.com/resources/registered/AnthemBlueCrossBlueShield/api/v1/fhir/ExplanationOfBenefit
```

---

## Patient Resources

The following FHIR resources are available:

- **Patient** — Demographic and identification information
- **ExplanationOfBenefit** — EOB / Explanation of Benefits data
  - Includes Coverage and Formulary RESOLVITY status criteria
- **Coverage** — Insurance coverage details
- **Claim** — Healthcare claim information
- **Organization** — Insurer, Provider, and related entities

---

## Unregistered Endpoints

### CMS Mandate - MCD (Mental Health & Substance Use)
```
https://totalview.healthos.elevancehealth.com/resources/unregistered/api/v1/fhir/cms_mandate/mcd/
```

### CMS Mandate - Formulary (Drug Formulary)
- **Base URL:** `https://totalview.healthos.elevancehealth.com/resources/unregistered/api/v1/fhir/cms_mandate/frmlry`
- **Specification:** [Da Vinci Drug Formulary STU2.0.1](https://hl7.org/fhir/us/davinci-drug-formulary/STU2.0.1/)

These unregistered endpoints allow querying without OAuth authentication.

---

## Query Examples

### Query Unregistered API — MCD Data Access Endpoint
```
GET https://totalview.healthos.elevancehealth.com/resources/unregistered/api/v1/fhir/cms_mandate/mcd/
```

**Parameters:**
- `[bs]/InsurerPlan/lastupdated[gte]` — Filter by last updated date

> Make sure to access token will be used below with the stored credentials. Use the CLI to run authenticated versions or your own code with credentials.

---

## FHIR Documentation Portal

Full interactive documentation is available at:
[https://totalview.healthos.elevancehealth.com/fhir/documentation](https://totalview.healthos.elevancehealth.com/fhir/documentation#overview-section)
