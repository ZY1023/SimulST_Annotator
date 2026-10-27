"""Contracts for future local OCR and subtitle analysis; no processing runs here."""
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class SubtitleROI:
    """Normalized frame coordinates, independent of video resolution."""
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class ProjectAnalysisSettings:
    """Project-specific values planned for projects.settings_json['analysis']."""
    roi: SubtitleROI
    ocr_language: str
    frame_sample_interval_ms: int
    refine_frame_boundaries: bool
    text_change_sensitivity: float
    output_gap_threshold_ms: int
    confidence_review_threshold: float


@dataclass(frozen=True)
class FrameSample:
    media_id: str
    frame_ms: int
    image_bytes: bytes


@dataclass(frozen=True)
class OCRResult:
    frame_ms: int
    raw_text: str
    confidence: float | None
    engine: str
    engine_version: str


@dataclass(frozen=True)
class StoredOCRObservation:
    id: str
    media_id: str
    result: OCRResult


@dataclass(frozen=True)
class ScreenStateCandidate:
    start_ms: int
    end_ms: int
    text: str  # Full visible text, including an empty screen after disappearance.
    observation_ids: tuple[str, ...]


@dataclass(frozen=True)
class TextChangeCandidate:
    start_ms: int
    end_ms: int
    kind: Literal['appearance', 'disappearance', 'insertion', 'deletion', 'replacement']
    before_text: str
    after_text: str
    state_ids: tuple[str, str]


class FrameSampler(Protocol):
    def sample(self, media_path: str, settings: ProjectAnalysisSettings) -> list[FrameSample]: ...


class OCRRecognizer(Protocol):
    def recognize(self, frame: FrameSample, settings: ProjectAnalysisSettings) -> OCRResult: ...


class ScreenStateBuilder(Protocol):
    def build(self, observations: list[StoredOCRObservation]) -> list[ScreenStateCandidate]: ...


class TextChangeDetector(Protocol):
    def compare(self, states: list[ScreenStateCandidate]) -> list[TextChangeCandidate]: ...
