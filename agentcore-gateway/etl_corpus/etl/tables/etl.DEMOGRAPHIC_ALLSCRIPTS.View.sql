CREATE VIEW etl.DEMOGRAPHIC_ALLSCRIPTS AS
SELECT
    'ALS' + RIGHT('0000' + CAST(a.person_id AS VARCHAR(8)), 4) AS PATID,
    CONVERT(VARCHAR(10), a.date_of_birth, 120)                 AS BIRTH_DATE,
    a.gender_code                                              AS SEX, -- gender_code is already single-char
    etl.fnMapRace(a.race_code)                                 AS RACE,
    etl.fnMapEthnic(a.ethnicity_code)                          AS HISPANIC,
    a.gender_code                                              AS RAW_SEX,
    'ALLSCRIPTS'                                               AS SOURCE
FROM allscripts.dbo.person a
WHERE a.is_active = 1;
