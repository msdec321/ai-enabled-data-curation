/******************************************************************************
  02_cpe_as_view.sql
  Recreate the missing CDWStaging.dbo.CompletedPatientEncounter_AS view.

  load_demographic's cohort step pulls Allscripts-side completed-encounter keys:
      SELECT DISTINCT UID FROM <cdwstaging>.CompletedPatientEncounter_AS WHERE UID IS NOT NULL
  The view is referenced by the proc but its definition exists nowhere in the
  ETL repo, so the proc errors before building the cohort. This reconstructs it.

  Faithful shape: the Allscripts analog of dbo.CompletedPatientEncounter_Clarity
  (Epic). Epic keys on PAT_ID; Allscripts keys on UID and its encounters live in
  dbo.Appointments (same source dbo.ArrivedAppointment reads). "Completed" mirrors
  the Clarity view's exclusion of non-attended statuses (cancelled/no-show/scheduled).

  Note: Appointments.Uid is varchar(20); the proc converts it to int for the
  cohort temp table, so stored UIDs must be numeric strings.
******************************************************************************/
USE CDWStaging;
GO

CREATE OR ALTER VIEW dbo.CompletedPatientEncounter_AS AS
SELECT
    a.Uid                  AS UID,
    a.PatientID,
    a.EncounterID,
    a.AppointmentID,
    a.AppointmentTime,
    a.AppointmentTypeName,
    a.DepartmentName,
    a.LocationName,
    a.ResourceName
FROM dbo.Appointments a
WHERE a.Uid IS NOT NULL
  AND (a.AppointmentStatusName IS NULL
       OR a.AppointmentStatusName NOT IN ('Cancelled', 'No Show', 'Scheduled', 'Left Without Being Seen'));
GO
