import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReviewStatus, TimePrecision } from './contracts'

export type SourceWord = {
  id: string; media_id: string; start_ms: number; end_ms: number; text: string
  precision: TimePrecision; review_status: ReviewStatus; edit_status: string
  speech_status: 'spoken' | 'not_spoken' | 'uncertain'
  attention: string[]; asr_run_id: string | null; asr_candidate_id?: string | null; provenance: Record<string, unknown>
}
type Candidate = { id: string; text: string; raw_start_seconds: number | null; raw_end_seconds: number | null; issues: string[] }
type SourceDocument = { revision: number; words: SourceWord[]; candidates_needing_attention: Candidate[] }
export type AlignmentChange = { word_id: string; text: string; old_start_ms: number; old_end_ms: number; proposed_start_ms: number; proposed_end_ms: number; review_status: ReviewStatus }
export type AlignmentPreview = { id: string; status: string; changes: AlignmentChange[]; reviewed_word_count: number }
export type ASRJob = { id: string; media_id: string; model: string; status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'; error: string | null; cancel_requested: number; applied: number; device: string | null }
type ASRStatus = { job: ASRJob | null; word_count: number }
export type ASRPreferences = { auto_transcribe: boolean; default_model: 'small' | 'medium' }

async function read<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => null)
  if (!response.ok) throw new Error(body?.detail || `Request failed (${response.status})`)
  return body as T
}
const json = (method: string, body: unknown) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export function useSourceTranscript(mediaId: string | null) {
  const [words, setWords] = useState<SourceWord[]>([])
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [job, setJob] = useState<ASRJob | null>(null)
  const [saveStatus, setSaveStatus] = useState<'idle' | 'unsaved' | 'saving' | 'saved' | 'failed'>('idle')
  const [error, setError] = useState('')
  const [preferences, setPreferences] = useState<ASRPreferences>({ auto_transcribe: true, default_model: 'small' })
  const currentMedia = useRef(mediaId)
  currentMedia.current = mediaId
  const revision = useRef(0)
  const dirty = useRef(false)
  const version = useRef(0)
  const queue = useRef<Promise<boolean>>(Promise.resolve(true))
  const undoStack = useRef<SourceWord[][]>([])
  const redoStack = useRef<SourceWord[][]>([])

  const refresh = useCallback(async () => {
    if (!mediaId || dirty.current) return
    try {
      const [document, status] = await Promise.all([
        fetch(`/api/media/${mediaId}/source-words`).then(read<SourceDocument>),
        fetch(`/api/media/${mediaId}/asr`).then(read<ASRStatus>),
      ])
      if (currentMedia.current !== mediaId) return
      revision.current = document.revision
      setWords(document.words); setCandidates(document.candidates_needing_attention); setJob(status.job)
      setError('')
    } catch (reason) { if (currentMedia.current === mediaId) setError(reason instanceof Error ? reason.message : String(reason)) }
  }, [mediaId])

  useEffect(() => {
    dirty.current = false; version.current += 1; revision.current = 0
    undoStack.current = []; redoStack.current = []; queue.current = Promise.resolve(true)
    setWords([]); setCandidates([]); setJob(null); setSaveStatus('idle'); setError('')
    void refresh()
  }, [refresh])
  useEffect(() => { void fetch('/api/asr/settings').then(read<ASRPreferences>).then(setPreferences).catch(() => undefined) }, [])
  useEffect(() => {
    if (!mediaId || !job || !['queued', 'running'].includes(job.status)) return
    const timer = window.setInterval(() => { void refresh() }, 2000)
    return () => window.clearInterval(timer)
  }, [mediaId, job?.id, job?.status, refresh])

  const mutate = (next: SourceWord[]) => {
    undoStack.current.push(words.map(word => ({ ...word, attention: [...word.attention] })))
    redoStack.current = []
    version.current += 1; dirty.current = true
    setWords(next); setSaveStatus('unsaved')
  }
  const undo = () => { const before = undoStack.current.pop(); if (!before) return; redoStack.current.push(words); version.current += 1; dirty.current = true; setWords(before); setSaveStatus('unsaved') }
  const redo = () => { const after = redoStack.current.pop(); if (!after) return; undoStack.current.push(words); version.current += 1; dirty.current = true; setWords(after); setSaveStatus('unsaved') }
  const saveNow = (): Promise<boolean> => {
    if (!mediaId || !dirty.current) return queue.current
    const snapshot = words.map(word => ({ id: word.id, start_ms: word.start_ms, end_ms: word.end_ms,
      text: word.text, precision: word.precision, review_status: word.review_status,
      speech_status: word.speech_status }))
    const currentVersion = version.current
    setSaveStatus('saving')
    const task = queue.current.catch(() => false).then(async () => {
      try {
        const document = await fetch(`/api/media/${mediaId}/source-words`, json('PUT', {
          expected_revision: revision.current, words: snapshot,
        })).then(read<SourceDocument>)
        revision.current = document.revision
        if (currentVersion === version.current) {
          dirty.current = false; setWords(document.words); setCandidates(document.candidates_needing_attention)
          setSaveStatus('saved'); setError('')
        }
        return true
      } catch (reason) {
        if (currentVersion === version.current) { setSaveStatus('failed'); setError(reason instanceof Error ? reason.message : String(reason)) }
        return false
      }
    })
    queue.current = task
    return task
  }
  useEffect(() => {
    if (!dirty.current) return
    const timer = window.setTimeout(() => { void saveNow() }, 650)
    return () => window.clearTimeout(timer)
  }, [words, mediaId])
  const flush = async () => dirty.current ? saveNow() : queue.current
  const startJob = async (model: 'small' | 'medium', rerun = false) => {
    if (!(await flush()) || !mediaId) return
    try {
      const next = await fetch(`/api/media/${mediaId}/asr/jobs`, json('POST', { model, rerun })).then(read<ASRJob>)
      if (currentMedia.current === mediaId) { setJob(next); setError('') }
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
  }
  const cancelJob = async () => {
    if (!mediaId || !job) return
    try { const next = await fetch(`/api/media/${mediaId}/asr/jobs/${job.id}/cancel`, { method: 'POST' }).then(read<ASRJob>); if (currentMedia.current === mediaId) setJob(next) }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
  }
  const updatePreferences = async (value: ASRPreferences) => {
    setPreferences(await fetch('/api/asr/settings', json('PUT', value)).then(read<ASRPreferences>))
  }
  const previewAlignment = async (startMs: number, endMs: number, model: 'small' | 'medium'): Promise<AlignmentPreview> => {
    if (!mediaId || !(await flush())) throw new Error('Save words before alignment')
    return fetch(`/api/media/${mediaId}/alignment/preview`, json('POST', {
      start_ms: startMs, end_ms: endMs, model, expected_revision: revision.current,
    })).then(read<AlignmentPreview>)
  }
  const rejectAlignment = async (previewId: string) => {
    if (!mediaId) return
    await fetch(`/api/media/${mediaId}/alignment/previews/${previewId}/reject`, { method: 'POST' }).then(read<{ status: string }>)
  }
  const applyAlignment = async (preview: AlignmentPreview, confirmReviewed = false) => {
    if (!mediaId || !(await flush())) throw new Error('Save words before applying alignment')
    const before = words.map(word => ({ ...word, attention: [...word.attention] }))
    const document = await fetch(`/api/media/${mediaId}/alignment/previews/${preview.id}/apply`, json('POST', {
      confirm_reviewed: confirmReviewed,
    })).then(read<SourceDocument>)
    undoStack.current.push(before); redoStack.current = []
    revision.current = document.revision; dirty.current = false; version.current += 1
    setWords(document.words); setCandidates(document.candidates_needing_attention); setSaveStatus('saved')
  }
  const simpleExport = async () => {
    if (!mediaId || !(await flush())) return
    try {
      const response = await fetch(`/api/media/${mediaId}/source-words/simple-json`)
      if (!response.ok) throw new Error(`Export failed (${response.status})`)
      const header = response.headers.get('Content-Disposition') ?? ''
      const filename = decodeURIComponent(/filename\*=UTF-8''([^;]+)/i.exec(header)?.[1] ?? 'source_words.json')
      const url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a'); link.href = url; link.download = filename
      document.body.appendChild(link); link.click(); link.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
  }
  return { words, candidates, job, saveStatus, error, preferences, updatePreferences,
    refresh, mutate, undo, redo, canUndo: undoStack.current.length > 0, canRedo: redoStack.current.length > 0,
    saveNow, flush, startJob, cancelJob, simpleExport, previewAlignment, rejectAlignment, applyAlignment, dirty }
}
