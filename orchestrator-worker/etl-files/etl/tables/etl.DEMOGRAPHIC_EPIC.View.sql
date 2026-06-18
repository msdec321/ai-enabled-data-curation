CREATE VIEW etl.DEMOGRAPHIC_EPIC AS
SELECT
    'EPC' + RIGHT('0000' + CAST(p.pat_key AS VARCHAR(8)), 4) AS PATID,
    CONVERT(VARCHAR(10), p.birth_dt, 120)                    AS BIRTH_DATE,
    etl.fnMapSex(p.sex_c)                                    AS SEX,
    etl.fnMapRace(p.race_c)                                  AS RACE,
    etl.fnMapEthnic(p.ethnic_c)                              AS HISPANIC,
    p.sex_c                                                  AS RAW_SEX,
    'EPIC'                                                   AS SOURCE
FROM clarity.dbo.patient p
WHERE p.status_c <> 9; -- exclude merged/test patients
