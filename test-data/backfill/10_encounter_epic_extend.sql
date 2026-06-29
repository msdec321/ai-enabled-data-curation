/******************************************************************************
  10_encounter_epic_extend.sql (ENCOUNTER back-population — step 6: EPIC extend)

  (A) Fix etl.refresh_encounter_mapping so EPIC_ENCOUNTER_MAPPING.CDM_PATID comes
      from etl.EPIC_PATIENT_MAPPING.CDM_PATID ('PAT#####') instead of the int
      EncounterIndex_EPIC.PATIENT_NUM. Two surgical changes to the DEPLOYED proc:
        - add MIN(CDM_PATID) to the PM grouped subquery
        - select PM.CDM_PATID instead of EM.CDM_PATID
  (B) Add the 4 selective columns to clarity.PAT_ENC + back-populate from datamart.
  (C) Extend etl.ENCOUNTER_EPIC to source those 4 columns from PAT_ENC (pe) instead
      of the hardcodes ('OT'/'NI'/''/IIf...). Local 3-part names only.
******************************************************************************/

/* ===== A. refresh_encounter_mapping: source CDM_PATID from EPIC_PATIENT_MAPPING ===== */
USE CDW;
GO
CREATE OR ALTER procedure [etl].[refresh_encounter_mapping]
as
begin
	select distinct
		a.ENCOUNTER_NUM,
		EncounterKey,
		a.ENCOUNTER_IDE,
		a.CDM_ENCOUNTERID,
		ContactDate as CONTACT_DATE,
		a.DEPARTMENT_ID,
		PatientKey,
		a.PATIENT_NUM,
		a.PATIENT_IDE,
		a.PATIENT_NUM as CDM_PATID,
		a.PROVIDER_ID as PROVIDER_ID   -- qualified: unqualified 'ProviderId' matched EM.PROVIDERID (null) instead of a.PROVIDER_ID
	into #newEncounters
	from CDWStaging.dbo.EncounterIndex_EPIC a
	left join etl.epic_encounter_mapping EM with(nolock)
		on a.ENCOUNTER_IDE=EM.ENCOUNTER_IDE
	WHERE EM.ENCOUNTER_IDE is null;

	insert into etl.epic_encounter_mapping ( ENCOUNTER_NUM, PATIENT_NUM, CDM_ENCOUNTERID, ENCOUNTER_IDE, PATIENT_IDE, CDM_PATID, EPIC_PAT_ID, CONTACT_DATE, HOSP_ADMSN_TIME, HOSP_DISCHRG_TIME, PROVIDER_ID, DEPARTMENT_ID )
	select distinct
		EM.ENCOUNTER_NUM,
		PM.PATIENT_NUM,
		EM.CDM_ENCOUNTERID,
		EM.ENCOUNTER_IDE,
		EM.PATIENT_IDE,
		PM.CDM_PATID,                          -- was EM.CDM_PATID (int PATIENT_NUM); now the CDM 'PAT#####'
		PM.EPIC_PAT_ID,
		EM.CONTACT_DATE,
		ISNULL(enc2.HOSP_ADMSN_TIME,enc.HOSP_ADMSN_TIME) as HOSP_ADMSN_TIME,
		enc2.HOSP_DISCH_TIME as HOSP_DISCHRG_TIME,
		EM.PROVIDER_ID,
		EM.DEPARTMENT_ID
	from (select MIN(EPIC_PAT_ID) as EPIC_PAT_ID, MIN(CDM_PATID) as CDM_PATID, PATIENT_IDE, PATIENT_NUM
	      from etl.EPIC_PATIENT_MAPPING PM with(nolock) group by PATIENT_IDE, PATIENT_NUM) as PM
	join #newEncounters EM
		on EM.PATIENT_IDE=PM.PATIENT_IDE
	left join CDWStaging.clarity.PAT_ENC enc with(nolock)
		on enc.PAT_ENC_CSN_ID=EM.EncounterKey
	left join CDWStaging.clarity.PAT_ENC_HSP enc2 WITH(NOLOCK)
		on enc.pat_enc_csn_id = enc2.pat_enc_csn_id

	drop table #newEncounters;

	delete EM
	from etl.EPIC_ENCOUNTER_MAPPING EM
	where not exists (
		select 1 from CDWStaging.dbo.EncounterIndex_EPIC EI where EI.ENCOUNTER_IDE=EM.ENCOUNTER_IDE
	)

	update etl.EPIC_ENCOUNTER_MAPPING
	set CDM_PATID = PATIENT_NUM
	Where len(CDM_PATID) > 12
