/******************************************************************************
  05_encounter_crosswalks.sql   (ENCOUNTER back-population — step 1: crosswalks)

  Populates the two CDW crosswalk tables the Allscripts encounter path needs:
    dbo.Patient_Map    - patient inclusion filter (ENCOUNTER_AS keeps AS encounters
                         only where PATID IN (SELECT PATID FROM Patient_Map))
    dbo.Encounter_Map  - source->CDM encounter id map; load_encounter_selectors
                         builds ENCOUNTER_SELECTOR_AS from rows where
                         ENCOUNTERID_SOURCE='EHR', joining RAW_EncounterId to
                         CDWStaging.dbo.EncounterIndex_AS.EncounterID.

  Reverse-mapping (CDW datamart is ground truth):
    Patient_Map.PATID/UID/PatientID  <- DEMOGRAPHIC (UID = digits of PATID, as in MPI)
    Encounter_Map.ENCOUNTERID        <- the AS encounter's CDM ENCOUNTERID
    Encounter_Map.RAW_EncounterId    <- digits of ENCOUNTERID (matches the int
                                        EncounterIndex_AS.EncounterID we mint in step 2)
    Encounter_Map.ENCOUNTERID_SOURCE = 'EHR' (the Allscripts EHR selector branch)

  Idempotent: tables are ours/empty staging; delete-all then reinsert.
  All source references are local 3-part names (no linked server).
******************************************************************************/
USE CDW;
GO
SET NOCOUNT ON;

/* -- Patient_Map: all real patients (superset of the 9,542 AS-encounter patients) -- */
DELETE FROM dbo.Patient_Map;
INSERT INTO dbo.Patient_Map (PATID, UID, PatientID, EpicOnly, Deleted)
SELECT
    d.PATID,
    CAST(SUBSTRING(d.PATID, 4, 7) AS int)  AS UID,
    CAST(SUBSTRING(d.PATID, 4, 7) AS int)  AS PatientID,
    0                                      AS EpicOnly,
    0                                      AS Deleted
FROM dbo.DEMOGRAPHIC d
WHERE d.CDW_Source <> 'DQATEST'
  AND d.PATID LIKE 'PAT[0-9][0-9][0-9][0-9][0-9][0-9][0-9]';

/* -- Encounter_Map: one row per ALLSCRIPTS datamart encounter --------------- */
DELETE FROM dbo.Encounter_Map;
INSERT INTO dbo.Encounter_Map (PATID, ENCOUNTERID, RAW_EncounterId, ENCOUNTERID_SOURCE)
SELECT
    e.PATID,
    e.ENCOUNTERID,
    CAST(CAST(SUBSTRING(e.ENCOUNTERID, 4, 10) AS int) AS varchar(50)) AS RAW_EncounterId,
    'EHR' AS ENCOUNTERID_SOURCE
FROM dbo.ENCOUNTER e
WHERE e.CDW_Source = 'ALLSCRIPTS'
  AND e.ENCOUNTERID LIKE 'ENC[0-9]%';

SELECT
    (SELECT COUNT(*) FROM dbo.Patient_Map)   AS patient_map_rows,
    (SELECT COUNT(*) FROM dbo.Encounter_Map) AS encounter_map_rows;
GO
