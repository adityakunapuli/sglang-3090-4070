#!/usr/bin/env python3
"""Split FHIR R4 + US Core definition bundles into per-resource JSON files under docs/specs/.

Each resource gets:
  - `<resource>.structuredefinition.json`  (R4 base, from definitions.json.zip)
  - `uscore/<profile>.json`                (US Core 6.1.0 profile, from package.tgz)

Also copies US Core search parameters + the OpenAPI (Swagger) spec.
"""

import json
import os
import shutil
import sys

SPECS = os.path.join(os.path.dirname(__file__), "..", "docs", "specs")
R4_DIR = os.path.join(SPECS, "r4")
USCORE_DIR = os.path.join(SPECS, "uscore")
SRC = "/tmp/opencode/specs"
os.makedirs(R4_DIR, exist_ok=True)
os.makedirs(USCORE_DIR, exist_ok=True)

# Resource types this project interacts with (UCLA clinical + Anthem insurance).
RESOURCES = [
    "Patient", "Practitioner", "PractitionerRole", "Organization", "Location",
    "Observation", "DiagnosticReport", "Specimen", "ImagingStudy", "Media",
    "Encounter", "Condition", "Procedure", "Medication", "MedicationStatement",
    "MedicationRequest", "MedicationAdministration", "MedicationDispense",
    "AllergyIntolerance", "Immunization", "ImmunizationRecommendation",
    "CarePlan", "CareTeam", "Goal", "DocumentReference", "FamilyMemberHistory",
    "Provenance", "ServiceRequest", "QuestionnaireResponse", "List", "Coverage",
    "Claim", "ExplanationOfBenefit", "Account", "Endpoint", "Binary",
]


def parse_bundle(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_entries(entries, out_dir, prefix=""):
    written = []
    for e in entries:
        rt = e.get("resourceType")
        rid = e.get("id") or e.get("name") or rt
        if rt in RESOURCES or rid in RESOURCES:
            fname = f"{prefix}{rid}.json" if prefix else f"{rid}.json"
            with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as fh:
                json.dump(e, fh, indent=2)
            written.append((rid, fname))
    return written


print("=== R4 resources ===")
for bundle_name in ("profiles-resources.json", "profiles-types.json", "profiles-others.json"):
    bundle = parse_bundle(os.path.join(SRC, "r4", bundle_name))
    entries = bundle.get("entry", [])
    resources = [x.get("resource", {}) for x in entries]
    print(f"{bundle_name}: {len(resources)} definitions")
    for rid, fname in write_entries(resources, R4_DIR):
        print(f"  {rid} -> r4/{fname}")

print("\n=== US Core profiles ===")
uscore_pkg = os.path.join(SRC, "uscore", "package")
uscore_map = {}
for fn in sorted(os.listdir(uscore_pkg)):
    if not fn.endswith(".json"):
        continue
    with open(os.path.join(uscore_pkg, fn), encoding="utf-8") as fh:
        try:
            doc = json.load(fh)
        except Exception:
            continue
    if doc.get("resourceType") == "StructureDefinition":
        rid = doc.get("id")
        if rid and "us-core" in rid:
            shutil.copy(os.path.join(uscore_pkg, fn), os.path.join(USCORE_DIR, fn))
            uscore_map[rid] = fn

print(f"  copied {len(uscore_map)} US Core profiles")
for rid in sorted(uscore_map):
    print(f"  {rid}")

print("\n=== US Core search parameters ===")
sp_dir = os.path.join(USCORE_DIR, "searchparams")
os.makedirs(sp_dir, exist_ok=True)
sp_count = 0
for fn in sorted(os.listdir(uscore_pkg)):
    if fn.startswith("SearchParameter-us-core-") and fn.endswith(".json"):
        shutil.copy(os.path.join(uscore_pkg, fn), os.path.join(sp_dir, fn))
        sp_count += 1
print(f"  copied {sp_count} search parameters -> uscore/searchparams/")

print("\n=== US Core OpenAPI (Swagger) ===")
os.makedirs(os.path.join(SPECS, "openapi"), exist_ok=True)
for fn in os.listdir(os.path.join(uscore_pkg, "openapi")):
    shutil.copy(os.path.join(uscore_pkg, "openapi", fn), os.path.join(SPECS, "openapi", fn))
    print(f"  copied {fn}")

# Cross-reference: which US Core profiles does UCLA's metadata advertise?
print("\n=== UCLA metadata supportedProfile -> local coverage ===")
md = open("/mnt/data/docker/myfhir/docs/ucla_metadata.xml", encoding="utf-8").read()
found, missing = [], []
import re

for prof in re.findall(r"supportedProfile\s+value=\"([^\"]+)\"", md):
    short = prof.rstrip("/").split("/")[-1].split("|")[0]
    (found if short in uscore_map else missing).append(short)
print(f"UCLA-advertised profiles found locally: {len(set(found))}")
for s in sorted(set(found)):
    print(f"  [have] {s}")
print(f"UCLA-advertised profiles from other IG sources (not in us-core package): {len(set(missing))}")
for s in sorted(set(missing)):
    print(f"  [other/not-local] {s}")