end
GO

/* ===== B. clarity.PAT_ENC: add 4 selective columns + back-populate ===== */
USE CDWStaging;
GO
SET NOCOUNT ON;
IF COL_LENGTH('clarity.PAT_ENC', 'DISCHARGE_STATUS') IS NULL
    ALTER TABLE clarity.PAT_ENC
        ADD DISCHARGE_DISPOSITION varchar(2) NULL,
            DISCHARGE_STATUS      varchar(2) NULL,
            DRG_TYPE              varchar(2) NULL,
            ADMITTING_SOURCE      varchar(2) NULL;
GO
UPDATE pe
SET DISCHARGE_DISPOSITION = d.DISCHARGE_DISPOSITION,
    DISCHARGE_STATUS      = d.DISCHARGE_STATUS,
    DRG_TYPE              = d.DRG_TYPE,
    ADMITTING_SOURCE      = d.ADMITTING_SOURCE
FROM clarity.PAT_ENC pe
JOIN CDW.dbo.ENCOUNTER d
    ON CAST(SUBSTRING(d.ENCOUNTERID, 4, 10) AS int) = pe.PAT_ENC_CSN_ID
   AND d.CDW_Source = 'EPIC';
GO

/* ===== C. Re-map etl.ENCOUNTER_EPIC to source the 4 columns from PAT_ENC ===== */
USE CDW;
GO
CREATE OR ALTER view [etl].[ENCOUNTER_EPIC] as
select
	cast(em.CDM_PATID as varchar(700)) as PATID,
	cast(em.CDM_ENCOUNTERID as varchar(700)) as ENCOUNTERID,
	cast(ISNULL(em.HOSP_ADMSN_TIME, em.CONTACT_DATE) as date) as ADMIT_DATE,
	etl.fnTimeString(ISNULL(em.HOSP_ADMSN_TIME, em.CONTACT_DATE)) as ADMIT_TIME,
	cast(COALESCE(em.HOSP_DISCHRG_TIME,
				case when epe.PCORNet_CODE in ('IP','ED','EI') then DATEADD(day, 1, cast(ISNULL(em.HOSP_ADMSN_TIME, em.CONTACT_DATE) as date))
					 else ISNULL(em.HOSP_ADMSN_TIME, em.CONTACT_DATE) end) as date) as DISCHARGE_DATE,
	etl.fnTimeString(COALESCE(em.HOSP_DISCHRG_TIME,
				case when epe.PCORNet_CODE in ('IP','ED','EI') then DATEADD(day, 1, cast(ISNULL(em.HOSP_ADMSN_TIME, em.CONTACT_DATE) as date))
					 else ISNULL(em.HOSP_ADMSN_TIME, em.CONTACT_DATE) end)) as DISCHARGE_TIME,
	ISNULL(epe.PCORNet_CODE,'OT') as ENC_TYPE,
	cast(NULLIF(em.provider_id,'NOPROVID') as varchar(700)) as PROVIDERID,
	pe.Department_ID as FACILITYID,
	'' as DRG,
	cast(DISCHARGE_STATUS as varchar(2)) as DISCHARGE_STATUS,          -- was 'OT'
	cast(DISCHARGE_DISPOSITION as varchar(2)) as DISCHARGE_DISPOSITION,-- was IIf(epd_PCORNet_CODE='EX','E','A')
	cast(ADMITTING_SOURCE as varchar(2)) as ADMITTING_SOURCE,          -- was ''
	cast(DRG_TYPE as varchar(2)) as DRG_TYPE,                          -- was 'NI'
	ISNULL(ADDRESS_ZIP_CODE,'') as FACILITY_LOCATION,
	'' as FACILITY_TYPE,
	coalesce(epp_PCORNET_Code, 'OT') as PAYER_TYPE_PRIMARY,
	'' as PAYER_TYPE_SECONDARY,
	ISNULL('','') as RAW_ADMITTING_SOURCE,
	ISNULL(DISCH_DISP_C,'') as RAW_DISCHARGE_DISPOSITION,
	ISNULL(DISCH_DISP_C,'') as RAW_DISCHARGE_STATUS,
	ISNULL('','') as RAW_DRG_TYPE,
	COALESCE(enctype_NAME,
			case when CDM_ENCOUNTERID like 'EPIC:CLARITY:TX:PB:%' then '[TX:PB]'
				when CDM_ENCOUNTERID like 'EPIC:CLARITY:PROBLEM:%' then '[PROBLEM]'
				when CDM_ENCOUNTERID like 'EPIC:CLARITY:IMM:%' then '[IMM]'
			end, '') as RAW_ENC_TYPE,
	ISNULL('','') as RAW_FACILITY_TYPE,
	ISNULL('','') as RAW_PAYER_ID_PRIMARY,
	ISNULL('','') as RAW_PAYER_ID_SECONDARY,
	ISNULL(PAYOR_NAME,'') as RAW_PAYER_NAME_PRIMARY,
	ISNULL('','') as RAW_PAYER_NAME_SECONDARY,
	ISNULL(epp_Description,'') as RAW_PAYER_TYPE_PRIMARY,
	ISNULL('','') as RAW_PAYER_TYPE_SECONDARY,
	ISNULL('EPIC','') as RAW_SITEID,
	'EPIC' as CDW_Source
