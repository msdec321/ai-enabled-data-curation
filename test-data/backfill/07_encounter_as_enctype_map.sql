/******************************************************************************
  07_encounter_as_enctype_map.sql  (ENCOUNTER back-population — step 3: ENC_TYPE map)

  etl.ENCOUNTER_AS maps Allscripts ENC_TYPE by looking up LocationType in
  CDW_COLUMN_MAP (Source='ALLSCRIPTS', CDMColumn='ENC_TYPE') on SourceName.
  Our synthetic source sets LocationType = the destination CDM code, and the
  existing map has no entries for ED/IS (and none that are identity AV->AV).
  Add identity rows so LocationType '<code>' maps back to ENC_TYPE '<code>'
  (and RAW_ENC_TYPE = LocationType reproduces too). SourceColumn matches the
  existing AS ENC_TYPE rows ('EntryName').

  Idempotent: delete our identity rows then reinsert.
******************************************************************************/
USE CDW;
GO
SET NOCOUNT ON;

DELETE FROM dbo.CDW_COLUMN_MAP
WHERE Source='ALLSCRIPTS' AND CDMColumn='ENC_TYPE' AND CDMTable='ENCOUNTER'
  AND SourceColumn='EntryName' AND SourceName IN ('AV','OA','IP','ED','IS') AND SourceName = CDMValue;

INSERT INTO dbo.CDW_COLUMN_MAP (Source, SourceColumn, SourceName, SourceValue, CDMTable, CDMColumn, CDMName, CDMValue)
SELECT 'ALLSCRIPTS', 'EntryName', v, v, 'ENCOUNTER', 'ENC_TYPE', v, v
FROM (VALUES ('AV'), ('OA'), ('IP'), ('ED'), ('IS')) AS x(v);

SELECT COUNT(*) AS identity_enctype_rows
FROM dbo.CDW_COLUMN_MAP
WHERE Source='ALLSCRIPTS' AND CDMColumn='ENC_TYPE' AND SourceName IN ('AV','OA','IP','ED','IS') AND SourceName = CDMValue;
GO
