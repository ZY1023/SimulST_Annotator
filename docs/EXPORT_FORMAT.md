# Selected annotation export format (schema version 2)

The Export dialog calls `POST /api/projects/{project_id}/export/advanced`. Its request selects `media_id` (or `null` for the whole project), `tier_ids`, `research_data`, `format` (`json`, `csv`, or `both`), `structure` (`combined`, `separate`, or `both`), and `include_empty_tiers`. The UI groups these as Source Transcript, Target Subtitle States, Text Change Events, and Human Annotation Tiers. Derived Metrics remain pending. See [workflow mappings](ANNOTATION_WORKFLOW.md).

Manual annotation tiers come from `annotation_tiers` and `human_annotations`. Specialized research data is selected independently from `source_transcripts`, `ocr_observations`, `subtitle_screen_states`, or `derived_events`. A display tier name does not imply that records from another table are present.

## Combined JSON

The top level contains `schema_version`, `exported_at`, `scope`, `project`, `media_assets`, `annotation_tiers`, `annotations`, and one array for each specialized collection. An unselected specialized collection is an empty array. Every annotation retains its original `id`, `project_id`, `media_id`, `tier_id`, `span` with integer `start_ms` and `end_ms`, text, label, metadata, source IDs, review status and creation/update timestamps. Specialized records retain their original IDs, timestamps, provenance, review status and table-specific relationships.

Synthetic shape example:

```json
{
  "schema_version": 2,
  "scope": "media",
  "project": {"id": "synthetic-project-id", "name": "Synthetic Study"},
  "media_assets": [{"id": "synthetic-media-id", "display_name": "synthetic.mp4"}],
  "annotation_tiers": [{"id": "synthetic-tier-id", "name": "Source words"}],
  "annotations": [{
    "id": "synthetic-annotation-id",
    "project_id": "synthetic-project-id",
    "media_id": "synthetic-media-id",
    "tier_id": "synthetic-tier-id",
    "span": {"start_ms": 100, "end_ms": 300, "precision": "estimated"},
    "text": "hello", "event_label": null, "metadata": {},
    "review_status": "unreviewed"
  }],
  "source_transcripts": [],
  "ocr_observations": [],
  "subtitle_screen_states": [],
  "derived_events": []
}
```

The example abbreviates media and annotation fields; downloaded records include the complete API contract. Empty tiers may appear in `annotation_tiers` when requested, but no fake annotations are generated.

## Combined CSV

Every row uses this consistent column set:

```text
project_id,media_id,media_name,tier_id,tier_name,record_type,record_id,start_ms,end_ms,duration_ms,text,label,provenance,review_status,metadata
```

`record_type` is `human_annotation` or the exact specialized collection name. `tier_id` and `tier_name` are blank for specialized records. OCR uses `frame_ms` as both start and end (duration 0). Table-specific fields that do not fit the common columns are serialized as JSON in `metadata`; provenance is also a JSON cell. CSV uses UTF-8 with BOM and standard quoting for commas, line breaks and quotation marks. The export does not invent rows for empty tiers.

## Separate and ZIP output

One generated file downloads directly. Multiple generated files download in one ZIP:

```text
manifest.json
combined/all_annotations.json
combined/all_annotations.csv
media/<sanitized-media-name>_<id>/tiers/<sanitized-tier-name>_<id>.json
media/<sanitized-media-name>_<id>/tiers/<sanitized-tier-name>_<id>.csv
research_data/source_transcripts.json
research_data/source_transcripts.csv
```

Only selected formats and datasets are included. Media IDs in paths prevent collisions between same-named files. The manifest records schema/software version, export time, scope, selected IDs/categories/options, counts and contained filenames. Original MP4/WAV bytes, absolute source paths, database location and account credentials are never intentionally included. Exports download to the browser's chosen directory and may contain sensitive annotation text; store them privately.

The older `GET /api/projects/{id}/export` route remains for compatibility and uses its earlier schema. New dialog exports use schema version 2.

The separate **Source Words → Simple JSON** option exports only the current editable word transcript as `start`, `end`, and `text`. It does not change either comprehensive export format. See [ASR and simple JSON](ASR.md).
