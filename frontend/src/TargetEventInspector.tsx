import type { TargetEvent } from './useTargetOCR'

type Draft = Pick<TargetEvent, 'human_event_type' | 'review_status' | 'notes'>
const types = ['appearance', 'addition', 'deletion', 'replacement', 'disappearance', 'mixed', 'layout_only', 'uncertain']

export function TargetEventInspector({ event, draft, onChange, onPrevious, onNext, onSeek, onReplay, onSave, onReviewNext }: {
  event: TargetEvent; draft: Draft; onChange: (value: Draft) => void
  onPrevious: () => void; onNext: () => void; onSeek: () => void; onReplay: () => void
  onSave: () => void; onReviewNext: () => void
}) {
  return <div className="editor-fields target-event-editor">
    {event.invalidated && <p className="event-invalidated" role="status">Source subtitle states changed. Review this event again; the previous human label and notes are preserved.{event.orphaned ? ' This transition is no longer active.' : ''}</p>}
    <p className="editor-footnote">Observed transition at {event.start_ms} ms · {event.time_precision ?? 'estimated'} timing. Visual change only; no linguistic classification.</p>
    <div className="event-texts"><div><strong>Previous complete subtitle state</strong><p>{event.before_text || '∅ (blank)'}</p></div><div><strong>Current complete subtitle state</strong><p>{event.after_text || '∅ (blank)'}</p></div></div>
    <div className="event-diff"><strong>Character-level change</strong><div>{event.diff.map((part, index) => <span key={index} className={`event-diff-${part.kind}`} title={part.kind}>{part.kind === 'delete' ? part.before : part.kind === 'replace' ? `${part.before} → ${part.after}` : part.after}</span>)}</div><small>Added {event.change_counts.added} · Deleted {event.change_counts.deleted} · Replaced {event.change_counts.replaced}</small></div>
    <div className="field-grid"><label>Detected visual type<input readOnly value={event.visual_event_type.replaceAll('_', ' ')} /></label><label>Human-reviewed visual type<select value={draft.human_event_type ?? ''} onChange={e => onChange({ ...draft, human_event_type: e.target.value || null })}><option value="">Use detected type</option>{types.map(type => <option key={type} value={type}>{type.replaceAll('_', ' ')}</option>)}</select></label></div>
    <label>Review status<select value={draft.review_status} onChange={e => onChange({ ...draft, review_status: e.target.value as Draft['review_status'] })}><option value="unreviewed">Unreviewed</option><option value="confirmed">Confirmed</option><option value="rejected">Rejected false positive</option><option value="uncertain">Uncertain</option></select></label>
    <label>Annotation notes<textarea value={draft.notes} onChange={e => onChange({ ...draft, notes: e.target.value })} maxLength={4000} placeholder="Describe what is visible; avoid inferring translation quality" /></label>
    <div className="editor-actions"><button className="ui-button subtle" onClick={onPrevious}>Previous event</button><button className="ui-button subtle" onClick={onNext}>Next event</button><button className="ui-button subtle" onClick={onSeek}>Seek</button><button className="ui-button subtle" onClick={onReplay}>Replay</button><button className="ui-button primary" onClick={onSave} disabled={event.orphaned}>Save</button><button className="ui-button primary" onClick={onReviewNext} disabled={event.orphaned}>Review &amp; Next</button></div>
  </div>
}
