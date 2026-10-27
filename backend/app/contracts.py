"""Public API contracts. All timeline positions are integer milliseconds."""
from enum import Enum
from pydantic import BaseModel, Field, model_validator


class ReviewStatus(str, Enum):
    unreviewed = "unreviewed"
    reviewed = "reviewed"
    rejected = "rejected"


class TimePrecision(str, Enum):
    exact = "exact"
    estimated = "estimated"
    frame = "frame"


class TimeSpan(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    precision: TimePrecision = TimePrecision.estimated

    @model_validator(mode="after")
    def ordered(self):
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms must be greater than or equal to start_ms")
        return self


class Provenance(BaseModel):
    method: str
    model: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class MediaAsset(BaseModel):
    id: str
    project_id: str
    filename: str
    display_name: str
    removed_at: str | None = None
    duration_ms: int = Field(ge=0)
    video_url: str
    audio_url: str
    media_offset_ms: int = 0
    created_at: str


class SourceToken(BaseModel):
    id: str
    media_id: str
    span: TimeSpan
    text: str
    language: str
    granularity: str  # word or character
    provenance: Provenance
    review_status: ReviewStatus = ReviewStatus.unreviewed


class OcrObservation(BaseModel):
    id: str
    media_id: str
    frame_ms: int = Field(ge=0)
    raw_text: str
    roi: dict
    provenance: Provenance


class SubtitleScreenState(BaseModel):
    id: str
    media_id: str
    span: TimeSpan
    text: str
    observation_ids: list[str]
    provenance: Provenance
    review_status: ReviewStatus = ReviewStatus.unreviewed


class DerivedEvent(BaseModel):
    id: str
    media_id: str
    span: TimeSpan
    event_type: str
    before_text: str
    after_text: str
    state_ids: list[str]
    provenance: Provenance
    review_status: ReviewStatus = ReviewStatus.unreviewed


class HumanAnnotation(BaseModel):
    id: str
    project_id: str
    media_id: str
    tier_id: str
    span: TimeSpan
    text: str
    event_label: str | None = None
    review_status: ReviewStatus = ReviewStatus.unreviewed
    source_ids: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    created_at: str
    updated_at: str


class Project(BaseModel):
    id: str
    name: str
    settings: dict = Field(default_factory=dict)
    storage_path: str
    storage_rel_path: str
    database_path: str
    archived_at: str | None = None
    deleted_at: str | None = None
    created_at: str
    updated_at: str
    last_opened_at: str

