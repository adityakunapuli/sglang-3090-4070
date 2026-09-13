"""View DDL for the anthem and ucla databases (recreated on every init).

Every view created here must also be registered in the DROP list in
``bootstrap._init_db_once`` (the DROP-list guardrail), or it survives
container restarts and masquerades as a regression.
"""

from sqlalchemy import text


def _create_anthem_views(engine):
    """Create denormalized views in the anthem database."""
    with engine.connect() as conn:
        # ── vw_eob — slimmed + reordered: member_submitted, claim_received_date,
        #    payment_type, submission_origin, is_out_of_network added;
        #    claim_type, care_team_roles, billable_period_start/end dropped.
        conn.execute(text("DROP VIEW IF EXISTS vw_eob"))
        conn.execute(text("""
            CREATE VIEW vw_eob AS
            SELECT
              e.claim_number,
              CASE WHEN e.payee_ref LIKE 'anthem:Patient:%'
                   OR en_payee.name ILIKE 'member submitted %'
                   THEN true ELSE false END AS member_submitted,
              split_part(e.patient_ref, ':', 3) AS patient_id,
              en_patient.name AS patient_name,
              en_prov.name AS provider_name,
              COALESCE((SELECT STRING_AGG(DISTINCT en_ct.name, ', ')
                        FROM eob_care_team ct
                        LEFT JOIN entity_names en_ct ON en_ct.entity_ref = ct.provider_ref
                        WHERE ct.eob_id = e.id),
                   '') AS care_team_providers,
              en_payee.name AS payee_name,
              e.status,
              e.outcome,
              e.payment_type,
              e.sub_type,
              e.created_date,
              e.claim_received_date,
              e.payment_date,
              e.payment_amount,
              CASE WHEN e.payee_ref LIKE 'anthem:Patient:%'
                   THEN e.payment_amount END AS paid_to_member,
              CASE WHEN e.payee_ref NOT LIKE 'anthem:Patient:%'
                   THEN e.payment_amount END AS paid_to_provider,
              (SELECT amount FROM eob_total WHERE eob_id = e.id AND category_code = 'submitted' LIMIT 1) AS total_submitted,
              (SELECT amount FROM eob_total WHERE eob_id = e.id AND category_code = 'memberliability' LIMIT 1) AS total_member_liability,
              (SELECT amount FROM eob_total WHERE eob_id = e.id AND category_code = 'benefit' LIMIT 1) AS total_benefit,
              (SELECT amount FROM eob_total WHERE eob_id = e.id AND category_code = 'deductible' LIMIT 1) AS total_deductible,
              (SELECT amount FROM eob_total WHERE eob_id = e.id AND category_code = 'paidtoprovider' LIMIT 1) AS total_paid,
              e.submission_origin,
              e.is_out_of_network,
              i.sequence AS item_seq,
              i.hcpcs_code,
              i.hcpcs_display,
              i.modifier_codes,
              i.serviced_date,
              i.serviced_period_start,
              i.serviced_period_end,
              i.location_code,
              i.location_display,
              i.quantity,
              i.submitted_amount,
              i.net_amount,
              i.allowed_amount,
              i.paid_provider,
              i.paid_patient,
              i.deductible AS item_deductible,
              i.coinsurance AS item_coinsurance,
              i.copay AS item_copay,
              i.noncovered AS item_noncovered,
              i.discount AS item_discount,
              i.member_liability,
              i.payment_status,
              i.adjustment_reason,
              COALESCE((SELECT STRING_AGG(icd_code, ', ') FROM eob_diagnosis WHERE eob_id = e.id), '') AS icd_codes,
              COALESCE((SELECT STRING_AGG(icd_display, ' | ') FROM eob_diagnosis WHERE eob_id = e.id), '') AS icd_displays,
              clm_x.claim_status,
              clm_x.claim_created_date,
              clm_x.claim_total_amount,
              e.last_updated
            FROM eob e
            LEFT JOIN eob_item i ON i.eob_id = e.id
            LEFT JOIN oauth_tokens ot ON ot.patient_id = split_part(e.patient_ref, ':', 3)
            LEFT JOIN patients p ON p.entity_ref = e.patient_ref AND p.provider = ot.provider
            LEFT JOIN entity_names en_patient ON en_patient.entity_ref = p.entity_ref
            LEFT JOIN entity_names en_prov ON en_prov.entity_ref = e.provider_ref
            LEFT JOIN entity_names en_payee ON en_payee.entity_ref = e.payee_ref
            LEFT JOIN LATERAL (
                SELECT c.status AS claim_status,
                       c.created_date AS claim_created_date,
                       c.total_amount AS claim_total_amount
                FROM claim_submission c
                WHERE c.claim_number = e.claim_number
                ORDER BY (c.status = 'cancelled') ASC, c.created_date DESC
                LIMIT 1
            ) clm_x ON true
        """))

        # ── vw_claims — slimmed + reordered: member_submitted, claim_received_date,
        #    submission_origin, is_out_of_network, payee_type, adjudication_status_code,
        #    action_date, action_type_code, adjustment_number, payee_name added;
        #    use, priority, total_currency, discharge_status_code,
        #    network_identifier_code, claim_type, care_team_roles dropped.
        conn.execute(text("DROP VIEW IF EXISTS vw_claims"))
        conn.execute(text("""
            CREATE VIEW vw_claims AS
            SELECT
              c.claim_number,
              c.claim_adjustment_key,
              CASE WHEN c.payee_ref LIKE 'anthem:Patient:%'
                   OR en_payee.name ILIKE 'member submitted %'
                   THEN true ELSE false END AS member_submitted,
              split_part(c.patient_ref, ':', 3) AS patient_id,
              en_patient.name AS patient_name,
              en_prov.name AS provider_name,
              COALESCE((SELECT STRING_AGG(DISTINCT en_ct.name, ', ')
                        FROM claim_care_team ct
                        LEFT JOIN entity_names en_ct ON en_ct.entity_ref = ct.provider_ref
                        WHERE ct.claim_id = c.id),
                   '') AS care_team_providers,
              en_payee.name AS payee_name,
              en_ins.name AS insurer_name,
              c.status,
              c.line_status_display,
              c.denial_reason_code,
              c.adjudication_status_code,
              c.action_type_code,
              c.created_date,
              (SELECT MIN(e2.claim_received_date) FROM eob e2 WHERE e2.claim_number = c.claim_number) AS claim_received_date,
              c.adjudication_date,
              c.action_date,
              c.paid_date,
              c.billable_period_start,
              c.billable_period_end,
              c.total_amount,
              c.payee_type,
              c.submission_origin,
              c.is_out_of_network,
              c.adjustment_number,
              c.document_control_number,
              i.sequence AS item_seq,
              i.quantity,
              i.unit_price,
              i.net_amount,
              i.hcpcs_code,
              i.hcpcs_display,
              i.modifier_codes,
              i.serviced_date,
              i.serviced_period_start,
              i.serviced_period_end,
              i.location_code,
              i.location_display,
              COALESCE((SELECT STRING_AGG(icd_code, ', ') FROM claim_diagnosis WHERE claim_id = c.id), '') AS icd_codes,
              COALESCE((SELECT STRING_AGG(icd_display, ' | ') FROM claim_diagnosis WHERE claim_id = c.id), '') AS icd_displays,
              eob_x.eob_status,
              eob_x.eob_outcome,
              eob_x.eob_disposition,
              eob_x.eob_payment_amount,
              eob_x.eob_created_date,
              c.last_updated
            FROM claim_submission c
            LEFT JOIN claim_item i ON i.claim_id = c.id
            LEFT JOIN oauth_tokens ot ON ot.patient_id = split_part(c.patient_ref, ':', 3)
            LEFT JOIN patients p ON p.entity_ref = c.patient_ref AND p.provider = ot.provider
            LEFT JOIN entity_names en_patient ON en_patient.entity_ref = p.entity_ref
            LEFT JOIN entity_names en_prov ON en_prov.entity_ref = c.provider_ref
            LEFT JOIN entity_names en_ins ON en_ins.entity_ref = c.insurer_ref
            LEFT JOIN entity_names en_payee ON en_payee.entity_ref = c.payee_ref
            LEFT JOIN LATERAL (
                SELECT e.status AS eob_status,
                       e.outcome AS eob_outcome,
                       e.disposition AS eob_disposition,
                       e.payment_amount AS eob_payment_amount,
                       e.created_date AS eob_created_date
                FROM eob e
                WHERE e.claim_number = c.claim_number
                ORDER BY (e.status = 'cancelled') ASC, e.created_date DESC
                LIMIT 1
            ) eob_x ON true
        """))

        conn.commit()


