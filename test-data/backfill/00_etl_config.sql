/******************************************************************************
  00_etl_config.sql
  Deploy the ETL source-resolution config that the refactored load procs need.

  The refactored procs (load_demographic, load_encounter_selectors,
  load_dispensing, refresh_encounter_mapping) resolve their source server/db/
  schema via etl.get_config(N'source.<src>.<part>'). The setup script that
  creates etl.config + etl.get_config (referenced in their headers as
  01_etl_config_setup.sql) was never deployed to this synthetic instance, so
  the procs fail to bind. This recreates a minimal, faithful version.

  Seeded for THIS single-instance synthetic box: every source is local
  (server = '' -> no linked server -> local [db].[schema]. naming):
    mpi              -> MasterPatientIndex.dbo   (EnterpriseRecords[_Ext])
    cdwstaging       -> CDWStaging.dbo           (ArrivedAppointment, CPE_* views)
    cdwstaging_clarity -> CDWStaging.clarity      (PAT_ENC, PATIENT, ... base tables)

  Idempotent: table created once, function CREATE OR ALTER, seed via MERGE.
******************************************************************************/
USE CDW;
GO

/* -- 1. Config table -------------------------------------------------------- */
IF OBJECT_ID('etl.config') IS NULL
BEGIN
    CREATE TABLE etl.config (
        name  nvarchar(128) NOT NULL CONSTRAINT pk_etl_config PRIMARY KEY,
        value nvarchar(512) NULL
    );
END
GO

/* -- 2. Accessor function (returns value, or NULL if key absent) ------------ */
CREATE OR ALTER FUNCTION etl.get_config (@name nvarchar(128))
RETURNS nvarchar(512)
AS
BEGIN
    DECLARE @v nvarchar(512);
    SELECT @v = value FROM etl.config WHERE name = @name;
    RETURN @v;
END
GO

/* -- 3. Seed local source pointers ------------------------------------------ */
MERGE etl.config AS tgt
USING (VALUES
    (N'source.mpi.server',                N''),
    (N'source.mpi.database',              N'MasterPatientIndex'),
    (N'source.mpi.schema',                N'dbo'),
    (N'source.cdwstaging.server',         N''),
    (N'source.cdwstaging.database',       N'CDWStaging'),
    (N'source.cdwstaging.schema',         N'dbo'),
    (N'source.cdwstaging_clarity.server',   N''),
    (N'source.cdwstaging_clarity.database', N'CDWStaging'),
    (N'source.cdwstaging_clarity.schema',   N'clarity')
) AS src(name, value)
ON tgt.name = src.name
WHEN MATCHED THEN UPDATE SET tgt.value = src.value
WHEN NOT MATCHED THEN INSERT (name, value) VALUES (src.name, src.value);
GO

/* -- 4. Report -------------------------------------------------------------- */
SELECT name, '['+value+']' AS value FROM etl.config WHERE name LIKE 'source.%' ORDER BY name;
GO
