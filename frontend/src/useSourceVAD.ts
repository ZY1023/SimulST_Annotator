import { useCallback, useEffect, useRef, useState } from 'react'

export type VADInterval = { id: string; run_id: string; media_id: string; kind: 'speech' | 'nonspeech'; start_ms: number; end_ms: number; precision: string; review_status: string }
export type VADJob = { id: string; status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'; error: string | null; cancel_requested: number }
export type VADRun = { id: string; engine: string; model_version: string; parameters: Record<string, number> }
type Document = { job: VADJob | null; run: VADRun | null; intervals: VADInterval[] }

async function read<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => null)
  if (!response.ok) throw new Error(body?.detail || `VAD request failed (${response.status})`)
  return body as T
}

export function useSourceVAD(mediaId: string | null) {
  const [document, setDocument] = useState<Document>({ job: null, run: null, intervals: [] })
  const [error, setError] = useState('')
  const currentMedia = useRef(mediaId)
  currentMedia.current = mediaId
  const refresh = useCallback(async () => {
    if (!mediaId) return
    try {
      const result = await fetch(`/api/media/${mediaId}/vad`).then(read<Document>)
      if (currentMedia.current === mediaId) { setDocument(result); setError('') }
    } catch (reason) { if (currentMedia.current === mediaId) setError(String(reason)) }
  }, [mediaId])
  useEffect(() => {
    setDocument({ job: null, run: null, intervals: [] }); setError('')
    void refresh()
  }, [refresh])
  useEffect(() => {
    if (!mediaId || !document.job || !['queued', 'running'].includes(document.job.status)) return
    const timer = window.setInterval(() => { void refresh() }, 1500)
    return () => window.clearInterval(timer)
  }, [mediaId, document.job?.id, document.job?.status, refresh])
  const start = async () => {
    if (!mediaId) return
    try {
      await fetch(`/api/media/${mediaId}/vad/jobs`, { method: 'POST' }).then(read<VADJob>)
      await refresh()
    } catch (reason) { setError(String(reason)) }
  }
  const cancel = async () => {
    if (!mediaId || !document.job) return
    try {
      await fetch(`/api/media/${mediaId}/vad/jobs/${document.job.id}/cancel`, { method: 'POST' }).then(read<VADJob>)
      await refresh()
    } catch (reason) { setError(String(reason)) }
  }
  return { ...document, error, refresh, start, cancel }
}
