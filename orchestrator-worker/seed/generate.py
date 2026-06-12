#!/usr/bin/env python3
"""Generate the synthetic Tier-1 demo dataset (seed/data.sql) and the synthetic
ETL codebase (etl-files/**) for the AutoDQA orchestrator PoC.

Deterministic (fixed seed). Four planted data-quality issues, each with a
matching root cause in the ETL files:

  1. DEMOGRAPHIC.SEX invalid values 'X'/'U' — only SOURCE=ALLSCRIPTS.
     Root cause: etl.DEMOGRAPHIC_ALLSCRIPTS.View.sql passes gender_code
     through raw instead of calling etl.fnMapSex (the EPIC view does it right).
  2. DEMOGRAPHIC.BIRTH_DATE NULL — most SOURCE=GECBI rows.
     Root cause: etl.DEMOGRAPHIC_GECBI.View.sql selects NULL AS BIRTH_DATE
     (HL7 feed doesn't supply PID-7).
  3. DIAGNOSIS orphans — ENCOUNTERIDs not present in ENCOUNTER.
     Root cause: etl.load_DIAGNOSIS.StoredProcedure.sql has no existence
     check against etl.ENCOUNTER (hard-deleted encounters leave orphans).
  4. ENCOUNTER DISCHARGE_DATE < ADMIT_DATE — some ED rows.
     Root cause: etl.ENCOUNTER_EPIC.View.sql maps the legacy ED interface
     columns reversed for enc_type 'ED'.
"""

import random
from datetime import date, timedelta
from pathlib import Path

random.seed(42)
HERE = Path(__file__).parent
ROOT = HERE.parent

def d(start: date, end: date) -> date:
    return start + timedelta(days=random.randrange((end - start).days))

RACES = ["05", "03", "02", "01", "NI"]
HISP = ["N", "N", "N", "Y", "NI"]
DX_POOL = ["E11.9", "I10", "J45.909", "N18.3", "F32.9", "M54.5", "K21.9",
           "E78.5", "I25.10", "J06.9", "R07.9", "Z00.00", "G47.33", "E66.9", "D64.9"]
FACILITIES = ["MAIN", "NORTH", "SOUTH", "CLINIC_A", "CLINIC_B"]

# ---------------- DEMOGRAPHIC ----------------
patients = []  # (PATID, BIRTH_DATE, SEX, RACE, HISPANIC, RAW_SEX, SOURCE)

for i in range(1, 31):  # EPIC — clean: fnMapSex applied
    raw = random.choice(["1", "2"])  # epic sex_c codes
    sex = "F" if raw == "1" else "M"
    patients.append((f"EPC{i:04d}", d(date(1940, 1, 1), date(2010, 12, 31)).isoformat(),
                     sex, random.choice(RACES), random.choice(HISP), raw, "EPIC"))

ALS_RAW = ["F"] * 5 + ["M"] * 3 + ["X"] * 6 + ["U"] * 4  # issue 1: raw codes leak through
random.shuffle(ALS_RAW)
for i, raw in enumerate(ALS_RAW, start=1):
    patients.append((f"ALS{i:04d}", d(date(1945, 1, 1), date(2008, 12, 31)).isoformat(),
                     raw, random.choice(RACES), random.choice(HISP), raw, "ALLSCRIPTS"))

for i in range(1, 13):  # GECBI — issue 2: 9 of 12 missing BIRTH_DATE
    bd = None if i <= 9 else d(date(1950, 1, 1), date(2005, 12, 31)).isoformat()
    raw = random.choice(["F", "M"])
    patients.append((f"GBI{i:04d}", bd, raw, random.choice(RACES), random.choice(HISP), raw, "GECBI"))

# ---------------- ENCOUNTER ----------------
encounters = []  # (ENCOUNTERID, PATID, ADMIT, DISCHARGE, ENC_TYPE, FACILITY, SOURCE)
eid = 0
for patid, *_rest, source in [(p[0], p[1], p[6]) for p in patients]:
    for _ in range(random.randint(1, 4)):
        eid += 1
        enc_type = random.choices(["AV", "ED", "IP", "TH"], weights=[55, 20, 18, 7])[0]
        admit = d(date(2024, 1, 1), date(2025, 12, 1))
        if enc_type == "IP":
            disch = admit + timedelta(days=random.randint(1, 9))
        elif enc_type == "ED":
            disch = admit + timedelta(days=random.choice([0, 1]))
        else:
            disch = admit
        encounters.append([f"E{eid:05d}", patid, admit.isoformat(), disch.isoformat(),
                           enc_type, random.choice(FACILITIES), source])

