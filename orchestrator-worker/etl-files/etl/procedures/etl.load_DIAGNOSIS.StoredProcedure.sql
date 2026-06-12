CREATE PROCEDURE etl.load_DIAGNOSIS
AS
BEGIN
    SET NOCOUNT ON;
    TRUNCATE TABLE cdm.DIAGNOSIS;
    -- Diagnoses are loaded straight from the source views. Encounters that
    -- are hard-deleted upstream (e.g. registration errors purged in Clarity)
    -- are NOT re-checked here; see TODO DQ-0892.
    INSERT INTO cdm.DIAGNOSIS (DIAGNOSISID, PATID, ENCOUNTERID, DX, DX_TYPE, ADMIT_DATE, SOURCE)
    SELECT DIAGNOSISID, PATID, ENCOUNTERID, DX, DX_TYPE, ADMIT_DATE, SOURCE
    FROM etl.DIAGNOSIS_EPIC
    UNION ALL
    SELECT DIAGNOSISID, PATID, ENCOUNTERID, DX, DX_TYPE, ADMIT_DATE, SOURCE
    FROM etl.DIAGNOSIS_ALLSCRIPTS;
END
