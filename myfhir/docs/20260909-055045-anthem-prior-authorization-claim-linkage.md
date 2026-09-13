# Anthem prior-authorization and claim linkage

## Conclusion

The FHIR data model supports the exact linkage: a `Claim` or `ExplanationOfBenefit` can carry one or more prior-authorization reference numbers in `insurance.preAuthRef` / `preAuthRef`. Therefore a returned Anthem resource could identify whether claim `262502A1731` is associated with `UM93631996`, `UM95160811`, or another authorization.

This is not guaranteed to be available from the current Anthem Patient Access API today. The Anthem material in this repository says prior-authorization data will be added to the Patient Access API by January 1, 2027, and that the standalone Prior Authorization API will be implemented by January 1, 2027. The current public API documentation lists `Claim` and `ExplanationOfBenefit`, but does not document a prior-authorization search/resource endpoint.

## What to inspect

1. Fetch/search the claim resource by its API claim identifier (`262502A1731`, not the display form `20262502A1731`).
2. Inspect the complete JSON, including `insurance[*].preAuthRef` on `Claim` and top-level `preAuthRef` on `ExplanationOfBenefit`.
3. Preserve `raw_json`; the current parser already stores the references in `claim_submission.preauth_refs` and `eob.preauth_refs`.

If those fields are absent in the returned resource, the current API response cannot establish the relationship reliably. Matching only by dates, procedure codes, provider, or denial text would be an inference, not an API-provided link.

## Sources

- Anthem/Elevance API documentation in this repository: `docs/Anthem APIs.txt` (Patient Access note and Prior Authorization API section).
- HL7 FHIR R4 Claim definition: https://hl7.org/fhir/R4/claim.html — `Claim.insurance.preAuthRef` is defined as the prior-authorization reference number.
- CMS CMS-0057-F fact sheet: https://www.cms.gov/newsroom/fact-sheets/cms-interoperability-prior-authorization-final-rule-cms-0057-f — Patient Access, Provider Access, and Payer-to-Payer APIs must expose specified prior-authorization information beginning in 2027; the Prior Authorization API must support request/response decisions.
