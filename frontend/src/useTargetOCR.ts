import { useEffect, useState } from 'react'
import type { ReviewStatus } from './contracts'

export type SubtitleROI = { x: number; y: number; width: number; height: number }
export type SubtitleState = {
  id: string; media_id: string; run_id: string | null; start_ms: number; end_ms: number
  text: string; precision: string; review_status: ReviewStatus
  observation_ids: string[]; sequence_id?: string | null
  provenance: { method: string; model: string; confidence: number | null; needs_review?: boolean }
}
type OCRJob = { id: string; status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'; error: string | null; cancel_requested: number }
type OCRRun = { id: string; engine: string; engine_version: string; job_status: string; created_at: string; parameters: Record<string, unknown> }
type OCRChunk = { chunk_index: number; status: string; core_start_ms: number; core_end_ms: number }
type OCRSummary = { frames_sampled: number; ocr_calls: number; raw_text_variants: number; proposed_states: number; uncertain_transitions: number; duplicate_observations_consolidated: number }
export type TargetEvent = { id: string; media_id: string; run_id: string | null; start_ms: number; end_ms: number
  previous_state_id: string; current_state_id: string; before_text: string; after_text: string
  visual_event_type: string; human_event_type: string | null; review_status: 'unreviewed' | 'confirmed' | 'rejected' | 'uncertain'
  notes: string; invalidated: boolean; orphaned: boolean; source_fingerprint: string
  time_precision: string
  change_counts: { added: number; deleted: number; replaced: number }
  diff: Array<{ kind: string; before: string; after: string }> }
type EventDocument = { run_id: string | null; events: TargetEvent[] }
type OCRDocument = { job: OCRJob | null; run: OCRRun | null; runs: OCRRun[]; chunks: OCRChunk[]; summary: OCRSummary | null; states: SubtitleState[] }
const empty: OCRDocument = { job: null, run: null, runs: [], chunks: [], summary: null, states: [] }

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) { const body = await response.json().catch(() => null); throw new Error(body?.detail || `Request failed (${response.status})`) }
  return response.json() as Promise<T>
}

export function useTargetOCR(mediaId: string | null) {
  const [document, setDocument] = useState<OCRDocument>(empty)
  const [events, setEvents] = useState<TargetEvent[]>([])
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [error, setError] = useState('')
  const refresh = async () => {
    if (!mediaId) return
    const data = await json<OCRDocument>(await fetch(`/api/media/${mediaId}/target-ocr${selectedRunId ? `?run_id=${encodeURIComponent(selectedRunId)}` : ''}`))
    setDocument(data)
    setEvents(data.run ? (await json<EventDocument>(await fetch(`/api/media/${mediaId}/target-ocr/events?run_id=${encodeURIComponent(data.run.id)}`))).events : [])
    return data
  }
  useEffect(() => {
    setDocument(empty); setEvents([]); setError('')
    if (!mediaId) return
    let active = true
    const update = async () => {
      try { const data = await json<OCRDocument>(await fetch(`/api/media/${mediaId}/target-ocr${selectedRunId ? `?run_id=${encodeURIComponent(selectedRunId)}` : ''}`)); if (active) setDocument(data)
        const derived = data.run ? (await json<EventDocument>(await fetch(`/api/media/${mediaId}/target-ocr/events?run_id=${encodeURIComponent(data.run.id)}`))).events : []
        if (active) setEvents(derived) }
      catch (reason) { if (active) setError(String(reason)) }
    }
    void update()
    const timer = window.setInterval(() => void update(), 1500)
    return () => { active = false; window.clearInterval(timer) }
  }, [mediaId, selectedRunId])
  useEffect(() => { setSelectedRunId(null) }, [mediaId])
  const start = async (params: { mode: 'range' | 'entire'; start_ms: number; end_ms: number; fps: number; roi: SubtitleROI }) => {
    if (!mediaId) return
    try {
      const job = await json<OCRJob>(await fetch(`/api/media/${mediaId}/target-ocr/jobs`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(params),
      }))
      setDocument(current => ({ ...current, job })); setError('')
    } catch (reason) { setError(String(reason)) }
  }
  const cancel = async () => {
    if (!mediaId || !document.job) return
    try {
      const job = await json<OCRJob>(await fetch(`/api/media/${mediaId}/target-ocr/jobs/${document.job.id}/cancel`, { method: 'POST' }))
      setDocument(current => ({ ...current, job })); setError('')
    } catch (reason) { setError(String(reason)) }
  }
  const resume = async () => {
    if (!mediaId || !document.job) return
    try {
      const job = await json<OCRJob>(await fetch(`/api/media/${mediaId}/target-ocr/jobs/${document.job.id}/resume`, { method: 'POST' }))
      setDocument(current => ({ ...current, job })); setError('')
    } catch (reason) { setError(String(reason)) }
  }
  const post = async <T,>(path: string, body?: object): Promise<T> => {
    if (!mediaId) throw new Error('No media selected')
    return json<T>(await fetch(`/api/media/${mediaId}/target-ocr/${path}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}),
    }))
  }
  const insert = async (change: { start_ms: number; end_ms: number; text: string }) => {
    if (!document.run) throw new Error('Select an OCR run first')
    const result = await post<SubtitleState>('states/insert', { ...change, run_id: document.run.id })
    await refresh(); return result
  }
  const previewMerge = (stateIds: string[]) => post<{ state_ids: string[]; start_ms: number; end_ms: number; texts: string[]; equivalent: boolean; requires_confirmation: boolean; recommended_action: string }>('states/merge/preview', { state_ids: stateIds })
  const merge = async (stateIds: string[], confirmDifferent = false, text?: string) => {
    const result = await post<{ operation_id: string; state: SubtitleState }>('states/merge', { state_ids: stateIds, confirm_different: confirmDifferent, text })
    await refresh(); return result
  }
  const split = async (stateId: string, splitMs: number) => {
    const result = await post<{ operation_id: string; states: SubtitleState[] }>(`states/${stateId}/split`, { split_ms: splitMs })
    await refresh(); return result
  }
  const group = async (stateIds: string[]) => { const result = await post<{ sequence_id: string }>('states/group', { state_ids: stateIds }); await refresh(); return result }
  const toggleOperation = async (operationId: string, redo: boolean) => { await post(`operations/${operationId}/${redo ? 'redo' : 'undo'}`); await refresh() }
  const saveState = async (state: SubtitleState, change: Pick<SubtitleState, 'start_ms' | 'end_ms' | 'text' | 'review_status'>) => {
    if (!mediaId) return
    const updated = await json<SubtitleState>(await fetch(`/api/media/${mediaId}/target-ocr/states/${state.id}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(change),
    }))
    setDocument(current => ({ ...current, states: current.states.map(item => item.id === state.id ? updated : item) }))
    await refresh()
    return updated
  }
  const saveEvent = async (event: TargetEvent, change: Pick<TargetEvent, 'human_event_type' | 'review_status' | 'notes'>) => {
    if (!mediaId) throw new Error('No media selected')
    const updated = await json<TargetEvent>(await fetch(`/api/media/${mediaId}/target-ocr/events/${encodeURIComponent(event.id)}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...change, run_id: event.run_id, source_fingerprint: event.source_fingerprint }),
    }))
    setEvents(current => current.map(item => item.id === event.id ? updated : item))
    return updated
  }
  return { ...document, events, selectedRunId, selectRun: setSelectedRunId, error, refresh, start, cancel, resume,
    saveState, saveEvent, insert, previewMerge, merge, split, group, toggleOperation }
}