# Issue 4: swap dates on 6 EPIC ED encounters that span midnight
ed_swappable = [e for e in encounters if e[4] == "ED" and e[6] == "EPIC" and e[2] != e[3]]
for e in ed_swappable[:6]:
    e[2], e[3] = e[3], e[2]  # DISCHARGE_DATE now before ADMIT_DATE

# ---------------- DIAGNOSIS ----------------
diagnoses = []
did = 0
for _ in range(200):
    did += 1
    enc = random.choice(encounters)
    diagnoses.append((f"DX{did:05d}", enc[1], enc[0], random.choice(DX_POOL), "10", enc[2], enc[6]))

# Issue 3: 12 orphan DX rows pointing at hard-deleted EPIC encounters
epic_patids = [p[0] for p in patients if p[6] == "EPIC"]
for i in range(12):
    did += 1
    admit = d(date(2024, 1, 1), date(2025, 12, 1)).isoformat()
    diagnoses.append((f"DX{did:05d}", random.choice(epic_patids), f"E9{900 + i:04d}",
                      random.choice(DX_POOL), "10", admit, "EPIC"))

# ---------------- write data.sql ----------------
def sqlval(v):
    return "NULL" if v is None else "'" + str(v).replace("'", "''") + "'"

def inserts(table, rows):
    out = []
    for chunk_start in range(0, len(rows), 50):
        chunk = rows[chunk_start:chunk_start + 50]
        vals = ",\n  ".join("(" + ", ".join(sqlval(v) for v in r) + ")" for r in chunk)
        out.append(f"INSERT INTO {table} VALUES\n  {vals};")
    return "\n".join(out)

data_sql = "\n\n".join([
    inserts("DEMOGRAPHIC", patients),
    inserts("ENCOUNTER", [tuple(e) for e in encounters]),
    inserts("DIAGNOSIS", diagnoses),
]) + "\n"
(HERE / "data.sql").write_text(data_sql)

