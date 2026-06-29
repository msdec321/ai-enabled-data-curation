/******************************************************************************
  08_encounter_as_extend.sql  (ENCOUNTER back-population — step 4: extend AS view)

  Selective fidelity: make 4 categorical valueset columns traceable from source
  instead of the view's hardcodes:
      DISCHARGE_DISPOSITION  (was NULL on the AS branch)
      DISCHARGE_STATUS       (was 'UN')
      DRG_TYPE               (was 'NI')
      ADMITTING_SOURCE       (was 'UN')

  Mechanism: carry the CDM code on new EncounterIndex_AS columns (back-populated
  from the datamart) and have etl.ENCOUNTER_AS pass them through (1-hop lineage:
  CDW.ENCOUNTER.<col> <- CDWStaging.dbo.EncounterIndex_AS.<col>). The other
  hardcoded columns (DRG code, FACILITY*, payer, DISCHARGE_DATE) are left as-is
  per the agreed selective scope. Local 3-part names only.
******************************************************************************/

/* ===== 1. Add the 4 source columns to EncounterIndex_AS + back-populate ===== */
USE CDWStaging;
GO
SET NOCOUNT ON;

IF COL_LENGTH('dbo.EncounterIndex_AS', 'DISCHARGE_STATUS') IS NULL
    ALTER TABLE dbo.EncounterIndex_AS
        ADD DISCHARGE_DISPOSITION varchar(2) NULL,
            DISCHARGE_STATUS      varchar(2) NULL,
            DRG_TYPE              varchar(2) NULL,
            ADMITTING_SOURCE      varchar(2) NULL;
GO

UPDATE eidx
SET DISCHARGE_DISPOSITION = d.DISCHARGE_DISPOSITION,
    DISCHARGE_STATUS      = d.DISCHARGE_STATUS,
    DRG_TYPE              = d.DRG_TYPE,
    ADMITTING_SOURCE      = d.ADMITTING_SOURCE
FROM dbo.EncounterIndex_AS eidx
JOIN CDW.dbo.ENCOUNTER d
    ON CAST(SUBSTRING(d.ENCOUNTERID, 4, 10) AS int) = eidx.EncounterID
   AND d.CDW_Source = 'ALLSCRIPTS';
GO

