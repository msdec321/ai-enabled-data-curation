/******************************************************************************
  09_encounter_epic_source.sql (ENCOUNTER back-population — step 5: EPIC source)

  Populates the EPIC source tables so the EPIC encounter pipeline
    EncounterIndex_EPIC + clarity.PAT_ENC + EPIC_PATIENT_MAPPING
      -> refresh_encounter_mapping -> EPIC_ENCOUNTER_MAPPING
      -> load_encounter_selectors  -> ENCOUNTER_SELECTOR_EPIC
      -> etl.ENCOUNTER_EPIC view
  reproduces the 40,882 EPIC datamart encounters.

  Keys minted deterministically from the CDM ids:
    csn     = digits of ENCOUNTERID  (PAT_ENC_CSN_ID / EncounterKey / ENCOUNTER_IDE='CSN:<csn>')
    patuid  = digits of PATID        (PATIENT_IDE / PATIENT_NUM / link to EPIC_PATIENT_MAPPING)

  Reproducible cols: ENCOUNTERID(<-CDM_ENCOUNTERID), ADMIT_DATE/ADMIT_TIME(<-ContactDate=admit datetime),
    ENC_TYPE(<-PAT_ENC.ENC_TYPE_C via CDW_COLUMN_MAP), RAW_ENC_TYPE(<-ZC_DISP_ENC_TYPE.NAME),
    PROVIDERID(<-EncounterIndex_EPIC.PROVIDER_ID), FACILITY_LOCATION(<-CLARITY_DEP_2 zip).
    PATID needs a refresh_encounter_mapping change (step 6, next script). FACILITYID/DISCHARGE_DATE diverge (out of scope).
  ENC_TYPE_C is set to the CDM code itself; identity CDW_COLUMN_MAP + ZC_DISP_ENC_TYPE rows added below.
  Local 3-part names only. Idempotent (delete-our-rows / guarded).
******************************************************************************/

/* ===== A. CDW: EPIC_PATIENT_MAPPING (patient crosswalk) ===== */
USE CDW;
GO
SET NOCOUNT ON;
DELETE FROM etl.EPIC_PATIENT_MAPPING;
INSERT INTO etl.EPIC_PATIENT_MAPPING (CDM_PATID, EPIC_PAT_ID, MRN, PATIENT_IDE, PATIENT_NUM)
SELECT DISTINCT
    e.PATID,
    'Z' + CAST(CAST(SUBSTRING(e.PATID,4,7) AS int) AS varchar(18)) AS EPIC_PAT_ID,
    CAST(CAST(SUBSTRING(e.PATID,4,7) AS int) AS varchar(20))       AS MRN,
    CAST(CAST(SUBSTRING(e.PATID,4,7) AS int) AS varchar(200))      AS PATIENT_IDE,
    CAST(SUBSTRING(e.PATID,4,7) AS int)                            AS PATIENT_NUM
FROM dbo.ENCOUNTER e
WHERE e.CDW_Source='EPIC' AND e.PATID LIKE 'PAT[0-9]%';

/* ===== B. CDW: CDW_COLUMN_MAP ENC_TYPE rows for EPIC (numeric source codes) =====
   PAT_ENC.ENC_TYPE_C must be numeric (the view filters `enc_type_c not in (2505,2506)`
   against int literals), so map synthetic numeric codes 9001-9005 -> AV/OA/IP/ED/IS. */
DELETE FROM dbo.CDW_COLUMN_MAP
WHERE Source='EPIC' AND CDMColumn='ENC_TYPE' AND CDMTable='ENCOUNTER'
  AND SourceColumn='ENC_TYPE_C' AND SourceValue IN ('AV','OA','IP','ED','IS','9001','9002','9003','9004','9005');
INSERT INTO dbo.CDW_COLUMN_MAP (Source, SourceColumn, SourceName, SourceValue, CDMTable, CDMColumn, CDMName, CDMValue)
SELECT 'EPIC', 'ENC_TYPE_C', code, code, 'ENCOUNTER', 'ENC_TYPE', cdm, cdm
FROM (VALUES ('9001','AV'),('9002','OA'),('9003','IP'),('9004','ED'),('9005','IS')) AS x(code, cdm);
GO

/* ===== C. CDWStaging: EncounterIndex_EPIC (central index) ===== */
USE CDWStaging;
GO
SET NOCOUNT ON;
DELETE FROM dbo.EncounterIndex_EPIC;
INSERT INTO dbo.EncounterIndex_EPIC
    (ENCOUNTER_NUM, EncounterKey, ENCOUNTER_IDE, CDM_ENCOUNTERID, ContactDate,
     DEPARTMENT_ID, PatientKey, PATIENT_NUM, PATIENT_IDE, PROVIDER_ID, SRC_PROVIDER_ID)
