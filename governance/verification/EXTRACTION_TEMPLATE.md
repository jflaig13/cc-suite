# Extraction record

**Objective:** {original instruction reference}
**Subject:** {artifact hash or build/commit identity}
**Environment and capture time:** {observed values}
**Source basis:** {authoritative source and revision}
**Extractor and method:** {independent actor/tool}
**Required manifest:** {path and revision}

| Field or behavior ID | Observed value or state | Evidence location |
|---|---|---|
| {ID} | {literal observation} | {source reference} |

Transcribe observations without judging them. Include every manifest field. Represent an absent value explicitly as `BLANK`, `NOT DISPLAYED`, or `UNAVAILABLE`, with the reason where known. Never drop a required row.

Store independent expected values separately from observed extraction. A calculation is not a literal observation and must identify its input basis and method. Capture each relevant identity, position, unit and effective time.

The deployment's validator must reject missing required fields before comparison. A validator described in Markdown is not assumed installed. Record the actual validation result and any missing evidence; missing required evidence leaves verification incomplete.
