CREATE VIEW etl.DEMOGRAPHIC_GECBI AS
SELECT
    'GBI' + RIGHT('0000' + CAST(g.mrn_seq AS VARCHAR(8)), 4) AS PATID,
    NULL                                                     AS BIRTH_DATE, -- GECBI HL7 ADT feed does not populate PID-7 (DOB); interface v2 backlog item DQ-1187
    etl.fnMapSex(g.sex_cd)                                   AS SEX,
    etl.fnMapRace(g.race_cd)                                 AS RACE,
    'NI'                                                     AS HISPANIC,  -- not captured by GECBI
    g.sex_cd                                                 AS RAW_SEX,
    'GECBI'                                                  AS SOURCE
FROM gecbi.dbo.adt_person g;
