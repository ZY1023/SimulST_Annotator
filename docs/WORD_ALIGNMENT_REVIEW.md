# Source word timing and review

## Diagnostic findings

The import path extracts mono 16 kHz PCM audio with FFmpeg and keeps the MP4 untouched. The media duration is read with ffprobe and converted to integer milliseconds by rounding seconds × 1000. The frontend uses the same media-relative millisecond origin for word intervals and its playhead; the configurable media offset is applied only when converting video playback time to displayed media time. Inspect `GET /api/media/{media_id}/alignment/diagnostics` for duration, offset, sample rate, raw candidate issues, editable word issues, and waveform RMS around suspicious word positions.

The observed error categories are local long gaps, zero-duration raw candidates, and individual word boundaries requiring review. There was no evidence supporting a global time shift. Legacy editable `word_order` values can change after editing, so they must not be used to pair a word with a raw ASR candidate. The additive migration links only exact, unique matches and leaves uncertain links empty.

## Local alignment workflow

Select a source word in Annotation Editor, choose Sentence or a time window, then select **Preview local alignment**. The operation uses stable-ts with the current corrected text and extracted audio. It accepts a range up to 30 seconds and returns a proposed interval for each selected word. Missing, zero-length, out-of-range, reordered, or text-mismatched results are rejected without guessing timestamps. The preview stores both previous and proposed times. Apply or reject explicitly; applying a preview that contains reviewed words requires an explicit labeled action. Undo and redo use the revisioned source-word save path. Raw ASR candidates and transcript text are not overwritten by alignment.

The transcript pane highlights every word whose half-open interval contains the playback time. A gap highlights none; overlapping intervals are all highlighted and reported. Click selects without seeking, double click focuses the word editor, and **Replay word** uses configurable context padding. **Seek to Word** and **Play from word** are explicit playback actions. Manual scrolling disables automatic following until it is enabled again. The timeline follows playback without changing zoom.

## Export

Ordinary exports choose scope, datasets, and Simple JSON, CSV, or Detailed JSON. The preview and download use the same builder, so listed filenames and counts reflect generated output. One dataset from one video downloads one file. Multiple datasets or project media use a ZIP with only selected, nonempty datasets organized by media. Source-word Simple JSON contains exactly `start`, `end`, and `text`, with seconds rounded to milliseconds. The advanced complete-project option produces a full detailed JSON. The legacy comprehensive export endpoints remain available.

## Limits

Stable-ts may decline to align corrected text when its output cannot be mapped one-to-one onto the selected words. In that case, use the existing manual timestamp editor. Waveform RMS is a diagnostic signal, not a word boundary classifier. OCR and target subtitle pipelines are outside this iteration.
