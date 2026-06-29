/******************************************************************************
  03_demographic_cohort.sql
  Minimal cohort scaffold so load_demographic includes all 10k patients.

  load_demographic only loads MPI patients who also appear in >=1 CDWStaging
  encounter source. Two of the four cohort paths key on UID (ArrivedAppointment
  and CompletedPatientEncounter_AS, both reading dbo.Appointments), and every
  patient already has a UID. So we give each patient ONE attended appointment.

  SCOPE: this is deliberately temporary scaffolding for the DEMOGRAPHIC milestone.
  It does NOT model real encounters and will be superseded/enriched when ENCOUNTER
  is back-populated. Rows are tagged ApptNumberEXT='DQASCAF<uid>' so they are
  identifiable and this script is idempotent (delete-by-tag then reinsert).

  Note: Appointments.Uid is varchar; we store the int UID as a numeric string so
  the proc's CAST(... AS int) into #aa_uids/#cpeas_uids round-trips and matches
  EnterpriseRecords.UID.
******************************************************************************/
USE CDWStaging;
GO
SET NOCOUNT ON;

/* -- Idempotent: clear any prior scaffold rows ------------------------------ */
DELETE FROM dbo.Appointments WHERE ApptNumberEXT LIKE 'DQASCAF%';

/* -- One attended appointment per real CDW patient -------------------------- */
INSERT INTO dbo.Appointments
    (AppointmentID, AppointmentTime, AppointmentStatusDE, AppointmentStatusName,
     AppointmentTypeDE, AppointmentTypeName, DepartmentName, LocationName, ResourceName,
     EncounterID, ApptNumberEXT, Uid, Mrn, PatientID, DateOfBirth)
SELECT
    CAST(SUBSTRING(d.PATID, 4, 7) AS int)            AS AppointmentID,
    CAST('2023-06-01' AS datetime)                   AS AppointmentTime,
    1                                                AS AppointmentStatusDE,
    'Arrived'                                        AS AppointmentStatusName,  -- passes ArrivedAppointment + CPE_AS
    1                                                AS AppointmentTypeDE,
    'Office Visit'                                   AS AppointmentTypeName,
    'General Medicine'                               AS DepartmentName,
    'Main Clinic'                                    AS LocationName,
    'Provider, Test'                                 AS ResourceName,
    CAST(SUBSTRING(d.PATID, 4, 7) AS int)            AS EncounterID,
    'DQASCAF' + SUBSTRING(d.PATID, 4, 7)             AS ApptNumberEXT,           -- scaffold tag
    CAST(CAST(SUBSTRING(d.PATID, 4, 7) AS int) AS varchar(20)) AS Uid,           -- numeric string
    CAST(CAST(SUBSTRING(d.PATID, 4, 7) AS int) AS varchar(20)) AS Mrn,
    CAST(SUBSTRING(d.PATID, 4, 7) AS int)            AS PatientID,
    d.BIRTH_DATE                                     AS DateOfBirth
FROM CDW.dbo.DEMOGRAPHIC d
WHERE d.CDW_Source <> 'DQATEST'
  AND d.PATID LIKE 'PAT[0-9][0-9][0-9][0-9][0-9][0-9][0-9]';

SELECT COUNT(*) AS appointments_inserted FROM dbo.Appointments WHERE ApptNumberEXT LIKE 'DQASCAF%';
GO
