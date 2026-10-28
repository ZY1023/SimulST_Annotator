import type { SourceWord } from './useSourceTranscript'
import type { ReviewStatus } from './contracts'
import type { RefObject } from 'react'

export function SourceWordInspector({ word, durationMs, onEdit, onSave, onSeek, onPlay, onSplit, onMerge,
  onDelete, onUndo, onRedo, canUndo, canRedo, onNext, textRef }: {
  word: SourceWord; durationMs: number; onEdit: (change: Partial<SourceWord>) => void
  onSave: () => void; onSeek: () => void; onPlay: () => void; onSplit: () => void; onMerge: () => void
  onDelete: () => void; onUndo: () => void; onRedo: () => void; canUndo: boolean; canRedo: boolean
  onNext: () => void; textRef: RefObject<HTMLTextAreaElement | null>
}) {
  return <div className="editor-fields source-word-editor">
    <label>English word<textarea ref={textRef} value={word.text} onChange={event => onEdit({ text: event.target.value })} placeholder="Recognized word" /></label>
    <div className="field-grid"><label>Start (ms)<input type="number" min="0" max={durationMs} value={word.start_ms} onChange={event => onEdit({ start_ms: Number(event.target.value) })} /></label><label>End (ms)<input type="number" min="1" max={durationMs} value={word.end_ms} onChange={event => onEdit({ end_ms: Number(event.target.value) })} /></label></div>
    <div className="field-grid"><label>Review status<select value={word.review_status} onChange={event => onEdit({ review_status: event.target.value as ReviewStatus })}><option value="unreviewed">Unreviewed</option><option value="reviewed">Reviewed</option><option value="rejected">Rejected</option></select></label><label>Time precision<select value={word.precision} onChange={event => onEdit({ precision: event.target.value as SourceWord['precision'] })}><option value="estimated">Estimated</option><option value="frame">Frame</option><option value="exact">Exact</option></select></label></div>
    <label>Speech status<select value={word.speech_status} onChange={event => onEdit({ speech_status: event.target.value as SourceWord['speech_status'], review_status: event.target.value === 'not_spoken' ? 'rejected' : 'unreviewed' })}><option value="spoken">Spoken</option><option value="uncertain">Uncertain</option><option value="not_spoken">Not Spoken / Hallucination</option></select></label>
    {word.attention.length > 0 && word.review_status !== 'reviewed' && <p className="word-attention">Review timing: {word.attention.join(', ').replaceAll('_', ' ')}</p>}
    <details className="word-more"><summary>More word actions and provenance</summary><div className="editor-actions word-actions"><button className="ui-button subtle" onClick={onSplit}>Split</button><button className="ui-button subtle" onClick={onMerge}>Merge next</button><button className="ui-button danger" onClick={onDelete}>Delete word</button><button className="ui-button subtle" onClick={onUndo} disabled={!canUndo}>Undo</button><button className="ui-button subtle" onClick={onRedo} disabled={!canRedo}>Redo</button></div><p className="editor-footnote">{word.edit_status === 'machine' ? 'ASR prediction · not human verified' : 'Editable source transcript'} · Source {String(word.provenance.method ?? 'unknown')}{word.provenance.model ? ` / ${String(word.provenance.model)}` : ''}{typeof word.provenance.confidence === 'number' ? ` · confidence ${(word.provenance.confidence * 100).toFixed(0)}%` : ''} · ID {word.id.slice(0, 8)}</p></details>
    <div className="editor-actions word-actions"><button className="ui-button primary" onClick={onSave}>Save word</button><button className="ui-button subtle" onClick={onSeek}>Seek to Word</button><button className="ui-button subtle" onClick={onPlay}>Play from word</button><button className="ui-button subtle" onClick={onNext}>Next unreviewed</button></div>
  </div>
}

