/******************************************************************************
  12_fix_encounter_selectors.sql (ENCOUNTER back-population — step 8: selector fix)

  The DEPLOYED etl.load_encounter_selectors (66->137-line local version, != repo)
  ends with an UNCONDITIONAL hotfix:
        update etl.ENCOUNTER_SELECTOR_EPIC Set CDM_PATID = PATIENT_NUM
  which overwrites our reconstructed CDM_PATID ('PAT#####') with the numeric
  PATIENT_NUM, so the EPIC encounters no longer join etl.CDW_PATIENT_COHORT and
  drop out of the load. Make the hotfix CONDITIONAL (only the old >12-char
  charstring PATIDs), matching refresh_encounter_mapping's step-7 intent.
  Full deployed proc reproduced verbatim except the final UPDATE.
******************************************************************************/
USE CDW;
GO
CREATE OR ALTER procedure [etl].[load_encounter_selectors]
as
begin
	if exists (select * from sys.tables where name='ENCOUNTER_SELECTOR_EPIC')
		drop table etl.ENCOUNTER_SELECTOR_EPIC;
	SELECT [ENCOUNTER_NUM]
		  ,[PATIENT_NUM]
		  ,[PATIENT_IDE]
		  ,[CDM_PATID]
		  ,[EPIC_PAT_ID]
		  ,[ENCOUNTER_IDE]
		  ,[HOSP_ADMSN_TIME]
		  ,[HOSP_DISCHRG_TIME]
		  ,[PROVIDER_ID]
		  ,[CONTACT_DATE]
		  ,[CDM_ENCOUNTERID]
		  ,[SRC_PROVIDER_ID]
	into etl.ENCOUNTER_SELECTOR_EPIC
	FROM [etl].[EPIC_ENCOUNTER_MAPPING] E
	where not exists (
		select top 1 1 from dbo.ENCOUNTER E2 with(nolock)
		where E2.ENCOUNTERID=E.CDM_ENCOUNTERID
	)

	CREATE CLUSTERED INDEX [ix_encmap_selector] ON [etl].ENCOUNTER_SELECTOR_EPIC
	(
		[CDM_ENCOUNTERID] ASC, [CDM_PATID] ASC, [ENCOUNTER_IDE] ASC, [PATIENT_IDE] ASC,
		[EPIC_PAT_ID] ASC, [HOSP_ADMSN_TIME] ASC, [CONTACT_DATE] ASC, [PROVIDER_ID] ASC, [SRC_PROVIDER_ID] ASC
	)
	CREATE NONCLUSTERED INDEX [ix_encmap_encid_to_cdm] ON [etl].ENCOUNTER_SELECTOR_EPIC ([ENCOUNTER_IDE] ASC)
	INCLUDE([CDM_PATID],[HOSP_DISCHRG_TIME],[PROVIDER_ID],[CDM_ENCOUNTERID])

	if exists (select * from sys.tables where name='ENCOUNTER_SELECTOR_AS')
		drop table etl.ENCOUNTER_SELECTOR_AS;
	select PATID, EncMap.ENCOUNTERID, ENCOUNTERID_SOURCE, RAW_ENCOUNTERID
	into etl.ENCOUNTER_SELECTOR_AS
	from dbo.Encounter_Map EncMap WITH(NOLOCK)
	inner join CDWStaging.dbo.EncounterIndex_AS E WITH(NOLOCK) on E.EncounterID = EncMap.RAW_EncounterId
	where EncMap.ENCOUNTERID_SOURCE='EHR'
	and not exists (
			select top 1 1 from dbo.ENCOUNTER E2 with(nolock)
			where E2.ENCOUNTERID=EncMap.ENCOUNTERID and CAST(E.EncounterDate as DATE)=E2.ADMIT_DATE
		)
	create clustered index ix_encmap_selector on etl.ENCOUNTER_SELECTOR_AS (PATID, ENCOUNTERID, ENCOUNTERID_SOURCE, RAW_ENCOUNTERID);

	if not exists (select * from CDWStaging.sys.indexes where name='ix_encindexge_selector')
		create index ix_encindexge_selector on CDWStaging.dbo.EncounterIndex_GECBI (
			INV_NUM, ENCOUNTER_IDE, SERVICEDATE, ADMITDATE, DISCHARGEDATE, LOCATIONTYPE, PATIENT_IDE, PROVIDERID)

	if exists (select * from sys.tables where name='ENCOUNTER_MAPPING_GE')
		drop table etl.ENCOUNTER_MAPPING_GE;
	select distinct
		cast(PATIENT_NUM as varchar(700)) AS PATID, cast(ENCOUNTER_IDE as varchar(700)) as ENCOUNTERID,
		coalesce(cast(EncMap.ADMITDATE as date), cast(EncMap.ServiceDate as date)) as ADMIT_DATE,
		ADMITDATE, SERVICEDATE, DISCHARGEDATE, PROVIDERID, cast(INV_NUM as VARCHAR(20)) as RAW_ENCOUNTERID, LOCATIONTYPE
	into etl.ENCOUNTER_MAPPING_GE
	from CDWStaging.dbo.EncounterIndex_GECBI EncMap WITH(NOLOCK)
	create clustered index ix_encmap_selector on etl.ENCOUNTER_MAPPING_GE (PATID, ENCOUNTERID, ADMITDATE, SERVICEDATE, DISCHARGEDATE, PROVIDERID, RAW_ENCOUNTERID, LOCATIONTYPE);

	if exists (select * from sys.tables where name='ENCOUNTER_SELECTOR_GE')
		drop table etl.ENCOUNTER_SELECTOR_GE;
	select distinct
		cast(PATIENT_NUM as varchar(700)) AS PATID, cast(ENCOUNTER_IDE as varchar(700)) as ENCOUNTERID,
		coalesce(cast(EncMap.ADMITDATE as date), cast(EncMap.ServiceDate as date)) as ADMIT_DATE,
		ADMITDATE, SERVICEDATE, DISCHARGEDATE, PROVIDERID, cast(INV_NUM as VARCHAR(20)) as RAW_ENCOUNTERID, LOCATIONTYPE
	into etl.ENCOUNTER_SELECTOR_GE
	from CDWStaging.dbo.EncounterIndex_GECBI EncMap WITH(NOLOCK)
	where not exists (
		select top 1 1 from dbo.ENCOUNTER E2 with(nolock)
		where E2.ENCOUNTERID=cast(EncMap.ENCOUNTER_IDE as varchar(700))
			and coalesce(cast(EncMap.ADMITDATE as date), cast(EncMap.ServiceDate as date))=E2.ADMIT_DATE
	)
	create clustered index ix_encmap_selector on etl.ENCOUNTER_SELECTOR_GE (PATID, ENCOUNTERID, ADMITDATE, SERVICEDATE, DISCHARGEDATE, PROVIDERID, RAW_ENCOUNTERID, LOCATIONTYPE);

	-- Hotfix made CONDITIONAL: only override the old >12-char charstring PATID, not synthetic 'PAT#####'
	update etl.ENCOUNTER_SELECTOR_EPIC Set CDM_PATID = PATIENT_NUM WHERE LEN(CDM_PATID) > 12
end
GO
