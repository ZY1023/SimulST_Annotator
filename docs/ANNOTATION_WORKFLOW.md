# Annotation workflow and OCR contract

The interface presents five research concepts. They do not imply that every automatic pipeline is implemented.

| User-facing concept | Current persistent source | Meaning |
| --- | --- | --- |
| Source Transcript | `source_transcripts`; manual corrections can also live in selected `human_annotations` tiers | Source-language timed text and provenance |
| Target Subtitle States | `ocr_observations`, `subtitle_screen_states` | Raw frame observations and complete visible screen text |
| Text Change Events | `derived_events` | Mechanical candidates between consecutive states |
| Human Annotations | `annotation_tiers`, `human_annotations` | Researcher interpretation, labels and verification |
| Derived Metrics | Pending | No current table, controls, or export category |

The Export dialog selects the existing tables independently. A manual tier named “Subtitle state” is still a human annotation tier; its name does not make it a row in `subtitle_screen_states`. The legacy export endpoint remains available; selected exports use schema version 2 in [EXPORT_FORMAT.md](EXPORT_FORMAT.md).

## Current OCR processing boundary

`backend/app/analysis_contracts.py` defines the data interfaces. The local MVP samples frames from an unmodified media file, recognizes the selected subtitle region, persists raw OCR observations, and assembles complete visible screen states. Empty states represent subtitle disappearance. OCR confidence is evidence for review, not a translation-quality or semantic-revision label. Text-change candidates and semantic labels are deferred.

The pipeline preserves raw OCR output in `ocr_observations`; corrections belong in separately reviewed states or human annotations. Generated states link to supporting observations. Stable IDs, integer millisecond timestamps, time precision, engine/version/settings provenance, confidence, and review status remain available. Human-confirmed event labels stay separate from any future machine-generated `derived_events` candidates.

The MVP saves normalized ROI, sample rate, time range, and crop-change threshold with each OCR job/run. The most recent ROI and splitter height are also kept in browser-local settings for convenience. Future project-specific analysis thresholds belong under `projects.settings_json.analysis`. Global shortcut preferences remain in browser local storage. See [Target OCR workflow](TARGET_OCR.md).
