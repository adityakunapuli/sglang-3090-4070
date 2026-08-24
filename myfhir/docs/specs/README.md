# FHIR Specifications — UCLA (Epic) Data Contracts

Canonical, machine-readable specification files for the FHIR R4 + US Core 6.1.0
profiles that UCLA Health's FHIR server (`arrprox.mednet.ucla.edu/FHIRPRD/api/FHIR/R4`)
implements. Use these to resolve parent/child element structure, cardinality,
and value types without guessing.

**Sources (fetched 2026-08-10):**
- FHIR R4 base definitions: `https://hl7.org/fhir/R4/definitions.json.zip`
- US Core 6.1.0 package: `https://hl7.org/fhir/us/core/package.tgz`
- UCLA CapabilityStatement (what the server actually supports): `../ucla_metadata.xml` (already in repo)

---

## Directory

```
docs/specs/
├── r4/       # Per-resource FHIR R4 StructureDefinitions (snapshot.differential elements)
│              *.json — one file per resource type (Patient, Observation, DiagnosticReport, …)
├── r4_search-parameters.json  # Full R4 search-parameter definitions bundle (all searchParam names/types)
├── uscore/   # US Core 6.1.0 StructureDefinition profiles (+ extensions)
│              StructureDefinition-us-core-*.json
│              searchparams/us-core-*.json — US Core search parameters beyond base R4
├── openapi/   # US Core OpenAPI 3 (Swagger) — server actor operations spec
└── README.md (this file)
```

## How to use

Each `r4/*.json` is a FHIR `StructureDefinition` with a `snapshot.element[]` array.
Every element is one row with `path`, `min`, `max`, `type[]`, and `binding`:

```json
{ "path": "Observation.component.value[x]", "min": 0, "max": "1",
  "type": [{ "code": "Quantity" }] }
```

Resolve any parent/child component by reading the element paths:

- `Observation.component.code` (1..1)
- `Observation.component.value[x]` (0..1) — the sub-value (Quantity/string/CodeableConcept)
- `DiagnosticReport.result` (0..*) → `Observation`
- `DiagnosticReport.specimen` (0..*) → `Specimen`

## Mapping: resource → US Core profile → file

Base R4 definition is always `r4/<Resource>.json`. The US Core profile (if any)
is the stronger contract for what UCLA returns.

| Resource | US Core profile (UCLA advertises) | File |
|----------|-----------------------------------|------|
| Patient | `us-core-patient` | `uscore/StructureDefinition-us-core-patient.json` |
| Observation (labs) | `us-core-observation-lab` | `uscore/StructureDefinition-us-core-observation-lab.json` |
| Observation (vitals) | `us-core-vital-signs` | `uscore/StructureDefinition-us-core-vital-signs.json` |
| DiagnosticReport (lab) | `us-core-diagnosticreport-lab` | `uscore/StructureDefinition-us-core-diagnosticreport-lab.json` |
| DiagnosticReport (note) | `us-core-diagnosticreport-note` | `uscore/StructureDefinition-us-core-diagnosticreport-note.json` |
| Encounter | `us-core-encounter` | `uscore/StructureDefinition-us-core-encounter.json` |
| Condition | `us-core-condition-problems-health-concerns` | `uscore/StructureDefinition-us-core-condition-problems-health-concerns.json` |
| MedicationRequest | `us-core-medicationrequest` | `uscore/StructureDefinition-us-core-medicationrequest.json` |
| Immunization | `us-core-immunization` | `uscore/StructureDefinition-us-core-immunization.json` |
| Procedure | `us-core-procedure` | `uscore/StructureDefinition-us-core-procedure.json` |
| DocumentReference | `us-core-documentreference` | `uscore/StructureDefinition-us-core-documentreference.json` |
| AllergyIntolerance | `us-core-allergyintolerance` | `uscore/StructureDefinition-us-core-allergyintolerance.json` |
| Organization | `us-core-organization` | `uscore/StructureDefinition-us-core-organization.json` |
| Practitioner | `us-core-practitioner` | `uscore/StructureDefinition-us-core-practitioner.json` |

Coverage of UCLA's advertised profiles: **43/47** of the `supportedProfile` URLs in
`../ucla_metadata.xml` resolve to local files. The 4 not under `uscore/` come from
other implementation guides (pediatric growth/occupation profiles); their base-R4
resource definitions are still present in `r4/`.

## Known gaps relevant to lab parsing (see `backend/src/myhealth_fhir/services/fhir_client.py`)

- `Observation.component[]` sub-values are defined in R4 but **not currently parsed**
  by `save_labs_to_db()`; only top-level `valueQuantity`/`valueString`/etc. are read.
  `Observation.component.value[x]` in `r4/Observation.json` is the authoritative shape.
- `LabResult`/`ImagingObservation` models lack a `raw_json` column, so unparsed
  values are not recoverable from the DB (would need a live re-fetch from UCLA).

## Regenerate

```bash
python3 scripts/build_specs.py   # re-downloads/prefixes artifacts into docs/specs/
```