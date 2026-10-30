import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent } from 'react'
import type { MediaAsset, Project } from './contracts'
import { actionEntries, annotationBindings, conflictFor, defaultBindings, keyFromEvent, reservedBinding, saveShortcutSettings, shortcutGroups } from './shortcuts'
import type { ShortcutAction, ShortcutSettingsValue } from './shortcuts'
import type { ASRPreferences } from './useSourceTranscript'

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  const body = await response.json().catch(() => null)
  if (!response.ok) throw new Error(body?.detail || `Request failed (${response.status})`)
  return body as T
}
const jsonRequest = (method: string, body?: unknown): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
const sizeText = (bytes: number) => bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`

export function Modal({ title, onClose, children, busy = false }: { title: string; onClose: () => void; children: React.ReactNode; busy?: boolean }) {
  const panel = useRef<HTMLDivElement>(null)
  const closeRef = useRef(onClose)
  const busyRef = useRef(busy)
  closeRef.current = onClose
  busyRef.current = busy
  useEffect(() => {
    const first = panel.current?.querySelector<HTMLElement>('input, button:not([disabled]), select, textarea')
    first?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); if (!busyRef.current) closeRef.current(); return }
      if (event.key !== 'Tab' || !panel.current) return
      const items = [...panel.current.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])')]
      if (!items.length) return
      const firstItem = items[0], lastItem = items[items.length - 1]
      if (!panel.current.contains(document.activeElement)) { event.preventDefault(); firstItem.focus(); return }
      if (event.shiftKey && document.activeElement === firstItem) { event.preventDefault(); lastItem.focus() }
      else if (!event.shiftKey && document.activeElement === lastItem) { event.preventDefault(); firstItem.focus() }
    }
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('keydown', onKey) }
  }, [])
  return <div className="project-overlay" onPointerDown={event => { if (event.target === event.currentTarget && !busy) onClose() }}>
    <div className="workspace-dialog" ref={panel} role="dialog" aria-modal="true" aria-label={title}>
      <header><h2>{title}</h2><button className="dialog-close" onClick={onClose} disabled={busy} aria-label="Close dialog">×</button></header>
      {children}
      <footer><button className="ui-button subtle" onClick={onClose} disabled={busy}>Cancel</button></footer>
    </div>
  </div>
}

type StorageInfo = {
  project: Project; media_count: number; annotation_count: number; total_storage_bytes: number
  media: Array<{ id: string; display_name: string; duration_ms: number; created_at: string; removed_at: string | null; annotation_count: number; original_path: string; audio_path: string }>
  tiers: Array<{ id: string; media_id: string; name: string; annotation_count: number }>
  research_counts: Record<string, number>
  files: Array<{ relative_path: string; size_bytes: number; reference_count: number; category: string }>
}

export function ProjectManager({ onClose, onOpen, onChanged, openFolder, beforeChange }: {
  onClose: () => void; onOpen: (id: string) => Promise<boolean>; onChanged: () => Promise<void>; openFolder: (id: string) => Promise<void>; beforeChange: () => Promise<boolean>
}) {
  const [projects, setProjects] = useState<Project[]>([])
  const [summaries, setSummaries] = useState<Record<string, StorageInfo>>({})
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<'recent' | 'name' | 'modified'>('recent')
  const [mode, setMode] = useState<'list' | 'create' | 'rename' | 'delete'>('list')
  const [target, setTarget] = useState<Project | null>(null)
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
  const nameInput = useRef<HTMLInputElement>(null)
  useEffect(() => { if (mode !== 'list') nameInput.current?.focus() }, [mode])
  const reload = async () => {
    const list = await api<Project[]>('/api/projects?include_inactive=true')
    setProjects(list)
    const details = await Promise.all(list.map(item => api<StorageInfo>(`/api/projects/${item.id}/storage`).catch(() => null)))
    setSummaries(Object.fromEntries(details.map((item, index) => [list[index].id, item]).filter((entry): entry is [string, StorageInfo] => entry[1] !== null)))
    await onChanged()
  }
  useEffect(() => { void reload().catch(reason => setError(String(reason))) }, [])
  const run = async (operation: () => Promise<void>) => {
    if (busyRef.current) return
    busyRef.current = true; setBusy(true); setError('')
    try { if (!(await beforeChange())) throw new Error('Save pending annotation changes before changing projects'); await operation() } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { busyRef.current = false; setBusy(false) }
  }
  const displayed = projects.filter(item => item.name.toLowerCase().includes(query.toLowerCase()) || item.id.includes(query))
    .sort((a, b) => sort === 'name' ? a.name.localeCompare(b.name) :
      sort === 'modified' ? b.updated_at.localeCompare(a.updated_at) : b.last_opened_at.localeCompare(a.last_opened_at))
  const reset = () => { setMode('list'); setTarget(null); setName(''); setError('') }
  return <Modal title="Project Manager" onClose={onClose} busy={busy}>
    {error && <p className="dialog-error" role="alert">{error}</p>}
    {mode === 'list' ? <>
      <div className="dialog-tools"><input aria-label="Search projects" placeholder="Search projects" value={query} onChange={event => setQuery(event.target.value)} /><select aria-label="Sort projects" value={sort} onChange={event => setSort(event.target.value as typeof sort)}><option value="recent">Recently opened</option><option value="modified">Last modified</option><option value="name">Name</option></select><button className="ui-button primary" onClick={() => { setMode('create'); setName('') }}>Create project</button></div>
      <div className="manager-list">{displayed.map(item => <div className="manager-item" key={item.id}>
        <div><strong>{item.name}</strong><small>{item.deleted_at ? 'Removed · restorable' : item.archived_at ? 'Archived' : 'Active'} · {summaries[item.id]?.media_count ?? 0} media · {summaries[item.id]?.annotation_count ?? 0} annotations · Modified {new Date(item.updated_at).toLocaleDateString()}</small></div>
        <div className="manager-actions">{!item.archived_at && !item.deleted_at && <button onClick={() => void run(async () => { if (await onOpen(item.id)) onClose() })}>Open</button>}
          <details className="manager-more"><summary>Actions</summary><div className="manager-actions">
            {item.archived_at || item.deleted_at ? <button onClick={() => void run(async () => { await api(`/api/projects/${item.id}/restore`, jsonRequest('POST')); await reload() })}>Restore</button> : <><button onClick={() => { setTarget(item); setName(item.name); setMode('rename') }}>Rename</button><button onClick={() => void run(async () => { await api(`/api/projects/${item.id}/archive`, jsonRequest('POST')); await reload() })}>Archive</button><button onClick={() => { setTarget(item); setName(''); setMode('delete') }}>Delete…</button></>}
            <button onClick={() => void run(() => openFolder(item.id))}>Open folder</button>
          </div></details></div>
        <details className="manager-details"><summary>Technical details</summary><small>Internal ID: {item.id}</small><small>Storage usage: {sizeText(summaries[item.id]?.total_storage_bytes ?? 0)}</small><small>Created: {new Date(item.created_at).toLocaleString()} · Last opened: {new Date(item.last_opened_at).toLocaleString()}</small><small>SQLite: {item.database_path}</small><small>Storage: {item.storage_path}</small><small>Research records: {Object.entries(summaries[item.id]?.research_counts ?? {}).map(([name, count]) => `${name} ${count}`).join(' · ')}</small></details>
      </div>)}{!displayed.length && <p>No matching projects.</p>}</div>
    </> : <div className="dialog-form"><p>{mode === 'delete' ? 'This removes the project from the active list. Its database records, media, and annotations remain recoverable. Type the exact project name to confirm.' : mode === 'rename' ? 'The project folder stays at its existing path.' : 'Create a named project in private local storage.'}</p>
      <label>{mode === 'delete' ? `Type “${target?.name}”` : 'Project name'}<input ref={nameInput} value={name} maxLength={120} onChange={event => setName(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') event.preventDefault() }} /></label>
      <div className="dialog-form-actions"><button className="ui-button subtle" onClick={reset}>Back</button><button className={`ui-button ${mode === 'delete' ? 'danger' : 'primary'}`} disabled={busy || !name.trim() || (mode === 'delete' && name !== target?.name)} onClick={() => void run(async () => {
        if (mode === 'create') { const created = await api<Project>('/api/projects', jsonRequest('POST', { name: name.trim() })); await reload(); if (await onOpen(created.id)) onClose() }
        if (mode === 'rename' && target) { await api(`/api/projects/${target.id}`, jsonRequest('PATCH', { name: name.trim() })); await reload(); reset() }
        if (mode === 'delete' && target) { await api(`/api/projects/${target.id}/delete`, jsonRequest('POST', { confirm_name: name })); await reload(); reset() }
      })}>{mode === 'create' ? 'Create project' : mode === 'rename' ? 'Save name' : 'Delete project (recoverable)'}</button></div>
    </div>}
  </Modal>
}

export function MediaManager({ project, onClose, onChanged, onSelect, onImport, beforeChange }: {
  project: Project; onClose: () => void; onChanged: () => Promise<void>; onSelect: (id: string) => Promise<boolean>; onImport: () => void; beforeChange: () => Promise<boolean>
}) {
  const [info, setInfo] = useState<StorageInfo | null>(null)
  const [renameId, setRenameId] = useState<string | null>(null)
  const [removeId, setRemoveId] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
  const reload = async () => { setInfo(await api<StorageInfo>(`/api/projects/${project.id}/storage`)); await onChanged() }
  useEffect(() => { void reload().catch(reason => setError(String(reason))) }, [project.id])
  const run = async (operation: () => Promise<void>) => { if (busyRef.current) return; busyRef.current = true; setBusy(true); setError(''); try { if (!(await beforeChange())) throw new Error('Save pending annotation changes before changing media'); await operation() } catch (reason) { setError(String(reason)) } finally { busyRef.current = false; setBusy(false) } }
  return <Modal title={`Media Manager · ${project.name}`} onClose={onClose} busy={busy}>
    {error && <p className="dialog-error" role="alert">{error}</p>}
    <div className="dialog-tools"><button className="ui-button primary" onClick={() => { onClose(); onImport() }}>Import video</button></div>
    <div className="manager-list">{info?.media.map(item => <div className="manager-item" key={item.id}><div><strong>{item.display_name}</strong><small>{item.removed_at ? 'Removed · recoverable' : 'Active'} · {(item.duration_ms / 1000).toFixed(1)} s · {item.annotation_count} annotations</small></div><div className="manager-actions">
      {!item.removed_at && <button onClick={() => void run(async () => { if (await onSelect(item.id)) onClose() })}>Open</button>}
      <details className="manager-more"><summary>Actions</summary><div className="manager-actions">{item.removed_at ? <button onClick={() => void run(async () => { await api(`/api/media/${item.id}/restore`, jsonRequest('POST')); await reload() })}>Restore</button> : <><button onClick={() => { setRenameId(item.id); setName(item.display_name); setRemoveId(null) }}>Rename</button><button onClick={() => { setRemoveId(item.id); setRenameId(null) }}>Remove</button></>}</div></details>
    </div><details className="manager-details"><summary>Technical details</summary><small>Media ID: {item.id}</small><small>Imported: {new Date(item.created_at).toLocaleString()}</small><small>Original file: {item.original_path}</small><small>Extracted audio: {item.audio_path}</small><small>Annotation records are retained when this media is removed.</small></details>{renameId === item.id && <div className="inline-confirm"><input aria-label="Media display name" value={name} maxLength={120} onChange={event => setName(event.target.value)} /><button disabled={!name.trim() || busy} onClick={() => void run(async () => { await api(`/api/media/${item.id}`, jsonRequest('PATCH', { display_name: name.trim() })); setRenameId(null); await reload() })}>Save</button><button onClick={() => setRenameId(null)}>Cancel</button></div>}
      {removeId === item.id && <div className="inline-confirm"><span>Remove this media record from the active list? Original files and annotations remain recoverable.</span><button disabled={busy} onClick={() => void run(async () => { await api(`/api/media/${item.id}/remove`, jsonRequest('POST')); setRemoveId(null); await reload() })}>Remove record</button><button onClick={() => setRemoveId(null)}>Cancel</button></div>}
    </div>)}{info?.media.length === 0 && <p>No media in this project.</p>}</div>
  </Modal>
}

export function StorageOverview({ project, onClose, openFolder }: { project: Project; onClose: () => void; openFolder: (id: string) => Promise<void> }) {
  const [info, setInfo] = useState<StorageInfo | null>(null)
  const [audit, setAudit] = useState<Array<{ relative_path: string; size_bytes: number; reference_count: number; category: string }> | null>(null)
  const [error, setError] = useState('')
  const refresh = async () => { try { const [details, inventory] = await Promise.all([api<StorageInfo>(`/api/projects/${project.id}/storage`), api<{ files: NonNullable<typeof audit> }>('/api/storage/audit')]); setInfo(details); setAudit(inventory.files); setError('') } catch (reason) { setError(String(reason)) } }
  useEffect(() => { void refresh() }, [project.id])
  const inside = info?.files.filter(file => file.relative_path.startsWith(`${project.storage_rel_path}/`)) ?? []
  const shared = info?.files.filter(file => !file.relative_path.startsWith(`${project.storage_rel_path}/`)) ?? []
  const missing = audit?.filter(file => file.category === 'missing_referenced_file') ?? []
  const backups = audit?.filter(file => file.category === 'backup_or_legacy_database') ?? []
  return <Modal title="Storage Overview" onClose={onClose}>
    {error && <p className="dialog-error" role="alert">{error}</p>}
    <div className="storage-summary"><div><strong>{info ? sizeText(info.total_storage_bytes) : '…'}</strong><small>Referenced project storage</small></div><div><strong>{info?.media_count ?? '…'}</strong><small>Media</small></div><div><strong>{info?.annotation_count ?? '…'}</strong><small>Human annotations</small></div></div>
    <dl className="storage-facts"><dt>Database</dt><dd>{info ? 'Available · SQLite' : 'Checking…'}</dd><dt>Backups</dt><dd>{audit ? `${backups.length} backup / legacy database files` : 'Checking…'}</dd><dt>Missing files</dt><dd className={missing.length ? 'storage-warning' : ''}>{audit ? missing.length ? `${missing.length} referenced file(s) missing` : 'None detected' : 'Checking…'}</dd><dt>Last modified</dt><dd>{new Date(info?.project.updated_at ?? project.updated_at).toLocaleString()}</dd></dl>
    <div className="dialog-tools"><button className="ui-button subtle" onClick={() => void refresh()}>Refresh</button><button className="ui-button subtle" onClick={() => void openFolder(project.id).catch(reason => setError(String(reason)))}>Open Project Folder</button><button className="ui-button subtle" onClick={() => void navigator.clipboard.writeText(project.storage_path).catch(reason => setError(String(reason)))}>Copy Storage Path</button></div>
    <details className="storage-advanced"><summary>Project records and locations</summary><dl className="storage-facts"><dt>Project</dt><dd>{project.name}</dd><dt>Internal ID</dt><dd>{project.id}</dd><dt>Project folder</dt><dd>{project.storage_path}</dd><dt>SQLite database</dt><dd>{project.database_path}</dd></dl><h3>Annotations by media and tier</h3>{info?.media.map(item => <div className="storage-row" key={item.id}><strong>{item.display_name}</strong><span>{item.annotation_count} annotations</span>{info.tiers.filter(tier => tier.media_id === item.id).map(tier => <small key={tier.id}>{tier.name}: {tier.annotation_count}</small>)}</div>)}<h3>Research data records</h3><div className="storage-counts">{Object.entries(info?.research_counts ?? {}).map(([name, count]) => <span key={name}>{name.replaceAll('_', ' ')}: {count}</span>)}</div><h3>Physically inside this project folder</h3><div className="audit-list">{inside.length ? inside.map(file => <div key={file.relative_path}>{file.relative_path} · {sizeText(file.size_bytes)}</div>) : 'No files in this directory.'}</div><h3>Referenced from shared or legacy folders</h3><div className="audit-list">{shared.map(file => <div key={file.relative_path}>{file.relative_path} · {sizeText(file.size_bytes)} · {file.reference_count} reference(s)</div>)}</div></details>
    <details className="storage-advanced"><summary>Advanced file-reference audit</summary><p>This read-only inventory distinguishes linked media, backups and potentially orphaned files. It does not move or delete anything.</p><div className="audit-list">{audit?.map(file => <div key={file.relative_path}>{file.relative_path} · {sizeText(file.size_bytes)} · {file.reference_count} reference(s) · {file.category}</div>)}</div></details>
  </Modal>
}

type ExportOptions = {
  tiers: Array<{ id: string; media_id: string; name: string; record_count: number }>
  research_data: Array<{ name: string; record_count: number }>
}
type ExportPreview = { filename: string; media_type: string; bytes: number; record_count: number; files: Array<{ filename: string; dataset: string; count: number }> }
const dataLabels: Record<string, string> = {
  source_transcripts: 'Source words', subtitle_screen_states: 'Target subtitle states',
  derived_events: 'Text change events', ocr_observations: 'OCR observations',
  vad_intervals: 'Source VAD intervals', target_update_events: 'Target update events',
  output_gap_candidates: 'Output gap candidates',
}
export function ExportDialog({ project, media, onClose, beforeExport, ocrRuns, selectedOCRRunId }: {
  project: Project; media: MediaAsset | null; onClose: () => void; beforeExport: () => Promise<boolean>
  ocrRuns?: Array<{ id: string; created_at: string }>; selectedOCRRunId?: string | null
}) {
  const [scope, setScope] = useState<'media' | 'project'>(media ? 'media' : 'project')
  const [options, setOptions] = useState<ExportOptions | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [format, setFormat] = useState<'simple_json' | 'csv' | 'detailed_json'>('simple_json')
  const [complete, setComplete] = useState(false)
  const [includeUnreviewed, setIncludeUnreviewed] = useState(false)
  const [includeRejected, setIncludeRejected] = useState(false)
  const [ocrRunId, setOCRRunId] = useState(selectedOCRRunId ?? '')
  const [gapThreshold, setGapThreshold] = useState(2000)
  const [preview, setPreview] = useState<ExportPreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const busyRef = useRef(false)
  useEffect(() => {
    let active = true
    const query = scope === 'media' && media ? `?media_id=${encodeURIComponent(media.id)}` : ''
    void api<ExportOptions>(`/api/projects/${project.id}/export/options${query}`).then(result => {
      if (!active) return
      setOptions(result)
      setSelected(current => current.filter(key => key.startsWith('tier:')
        ? result.tiers.some(tier => `tier:${tier.id}` === key)
        : result.research_data.some(item => item.name === key)))
    }).catch(reason => { if (active) setError(String(reason)) })
    return () => { active = false }
  }, [project.id, media?.id, scope])
  const payload = { media_id: scope === 'media' ? media?.id : null, datasets: selected, format, complete_project: complete,
    include_unreviewed: includeUnreviewed, include_rejected: includeRejected,
    ocr_run_id: scope === 'media' ? ocrRunId || null : null, gap_threshold_ms: gapThreshold }
  useEffect(() => {
    if ((!selected.length && !complete) || (complete && scope !== 'project')) { setPreview(null); return }
    let active = true
    const timer = window.setTimeout(() => {
      void api<ExportPreview>(`/api/projects/${project.id}/export/simple/preview`, jsonRequest('POST', payload))
        .then(result => { if (active) { setPreview(result); setError('') } })
        .catch(reason => { if (active) { setPreview(null); setError(reason instanceof Error ? reason.message : String(reason)) } })
    }, 150)
    return () => { active = false; window.clearTimeout(timer) }
  }, [project.id, scope, media?.id, selected.join('|'), format, complete, includeUnreviewed, includeRejected, ocrRunId, gapThreshold])
  const download = async () => {
    if (busyRef.current) return
    busyRef.current = true; setBusy(true); setError('')
    try {
      if (!(await beforeExport())) throw new Error('Save pending changes before exporting')
      const response = await fetch(`/api/projects/${project.id}/export/simple/download`, jsonRequest('POST', payload))
      if (!response.ok) { const body = await response.json().catch(() => null); throw new Error(body?.detail || `Export failed (${response.status})`) }
      const filename = decodeURIComponent(/filename\*=UTF-8''([^;]+)/i.exec(response.headers.get('Content-Disposition') ?? '')?.[1] ?? preview?.filename ?? 'annotations.zip')
      const url = URL.createObjectURL(await response.blob())
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = filename
      document.body.appendChild(anchor); anchor.click(); anchor.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
      onClose()
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { busyRef.current = false; setBusy(false) }
  }
  const choices = [
    ...(options?.research_data ?? []).map(item => ({ key: item.name, label: dataLabels[item.name] ?? item.name, count: item.record_count })),
    ...(options?.tiers ?? []).map(tier => ({ key: `tier:${tier.id}`, label: `${tier.name} annotations`, count: tier.record_count })),
  ].filter(item => item.count > 0 || ['subtitle_screen_states', 'target_update_events', 'output_gap_candidates'].includes(item.key))
  return <Modal title="Export Annotations" onClose={onClose} busy={busy}>
    {error && <p className="dialog-error" role="alert">{error}</p>}
    <section className="dataset-group"><h3>1. Export Scope</h3><div className="export-grid"><label>Scope<select value={scope} onChange={event => { setScope(event.target.value as typeof scope); setComplete(false) }}><option value="project">Entire project</option>{media && <option value="media">Current video</option>}</select></label></div></section>
    <section className="dataset-group"><h3>2. Select Data</h3><div className="tier-choices">{choices.length ? choices.map(item => <label key={item.key} className="checkbox-line"><input type="checkbox" disabled={complete} checked={selected.includes(item.key)} onChange={event => setSelected(current => event.target.checked ? [...current, item.key] : current.filter(key => key !== item.key))} />{item.label} <small>({item.count} available records)</small></label>) : <p>No exportable records in this scope.</p>}</div>{selected.some(key => ['subtitle_screen_states', 'target_update_events', 'output_gap_candidates'].includes(key)) && <div className="export-grid">{scope === 'media' && !!ocrRuns?.length && <label>OCR run<select value={ocrRunId} onChange={event => setOCRRunId(event.target.value)}><option value="">Latest completed</option>{ocrRuns.map(run => <option key={run.id} value={run.id}>{run.created_at} · {run.id.slice(0, 8)}</option>)}</select></label>}{format !== 'detailed_json' && <><label className="checkbox-line"><input type="checkbox" checked={includeUnreviewed} onChange={event => setIncludeUnreviewed(event.target.checked)} />Include unreviewed states / events</label><label className="checkbox-line"><input type="checkbox" checked={includeRejected} onChange={event => setIncludeRejected(event.target.checked)} />Include rejected states / events</label></>}{selected.includes('output_gap_candidates') && <label>Output gap threshold (ms)<input type="number" min="1" step="100" value={gapThreshold} onChange={event => setGapThreshold(Math.max(1, Number(event.target.value) || 1))} /></label>}</div>}{selected.includes('subtitle_screen_states') && format !== 'detailed_json' && <p className="pending-dataset">Simple export uses active subtitle states from the selected OCR run. Reviewed states are included by default.</p>}{selected.includes('target_update_events') && format !== 'detailed_json' && <p className="pending-dataset">Target update export includes confirmed, current human reviews by default. Invalidated event reviews need rechecking.</p>}</section>
    <section className="dataset-group"><h3>3. Output Format</h3><div className="export-grid"><label>Format<select value={format} disabled={complete} onChange={event => setFormat(event.target.value as typeof format)}><option value="simple_json">Simple JSON</option><option value="csv">CSV</option><option value="detailed_json">Detailed JSON</option></select></label></div></section>
    {scope === 'project' && <details className="export-technical"><summary>Advanced: complete project archive</summary><label className="checkbox-line"><input type="checkbox" checked={complete} onChange={event => setComplete(event.target.checked)} />Complete project JSON with metadata and relationships</label></details>}
    <section className="dataset-group" aria-live="polite"><h3>Preview</h3>{preview ? <><p>{preview.record_count} records · {preview.media_type === 'application/zip' ? 'ZIP' : preview.media_type === 'text/csv' ? 'CSV' : 'JSON'} · {preview.filename}</p><div className="export-preview-files">{preview.files.map(item => <div key={item.filename}>{item.filename} · {item.count} records</div>)}</div></> : <p>Select data to preview exact output files.</p>}</section>
    <div className="dialog-form-actions"><button className="ui-button primary" disabled={busy || !preview} onClick={() => void download()}>{busy ? 'Preparing…' : '4. Download'}</button></div>
  </Modal>
}
export function SettingsDialog({ project, shortcuts, onApplyShortcuts, onStorage, onClose, asrPreferences, onSaveASR }: {
  project: Project | null; shortcuts: ShortcutSettingsValue; onApplyShortcuts: (value: ShortcutSettingsValue) => void
  onStorage: () => void; onClose: () => void; asrPreferences: ASRPreferences
  onSaveASR: (value: ASRPreferences) => Promise<void>
}) {
  const [tab, setTab] = useState<'annotation' | 'keyboard' | 'storage' | 'appearance'>('annotation')
  const [working, setWorking] = useState<ShortcutSettingsValue>(() => ({ preset: shortcuts.preset, bindings: { ...shortcuts.bindings } }))
  const [recording, setRecording] = useState<ShortcutAction | null>(null)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const [asrWorking, setAsrWorking] = useState(asrPreferences)
  const [asrBusy, setAsrBusy] = useState(false)
  const choosePreset = (preset: 'default' | 'annotation') => { setWorking({ preset, bindings: { ...(preset === 'default' ? defaultBindings : annotationBindings) } }); setError(''); setSaved(false) }
  const record = (event: ReactKeyboardEvent<HTMLButtonElement>, action: ShortcutAction) => {
    if (recording !== action) return
    event.preventDefault(); event.stopPropagation()
    if (event.key === 'Escape') { setRecording(null); return }
    const key = keyFromEvent(event)
    if (!key || reservedBinding(key)) { setError('This key is reserved for the browser, operating system or dialog navigation.'); return }
    const other = conflictFor(working.bindings, action, key)
    if (other) { setError(`${key} is already assigned to ${actionEntries.find(entry => entry.id === other)?.label}. Choose another key.`); return }
    setWorking(current => ({ preset: 'custom', bindings: { ...current.bindings, [action]: key } }))
    setRecording(null); setError(''); setSaved(false)
  }
  const save = () => {
    try { saveShortcutSettings(working); onApplyShortcuts(working); setSaved(true); setError('') }
    catch { setError('Could not save keyboard preferences in this browser.') }
  }
  return <Modal title="Settings" onClose={onClose}>
    <div className="settings-tabs" role="tablist" aria-label="Settings sections">{(['annotation', 'keyboard', 'storage', 'appearance'] as const).map(item => <button key={item} role="tab" aria-selected={tab === item} className={tab === item ? 'active' : ''} onClick={() => { setTab(item); setRecording(null); setError('') }}>{item === 'keyboard' ? 'Keyboard Shortcuts' : item[0].toUpperCase() + item.slice(1)}</button>)}</div>
    {error && <p className="dialog-error" role="alert">{error}</p>}
    {tab === 'annotation' && <div className="settings-section"><h3>Automatic source transcription</h3><label className="checkbox-line"><input type="checkbox" checked={asrWorking.auto_transcribe} onChange={event => setAsrWorking(current => ({ ...current, auto_transcribe: event.target.checked }))} />Automatically transcribe newly imported videos</label><label className="settings-field">Default English model<select value={asrWorking.default_model} onChange={event => setAsrWorking(current => ({ ...current, default_model: event.target.value as 'small' | 'medium' }))}><option value="small">small</option><option value="medium">medium</option></select></label><button className="ui-button primary" disabled={asrBusy} onClick={() => { setAsrBusy(true); void onSaveASR(asrWorking).then(() => { setError(''); setSaved(true) }).catch(reason => setError(String(reason))).finally(() => setAsrBusy(false)) }}>Save transcription preferences</button>{saved && <p role="status">Preferences saved locally.</p>}<h3>Annotation timing</h3><p>Timeline and annotation timestamps use integer milliseconds. Time precision is set per interval in the Annotation editor.</p><details><summary>What does time precision mean?</summary><p>Estimated: manually judged boundary. Frame: aligned to a video frame. Exact: confirmed at the selected time. The label describes the annotation, not a guarantee from the media codec.</p></details><h3>Target subtitle OCR</h3><p>Set the subtitle region and sample range in the Target OCR timeline strip. OCR observations and reviewed screen states remain separate. Advanced text-change classification is planned for a later release.</p></div>}
    {tab === 'keyboard' && <div className="settings-section"><div className="dialog-tools"><label>Preset <select value={working.preset} onChange={event => { if (event.target.value !== 'custom') choosePreset(event.target.value as 'default' | 'annotation') }}><option value="default">Default</option><option value="annotation">Annotation focused</option><option value="custom">Custom</option></select></label><button className="ui-button subtle" onClick={() => choosePreset('default')}>Reset to defaults</button></div><p>Choose Record, then press a key. Conflicting and browser-reserved shortcuts are rejected. Changes take effect after Save.</p><div className="shortcut-settings-list">{Object.entries(shortcutGroups).map(([group, actions]) => <section key={group}><h3>{group}</h3>{actions.map(([id, label]) => <div className="shortcut-setting" key={id}><span>{label}</span><kbd>{working.bindings[id]}</kbd><button className="ui-button subtle" aria-label={`Record shortcut for ${label}`} onClick={() => { setRecording(id); setError('') }} onKeyDown={event => record(event, id)}>{recording === id ? 'Press a key…' : 'Record'}</button></div>)}</section>)}</div><div className="dialog-form-actions"><button className="ui-button primary" onClick={save}>Save shortcuts</button></div>{saved && <p role="status">Shortcut preferences saved in this browser.</p>}</div>}
    {tab === 'storage' && <div className="settings-section"><h3>Local project storage</h3><p>{project ? `Current project: ${project.name}` : 'Open a project to inspect its storage.'}</p><button className="ui-button subtle" disabled={!project} onClick={onStorage}>Open Storage Overview</button><details><summary>Technical storage details</summary><p>SQLite is the authoritative project database. Source media and research data remain in private local storage outside the source repository.</p></details></div>}
    {tab === 'appearance' && <div className="settings-section"><h3>Workspace appearance</h3><p>The current light theme is optimized for dense timeline editing.</p><label className="checkbox-line"><input type="checkbox" checked disabled />Light theme</label><p>Additional themes are not available yet.</p></div>}
  </Modal>
}

export function ShortcutHelp({ bindings, onClose }: { bindings: ShortcutSettingsValue['bindings']; onClose: () => void }) {
  return <Modal title="Keyboard Shortcuts" onClose={onClose}><div className="shortcut-groups">{Object.entries(shortcutGroups).map(([title, actions]) => <section key={title}><h3>{title}</h3>{actions.map(([id, label]) => <p key={id}><kbd>{bindings[id]}</kbd> {label}</p>)}</section>)}<section><h3>Project</h3><p>Project and media selectors open their managers. Escape closes dialogs or clears a selection.</p></section></div></Modal>
}

