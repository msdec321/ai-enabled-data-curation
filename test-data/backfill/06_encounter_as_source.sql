/******************************************************************************
  06_encounter_as_source.sql   (ENCOUNTER back-population — step 2: AS source tbls)

  Populates the CDWStaging source tables the Allscripts encounter path reads,
  keyed off the RAW_EncounterId minted in step 1 (= digits of ENCOUNTERID).
  etl.ENCOUNTER_AS resolves an AS encounter as:
     ENCOUNTER_SELECTOR_AS  (RAW_EncounterId)
       INNER JOIN dbo.EncounterIndex_AS  E    (E.EncounterID = RAW_EncounterId)
       INNER JOIN allscripts.Encounter   E_AS (E_AS.ID = E.EncounterID)   <-- inner: must exist
       LEFT  JOIN allscripts.Visit       V    (V.ID = E_AS.VisitID)       --> RAW_SITEID
       LEFT  JOIN dbo.ProviderIndex      prov (prov.ProviderKey = E.ProviderID
                                               AND prov.ProviderSource='ALLSCRIPTS') --> PROVIDERID

  Reverse-mapping (datamart -> source):
    EncounterIndex_AS.EncounterID  <- digits of ENCOUNTERID  (int; = RAW_EncounterId)
    EncounterIndex_AS.EncounterDate<- ADMIT_DATE
    EncounterIndex_AS.ProviderID   <- digits of PROVIDERID   (int provider key)
    EncounterIndex_AS.LocationType <- ENC_TYPE (CDM code; identity CDW_COLUMN_MAP rows added step 3,
                                      so the view maps it back to the same code, and RAW_ENC_TYPE=LocationType)
    ProviderIndex (ALLSCRIPTS)     <- one row per distinct AS PROVIDERID (ProviderKey=digits, PROVIDERID=PROV#####)
    allscripts.Encounter           <- one row per AS encounter (ID=VisitID=EncounterID)
    allscripts.Visit               <- one row per AS encounter (InterfaceSourceID const)

  Local 3-part names only. Idempotent (delete-our-rows then reinsert).
******************************************************************************/
USE CDWStaging;
GO
SET NOCOUNT ON;

/* -- 1. ProviderIndex: ALLSCRIPTS provider crosswalk ------------------------ */
DELETE FROM dbo.ProviderIndex WHERE ProviderSource = 'ALLSCRIPTS';
INSERT INTO dbo.ProviderIndex (PROVIDERID, ProviderKey, ProviderSource)
SELECT DISTINCT
    e.PROVIDERID,
    CAST(CAST(SUBSTRING(e.PROVIDERID, 5, 6) AS int) AS varchar(32)) AS ProviderKey,
    'ALLSCRIPTS'
FROM CDW.dbo.ENCOUNTER e
WHERE e.CDW_Source = 'ALLSCRIPTS' AND e.PROVIDERID LIKE 'PROV[0-9]%';

/* -- 2. EncounterIndex_AS: one row per AS encounter ------------------------- */
DELETE FROM dbo.EncounterIndex_AS;
INSERT INTO dbo.EncounterIndex_AS (EncounterID, EncounterDate, ProviderID, LocationType)
SELECT
    CAST(SUBSTRING(e.ENCOUNTERID, 4, 10) AS int) AS EncounterID,
    -- EncounterDate carries date + time so the view reproduces ADMIT_DATE and
    -- ADMIT_TIME (= fnTimeString(EncounterDate) = 'HH:MM')
    CASE WHEN ISNULL(e.ADMIT_TIME,'')='' THEN CAST(e.ADMIT_DATE AS datetime)
         ELSE CAST(CONVERT(varchar(10), e.ADMIT_DATE, 23) + ' ' + e.ADMIT_TIME AS datetime) END AS EncounterDate,
    CAST(SUBSTRING(e.PROVIDERID, 5, 6) AS int)   AS ProviderID,
    e.ENC_TYPE                                   AS LocationType
FROM CDW.dbo.ENCOUNTER e
WHERE e.CDW_Source = 'ALLSCRIPTS' AND e.ENCOUNTERID LIKE 'ENC[0-9]%';

/* -- 3. allscripts.Encounter (INNER JOIN target — must exist per encounter) - */
DELETE FROM allscripts.Encounter;
INSERT INTO allscripts.Encounter (ID, VisitID, EncounterDate)
SELECT
    CAST(SUBSTRING(e.ENCOUNTERID, 4, 10) AS int),
    CAST(SUBSTRING(e.ENCOUNTERID, 4, 10) AS int),   -- VisitID = ID (1 visit per encounter)
    CASE WHEN ISNULL(e.ADMIT_TIME,'')='' THEN CAST(e.ADMIT_DATE AS datetime)
         ELSE CAST(CONVERT(varchar(10), e.ADMIT_DATE, 23) + ' ' + e.ADMIT_TIME AS datetime) END
FROM CDW.dbo.ENCOUNTER e
WHERE e.CDW_Source = 'ALLSCRIPTS' AND e.ENCOUNTERID LIKE 'ENC[0-9]%';

/* -- 4. allscripts.Visit (LEFT JOIN; supplies RAW_SITEID) ------------------- */
DELETE FROM allscripts.Visit;
INSERT INTO allscripts.Visit (ID, InterfaceSourceID)
SELECT CAST(SUBSTRING(e.ENCOUNTERID, 4, 10) AS int), 1
FROM CDW.dbo.ENCOUNTER e
WHERE e.CDW_Source = 'ALLSCRIPTS' AND e.ENCOUNTERID LIKE 'ENC[0-9]%';

SELECT
    (SELECT COUNT(*) FROM dbo.ProviderIndex WHERE ProviderSource='ALLSCRIPTS') AS providerindex_as,
    (SELECT COUNT(*) FROM dbo.EncounterIndex_AS)  AS encounterindex_as,
    (SELECT COUNT(*) FROM allscripts.Encounter)   AS allscripts_encounter,
    (SELECT COUNT(*) FROM allscripts.Visit)       AS allscripts_visit;
GO
