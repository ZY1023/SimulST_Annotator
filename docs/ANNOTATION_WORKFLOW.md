# Annotation workflow and future OCR contract

The interface presents five research concepts. They do not imply that every automatic pipeline is implemented.

| User-facing concept | Current persistent source | Meaning |
| --- | --- | --- |
| Source Transcript | `source_transcripts`; manual corrections can also live in selected `human_annotations` tiers | Source-language timed text and provenance |
| Target Subtitle States | `ocr_observations`, `subtitle_screen_states` | Raw frame observations and complete visible screen text |
| Text Change Events | `derived_events` | Mechanical candidates between consecutive states |
| Human Annotations | `annotation_tiers`, `human_annotations` | Researcher interpretation, labels and verification |
| Derived Metrics | Pending | No current table, controls, or export category |

The Export dialog selects the existing tables independently. A manual tier named “Subtitle state” is still a human annotation tier; its name does not make it a row in `subtitle_screen_states`. The legacy export endpoint remains available; selected exports use schema version 2 in [EXPORT_FORMAT.md](EXPORT_FORMAT.md).

## Processing boundary for a later OCR milestone

`backend/app/analysis_contracts.py` defines interfaces only. A future local pipeline would sample frames from an unmodified media file, recognize the configured subtitle region, persist every raw OCR observation, assemble complete visible screen states, and compare successive states to produce candidate text changes. Empty states must represent subtitle disappearance. Replacement must retain both the previous and next full text. OCR confidence is evidence for review, not a translation-quality or semantic-revision label.

The future pipeline must preserve raw OCR output in `ocr_observations`; corrections belong in separately reviewed states or human annotations. Each generated state must link to the observations that support it, and each change candidate must link to its pair of states. Stable IDs, integer millisecond timestamps, time precision, engine/version/settings provenance, confidence, and review status must remain available. Human-confirmed event labels must remain separate from machine-generated `derived_events` candidates.

Project-specific future analysis settings belong under `projects.settings_json.analysis`: normalized subtitle ROI, OCR language, frame sample interval, frame-boundary refinement, text-change sensitivity, candidate output-gap threshold, and OCR confidence review threshold. Their ranges and migration defaults must be validated when processing is implemented. Global UI preferences, including shortcut bindings, remain in browser local storage and are never read as project analysis thresholds. This release exposes no active OCR settings or processing endpoint.
