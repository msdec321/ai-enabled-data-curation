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
