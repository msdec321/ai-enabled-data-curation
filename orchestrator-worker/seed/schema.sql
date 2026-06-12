-- Synthetic PCORnet CDM subset (Tier-1 demo data) — SQLite/D1
DROP TABLE IF EXISTS DIAGNOSIS;
DROP TABLE IF EXISTS ENCOUNTER;
DROP TABLE IF EXISTS DEMOGRAPHIC;

CREATE TABLE DEMOGRAPHIC (
  PATID      TEXT PRIMARY KEY,
  BIRTH_DATE TEXT,           -- YYYY-MM-DD; planted issue: NULL for most GECBI rows
  SEX        TEXT,           -- PCORnet valueset F,M,A,NI,UN,OT; planted issue: raw 'X'/'U' from ALLSCRIPTS
  RACE       TEXT,
  HISPANIC   TEXT,
  RAW_SEX    TEXT,           -- source-system code before mapping
  SOURCE     TEXT            -- EPIC | ALLSCRIPTS | GECBI
);

CREATE TABLE ENCOUNTER (
  ENCOUNTERID       TEXT PRIMARY KEY,
  PATID             TEXT,
  ADMIT_DATE        TEXT,    -- planted issue: swapped with DISCHARGE_DATE for some ED rows
  DISCHARGE_DATE    TEXT,
  ENC_TYPE          TEXT,    -- AV | ED | IP | TH
  FACILITY_LOCATION TEXT,
  SOURCE            TEXT
);

CREATE TABLE DIAGNOSIS (
  DIAGNOSISID TEXT PRIMARY KEY,
  PATID       TEXT,
  ENCOUNTERID TEXT,          -- planted issue: orphan references to deleted encounters
  DX          TEXT,
  DX_TYPE     TEXT,
  ADMIT_DATE  TEXT,
  SOURCE      TEXT
);
