import { useEffect, useMemo, useRef, useState } from 'react'
import type { SourceWord, AlignmentPreview } from './useSourceTranscript'

export function activeWords(words: SourceWord[], timeMs: number) {
  return words.filter(word => word.speech_status !== 'not_spoken' && word.review_status !== 'rejected' && word.start_ms <= timeMs && timeMs < word.end_ms)
}

export function SourceTranscriptReview({ words, timeMs, selectedId, durationMs, onChoose, onEdit,
  onPlayRange, onNext, onPreview, onApply, onReject }: {
  words: SourceWord[]; timeMs: number; selectedId: string | null; durationMs: number
  onChoose: (word: SourceWord) => void; onEdit: (word: SourceWord) => void
  onPlayRange: (start: number, end: number) => void; onNext: () => void
  onPreview: (start: number, end: number) => Promise<AlignmentPreview>
  onApply: (preview: AlignmentPreview, confirmReviewed: boolean) => Promise<void>
  onReject: (previewId: string) => Promise<void>
}) {
  const [mode, setMode] = useState<'sentence' | 'window'>('sentence')
  const [windowSeconds, setWindowSeconds] = useState(20)
  const [follow, setFollow] = useState(true)
  const [padding, setPadding] = useState(200)
  const [preview, setPreview] = useState<AlignmentPreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const container = useRef<HTMLDivElement>(null)
  const ordered = useMemo(() => words.filter(word => word.speech_status !== 'not_spoken' && word.review_status !== 'rejected').sort((a, b) => a.start_ms - b.start_ms || a.id.localeCompare(b.id)), [words])
  const active = activeWords(ordered, timeMs)
  const selected = ordered.find(word => word.id === selectedId)
  const anchor = follow && active[0] ? active[0] : selected ?? active[0]
  const focusIndex = Math.max(0, ordered.findIndex(word => word.id === anchor?.id))
  const visible = useMemo(() => {
    if (mode === 'window') {
      const center = anchor?.start_ms ?? timeMs
      const half = windowSeconds * 500
      return ordered.filter(word => word.end_ms >= center - half && word.start_ms <= center + half)
    }
    let first = focusIndex, last = focusIndex
    while (first > 0 && !/[.!?]$/.test(ordered[first - 1].text.trim())) first--
    while (last < ordered.length - 1 && !/[.!?]$/.test(ordered[last].text.trim())) last++
    return ordered.slice(first, last + 1)
  }, [ordered, mode, windowSeconds, anchor?.id, timeMs, focusIndex])
  useEffect(() => {
    if (!follow || !active.length) return
    const token = container.current?.querySelector<HTMLElement>(`[data-review-id="${active[0].id}"]`)
    if (token && container.current) {
      const box = container.current.getBoundingClientRect()
      const item = token.getBoundingClientRect()
      if (item.top < box.top) container.current.scrollTop += item.top - box.top
      else if (item.bottom > box.bottom) container.current.scrollTop += item.bottom - box.bottom
    }
  }, [active[0]?.id, follow, visible])
  const runPreview = async () => {
    if (!anchor) return
    setBusy(true); setError(''); setPreview(null)
    try {
      const spanStart = mode === 'window' ? Math.max(0, anchor.start_ms - windowSeconds * 500) : visible[0]?.start_ms ?? anchor.start_ms
      const spanEnd = mode === 'window' ? Math.min(durationMs, anchor.start_ms + windowSeconds * 500) : visible.at(-1)?.end_ms ?? anchor.end_ms
      setPreview(await onPreview(spanStart, spanEnd))
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }
  return <section className="transcript-review" aria-label="Source Transcript Review">
    <header><strong>Source Transcript Review</strong><label>View <select value={mode} onChange={event => setMode(event.target.value as typeof mode)}><option value="sentence">Sentence</option><option value="window">Time window</option></select></label>{mode === 'window' && <label>Seconds <input type="number" min="2" max="60" value={windowSeconds} onChange={event => setWindowSeconds(Math.max(2, Math.min(60, Number(event.target.value) || 20)))} /></label>}<label className="review-follow"><input type="checkbox" checked={follow} onChange={event => setFollow(event.target.checked)} /> Auto-follow</label>{anchor && <button className="review-replay" onClick={() => onPlayRange(Math.max(0, anchor.start_ms - padding), Math.min(durationMs, anchor.end_ms + padding))}>Replay</button>}<details className="review-tools"><summary>More</summary><div className="review-tools-content">
      {active.length > 1 && <p className="review-ambiguity">{active.length} word intervals overlap at this playback position.</p>}
      {anchor && <div className="review-details"><span>{anchor.text} · {anchor.start_ms}–{anchor.end_ms} ms</span><label>Context <input type="number" min="0" max="3000" step="50" value={padding} onChange={event => setPadding(Math.max(0, Math.min(3000, Number(event.target.value) || 0)))} /> ms</label><button onClick={() => onPlayRange(Math.max(0, anchor.start_ms - padding), Math.min(durationMs, anchor.end_ms + padding))}>Replay word</button><button onClick={onNext}>Next unreviewed</button></div>}
      {anchor && <div className="review-align"><button disabled={busy} onClick={() => void runPreview()}>{busy ? 'Aligning…' : 'Preview local alignment'}</button><small>Uses current corrected text. Maximum 30 seconds.</small></div>}
      {preview && <div className="alignment-preview"><strong>Proposed timing · {preview.changes.length} words</strong><div className="alignment-changes">{preview.changes.map(change => <div key={change.word_id}>{change.text} · {change.old_start_ms}–{change.old_end_ms} → {change.proposed_start_ms}–{change.proposed_end_ms} ms{change.review_status === 'reviewed' ? ' · reviewed' : ''}</div>)}</div><button onClick={() => void onReject(preview.id).then(() => setPreview(null)).catch(reason => setError(String(reason)))}>Reject</button><button onClick={() => void onApply(preview, preview.reviewed_word_count > 0).then(() => setPreview(null)).catch(reason => setError(String(reason)))}>Apply{preview.reviewed_word_count ? ` including ${preview.reviewed_word_count} reviewed` : ''}</button></div>}
      {error && <p role="alert" className="word-attention">{error}</p>}
    </div></details></header>
    <div ref={container} className="transcript-text" onWheel={() => setFollow(false)} onTouchMove={() => setFollow(false)}>{visible.map(word => <button key={word.id} data-review-id={word.id} title={`${word.text} · ${word.start_ms}–${word.end_ms} ms`} className={`${active.some(item => item.id === word.id) ? 'spoken' : ''} ${selectedId === word.id ? 'chosen' : ''} ${word.attention.length && word.review_status !== 'reviewed' ? 'timing-review' : ''}`} onClick={() => { setFollow(false); onChoose(word) }} onDoubleClick={() => { setFollow(false); onEdit(word) }}>{word.text}</button>)}{!visible.length && <span>No active source words in this view.</span>}</div>
  </section>
}