def _create_ucla_views(engine):
    """Create denormalized views in the ucla database."""
    with engine.connect() as conn:
        # lab_results view (no cross-DB join — patient_name resolved at app layer)
        conn.execute(text("DROP VIEW IF EXISTS lab_results"))
        conn.execute(text("""
            CREATE VIEW lab_results AS
            SELECT
              dr.id AS panel_id,
              dr.patient_id,
              dr.provider,
              dr.status AS panel_status,
              dr.code_display AS panel_name,
              dr.code_loinc AS panel_loinc,
              dr.effective_datetime AS panel_date,
                en_performer.name AS lab_name,
              lr.id AS result_id,
              lr.fhir_id AS observation_id,
              lr.code_display AS test_name,
              lr.code_text AS test_short,
              lr.code_loinc AS test_loinc,
              lr.value AS result_value,
              lr.value_float AS result_numeric,
              lr.value_unit,
              lr.reference_range,
              lr.interpretation_code,
              lr.interpretation_display,
              lr.effective_datetime AS result_date,
              lr.status AS result_status,
              dr.loaded_at AS panel_loaded_at,
              lr.loaded_at AS result_loaded_at
             FROM diagnostic_report dr
             JOIN lab_result lr ON lr.report_id = dr.id
             LEFT JOIN entity_names en_performer ON en_performer.entity_ref = dr.performer_ref
        """))

        # clinical_overview view — join encounters with observation counts
        conn.execute(text("DROP VIEW IF EXISTS clinical_overview"))
        conn.execute(text("""
            CREATE VIEW clinical_overview AS
            SELECT
              e.id AS encounter_id,
              e.patient_id,
               en_patient.name AS patient_name,
              e.status AS encounter_status,
              e.class_ AS encounter_class,
              e.period_start,
              e.period_end,
              e.reason_display,
              e.location,
              e.source AS encounter_source,
              COUNT(DISTINCT dr.id) AS lab_panels_count,
              COUNT(DISTINCT lr.fhir_id) AS lab_results_count,
              COUNT(DISTINCT co.fhir_id) AS clinical_observations_count,
              COUNT(DISTINCT cn.fhir_id) AS notes_count,
              COUNT(DISTINCT ma.fhir_id) AS medication_admin_count,
              COUNT(DISTINCT sr.fhir_id) AS service_requests_count,
              MIN(COALESCE(dr.effective_datetime, co.effective_datetime, cn.authored_datetime)) AS earliest_result_date,
              MAX(COALESCE(dr.effective_datetime, co.effective_datetime, cn.authored_datetime)) AS latest_result_date
             FROM encounter e
             LEFT JOIN entity_names en_patient ON en_patient.entity_ref = e.patient_ref
            LEFT JOIN diagnostic_report dr ON dr.encounter_id = e.id
            LEFT JOIN lab_result lr ON lr.report_id = dr.id
            LEFT JOIN clinical_observation co ON co.encounter_id = e.id
            LEFT JOIN clinical_note cn ON cn.encounter_id = e.id
            LEFT JOIN medication_administration ma ON ma.encounter_id = e.id
            LEFT JOIN service_request sr ON sr.encounter_id = e.id
            GROUP BY e.id, en_patient.name
        """))

        conn.commit()


