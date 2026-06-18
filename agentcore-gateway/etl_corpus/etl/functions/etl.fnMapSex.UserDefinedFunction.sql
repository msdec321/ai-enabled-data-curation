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
