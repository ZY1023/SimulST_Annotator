# SimulST Annotator

Local-first research workspace for simultaneous speech-to-text translation subtitles. The current release supports named projects, MP4 import, source-speech transcription and VAD, selected-range or chunked full-video target OCR, reviewable subtitle screen states, descriptive timing events, manual annotation timelines, and structured exports. The light workspace keeps its synchronized video, waveform, inspector, and multi-tier timeline.

## Architecture

```text
frontend/               React + TypeScript + Vite
  src/ResearchApp.tsx    project workflow, player, timeline and manual editor
  src/WorkspaceDialogs.tsx  project/media managers, storage, export and settings dialogs
  src/shortcuts.ts       validated browser-local shortcut presets and bindings
  src/useSourceTranscript.ts  editable source-word state and autosave
  src/useTargetOCR.ts      OCR jobs and editable subtitle-state client
  src/VideoROI.tsx         frame-relative subtitle crop selection
  src/SourceWordInspector.tsx  word review and correction controls
  src/App.tsx            preserved first-milestone UI (not used by the entry point)
  src/contracts.ts       shared wire-format types
backend/                FastAPI
  app/main.py            project, media, annotation and export API
  app/config.py          private data directory and path validation
  app/contracts.py       Pydantic data contracts
  app/db.py              SQLite schema and legacy migration
  app/media.py           FFprobe and FFmpeg integration
  app/storage.py         read-only file reference inventory
  app/exports.py         selected-tier and research-data export
  app/analysis_contracts.py  OCR and text-change data interfaces
  app/ocr.py             local PaddleOCR worker and subtitle-state assembly
  app/subtitle_states.py reversible state merging, splitting and grouping
  app/timing_metrics.py  descriptive subtitle update and output-gap intervals
  app/simple_exports.py  selected dataset JSON/CSV and ZIP generation
  app/asr.py             stable-ts engine interface and SQLite-backed worker
  tests/                backend tests
../<repository-name>_data/ private SQLite database and media copies (outside Git)
  simulst.sqlite3        authoritative database for all projects
  projects/<sanitized-name>_<short-id>/
    media/<media-id>/original.mp4
    derived/audio/<media-id>.wav
    exports/
  media/<legacy-media-id>/  preserved pre-project media paths, if any
  backups/                  verified migration backups, if any
```

The default private directory is a sibling named `<repository-name>_data`. The database is **one global SQLite file**, with project IDs connecting records. The app does not create a separate `annotations.sqlite` or `project.json` per project. New projects use a sanitized readable folder name plus a unique suffix; the relative folder path is stored in SQLite and does not change when a project is renamed. Existing UUID folders and legacy media paths remain unchanged. The original MP4 copy is never transcoded.

The video element is the playback clock. Its current position is converted to integer milliseconds, with `media_offset_ms` added for the canonical timeline. WaveSurfer displays the extracted mono WAV and follows the video; clicking the waveform seeks the video.

SQLite tables include `projects`, `media_assets`, `annotation_tiers`, `human_annotations`, `source_transcripts`, `asr_jobs`, `asr_runs`, `asr_word_candidates`, `source_transcript_meta`, `app_preferences`, `ocr_jobs`, `ocr_runs`, `ocr_observations`, `subtitle_screen_states`, and `derived_events`. Source words are edited only in `source_transcripts`; raw model output and every candidate remain in separate ASR tables. Projects store stable IDs, names, JSON settings and relative storage paths. Media rows retain private original and audio paths. Human annotations remain separate from source words. OCR observations are immutable; corrected subtitle states live in their own table.

## Requirements

- Node.js 20.19+ and npm
- Python 3.10+
- FFmpeg and FFprobe on `PATH`

## Run locally

Open two terminals at the repository root:

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

```powershell
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` to FastAPI. The toolbar opens compact Project Manager and Media Manager dialogs. Projects can be searched, sorted, renamed, archived, restored, and removed through an explicit typed confirmation. Removal is a reversible database status change and does not delete files. Media can be renamed or removed from the active list while its original files and annotations remain recoverable. Choose **Add Video** in the media selector, then drag across a tier to create an interval. Click it to edit text, timestamps, event label, precision, and review status. Drag a token to move it or drag its edge to resize it. Switching media preserves separate timelines. The API documentation is at `http://127.0.0.1:8000/docs`. H.264/AAC MP4 is recommended for browser playback.

For a clean clone without research data, run `python scripts/make_synthetic_demo.py` from the repository root, then import the generated `synthetic-demo.mp4` from the private sibling data directory. The script does not put media in the repository.

`SIMULST_DATA_DIR` can point to another local **outside-repository** data folder. Relative values are resolved from the repository's parent. The default is a sibling directory named `<repository-name>_data`; configurations inside the repository are rejected. See [data privacy](docs/DATA_PRIVACY.md) and `.env.example`. The old repository-local `data/` path is ignored and no longer used.

## Saving and export

Creating or deleting an interval, adding a tier, importing media, and renaming a project call the backend immediately. Inspector edits wait 650 ms after the last change and then update SQLite. A small toolbar indicator shows **Unsaved**, **Saving…**, **Saved**, or **Save failed**. **Saved** appears only after the API confirms a write. Click the indicator for database location, project, last successful save, annotation count, Open Data Folder and a Retry action after failures. `Ctrl+S` flushes pending edits; switching annotations, media, or projects and exporting also flushes edits first. Browser navigation warns about pending writes. Failed edits remain in the editor for retry. Success banners are not shown after autosave.

