# ENCOUNTER

One row per encounter. ENC_TYPE valueset: AV (ambulatory), ED (emergency),
IP (inpatient), TH (telehealth). DISCHARGE_DATE must be >= ADMIT_DATE.
ED encounters from the pre-2019 tracking board are bridged through the
legacy `ed_visit_xfer` interface table.
