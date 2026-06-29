/******************************************************************************
  01_demographic_mpi.sql
  Reverse-ETL back-population: CDW.dbo.DEMOGRAPHIC  -->  MasterPatientIndex

  Goal: regenerate the MPI source rows (EnterpriseRecords + EnterpriseRecords_Ext)
  so that the etl.DEMOGRAPHIC view (and load_demographic proc) reproduce the
  existing, populated CDW.dbo.DEMOGRAPHIC datamart.

  Direction agreed 2026-06-24: CDW datamart is ground truth; the pre-existing,
  unrelated MPI seed (0 ID overlap) is BACKED UP then OVERWRITTEN.

  Mapping (from view etl.DEMOGRAPHIC):
    PATID      = ISNULL(ERX.CDM_PATID, ER.UID)      <- ERX.CDM_PATID = PATID
    SEX        = ER.G when in (M,F) else CDM_SEX     <- ER.G = SEX (real data is M/F only)
    RACE       = COALESCE(ERX.CDM_RACE,'NI')         <- ERX.CDM_RACE = RACE
    HISPANIC   = COALESCE(ERX.CDM_HISPANIC,'NI')     <- ERX.CDM_HISPANIC = HISPANIC
    BIRTH_DATE = CAST(ER.DoB AS date)                <- ER.DoB = BIRTH_DATE
  UID is minted deterministically from PATID ('PAT' + 7 digits) so it is stable
  and reversible. Identity/lineage cols (EPIC_PAT_ID, names, addr) are left NULL
  here to match the current sparse datamart; the cohort step adds EPIC_PAT_ID.

  Idempotent: re-running rebuilds ER/ERX from CDW; backup is taken only once.
******************************************************************************/
SET NOCOUNT ON;

/* -- 1. One-time backup of the pre-existing (unrelated) MPI seed ------------- */
IF OBJECT_ID('MasterPatientIndex.dbo.EnterpriseRecords_bak_predemo') IS NULL
    SELECT * INTO MasterPatientIndex.dbo.EnterpriseRecords_bak_predemo
    FROM MasterPatientIndex.dbo.EnterpriseRecords;

IF OBJECT_ID('MasterPatientIndex.dbo.EnterpriseRecords_Ext_bak_predemo') IS NULL
    SELECT * INTO MasterPatientIndex.dbo.EnterpriseRecords_Ext_bak_predemo
    FROM MasterPatientIndex.dbo.EnterpriseRecords_Ext;

/* -- 2. Clear current MPI rows (no FKs on these tables) ---------------------- */
DELETE FROM MasterPatientIndex.dbo.EnterpriseRecords_Ext;
DELETE FROM MasterPatientIndex.dbo.EnterpriseRecords;

/* -- 3. Rebuild EnterpriseRecords (raw identity/demographics) ---------------- */
INSERT INTO MasterPatientIndex.dbo.EnterpriseRecords
    (Uid, DoB, G, LastEditDTTM, isActive)
SELECT
    CAST(SUBSTRING(d.PATID, 4, 7) AS int)            AS Uid,
    d.BIRTH_DATE                                     AS DoB,
    CASE WHEN d.SEX IN ('M','F') THEN d.SEX END      AS G,   -- raw sex; non-M/F -> via CDM_SEX
    GETDATE()                                        AS LastEditDTTM,
    1                                                AS isActive
FROM CDW.dbo.DEMOGRAPHIC d
WHERE d.CDW_Source <> 'DQATEST'
  AND d.PATID LIKE 'PAT[0-9][0-9][0-9][0-9][0-9][0-9][0-9]';

/* -- 4. Rebuild EnterpriseRecords_Ext (CDM-coded demographics + crosswalk) --- */
INSERT INTO MasterPatientIndex.dbo.EnterpriseRecords_Ext
    (Uid, MPIUpdateDTTM, CDM_PATID, CDM_SEX, CDM_RACE, CDM_HISPANIC, EpicOnly)
SELECT
    CAST(SUBSTRING(d.PATID, 4, 7) AS int)            AS Uid,
    GETDATE()                                        AS MPIUpdateDTTM,
    d.PATID                                          AS CDM_PATID,
    d.SEX                                            AS CDM_SEX,
    d.RACE                                           AS CDM_RACE,
    d.HISPANIC                                       AS CDM_HISPANIC,
    CASE WHEN d.CDW_Source = 'EPIC' THEN 'Y' ELSE 'N' END AS EpicOnly
FROM CDW.dbo.DEMOGRAPHIC d
WHERE d.CDW_Source <> 'DQATEST'
  AND d.PATID LIKE 'PAT[0-9][0-9][0-9][0-9][0-9][0-9][0-9]';

/* -- 5. Report -------------------------------------------------------------- */
SELECT
    (SELECT COUNT(*) FROM MasterPatientIndex.dbo.EnterpriseRecords)     AS er_rows,
    (SELECT COUNT(*) FROM MasterPatientIndex.dbo.EnterpriseRecords_Ext) AS erx_rows;
GO
