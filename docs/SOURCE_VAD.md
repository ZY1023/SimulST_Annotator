# Source VAD and timing review

## Current pipeline

FFmpeg extracts unchanged media audio as 16 kHz, mono, 16-bit PCM WAV. The original MP4 is left intact. Source ASR uses stable-ts English word timestamps with `vad=False` and `regroup=False`; raw outputs remain in `asr_runs` and `asr_word_candidates`. The editable word table remains separate. Video, audio, words and VAD use the same media-relative integer-millisecond timeline, with `media_offset_ms` applied only for playback display.

The independent Source VAD worker uses Silero VAD 6.2.3 on the extracted WAV. Install `backend/requirements-vad.txt` to run it. Parameters are stored per run: 16 kHz, threshold 0.5, minimum speech 250 ms, minimum silence 100 ms, speech padding 30 ms. The model is loaded locally from the installed package and run on CPU. The audio is not modified. `vad_jobs` stores queued/running/completed/failed/cancelled status and cancellation requests. `vad_runs` stores engine, version and parameters. `vad_intervals` stores both speech and complementary nonspeech intervals with stable IDs, media ID, estimated precision and machine review status. Re-running creates a new run; the timeline displays the latest completed run.

The read-only `GET /api/media/{media_id}/alignment/diagnostics` returns suspected word IDs, current and raw timestamps, durations, waveform RMS, VAD nonspeech overlap, reasons and a synchronization assessment. Words longer than 1,000 ms or overlapping at least 300 ms and 40% VAD nonspeech are flagged for review. These thresholds are screening aids, not automatic timing corrections. Low waveform amplitude and VAD nonspeech are not proof of an incorrect word boundary, especially with music, noise or overlapping voices. Local stable-ts alignment retains its explicit preview, apply and reject workflow. Previously reviewed word boundaries require explicit confirmation; human text and raw ASR are preserved.

Selecting a word only selects its stable database ID. The **Seek to Word** and **Play from word** buttons control playback explicitly. `speech_status` distinguishes spoken, uncertain and Not Spoken / Hallucination; setting the latter also rejects the editable word. Rejected words remain in SQLite and the timeline for correction but do not appear as active transcript words or in Simple JSON/CSV exports. Detailed and complete exports preserve them and their provenance.

## Future comparison interfaces

All proposed intervals use `[start_ms, end_ms)` on the canonical media timeline:

| Interface | Required fields | Source |
| --- | --- | --- |
| Speech-active interval | media_id, run_id, start_ms, end_ms | Latest VAD speech interval |
| Speech-inactive interval | media_id, run_id, start_ms, end_ms | Complementary VAD interval |
| Subtitle update event | media_id, event_id, time_ms | Future reviewed subtitle state change |
| Subtitle no-update interval | media_id, start_ms, end_ms | Future interval between reviewed updates |
| Activity/no-update overlap | media_id, vad_run_id, no_update_id, overlap_ms | `max(0, min(end) - max(start))` |
| Active proportion | media_id, no_update_id, numerator_ms, denominator_ms, proportion | Overlap duration divided by no-update duration |

For a no-update interval of `[1000, 3000)` and speech interval `[1800, 2600)`, overlap is 800 ms and speech-active proportion is `800 / 2000 = 0.4`. Zero-length no-update intervals must be excluded from division. Future calculations must record the selected VAD run and subtitle revision. Temporal overlap alone does not establish causality.

No OCR, subtitle-update extraction or cross-modal statistic is implemented here.