Source words have their own compact save and ASR job status beside the timeline. See [local ASR setup](docs/ASR.md) and [Source VAD and timing review](docs/SOURCE_VAD.md). Install `backend/requirements-asr.txt` for stable-ts and `backend/requirements-vad.txt` for independent local Silero VAD.

For Target OCR, install `backend/requirements-ocr.txt`, open an MP4, choose **Set ROI / range**, and draw the crop over subtitles on the video. The default range is the first 30 seconds at 5 fps. **Run selected range** samples only that range. Select a state in the Target Subtitle States tier to correct complete screen text and millisecond boundaries, then use **Review & Next**. See [Target OCR workflow](docs/TARGET_OCR.md).

The Export dialog now offers scope, available datasets with counts, Simple JSON, CSV, or Detailed JSON, and an exact file preview. One selected dataset from one video downloads directly; multiple selected datasets or project media are packaged in a ZIP organized by media. An explicit advanced option produces a complete project JSON archive. Source words export as a bare array of `start`, `end`, and `text`. The old comprehensive API remains available for compatibility. JSON is UTF-8; CSV is UTF-8 with a BOM. Exports do not include original videos or private source paths. See [source word timing, review, and export](docs/WORD_ALIGNMENT_REVIEW.md) and [legacy export format](docs/EXPORT_FORMAT.md).

The Project Manager or Settings menu opens a read-only Storage Overview with database and folder paths, counts by media/tier, research-data counts, total project file size and a file-reference audit. Audit labels distinguish active databases, backups, referenced media, standalone demo material and potentially orphaned files. No audit action removes files.

Keyboard shortcuts: Space plays/pauses; Left/Right nudges the selected token 10 ms (Shift: 100 ms); Alt+Left/Right seeks one second; Delete deletes the selected token; Ctrl+Z/Ctrl+Y (or Ctrl+Shift+Z) undo/redo annotation edits while the timeline or inspector owns focus; Ctrl+S saves; +/− zoom; Escape closes a menu/dialog or clears selection; ? opens shortcut help. The Settings dialog offers default, annotation-focused and custom presets with recording, conflict detection and browser-local persistence. Text fields retain their native typing and undo behavior. Project-specific future OCR thresholds are kept separate from global shortcut preferences; see [workflow and future analysis contracts](docs/ANNOTATION_WORKFLOW.md).

## Current API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Health check |
| GET/POST | `/api/projects` | List recent projects / create a project |
| GET/PATCH | `/api/projects/{id}` | Read / rename a project |
| POST | `/api/projects/{id}/open` | Record opening time |
| POST | `/api/projects/{id}/archive`, `/restore`, `/delete` | Reversible project status changes; delete requires exact name |
| POST | `/api/projects/{id}/open-folder` | Open private folder on Windows |
| GET/POST | `/api/projects/{id}/media` | List / import project media |
| GET | `/api/projects/{id}/storage`, `/api/storage/audit` | Storage overview / read-only file audit |
| POST | `/api/storage/open-folder` | Open the private data root on Windows |
| GET | `/api/projects/{id}/export?format=json\|csv` | Download project or media export (optional `media_id`) |
| GET/POST | `/api/projects/{id}/export/options`, `/export/advanced` | Tier counts / selected export |
| GET | `/api/media/{id}` | Get one media asset |
| PATCH/POST | `/api/media/{id}`, `/remove`, `/restore` | Rename display name / reversible removal |
| GET | `/api/media/{id}/video` | Stream original MP4 |
| GET | `/api/media/{id}/audio` | Stream derived WAV |
| GET/POST | `/api/media/{id}/tiers` | List/create manual annotation tiers |
| PUT/DELETE | `/api/media/{id}/tiers/{tier_id}` | Rename / delete an empty custom tier |
| PUT | `/api/media/{id}/tiers/order` | Reorder manual tiers |
| GET/POST | `/api/media/{id}/annotations` | List/create manual intervals |
| PUT/DELETE | `/api/media/{id}/annotations/{annotation_id}` | Edit/delete a manual interval |
| GET/POST | `/api/media/{id}/target-ocr`, `/target-ocr/jobs` | Read OCR state / start a bounded local job |
| POST | `/api/media/{id}/target-ocr/jobs/{job_id}/cancel` | Cancel an OCR job |
| PUT | `/api/media/{id}/target-ocr/states/{state_id}` | Save corrected text, times, and review status |

The earlier `/api/media` list/import routes remain for compatibility; the interface uses project-scoped routes.

## Test

```powershell
cd backend
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

```powershell
cd frontend
npm run build
```

The backend tests cover millisecond conversion, interval validation, private-storage boundaries, MP4 import, original-file preservation, derived-audio serving, manual interval CRUD, two-media project persistence across reopening, tier separation, Unicode JSON/CSV exports, selected-tier/specialized-data exports, reversible project/media status changes and legacy migration. Text-diff tests belong with the later subtitle inference feature.

## Milestone boundary

The Target Subtitle States tier is generated by OCR. The older manual “Subtitle state” tier remains a distinct annotation tier. Text-change event classification and semantic revision analysis are not implemented. Frame stepping is an approximate 40 ms seek, not codec-accurate frame decoding. For long recordings, waveform decoding may use substantial browser memory.

## Public repository check

Run `python scripts/check_public_repo.py` before committing or pushing. It checks staged, tracked, and visible untracked files plus historical path names. Review `git status --short` and `git diff --cached --name-only` manually. Do not stage or push real research data. See [data privacy](docs/DATA_PRIVACY.md) for limitations and the optional pre-commit hook.
