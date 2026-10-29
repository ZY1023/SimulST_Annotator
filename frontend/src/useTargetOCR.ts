import { useEffect, useState } from 'react'
import type { ReviewStatus } from './contracts'

export type SubtitleROI = { x: number; y: number; width: number; height: number }
export type SubtitleState = {
  id: string; media_id: string; run_id: string | null; start_ms: number; end_ms: number
  text: string; precision: string; review_status: ReviewStatus
  observation_ids: string[]; provenance: { method: string; model: string; confidence: number | null; needs_review?: boolean }
}
type OCRJob = { id: string; status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'; error: string | null; cancel_requested: number }
type OCRRun = { id: string; engine: string; engine_version: string; parameters: Record<string, unknown> }
type OCRDocument = { job: OCRJob | null; run: OCRRun | null; states: SubtitleState[] }

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) { const body = await response.json().catch(() => null); throw new Error(body?.detail || `Request failed (${response.status})`) }
  return response.json() as Promise<T>
}

export function useTargetOCR(mediaId: string | null) {
  const [document, setDocument] = useState<OCRDocument>({ job: null, run: null, states: [] })
  const [error, setError] = useState('')
  const refresh = async () => {
    if (!mediaId) return
    const data = await json<OCRDocument>(await fetch(`/api/media/${mediaId}/target-ocr`))
    setDocument(data)
    return data
  }
  useEffect(() => {
    setDocument({ job: null, run: null, states: [] }); setError('')
    if (!mediaId) return
    let active = true
    const update = async () => {
      try { const data = await json<OCRDocument>(await fetch(`/api/media/${mediaId}/target-ocr`)); if (active) setDocument(data) }
      catch (reason) { if (active) setError(String(reason)) }
    }
    void update()
    const timer = window.setInterval(() => void update(), 1500)
    return () => { active = false; window.clearInterval(timer) }
  }, [mediaId])
  const start = async (params: { start_ms: number; end_ms: number; fps: number; roi: SubtitleROI }) => {
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
  const saveState = async (state: SubtitleState, change: Pick<SubtitleState, 'start_ms' | 'end_ms' | 'text' | 'review_status'>) => {
    if (!mediaId) return
    const updated = await json<SubtitleState>(await fetch(`/api/media/${mediaId}/target-ocr/states/${state.id}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(change),
    }))
    setDocument(current => ({ ...current, states: current.states.map(item => item.id === state.id ? updated : item) }))
    return updated
  }
  return { ...document, error, refresh, start, cancel, saveState }
}