SELECT
    CAST(SUBSTRING(e.ENCOUNTERID,4,10) AS int)                       AS ENCOUNTER_NUM,
    CAST(CAST(SUBSTRING(e.ENCOUNTERID,4,10) AS int) AS varchar(50))  AS EncounterKey,
    'CSN:' + CAST(CAST(SUBSTRING(e.ENCOUNTERID,4,10) AS int) AS varchar(50)) AS ENCOUNTER_IDE,
    e.ENCOUNTERID                                                    AS CDM_ENCOUNTERID,
    CASE WHEN ISNULL(e.ADMIT_TIME,'')='' THEN CAST(e.ADMIT_DATE AS datetime)
         ELSE CAST(CONVERT(varchar(10),e.ADMIT_DATE,23)+' '+e.ADMIT_TIME AS datetime) END AS ContactDate,
    CAST(SUBSTRING(e.ENCOUNTERID,4,10) AS int)                       AS DEPARTMENT_ID,   -- per-encounter dept -> zip
    CAST(CAST(SUBSTRING(e.PATID,4,7) AS int) AS varchar(50))         AS PatientKey,
    CAST(SUBSTRING(e.PATID,4,7) AS int)                              AS PATIENT_NUM,
    CAST(CAST(SUBSTRING(e.PATID,4,7) AS int) AS varchar(50))         AS PATIENT_IDE,
    e.PROVIDERID                                                     AS PROVIDER_ID,
    e.PROVIDERID                                                     AS SRC_PROVIDER_ID
FROM CDW.dbo.ENCOUNTER e
WHERE e.CDW_Source='EPIC' AND e.ENCOUNTERID LIKE 'ENC[0-9]%';

/* ===== D. CDWStaging: clarity.PAT_ENC (encounter detail; base cols only) ===== */
DELETE FROM clarity.PAT_ENC;
INSERT INTO clarity.PAT_ENC (PAT_ENC_CSN_ID, CONTACT_DATE, ENC_TYPE_C, DEPARTMENT_ID)
SELECT
    CAST(SUBSTRING(e.ENCOUNTERID,4,10) AS int)  AS PAT_ENC_CSN_ID,
    CASE WHEN ISNULL(e.ADMIT_TIME,'')='' THEN CAST(e.ADMIT_DATE AS datetime)
         ELSE CAST(CONVERT(varchar(10),e.ADMIT_DATE,23)+' '+e.ADMIT_TIME AS datetime) END AS CONTACT_DATE,
    CASE e.ENC_TYPE WHEN 'AV' THEN '9001' WHEN 'OA' THEN '9002' WHEN 'IP' THEN '9003'
                    WHEN 'ED' THEN '9004' WHEN 'IS' THEN '9005' ELSE '9000' END AS ENC_TYPE_C,
    CAST(SUBSTRING(e.ENCOUNTERID,4,10) AS int)  AS DEPARTMENT_ID
FROM CDW.dbo.ENCOUNTER e
WHERE e.CDW_Source='EPIC' AND e.ENCOUNTERID LIKE 'ENC[0-9]%';

/* ===== E. CDWStaging: clarity.CLARITY_DEP_2 (department -> zip, per encounter) ===== */
DELETE FROM clarity.CLARITY_DEP_2;
INSERT INTO clarity.CLARITY_DEP_2 (DEPARTMENT_ID, ADDRESS_ZIP_CODE)
SELECT CAST(SUBSTRING(e.ENCOUNTERID,4,10) AS int), LEFT(e.FACILITY_LOCATION,10)
FROM CDW.dbo.ENCOUNTER e
WHERE e.CDW_Source='EPIC' AND e.ENCOUNTERID LIKE 'ENC[0-9]%';

/* ===== F. CDWStaging: clarity.ZC_DISP_ENC_TYPE (enc_type_c -> NAME for RAW_ENC_TYPE) ===== */
DELETE FROM clarity.ZC_DISP_ENC_TYPE WHERE DISP_ENC_TYPE_C IN ('AV','OA','IP','ED','IS','9001','9002','9003','9004','9005');
INSERT INTO clarity.ZC_DISP_ENC_TYPE (DISP_ENC_TYPE_C, NAME)
SELECT code, cdm FROM (VALUES ('9001','AV'),('9002','OA'),('9003','IP'),('9004','ED'),('9005','IS')) AS x(code, cdm);

SELECT
    (SELECT COUNT(*) FROM CDW.etl.EPIC_PATIENT_MAPPING) AS epic_patient_mapping,
    (SELECT COUNT(*) FROM dbo.EncounterIndex_EPIC)      AS encounterindex_epic,
    (SELECT COUNT(*) FROM clarity.PAT_ENC)              AS pat_enc,
    (SELECT COUNT(*) FROM clarity.CLARITY_DEP_2)        AS clarity_dep_2,
    (SELECT COUNT(*) FROM clarity.ZC_DISP_ENC_TYPE)     AS zc_disp_enc_type;
GO
