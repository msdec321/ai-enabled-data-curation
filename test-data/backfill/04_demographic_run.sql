/******************************************************************************
  04_demographic_run.sql
  Guarded literal run of etl.load_demographic against the real datamart.

  Why guarded: 6 FKs reference dbo.DEMOGRAPHIC.PATID (ENCOUNTER, PRESCRIBING,
  DISPENSING, ENROLLMENT, IMMUNIZATION, OBS_GEN). The already-populated children
  pin the parent, so the real rows can't be cleared/reloaded without disabling
  those FKs first. Because the ETL reproduces the SAME PATIDs, re-enabling the
  FKs WITH CHECK afterwards validates and succeeds.

  Flow: backup -> disable FKs -> delete real rows (keep the 7 DQATEST rows so
  their enc/dx children stay valid) -> EXEC load_demographic -> re-enable FKs.
  Backups (dbo.DEMOGRAPHIC[_UNIQUE]_bak_preETL) allow full restore/compare.
******************************************************************************/
USE CDW;
GO
SET NOCOUNT ON;

/* -- 1. Backup current datamart -------------------------------------------- */
IF OBJECT_ID('dbo.DEMOGRAPHIC_bak_preETL') IS NOT NULL DROP TABLE dbo.DEMOGRAPHIC_bak_preETL;
SELECT * INTO dbo.DEMOGRAPHIC_bak_preETL FROM dbo.DEMOGRAPHIC;
IF OBJECT_ID('dbo.DEMOGRAPHIC_UNIQUE_bak_preETL') IS NOT NULL DROP TABLE dbo.DEMOGRAPHIC_UNIQUE_bak_preETL;
SELECT * INTO dbo.DEMOGRAPHIC_UNIQUE_bak_preETL FROM dbo.DEMOGRAPHIC_UNIQUE;
SELECT COUNT(*) AS backup_demographic_rows FROM dbo.DEMOGRAPHIC_bak_preETL;

/* -- 2. Disable FKs referencing dbo.DEMOGRAPHIC ----------------------------- */
DECLARE @off nvarchar(max) = N'';
SELECT @off = @off + N'ALTER TABLE ' + QUOTENAME(OBJECT_SCHEMA_NAME(parent_object_id)) + N'.'
            + QUOTENAME(OBJECT_NAME(parent_object_id)) + N' NOCHECK CONSTRAINT ' + QUOTENAME(name) + N';' + CHAR(10)
FROM sys.foreign_keys WHERE referenced_object_id = OBJECT_ID('dbo.DEMOGRAPHIC');
EXEC sp_executesql @off;

/* -- 3. Clear real rows (keep DQATEST), then run the ETL -------------------- */
BEGIN TRY
    DELETE FROM dbo.DEMOGRAPHIC WHERE CDW_Source <> 'DQATEST';
    EXEC etl.load_demographic @is_cdw_prod = 0;
    SELECT 'load_demographic completed' AS run_status;
END TRY
BEGIN CATCH
    SELECT 'PROC FAILED: ' + ERROR_MESSAGE() AS run_status;
END CATCH;

/* -- 4. Re-enable FKs WITH CHECK (validates referential integrity) ---------- */
DECLARE @on nvarchar(max) = N'';
SELECT @on = @on + N'ALTER TABLE ' + QUOTENAME(OBJECT_SCHEMA_NAME(parent_object_id)) + N'.'
           + QUOTENAME(OBJECT_NAME(parent_object_id)) + N' WITH CHECK CHECK CONSTRAINT ' + QUOTENAME(name) + N';' + CHAR(10)
FROM sys.foreign_keys WHERE referenced_object_id = OBJECT_ID('dbo.DEMOGRAPHIC');
EXEC sp_executesql @on;

/* -- 5. Confirm no FK left untrusted --------------------------------------- */
SELECT COUNT(*) AS untrusted_fks_remaining
FROM sys.foreign_keys
WHERE referenced_object_id = OBJECT_ID('dbo.DEMOGRAPHIC') AND (is_disabled = 1 OR is_not_trusted = 1);
GO