# ---------------- synthetic ETL codebase ----------------
ETL = {
"etl/functions/etl.fnMapSex.UserDefinedFunction.sql": """\
CREATE FUNCTION etl.fnMapSex (@code VARCHAR(10))
RETURNS VARCHAR(2)
AS
BEGIN
    -- Map source-system sex codes to the PCORnet SEX valueset (F,M,A,NI,UN,OT)
    RETURN CASE UPPER(LTRIM(RTRIM(@code)))
        WHEN '1' THEN 'F'   -- Epic sex_c
        WHEN '2' THEN 'M'   -- Epic sex_c
        WHEN 'F' THEN 'F'
        WHEN 'M' THEN 'M'
        WHEN 'A' THEN 'A'   -- ambiguous
        WHEN 'U' THEN 'UN'  -- unknown
        WHEN 'X' THEN 'NI'  -- not recorded in source
        WHEN 'O' THEN 'OT'
        ELSE 'NI'
    END
END
""",
"etl/tables/etl.DEMOGRAPHIC_EPIC.View.sql": """\
CREATE VIEW etl.DEMOGRAPHIC_EPIC AS
SELECT
    'EPC' + RIGHT('0000' + CAST(p.pat_key AS VARCHAR(8)), 4) AS PATID,
    CONVERT(VARCHAR(10), p.birth_dt, 120)                    AS BIRTH_DATE,
    etl.fnMapSex(p.sex_c)                                    AS SEX,
    etl.fnMapRace(p.race_c)                                  AS RACE,
    etl.fnMapEthnic(p.ethnic_c)                              AS HISPANIC,
    p.sex_c                                                  AS RAW_SEX,
    'EPIC'                                                   AS SOURCE
FROM clarity.dbo.patient p
WHERE p.status_c <> 9; -- exclude merged/test patients
""",
"etl/tables/etl.DEMOGRAPHIC_ALLSCRIPTS.View.sql": """\
CREATE VIEW etl.DEMOGRAPHIC_ALLSCRIPTS AS
SELECT
    'ALS' + RIGHT('0000' + CAST(a.person_id AS VARCHAR(8)), 4) AS PATID,
    CONVERT(VARCHAR(10), a.date_of_birth, 120)                 AS BIRTH_DATE,
    a.gender_code                                              AS SEX, -- gender_code is already single-char
    etl.fnMapRace(a.race_code)                                 AS RACE,
    etl.fnMapEthnic(a.ethnicity_code)                          AS HISPANIC,
    a.gender_code                                              AS RAW_SEX,
    'ALLSCRIPTS'                                               AS SOURCE
FROM allscripts.dbo.person a
WHERE a.is_active = 1;
""",
"etl/tables/etl.DEMOGRAPHIC_GECBI.View.sql": """\
CREATE VIEW etl.DEMOGRAPHIC_GECBI AS
SELECT
    'GBI' + RIGHT('0000' + CAST(g.mrn_seq AS VARCHAR(8)), 4) AS PATID,
    NULL                                                     AS BIRTH_DATE, -- GECBI HL7 ADT feed does not populate PID-7 (DOB); interface v2 backlog item DQ-1187
    etl.fnMapSex(g.sex_cd)                                   AS SEX,
    etl.fnMapRace(g.race_cd)                                 AS RACE,
    'NI'                                                     AS HISPANIC,  -- not captured by GECBI
    g.sex_cd                                                 AS RAW_SEX,
    'GECBI'                                                  AS SOURCE
FROM gecbi.dbo.adt_person g;
""",
"etl/tables/etl.DEMOGRAPHIC.View.sql": """\
CREATE VIEW etl.DEMOGRAPHIC AS
-- Master demographic view: one row per patient across all source systems.
SELECT * FROM etl.DEMOGRAPHIC_EPIC
UNION ALL
SELECT * FROM etl.DEMOGRAPHIC_ALLSCRIPTS
UNION ALL
SELECT * FROM etl.DEMOGRAPHIC_GECBI;
""",
"etl/procedures/etl.load_DEMOGRAPHIC.StoredProcedure.sql": """\
CREATE PROCEDURE etl.load_DEMOGRAPHIC
AS
BEGIN
    SET NOCOUNT ON;
    TRUNCATE TABLE cdm.DEMOGRAPHIC;
    INSERT INTO cdm.DEMOGRAPHIC (PATID, BIRTH_DATE, SEX, RACE, HISPANIC, RAW_SEX, SOURCE)
    SELECT PATID, BIRTH_DATE, SEX, RACE, HISPANIC, RAW_SEX, SOURCE
    FROM etl.DEMOGRAPHIC;
    -- Post-load row count audit
    INSERT INTO etl.load_audit (table_name, row_count, load_dt)
    SELECT 'DEMOGRAPHIC', COUNT(*), GETDATE() FROM cdm.DEMOGRAPHIC;
END
""",
"etl/tables/etl.ENCOUNTER_EPIC.View.sql": """\
CREATE VIEW etl.ENCOUNTER_EPIC AS
SELECT
    'E' + RIGHT('00000' + CAST(e.enc_key AS VARCHAR(10)), 5) AS ENCOUNTERID,
    'EPC' + RIGHT('0000' + CAST(e.pat_key AS VARCHAR(8)), 4) AS PATID,
    -- The legacy ED interface table (ed_visit_xfer) stores arrival in
    -- depart_dt and departure in arrive_dt for visits migrated from the
    -- pre-2019 tracking board, so ED rows read from it directly:
    CASE WHEN e.enc_type_cd = 'ED' THEN x.depart_dt ELSE e.adm_dt   END AS ADMIT_DATE,
    CASE WHEN e.enc_type_cd = 'ED' THEN x.arrive_dt ELSE e.disch_dt END AS DISCHARGE_DATE,
    e.enc_type_cd                                             AS ENC_TYPE,
    e.facility_cd                                             AS FACILITY_LOCATION,
    'EPIC'                                                    AS SOURCE
FROM clarity.dbo.pat_enc e
LEFT JOIN clarity.dbo.ed_visit_xfer x ON x.enc_key = e.enc_key;
""",
"etl/procedures/etl.load_ENCOUNTER.StoredProcedure.sql": """\
CREATE PROCEDURE etl.load_ENCOUNTER
AS
BEGIN
    SET NOCOUNT ON;
    TRUNCATE TABLE cdm.ENCOUNTER;
    INSERT INTO cdm.ENCOUNTER (ENCOUNTERID, PATID, ADMIT_DATE, DISCHARGE_DATE, ENC_TYPE, FACILITY_LOCATION, SOURCE)
    SELECT ENCOUNTERID, PATID, ADMIT_DATE, DISCHARGE_DATE, ENC_TYPE, FACILITY_LOCATION, SOURCE
    FROM etl.ENCOUNTER_EPIC
    UNION ALL
    SELECT ENCOUNTERID, PATID, ADMIT_DATE, DISCHARGE_DATE, ENC_TYPE, FACILITY_LOCATION, SOURCE
    FROM etl.ENCOUNTER_ALLSCRIPTS
    UNION ALL
    SELECT ENCOUNTERID, PATID, ADMIT_DATE, DISCHARGE_DATE, ENC_TYPE, FACILITY_LOCATION, SOURCE
    FROM etl.ENCOUNTER_GECBI;
END
""",
"etl/tables/etl.DIAGNOSIS_EPIC.View.sql": """\
CREATE VIEW etl.DIAGNOSIS_EPIC AS
SELECT
    'DX' + RIGHT('00000' + CAST(d.dx_key AS VARCHAR(10)), 5) AS DIAGNOSISID,
    'EPC' + RIGHT('0000' + CAST(d.pat_key AS VARCHAR(8)), 4) AS PATID,
    'E' + RIGHT('00000' + CAST(d.enc_key AS VARCHAR(10)), 5) AS ENCOUNTERID,
    d.icd10_cd                                                AS DX,
    '10'                                                      AS DX_TYPE,
    CONVERT(VARCHAR(10), d.contact_dt, 120)                   AS ADMIT_DATE,
    'EPIC'                                                    AS SOURCE
FROM clarity.dbo.pat_enc_dx d
WHERE d.line_status = 'A';
""",
"etl/procedures/etl.load_DIAGNOSIS.StoredProcedure.sql": """\
CREATE PROCEDURE etl.load_DIAGNOSIS
AS
BEGIN
    SET NOCOUNT ON;
    TRUNCATE TABLE cdm.DIAGNOSIS;
    -- Diagnoses are loaded straight from the source views. Encounters that
    -- are hard-deleted upstream (e.g. registration errors purged in Clarity)
    -- are NOT re-checked here; see TODO DQ-0892.
    INSERT INTO cdm.DIAGNOSIS (DIAGNOSISID, PATID, ENCOUNTERID, DX, DX_TYPE, ADMIT_DATE, SOURCE)
    SELECT DIAGNOSISID, PATID, ENCOUNTERID, DX, DX_TYPE, ADMIT_DATE, SOURCE
    FROM etl.DIAGNOSIS_EPIC
    UNION ALL
    SELECT DIAGNOSISID, PATID, ENCOUNTERID, DX, DX_TYPE, ADMIT_DATE, SOURCE
    FROM etl.DIAGNOSIS_ALLSCRIPTS;
END
""",
"Documentation/PCORNet_CDM+/DEMOGRAPHIC.md": """\
# DEMOGRAPHIC

One row per patient. Sources: EPIC (Clarity), ALLSCRIPTS, GECBI (HL7 ADT feed).

| Column | Type | Null | Valueset |
|---|---|---|---|
| PATID | varchar | NO | — (PK) |
| BIRTH_DATE | date | YES | — |
| SEX | varchar(2) | YES | F, M, A, NI, UN, OT |
| RACE | varchar(2) | YES | 01-07, NI, UN, OT |
| HISPANIC | varchar(2) | YES | Y, N, R, NI, UN, OT |

Source-system sex codes must be normalized through `etl.fnMapSex` before load.

Known issues: GECBI ADT feed does not supply DOB (PID-7); interface v2 is
tracked as DQ-1187.
""",
"Documentation/PCORNet_CDM+/ENCOUNTER.md": """\
# ENCOUNTER

One row per encounter. ENC_TYPE valueset: AV (ambulatory), ED (emergency),
IP (inpatient), TH (telehealth). DISCHARGE_DATE must be >= ADMIT_DATE.
ED encounters from the pre-2019 tracking board are bridged through the
legacy `ed_visit_xfer` interface table.
""",
"Documentation/PCORNet_CDM+/DIAGNOSIS.md": """\
# DIAGNOSIS

One row per coded diagnosis per encounter. DX_TYPE '10' = ICD-10-CM.
Every ENCOUNTERID must exist in ENCOUNTER (referential integrity is NOT
enforced by the database; it is the loader's responsibility).
""",
}

for relpath, content in ETL.items():
    p = ROOT / "etl-files" / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)

print(f"DEMOGRAPHIC: {len(patients)} rows "
      f"(invalid SEX: {sum(1 for p in patients if p[2] in ('X', 'U'))}, "
      f"null BIRTH_DATE: {sum(1 for p in patients if p[1] is None)})")
print(f"ENCOUNTER:   {len(encounters)} rows "
      f"(discharge<admit: {sum(1 for e in encounters if e[3] < e[2])})")
enc_ids = {e[0] for e in encounters}
print(f"DIAGNOSIS:   {len(diagnoses)} rows "
      f"(orphans: {sum(1 for x in diagnoses if x[2] not in enc_ids)})")
print(f"ETL files:   {len(ETL)}")
