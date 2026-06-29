# PCORnet CDM valueset catalog

`pcornet_cdm.yaml` is the agent's reference for **which columns have a predefined
valueset and what the permissible values are**, used during data-quality
profiling to flag values that don't conform (e.g. a `SEX` outside
`A/F/M/NI/UN/OT`). It is **generated** from the authoritative source documents by
`build_catalog.py` — don't hand-edit it; re-run the generator.

## Sources

| Source | Gives us |
|---|---|
| `docs/PCORI_CDM_SPECIFICATION.pdf` (CDM **v7.0**) | The inline "Predefined Value Sets" of each categorical field (SEX, RACE, DX_TYPE, ENC_TYPE, …) — small, enumerable, embedded per column. |
| `docs/PCORNET_ADDITIONAL_VALUESETS.xlsx` (**v1.14**) | The large valuesets that don't fit inline (FACILITY_TYPE, PAYER_TYPE, UNIT, SPECIMEN_SOURCE, …). Stored once and referenced. |

Current draft: **177 constrained columns across 26 tables**, plus **12 large
named valuesets**.

## Structure

```yaml
meta: { cdm, cdm_spec_version, additional_valuesets_version, scope, ... }

valuesets:                       # large, shared sets — defined ONCE
  FACILITY_TYPE:
    kind: enumerable
    source: additional_valuesets_v1.14
    size: 105
    values: { ... }

columns:                         # every constrained TABLE.COLUMN
  DEMOGRAPHIC.SEX:               # small inline set — values embedded
    kind: enumerable
    source: cdm_spec
    values: { A: Ambiguous, F: Female, M: Male, NI: No information, UN: Unknown, OT: Other }
  ENCOUNTER.FACILITY_TYPE:       # large set — references valuesets: above
    kind: enumerable
    source: additional_valuesets_v1.14
    valueset: FACILITY_TYPE
```

A column is constrained iff it appears under `columns:`. Anything absent (free
text, dates, numerics, IDs) is unconstrained — profile it differently. To resolve
a column: read its entry; if it has `values`, use them; if it has `valueset`,
look that name up under `valuesets:`.

## Scope and known nuances

- **Enumerable valuesets only.** External code systems — ICD (DIAGNOSIS.DX),
  LOINC, NDC, RxNorm, SNOMED — are *not* enumerable and are deliberately
  excluded; validate those by code-system/format, not set membership.
- **`RACE_ETH_*` indicator fields are `Y`-only** by design in the spec (the field
  is `Y` or absent), not a truncated extraction.
- **Workbook predates v7 renames.** Two xlsx field labels are mapped to their
  actual v7 columns in `build_catalog.py` (`XLSX_FIELD_OVERRIDES`):
  `PATIENT_PREF_LANGUAGE_SPOKEN → PAT_PREF_LANGUAGE_SPOKEN`,
  `PROVIDER_SPECIALTY_PRIMARY → PROVIDER.PROVIDER_SPECIALTY`.
- A few descriptions carry minor trailing punctuation from the PDF layout (e.g.
  `OT: "Other ."`); the **codes** — what conformance checks on — are clean.

## Regenerate

```bash
./.venv/bin/pip install openpyxl pdfplumber pyyaml
./.venv/bin/python valuesets/build_catalog.py
```

The generator parses the PDF's value-set column by ruling-line geometry (carrying
column anchors across continuation pages) and the xlsx by sheet, cross-references
each xlsx valueset to its CDM field(s) via the workbook's `SOURCES` sheet, and
prints a summary plus any unresolved field→table mappings.