from etl.ENCOUNTER_SELECTOR_EPIC as em with(nolock)
left join (
	select
		'CSN:'+cast(pe.PAT_ENC_CSN_ID as varchar) as ENCOUNTER_IDE,
		pe.HOSP_ADMSN_TIME,
		pe.CONTACT_DATE,
		pe.HOSP_DISCHRG_TIME,
		pe.Department_ID,
		epd.CDMValue as epd_PCORNET_Code,
		epp.CDMValue as epp_PCORNET_Code,
		DISCH_DISP_C,
		PE.ENC_TYPE_C,
		enctype.NAME as enctype_NAME,
		PP.PAYOR_NAME,
		epp.SourceName as epp_Description,
		LEFT(cd2.ADDRESS_ZIP_CODE,5) AS ADDRESS_ZIP_CODE,
		pe.DISCHARGE_DISPOSITION,          -- NEW (sourced)
		pe.DISCHARGE_STATUS,               -- NEW (sourced)
		pe.DRG_TYPE,                       -- NEW (sourced)
		pe.ADMITTING_SOURCE                -- NEW (sourced)
		from CDWStaging.clarity.pat_enc as pe with(nolock)
		left join CDWStaging.clarity.pat_enc_hsp AS hsp with(nolock)
			ON pe.PAT_ENC_CSN_ID = hsp.PAT_ENC_CSN_ID
		left join dbo.CDW_COLUMN_MAP as epd with(nolock)
			on hsp.DISCH_DISP_C = epd.SourceValue and epd.CDMColumn = 'DISCHARGE_DISPOSITION'
		left join CDWStaging.clarity.ZC_DISP_ENC_TYPE enctype with(nolock)
			on enctype.DISP_ENC_TYPE_C = pe.ENC_TYPE_C
		left join CDWStaging.clarity.V_COVERAGE_PAYOR_PLAN PP with(nolock)
			on PE.COVERAGE_ID=PP.COVERAGE_ID
		left join dbo.CDW_COLUMN_MAP as epp with(nolock)
			on PP.FIN_CLASS_C = epp.SourceValue and epp.CDMColumn = 'PAYER_TYPE_PRIMARY'
		left join CDWStaging.clarity.CLARITY_DEP_2 as cd2 with(nolock)
		    on PE.DEPARTMENT_ID = cd2.DEPARTMENT_ID
		where
			pe.enc_type_c not in (2505,2506)
	) PE
	on em.encounter_ide = pe.encounter_ide
left join (
	select distinct SourceValue as ENC_TYPE_C, CDMValue as PCORNET_Code
	from dbo.CDW_COLUMN_MAP as epe with(nolock)
	where epe.Source='EPIC' and epe.CDMTable='ENCOUNTER' and epe.CDMColumn = 'ENC_TYPE'
) epe
	on epe.ENC_TYPE_C = pe.ENC_TYPE_C;
GO
