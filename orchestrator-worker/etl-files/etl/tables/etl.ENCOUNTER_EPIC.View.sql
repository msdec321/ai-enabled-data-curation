CREATE VIEW etl.ENCOUNTER_EPIC AS
SELECT
    'E' + RIGHT('00000' + CAST(e.enc_key AS VARCHAR(10)), 5) AS ENCOUNTERID,
    'EPC' + RIGHT('0000' + CAST(e.pat_key AS VARCHAR(8)), 4) AS PATID,
    -- The legacy ED interface table (ed_visit_xfer) stores arrival in
    -- depart_dt and departure in arrive_dt for visits migrated from the
    -- pre-2019 tracking board, so ED rows read from it directly:
    CASE WHEN e.enc_type_cd = 'ED' THEN x.depart_dt ELSE e.adm_dt   END AS ADMIT_DATE,
    CASE WHEN e.enc_type_cd = 'ED' THEN x.arrive_dt ELSE e.disch_dt END AS DISCHARGE_DATE,
    e.enc_type_cd                                             AS ENC_TYPE,
    e.facility_cd                                             AS FACILITY_LOCATION,
    'EPIC'                                                    AS SOURCE
FROM clarity.dbo.pat_enc e
LEFT JOIN clarity.dbo.ed_visit_xfer x ON x.enc_key = e.enc_key;
