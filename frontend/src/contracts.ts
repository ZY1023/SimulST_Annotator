/** API contract. Timeline values are integer milliseconds. */
export type ReviewStatus = 'unreviewed' | 'reviewed' | 'rejected'
export type TimePrecision = 'exact' | 'estimated' | 'frame'
export interface TimeSpan { start_ms: number; end_ms: number; precision: TimePrecision }
export interface Provenance { method: string; model: string | null; confidence: number | null }
export interface MediaAsset {
  id: string
  project_id: string
  filename: string
  display_name: string
  removed_at: string | null
  duration_ms: number
  video_url: string
  audio_url: string
  media_offset_ms: number
  created_at: string
}
export interface SourceToken {
  id: string; media_id: string; span: TimeSpan; text: string; language: string
  granularity: 'word' | 'character'; provenance: Provenance; review_status: ReviewStatus
}
export interface OcrObservation {
  id: string; media_id: string; frame_ms: number; raw_text: string
  roi: Record<string, unknown>; provenance: Provenance
}
export interface SubtitleScreenState {
  id: string; media_id: string; span: TimeSpan; text: string
  observation_ids: string[]; provenance: Provenance; review_status: ReviewStatus
}
export interface DerivedEvent {
  id: string; media_id: string; span: TimeSpan; event_type: string
  before_text: string; after_text: string; state_ids: string[]
  provenance: Provenance; review_status: ReviewStatus
}
export interface HumanAnnotation {
  id: string; project_id: string; media_id: string; tier_id: string; span: TimeSpan; text: string
  event_label: string | null; review_status: ReviewStatus; source_ids: string[]
  metadata: Record<string, unknown>; created_at: string; updated_at: string
}
export interface Tier {
  id: string; project_id: string; media_id: string; name: string; kind: string; sort_order: number
}
export interface Project {
  id: string; name: string; settings: Record<string, unknown>; storage_path: string; storage_rel_path: string; database_path: string
  archived_at: string | null; deleted_at: string | null
  created_at: string; updated_at: string; last_opened_at: string
}
