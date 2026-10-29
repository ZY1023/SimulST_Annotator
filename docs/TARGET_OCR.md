# Target subtitle OCR MVP

Install the optional local OCR dependencies in the backend Python environment:

```powershell
cd backend
python -m pip install -r requirements-ocr.txt
```

FFmpeg and FFprobe must be on `PATH`. PaddleOCR downloads its Chinese PP-OCRv5 mobile detection and recognition models on the first run, then uses the local cache. The mobile models support both Simplified and Traditional Chinese. No media is uploaded by this pipeline. The original MP4 is never modified.

Open a media item, choose **Set ROI / range**, and drag or resize the box on the video. Its fractional `x`, `y`, `width`, and `height` refer to the original video frame, even if the player has letterboxing. Set start/end in milliseconds and a sample rate (default 5 fps). The initial end is capped at 30 seconds. **Run selected range** creates a background job; the status strip shows progress and allows cancellation. Processing a long range can take several minutes on CPU.

The worker samples cropped frames with FFmpeg, skips an OCR inference when the crop is nearly unchanged, and still stores an observation for every sampled frame. It stores raw recognized text, confidence, timestamp, crop parameters, and whether the previous recognition was reused in `ocr_observations`. Each run has its own `ocr_run` and stable IDs. Exact changes in recognized full text create consecutive `subtitle_screen_states`; an empty-text state records complete subtitle disappearance. Similar-looking strings are kept separate when they are not exactly equal. Low-confidence states are marked for review. The worker does not decide whether a change is a semantic revision.

Click a state on the timeline to seek the video to its start and edit its complete text and start/end times. **Save state** persists corrections to `subtitle_screen_states`; **Review & Next** marks it reviewed and advances. Raw observations remain unchanged. Switching selections/media/projects and exporting also saves the current state edit. A later OCR run creates a new run; previous runs and their corrections remain in SQLite, while the timeline shows the latest completed run.

In Export, select the current media and only **Target subtitle states**, then Simple JSON. It exports reviewed, nonempty states from the latest run as a single array, for example:

```json
[
  {"start": 1.2, "end": 1.8, "text": "我們需要"},
  {"start": 1.8, "end": 2.4, "text": "我們必須"}
]
```

Detailed and complete exports retain provenance and raw data. Recognition quality depends on subtitle size, contrast, crop accuracy, video compression, and sampling rate. Timing resolution is limited by the sampled frames and can be adjusted manually.
