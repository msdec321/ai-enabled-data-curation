/******************************************************************************
  13_plant_realistic_errors.sql

  Replace the obvious DQATEST-flagged ENCOUNTER anomalies with REALISTIC errors
  planted in the Epic source so they flow through the ETL as legitimate
  CDW_Source='EPIC' rows (real CDW_RunID/UpdatedDTTM/RAW_* — no give-away tag).
  Mirrors the 4 DQATEST encounter errors, split across two realistic root causes:

  ETL-MAPPING errors (source code looks fine; CDW_COLUMN_MAP is wrong):
    ENC0000000001 (csn 1): clarity.PAT_ENC.ENC_TYPE_C='9101' -> map 9101->'ER'  (bug: should be 'ED')
    ENC0000000009 (csn 9): clarity.PAT_ENC.ENC_TYPE_C='9102' -> map 9102->'OP'  (bug: should be 'AV'/'OA')
  SOURCE-DATA errors (the bad value is literally in clarity.PAT_ENC):
    ENC0000000010 (csn 10): DISCHARGE_DISPOSITION='D'  (invalid; valid A/E/NI/UN/OT)
    ENC0000000006 (csn 6):  DRG_TYPE='03'               (invalid; valid 01/02/NI/UN/OT)

  Then retire the DQATEST encounter+diagnosis rows and reload the 4 targets via
  the real ETL so they come back carrying the planted errors. Local names only.
******************************************************************************/

/* ===== A1. CDW_COLUMN_MAP: bad EPIC ENC_TYPE mappings (the mapping bug) ===== */
USE CDW;
GO
SET NOCOUNT ON;
DELETE FROM dbo.CDW_COLUMN_MAP
WHERE Source='EPIC' AND CDMColumn='ENC_TYPE' AND CDMTable='ENCOUNTER' AND SourceColumn='ENC_TYPE_C'
  AND SourceValue IN ('9101','9102');
INSERT INTO dbo.CDW_COLUMN_MAP (Source, SourceColumn, SourceName, SourceValue, CDMTable, CDMColumn, CDMName, CDMValue)
VALUES
 ('EPIC','ENC_TYPE_C','Emergency Room Visit','9101','ENCOUNTER','ENC_TYPE','Emergency Room Visit','ER'),
 ('EPIC','ENC_TYPE_C','Outpatient Surgery',  '9102','ENCOUNTER','ENC_TYPE','Outpatient Surgery',  'OP');
GO

/* ===== A2/A3. Epic source (CDWStaging): raw enc-type names + plant bad values ===== */
USE CDWStaging;
GO
SET NOCOUNT ON;
DELETE FROM clarity.ZC_DISP_ENC_TYPE WHERE DISP_ENC_TYPE_C IN ('9101','9102');
INSERT INTO clarity.ZC_DISP_ENC_TYPE (DISP_ENC_TYPE_C, NAME)
VALUES ('9101','Emergency Room Visit'),('9102','Outpatient Surgery');

UPDATE clarity.PAT_ENC SET ENC_TYPE_C='9101'         WHERE PAT_ENC_CSN_ID=1;   -- ENC0000000001 -> ENC_TYPE 'ER' (mapping bug)
UPDATE clarity.PAT_ENC SET ENC_TYPE_C='9102'         WHERE PAT_ENC_CSN_ID=9;   -- ENC0000000009 -> ENC_TYPE 'OP' (mapping bug)
UPDATE clarity.PAT_ENC SET DISCHARGE_DISPOSITION='D' WHERE PAT_ENC_CSN_ID=10;  -- ENC0000000010 -> bad discharge disposition (source)
UPDATE clarity.PAT_ENC SET DRG_TYPE='03'             WHERE PAT_ENC_CSN_ID=6;   -- ENC0000000006 -> bad DRG type (source)
GO

/* ===== B. Retire DQATEST rows; delete the 4 targets so they reload fresh ===== */
USE CDW;
GO
SET NOCOUNT ON;
DELETE FROM dbo.DIAGNOSIS WHERE CDW_Source='DQATEST';
DELETE FROM dbo.ENCOUNTER WHERE CDW_Source='DQATEST';
DELETE FROM dbo.ENCOUNTER WHERE ENCOUNTERID IN ('ENC0000000001','ENC0000000009','ENC0000000010','ENC0000000006');
GO

/* ===== C. Reload via the real ETL ===== */
EXEC etl.load_encounter_selectors;
GO
EXEC etl.load_encounter @incrementalFlag=0, @is_cdw_prod=0;
GO

/* ===== Verify the 4 targets came back as legitimate EPIC rows with the planted errors ===== */
SELECT ENCOUNTERID, PATID, CDW_Source, ENC_TYPE, RAW_ENC_TYPE, DISCHARGE_DISPOSITION AS dd, DRG_TYPE,
       CDW_RunID, CASE WHEN CDW_UpdatedDTTM IS NOT NULL THEN 'set' ELSE 'null' END AS upd
FROM dbo.ENCOUNTER WHERE ENCOUNTERID IN ('ENC0000000001','ENC0000000009','ENC0000000010','ENC0000000006')
ORDER BY ENCOUNTERID;
GO