/* ===== 2. Re-map etl.ENCOUNTER_AS to read the 4 columns from source ===== */
USE CDW;
GO
CREATE OR ALTER VIEW [etl].[ENCOUNTER_AS] AS (
select
	  cast(PATID as varchar(700)) as PATID
      ,cast(ENCOUNTERID as varchar(700)) as ENCOUNTERID
      ,cast(ADMIT_DATE as date) ADMIT_DATE
      ,etl.fnTimeString(ADMIT_DATE) ADMIT_TIME
      ,cast(COALESCE(DISCHARGE_DATE,
				case when ENC_TYPE in ('IP','ED','EI') then DATEADD(day, 1, cast(ADMIT_DATE as date))
					 else ADMIT_DATE end) as date) as DISCHARGE_DATE
      ,etl.fnTimeString(COALESCE(DISCHARGE_DATE,
				case when ENC_TYPE in ('IP','ED','EI') then DATEADD(day, 1, cast(ADMIT_DATE as date))
					 else ADMIT_DATE end)) as DISCHARGE_TIME
	  ,cast(NULLIF(PROVIDERID,'NOPROVID') as varchar(700)) PROVIDERID
      ,cast(null as varchar(max)) FACILITY_LOCATION
	  ,CASE WHEN ENC_TYPE IS NULL THEN 'OT' else ENC_TYPE END as ENC_TYPE
      ,cast(NULL as varchar(max)) as FACILITYID
      ,DISCHARGE_DISPOSITION                              -- now sourced (AS branch)
      ,cast(DISCHARGE_STATUS as varchar(2)) DISCHARGE_STATUS   -- was 'UN'
      ,cast(null as varchar(max)) DRG
      ,cast(DRG_TYPE as varchar(2)) DRG_TYPE                   -- was 'NI'
      ,cast(ADMITTING_SOURCE as varchar(2)) ADMITTING_SOURCE   -- was 'UN'
      ,RAW_SITEID
      ,RAW_ENC_TYPE
      ,cast(null as varchar(max)) RAW_DISCHARGE_DISPOSITION
      ,cast(null as varchar(max)) RAW_DISCHARGE_STATUS
      ,cast(null as varchar(max)) RAW_DRG_TYPE
      ,cast(null as varchar(max)) RAW_ADMITTING_SOURCE
	  ,RAW_LOCATION_TYPE
	  ,cast(RAW_PROVIDER as varchar) as RAW_PROVIDER,
		cast(null as varchar(max)) FACILITY_TYPE,
		cast(null as varchar(max)) PAYER_TYPE_PRIMARY,
		cast(null as varchar(max)) PAYER_TYPE_SECONDARY,
		cast(null as varchar(max)) RAW_FACILITY_TYPE,
		cast(null as varchar(max)) RAW_PAYER_ID_PRIMARY,
		cast(null as varchar(max)) RAW_PAYER_ID_SECONDARY,
		cast(null as varchar(max)) RAW_PAYER_NAME_PRIMARY,
		cast(null as varchar(max)) RAW_PAYER_NAME_SECONDARY,
		cast(null as varchar(max)) RAW_PAYER_TYPE_PRIMARY,
		cast(null as varchar(max)) RAW_PAYER_TYPE_SECONDARY,
		CDW_Source
from (
	-- UTPhysicians EHR (Allscripts)
	select
		EncMap.PATID,
		EncMap.ENCOUNTERID,
		E.EncounterDate as ADMIT_DATE,
		E.EncounterDate as DISCHARGE_DATE,
		E.DISCHARGE_DISPOSITION as DISCHARGE_DISPOSITION,   -- was NULL
		cast(E.ProviderID as varchar) RAW_PROVIDER,
		ISNULL(cast(prov.PROVIDERID as varchar(700)),cast('NOPROVID' as varchar(700))) PROVIDERID,
		'OUTPATIENT' RAW_LOCATION_TYPE,
		E.LocationType RAW_ENC_TYPE,
		epe.PCORNET_Code as ENC_TYPE,
		cast(V.InterfaceSourceID as varchar(500)) RAW_SITEID,
		NULL RAW_VISITID,
		E.DISCHARGE_STATUS as DISCHARGE_STATUS,             -- NEW (sourced)
		E.DRG_TYPE as DRG_TYPE,                             -- NEW (sourced)
		E.ADMITTING_SOURCE as ADMITTING_SOURCE,             -- NEW (sourced)
		'ALLSCRIPTS' as CDW_Source
	FROM etl.ENCOUNTER_SELECTOR_AS EncMap
	inner join CDWStaging.dbo.EncounterIndex_AS E WITH(NOLOCK)
		on E.EncounterID = EncMap.RAW_EncounterId
	inner join CDWStaging.allscripts.Encounter E_AS
		on E_AS.ID=E.EncounterID
	left join CDWStaging.allscripts.Visit V
		on V.ID = E_AS.VisitID
	left join CDWStaging.dbo.ProviderIndex prov WITH(NOLOCK)
		on prov.ProviderKey = E.ProviderID and prov.ProviderSource='ALLSCRIPTS'
	left join (	select distinct c.SourceName, c.CDMValue as PCORNET_Code
				from dbo.CDW_COLUMN_MAP as c with(nolock)
				where c.CDMColumn = 'ENC_TYPE' and c.Source IN ( 'ALLSCRIPTS') and CDMTable='ENCOUNTER'
			  ) epe on epe.SourceName = LTRIM(RTRIM(E.LocationType))
	where
		EncMap.PATID in (select distinct PM.PATID from dbo.Patient_Map PM WITH(NOLOCK))

	UNION ALL

	-- GECBI (billing data) — empty in this environment
	select
		EncMap.PATID
		,cast(ENCOUNTERID as varchar(700)) as ENCOUNTERID   -- GE selector's EncounterID is int; cast so UNION ALL stays varchar
		,ADMIT_DATE
		,DischargeDate as DISCHARGE_DATE
		,CASE WHEN ddm.Deceased IS NULL THEN 'A' ELSE 'E' END DISCHARGE_DISPOSITION
		,cast(EncMap.ProviderID as varchar) as RAW_PROVIDER
		,ISNULL(cast(prov.PROVIDERID as varchar),'NOPROVID') as PROVIDERID
		,LocationType as RAW_LOCATION_TYPE
		,LocationType as RAW_ENC_TYPE
		,ISNULL(epe.PCORNET_Code, 'UN') as ENC_TYPE
		,cast(NULL as varchar(max)) RAW_SITEID
		,cast(NULL as varchar(max)) RAW_VISITID
		,cast(null as varchar(2)) as DISCHARGE_STATUS       -- NEW (GECBI: default)
		,cast(null as varchar(2)) as DRG_TYPE               -- NEW
		,cast(null as varchar(2)) as ADMITTING_SOURCE       -- NEW
		,'GECBI' as CDW_Source
	from etl.ENCOUNTER_SELECTOR_GE EncMap WITH(NOLOCK)
	left join CDWStaging.dbo.ProviderIndex prov WITH(NOLOCK)
		on prov.ProviderKey = EncMap.ProviderID and prov.ProviderSource='GECBI'
	LEFT join dbo.DISCHARGED_DECEASED_MHH ddm ON ddm.OTHER_PATID = EncMap.PATID and CAST(ddm.EVENT_END_DT_TM as date) between EncMap.ADMIT_DATE and EncMap.DISCHARGEDATE
	left join (	select distinct c.SourceName, c.CDMValue as PCORNET_Code
				from dbo.CDW_COLUMN_MAP as c with(nolock)
				where c.CDMColumn = 'ENC_TYPE' and c.Source IN ( 'GECBI') and CDMTable='ENCOUNTER'
			  ) epe on epe.SourceName = EncMap.LOCATIONTYPE
	where
		EncMap.PATID in (select distinct PM.PATID from dbo.Patient_Map PM WITH(NOLOCK))
) T
);
GO
