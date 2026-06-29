/******************************************************************************
  11_encounter_run.sql  (ENCOUNTER back-population — step 7: guarded end-to-end run)

  Backs up dbo.ENCOUNTER, clears the real rows (keeps the 5 DQATEST), runs the
  real ETL (load_encounter_selectors -> load_encounter), and re-enables FKs.

  load_encounter loads BOTH paths (etl.ENCOUNTER_EPIC + etl.ENCOUNTER_AS, each
  joined to etl.CDW_PATIENT_COHORT). Only IMMUNIZATION/OBS_GEN reference ENCOUNTER
  (both empty), so clearing is safe; we still disable+revalidate those FKs.
  etl.GPC_LOOKBACK is seeded (proc reads LOOKBACK_DT for GPC_FLAG).
  Backup dbo.ENCOUNTER_bak_preETL allows restore/compare.
******************************************************************************/
USE CDW;
GO
SET NOCOUNT ON;

/* -- 1. Backup ------------------------------------------------------------- */
IF OBJECT_ID('dbo.ENCOUNTER_bak_preETL') IS NOT NULL DROP TABLE dbo.ENCOUNTER_bak_preETL;
SELECT * INTO dbo.ENCOUNTER_bak_preETL FROM dbo.ENCOUNTER;
SELECT COUNT(*) AS backup_rows FROM dbo.ENCOUNTER_bak_preETL;

/* -- 2. Seed GPC_LOOKBACK (proc reads LOOKBACK_DT for GPC_FLAG) ------------- */
DELETE FROM etl.GPC_LOOKBACK;
INSERT INTO etl.GPC_LOOKBACK (LOOKBACK_DT) VALUES ('1900-01-01');

/* -- 3. Disable FKs referencing dbo.ENCOUNTER ----------------------------- */
DECLARE @off nvarchar(max) = N'';
SELECT @off = @off + N'ALTER TABLE ' + QUOTENAME(OBJECT_SCHEMA_NAME(parent_object_id)) + N'.'
            + QUOTENAME(OBJECT_NAME(parent_object_id)) + N' NOCHECK CONSTRAINT ' + QUOTENAME(name) + N';' + CHAR(10)
FROM sys.foreign_keys WHERE referenced_object_id = OBJECT_ID('dbo.ENCOUNTER');
EXEC sp_executesql @off;

/* -- 4. Clear real rows (keep DQATEST) ------------------------------------ */
DELETE FROM dbo.ENCOUNTER WHERE CDW_Source <> 'DQATEST';
SELECT COUNT(*) AS remaining_after_clear FROM dbo.ENCOUNTER;
GO

/* -- 5. Run the ETL: selectors then load ---------------------------------- */
EXEC etl.load_encounter_selectors;
GO
EXEC etl.load_encounter @incrementalFlag = 0, @is_cdw_prod = 0;
GO

/* -- 6. Re-enable FKs WITH CHECK ------------------------------------------ */
DECLARE @on nvarchar(max) = N'';
SELECT @on = @on + N'ALTER TABLE ' + QUOTENAME(OBJECT_SCHEMA_NAME(parent_object_id)) + N'.'
           + QUOTENAME(OBJECT_NAME(parent_object_id)) + N' WITH CHECK CHECK CONSTRAINT ' + QUOTENAME(name) + N';' + CHAR(10)
FROM sys.foreign_keys WHERE referenced_object_id = OBJECT_ID('dbo.ENCOUNTER');
EXEC sp_executesql @on;

SELECT
  (SELECT COUNT(*) FROM sys.foreign_keys WHERE referenced_object_id=OBJECT_ID('dbo.ENCOUNTER') AND (is_disabled=1 OR is_not_trusted=1)) AS untrusted_fks,
  (SELECT COUNT(*) FROM dbo.ENCOUNTER) AS total_now,
  (SELECT COUNT(*) FROM dbo.ENCOUNTER WHERE CDW_Source='DQATEST') AS dqatest_now,
  (SELECT COUNT(*) FROM dbo.ENCOUNTER WHERE CDW_Source IN ('EPIC','ALLSCRIPTS')) AS loaded_now;
GO
