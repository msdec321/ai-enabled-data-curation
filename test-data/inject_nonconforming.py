#!/usr/bin/env python3
"""Inject NON-CONFORMING synthetic rows into CDW.dbo.{DEMOGRAPHIC,ENCOUNTER,
DIAGNOSIS} to test the agent's valueset-conformance detection.

The stock synthetic data fully conforms to the PCORnet valuesets, so the agent
has nothing to flag. This adds a handful of rows whose coded values violate the
catalog (valuesets/pcornet_cdm.yaml) — to verify the agent finds them and (for
the SEX cases) traces them to an ETL root cause.

Every row is tagged `CDW_Source='DQATEST'` and given a `DQTEST_*` id, so cleanup
is a single predicate. Idempotent: re-running clears prior DQATEST rows first.

NOTE: the coded columns are varchar(2), so bad values are <=2 chars — realistic
failure modes for a narrow column: numeric source codes, null-flavor confusion
(U vs UN), dropped leading zeros (5 vs 05), out-of-range codes, and truncation
artifacts (MA = a 'Male' the ETL cut to 2 chars).

    ./.venv/bin/python test-data/inject_nonconforming.py            # insert
    ./.venv/bin/python test-data/inject_nonconforming.py --verify   # show vs catalog
    ./.venv/bin/python test-data/inject_nonconforming.py --clean    # remove
"""
import argparse
import datetime
import sys
from pathlib import Path

import pytds
import yaml

ROOT = Path(__file__).resolve().parent.parent
TAG = "DQATEST"
ADMIT = datetime.date(2026, 6, 23)

# (id, SEX, RACE, HISPANIC) — first row conforms (FK anchor); rest each break one field
DEMOGRAPHIC = [
    ("DQTEST_PAT_BASE",   "M",  "05", "N"),   # conforming anchor
    ("DQTEST_PAT_SEX_MA", "MA", "05", "N"),   # SEX: truncated 'Male'
    ("DQTEST_PAT_SEX_U",  "U",  "05", "N"),   # SEX: null-flavor (should be UN)
    ("DQTEST_PAT_SEX_1",  "1",  "05", "N"),   # SEX: numeric source code
    ("DQTEST_PAT_RACE_5", "F",  "5",  "N"),   # RACE: dropped leading zero (05)
    ("DQTEST_PAT_RACE_8", "F",  "08", "N"),   # RACE: out of range (01-07)
    ("DQTEST_PAT_HISP_U", "F",  "05", "U"),   # HISPANIC: should be UN
]
# (id, ENC_TYPE, DISCHARGE_DISPOSITION, DRG_TYPE) — PATID -> base patient (FK)
ENCOUNTER = [
    ("DQTEST_ENC_BASE",   "AV", "A", "01"),   # conforming anchor for diagnoses
    ("DQTEST_ENC_ER",     "ER", "A", "01"),   # ENC_TYPE: unmapped (should be ED)
    ("DQTEST_ENC_OP",     "OP", "A", "01"),   # ENC_TYPE: non-CDM (should be AV)
    ("DQTEST_ENC_DISP_D", "IP", "D", "01"),   # DISCHARGE_DISPOSITION (should be E)
    ("DQTEST_ENC_DRG_03", "IP", "A", "03"),   # DRG_TYPE: out of range (01/02)
]
# (id, DX_TYPE, DX_SOURCE, PDX, DX) — point at base patient/encounter
DIAGNOSIS = [
    ("DQTEST_DX_TYPE_9",  "9",  "FI", "P", "25000"),  # DX_TYPE: leading zero (09)
    ("DQTEST_DX_TYPE_IC", "IC", "FI", "P", "I10"),    # DX_TYPE: truncated 'ICD10' (10)
    ("DQTEST_DX_PDX_1",   "10", "FI", "1", "E119"),   # PDX: numeric (should be P)
    ("DQTEST_DX_SRC_F",   "10", "F",  "P", "E119"),   # DX_SOURCE: should be FI
]


def connect():
    c = yaml.safe_load((ROOT / "config.yaml").read_text())["connection"]
    return pytds.connect(server=c.get("server", "127.0.0.1"), port=int(c.get("port", 1433)),
                         database=c.get("database", "CDW"), user=c["uid"], password=c["pwd"],
                         autocommit=True)


def clean(cur):
    for t in ("DIAGNOSIS", "ENCOUNTER", "DEMOGRAPHIC"):  # child -> parent (FK)
        cur.execute(f"DELETE FROM {t} WHERE CDW_Source=%s", (TAG,))
    print("removed DQATEST rows from all three tables")


def insert(cur):
    clean(cur)
    cur.executemany("INSERT INTO DEMOGRAPHIC (PATID, SEX, RACE, HISPANIC, CDW_Source) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    [(p, s, r, h, TAG) for p, s, r, h in DEMOGRAPHIC])
    cur.executemany("INSERT INTO ENCOUNTER (ENCOUNTERID, PATID, ADMIT_DATE, ENC_TYPE, "
                    "DISCHARGE_DISPOSITION, DRG_TYPE, CDW_Source) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    [(e, "DQTEST_PAT_BASE", ADMIT, et, dp, dg, TAG) for e, et, dp, dg in ENCOUNTER])
    cur.executemany("INSERT INTO DIAGNOSIS (DIAGNOSISID, PATID, ENCOUNTERID, ADMIT_DATE, DX, "
                    "DX_TYPE, DX_SOURCE, ENC_TYPE, PDX, PROVIDERID, GPC_FLAG, CDW_Source) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [(d, "DQTEST_PAT_BASE", "DQTEST_ENC_BASE", ADMIT, dx, dt, ds, "AV", pdx,
                      "DQTEST_PROV", "Y", TAG) for d, dt, ds, pdx, dx in DIAGNOSIS])
    print(f"inserted {len(DEMOGRAPHIC)} DEMOGRAPHIC, {len(ENCOUNTER)} ENCOUNTER, "
          f"{len(DIAGNOSIS)} DIAGNOSIS rows (tagged {TAG})")


def verify(cur):
    cat = yaml.safe_load((ROOT / "valuesets" / "pcornet_cdm.yaml").read_text())["columns"]
    checks = [("DEMOGRAPHIC", "SEX"), ("DEMOGRAPHIC", "RACE"), ("DEMOGRAPHIC", "HISPANIC"),
              ("ENCOUNTER", "ENC_TYPE"), ("ENCOUNTER", "DISCHARGE_DISPOSITION"),
              ("ENCOUNTER", "DRG_TYPE"), ("DIAGNOSIS", "DX_TYPE"), ("DIAGNOSIS", "PDX"),
              ("DIAGNOSIS", "DX_SOURCE")]
    for tbl, col in checks:
        allowed = set(cat[f"{tbl}.{col}"]["values"])
        cur.execute(f"SELECT {col}, COUNT(*) FROM {tbl} WHERE CDW_Source='{TAG}' "
                    f"GROUP BY {col} ORDER BY {col}")
        bad = [(v, n) for v, n in cur.fetchall() if v not in allowed]
        print(f"  {tbl}.{col}: " + (", ".join(f"{v!r} x{n}" for v, n in bad) or "(none bad)"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", action="store_true", help="remove the test rows")
    ap.add_argument("--verify", action="store_true", help="show injected non-conforming values vs catalog")
    args = ap.parse_args()
    conn = connect()
    cur = conn.cursor()
    if args.clean:
        clean(cur)
    elif args.verify:
        verify(cur)
    else:
        insert(cur)
    conn.close()


if __name__ == "__main__":
    main()
