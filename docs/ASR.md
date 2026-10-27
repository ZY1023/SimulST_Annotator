# Local English source transcription

The Source words tier now reads the canonical editable rows in `source_transcripts`. It does not copy ASR words into `human_annotations`. The durable `asr_jobs` table stores queued/running/completed/failed/cancelled status. `asr_runs.raw_json` preserves the unchanged stable-ts result. `asr_word_candidates` preserves every model word, including missing or invalid timestamps. Valid aligned words become editable `source_transcripts` rows with stable IDs, integer-millisecond intervals, provenance, review status, edit status and timing warnings. Leading/trailing spacing around a model word is removed only in the editable copy; punctuation and repeated words remain. `source_transcript_meta.revision` prevents stale browser edits from overwriting newer changes.

## Install

Use Python 3.8 or newer, FFmpeg on PATH, and a PyTorch build appropriate for the computer. Then, in `backend/`, run:

```powershell
python -m pip install -r requirements.txt
python -m pip install -r requirements-asr.txt
```

`requirements-asr.txt` pins `stable-ts==2.19.1`. The package requires PyTorch, torchaudio and `openai-whisper>=20230314,<=20250625`; no diarization or speaker-recognition package is installed. Choose a CUDA-capable PyTorch build from the [official PyTorch installer](https://pytorch.org/get-started/locally/) if GPU processing is desired. The engine detects CUDA at runtime and falls back to CPU when unavailable or model loading fails on CUDA. First use downloads the chosen Whisper model into the private data directory at `models/stable-ts/`; it is never placed in Git. The `small` model download is roughly 461 MB. `medium` requires substantially more storage and memory. The upstream [stable-ts repository](https://github.com/jianfch/stable-ts) is archived; pinning the dependency and keeping the engine behind `TranscriptionEngine` limits future migration work.

## Workflow

By default, importing a video queues `small` English transcription after FFmpeg has extracted audio. Settings → Annotation can disable automatic transcription or choose `small`/`medium` as the default. Existing media can be transcribed from the strip above the Source words tier. The backend worker processes jobs without blocking API requests. Reopening media does not enqueue work again. Only one queued or running job is allowed per media.

Run the local API as one Uvicorn process (the README command does this). The in-process worker is not intended for a multi-process server deployment.

The strip shows the actual job state and CPU/CUDA device without fabricated percentages. Cancel marks a queued job immediately or requests cancellation of a running job. stable-ts inference cannot be interrupted inside one blocking model call; after that call returns, the worker discards cancelled output. A restart marks interrupted running jobs as failed so they can be retried; queued jobs remain queued. Missing dependencies, model download errors and inference failures produce a retryable failed job and no partially applied transcript.

Re-transcribe deliberately creates another raw run. If an editable transcript already exists, the new run is retained as raw evidence but **does not overwrite manual edits**. Applying or comparing a second run is not implemented yet. Local stable-ts alignment is available through an explicit preview/apply/reject workflow; see [Source VAD and timing review](SOURCE_VAD.md).

In the timeline, each aligned word is a separate token. Click or double-click to select or edit without seeking; drag or resize the token to adjust times. Use the Source words strip or drag an empty part of the tier to insert a missing word. The inspector supports text correction, millisecond timestamp changes, review and speech status, split, merge with the next word, delete, undo/redo, immediate save, and explicit seeking or playback from the word. Edits autosave after about 650 ms and persist in SQLite. Suspicious overlaps, long intervals and VAD nonspeech overlap remain marked for review. Model words with missing or unusable timestamps stay in raw candidates and are counted as needing attention; the app does not invent exact boundaries for them.

## Simple JSON

`GET /api/media/{media_id}/source-words/simple-json` downloads the **current editable transcript** as UTF-8 JSON. One word becomes one object with only `start`, `end` (seconds rounded to three decimals), and `text`, sorted by time. Deleted words are absent. Example using synthetic text:

```json
[
  {"start": 0.1, "end": 0.3, "text": "We"},
  {"start": 0.32, "end": 0.6, "text": "need,"}
]
```

JSON numbers may omit trailing zeroes after rounding. The browser receives `<sanitized-media-name>_source_words.json`. Comprehensive project exports remain available. Raw ASR text, model metadata, IDs, private paths and database details are excluded from this simple format.

## API

- `GET/PUT /api/asr/settings`: global auto-transcribe preference and default model, stored in private SQLite `app_preferences`.
- `GET /api/asr/capabilities`: dependency availability and selected compute device.
- `GET/PUT /api/media/{id}/source-words`: canonical editable transcript and optimistic revision.
- `GET /api/media/{id}/asr`: latest durable job and current word count.
- `POST /api/media/{id}/asr/jobs`: queue with model and optional deliberate `rerun`.
- `POST /api/media/{id}/asr/jobs/{job_id}/cancel`: request cancellation.

All source audio, model files, raw ASR JSON, candidates and edited words remain in the private data directory. The original MP4 and extracted WAV are not modified by transcription.
