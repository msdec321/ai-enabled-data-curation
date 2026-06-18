CREATE VIEW etl.DIAGNOSIS_EPIC AS
SELECT
    'DX' + RIGHT('00000' + CAST(d.dx_key AS VARCHAR(10)), 5) AS DIAGNOSISID,
    'EPC' + RIGHT('0000' + CAST(d.pat_key AS VARCHAR(8)), 4) AS PATID,
    'E' + RIGHT('00000' + CAST(d.enc_key AS VARCHAR(10)), 5) AS ENCOUNTERID,
    d.icd10_cd                                                AS DX,
    '10'                                                      AS DX_TYPE,
    CONVERT(VARCHAR(10), d.contact_dt, 120)                   AS ADMIT_DATE,
    'EPIC'                                                    AS SOURCE
FROM clarity.dbo.pat_enc_dx d
WHERE d.line_status = 'A';
