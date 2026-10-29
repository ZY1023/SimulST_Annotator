import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import WaveSurfer from 'wavesurfer.js'
import type { HumanAnnotation, MediaAsset, Project, ReviewStatus, Tier, TimePrecision } from './contracts'
import { ExportDialog, MediaManager, ProjectManager, SettingsDialog, ShortcutHelp, StorageOverview } from './WorkspaceDialogs'
import { keyFromEvent, loadShortcutSettings } from './shortcuts'
import type { ShortcutAction } from './shortcuts'
import { useSourceTranscript } from './useSourceTranscript'
import type { SourceWord } from './useSourceTranscript'
import { SourceWordInspector } from './SourceWordInspector'
import { SourceTranscriptReview } from './SourceTranscriptReview'
import { useSourceVAD } from './useSourceVAD'
import { useTargetOCR } from './useTargetOCR'
import type { SubtitleROI, SubtitleState } from './useTargetOCR'
import { VideoROI } from './VideoROI'
import './research.css'

const defaultTiers: Array<[string, string]> = [
  ['source_words', 'Source words'], ['target_characters', 'Target characters'],
  ['subtitle_state', 'Subtitle state'], ['disfluency', 'Disfluency'], ['revision', 'Revision'],
]
const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max)
const defaultSubtitleROI: SubtitleROI = { x: .06, y: .72, width: .88, height: .23 }
const toMs = (seconds: number) => Math.round(seconds * 1000)
function timeText(ms: number) {
  const value = Math.max(0, Math.round(ms))
  return `${String(Math.floor(value / 60000)).padStart(2, '0')}:${String(Math.floor(value % 60000 / 1000)).padStart(2, '0')}.${String(value % 1000).padStart(3, '0')}`
}
function position(start: number, end: number, pixelsPerSecond: number) {
  return { left: start / 1000 * pixelsPerSecond, width: Math.max(2, (end - start) / 1000 * pixelsPerSecond) }
}
async function readJson<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => null)
  if (!response.ok) throw new Error(body?.detail || `Request failed (${response.status})`)
  if (body === null) throw new Error('The server returned an empty or invalid response')
  return body as T
}
type Draft = { start_ms: number; end_ms: number; text: string; event_label: string; review_status: ReviewStatus; precision: TimePrecision }
type MenuName = 'project' | 'media' | 'save'
type DialogName = 'projects' | 'media' | 'export' | 'storage' | 'shortcuts' | 'settings'

export default function ResearchApp() {
  const [projects, setProjects] = useState<Project[]>([])
  const [projectId, setProjectId] = useState<string | null>(null)
  const [activeDialog, setActiveDialog] = useState<DialogName | null>(null)
  const [assets, setAssets] = useState<MediaAsset[]>([])
  const [mediaId, setMediaId] = useState<string | null>(null)
  const source = useSourceTranscript(mediaId)
  const vad = useSourceVAD(mediaId)
  const targetOCR = useTargetOCR(mediaId)
  const [splitHeight, setSplitHeight] = useState(() => {
    const stored = Number(localStorage.getItem('simulst.upperHeight'))
    return Number.isFinite(stored) && stored >= 240 ? stored : Math.round(window.innerHeight * .35)
  })
  const [videoAspect, setVideoAspect] = useState(16 / 9)
  const splitStart = useRef<{ y: number; height: number } | null>(null)
  const [ocrSetup, setOCRSetup] = useState(false)
  const [ocrROI, setOCRROI] = useState<SubtitleROI>(() => {
    try { return JSON.parse(localStorage.getItem('simulst.targetROI') || '') as SubtitleROI }
    catch { return defaultSubtitleROI }
  })
  const [ocrStart, setOCRStart] = useState(0)
  const [ocrEnd, setOCREnd] = useState(30000)
  const [ocrFPS, setOCRFPS] = useState(5)
  const [selectedStateId, setSelectedStateId] = useState<string | null>(null)
  const [stateDraft, setStateDraft] = useState<{ start_ms: number; end_ms: number; text: string; review_status: ReviewStatus } | null>(null)
  const [showVAD, setShowVAD] = useState(true)
  const [showTargetStates, setShowTargetStates] = useState(true)
  const [timingIssues, setTimingIssues] = useState<Record<string, string[]>>({})
  const [selectedWordId, setSelectedWordId] = useState<string | null>(null)
  const [asrModel, setAsrModel] = useState<'small' | 'medium'>('small')
  const [tiers, setTiers] = useState<Tier[]>([])
  const [hiddenTiers, setHiddenTiers] = useState<Set<string>>(new Set())
  const [tierManagerOpen, setTierManagerOpen] = useState(false)
  const [annotations, setAnnotations] = useState<HumanAnnotation[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [timeMs, setTimeMs] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [zoom, setZoom] = useState(84)
  const [speed, setSpeed] = useState(1)
  const [timelineFollow, setTimelineFollow] = useState(true)
  const [busy, setBusy] = useState(false)
  const [saveStatus, setSaveStatus] = useState<'saved' | 'unsaved' | 'saving' | 'failed' | 'idle'>('idle')
  const [lastSavedAt, setLastSavedAt] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [activeMenu, setActiveMenu] = useState<MenuName | null>(null)
  const [shortcutSettings, setShortcutSettings] = useState(loadShortcutSettings)
  const [waveReady, setWaveReady] = useState(false)
  const [drag, setDrag] = useState<{ tierId: string; start: number; end: number } | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const videoBoxRef = useRef<HTMLDivElement>(null)
  const waveformNode = useRef<HTMLDivElement>(null)
  const timelineNode = useRef<HTMLDivElement>(null)
  const playbackEnd = useRef<number | null>(null)
  const textAreaRef = useRef<HTMLTextAreaElement>(null)
  const waveRef = useRef<WaveSurfer | null>(null)
  const readyRef = useRef(false)
  const rafRef = useRef<number | null>(null)
  const editVersion = useRef(0)
  const dirty = useRef(false)
  const saveQueue = useRef<Promise<boolean>>(Promise.resolve(true))
  const undoStack = useRef<Array<{ id: string; before: Draft; after: Draft }>>([])
  const redoStack = useRef<Array<{ id: string; before: Draft; after: Draft }>>([])
  const tokenStart = useRef<{ item: HumanAnnotation; clientX: number; mode: 'move' | 'start' | 'end' } | null>(null)
  const wordStart = useRef<{ item: SourceWord; clientX: number; mode: 'move' | 'start' | 'end' } | null>(null)
  const [wordDrag, setWordDrag] = useState<{ id: string; start_ms: number; end_ms: number } | null>(null)
  const suppressTokenClick = useRef(false)
  const [tokenDrag, setTokenDrag] = useState<{ id: string; start_ms: number; end_ms: number } | null>(null)
  const menuAnchors = useRef<Record<MenuName, HTMLDivElement | null>>({ project: null, media: null, save: null })
  const dialogReturnFocus = useRef<HTMLElement | null>(null)

  const openDialog = (name: DialogName) => {
    const focused = document.activeElement as HTMLElement | null
    dialogReturnFocus.current = focused?.closest('.menu-anchor')?.querySelector<HTMLElement>('button') ?? focused
    setActiveMenu(null)
    setActiveDialog(name)
  }
  const closeDialog = () => {
    setActiveDialog(null)
    requestAnimationFrame(() => dialogReturnFocus.current?.focus())
  }

  useEffect(() => {
    if (!activeMenu) return
    const onPointerDown = (event: PointerEvent) => {
      if (!menuAnchors.current[activeMenu]?.contains(event.target as Node)) setActiveMenu(null)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      menuAnchors.current[activeMenu]?.querySelector('button')?.focus()
      setActiveMenu(null)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [activeMenu])

  const toggleMenu = (name: MenuName) => setActiveMenu(current => current === name ? null : name)

  const project = projects.find(item => item.id === projectId) ?? null
  const media = assets.find(asset => asset.id === mediaId) ?? null
  const durationMs = media?.duration_ms ?? 30000
  const effectiveSplitHeight = clamp(splitHeight, 240, Math.max(260, window.innerHeight - 240))
  useEffect(() => {
    const resize = () => setSplitHeight(value => clamp(value, 240, Math.max(260, window.innerHeight - 240)))
    window.addEventListener('resize', resize)
    return () => window.removeEventListener('resize', resize)
  }, [])
  useEffect(() => { localStorage.setItem('simulst.targetROI', JSON.stringify(ocrROI)) }, [ocrROI])
  useEffect(() => { setOCRStart(0); setOCREnd(Math.min(media?.duration_ms ?? 30000, 30000)); setSelectedStateId(null); setStateDraft(null); setVideoAspect(16 / 9) }, [media?.id])
  useEffect(() => {
    try { setHiddenTiers(new Set(JSON.parse(localStorage.getItem(`simulst.hiddenTiers.${mediaId}`) || '[]') as string[])) }
    catch { setHiddenTiers(new Set()) }
  }, [mediaId])
  const timelineWidth = Math.max(900, Math.ceil(durationMs / 1000 * zoom))
  const pixelsPerSecond = timelineWidth / Math.max(durationMs / 1000, 0.001)
  const playheadMs = clamp(timeMs - (media?.media_offset_ms ?? 0), 0, durationMs)
  const changeZoom = (next: number) => {
    const value = clamp(next, 36, 500)
    const scroller = timelineNode.current
    const playheadInView = scroller ? 148 + playheadMs / 1000 * pixelsPerSecond - scroller.scrollLeft : 0
    setZoom(value)
    if (scroller) requestAnimationFrame(() => {
      const nextWidth = Math.max(900, Math.ceil(durationMs / 1000 * value))
      const nextPixelsPerSecond = nextWidth / Math.max(durationMs / 1000, 0.001)
      scroller.scrollLeft = Math.max(0, 148 + playheadMs / 1000 * nextPixelsPerSecond - playheadInView)
    })
  }
  const selected = annotations.find(item => item.id === selectedId) ?? null
  const selectedWord = source.words.find(item => item.id === selectedWordId) ?? null
  const selectedState = targetOCR.states.find(item => item.id === selectedStateId) ?? null
  const displayWords = useMemo(() => source.words.map(word => ({ ...word,
    attention: [...new Set([...word.attention, ...(timingIssues[word.id] ?? [])])] })), [source.words, timingIssues])
  const spokenWordIds = source.words.filter(word => word.speech_status !== 'not_spoken' && word.review_status !== 'rejected' && word.start_ms <= playheadMs && playheadMs < word.end_ms).map(word => word.id)
  const shownTiers = (media ? tiers : defaultTiers.map(([kind, name], sort_order) => ({ id: `preview:${kind}`, project_id: '', media_id: '', kind, name, sort_order }))).filter(tier => !hiddenTiers.has(tier.id))

  useEffect(() => {
    fetch('/api/projects').then(readJson<Project[]>).then(items => {
      setProjects(items)
      if (items.length) { setProjectId(items[0].id); setSaveStatus('idle'); setLastSavedAt(null) }
      else openDialog('projects')
    }).catch(reason => setError(String(reason)))
  }, [])
  useEffect(() => {
    if (!projectId) { setAssets([]); setMediaId(null); return }
    let active = true
    fetch(`/api/projects/${projectId}/media`).then(readJson<MediaAsset[]>).then(items => {
      if (!active) return
      setAssets(items); setMediaId(items[0]?.id ?? null)
    }).catch(reason => { if (active) setError(String(reason)) })
    return () => { active = false }
  }, [projectId])
  useEffect(() => {
    if (!media) { setTiers([]); setAnnotations([]); return }
    setSelectedId(null); setSelectedWordId(null); setDraft(null); setTimeMs(0); dirty.current = false
    undoStack.current = []; redoStack.current = []
    setSaveStatus('idle'); setLastSavedAt(null)
    let active = true
    Promise.all([
      fetch(`/api/media/${media.id}/tiers`).then(readJson<Tier[]>),
      fetch(`/api/media/${media.id}/annotations`).then(readJson<HumanAnnotation[]>),
    ]).then(([loadedTiers, loadedAnnotations]) => {
      if (!active) return
      setTiers(loadedTiers); setAnnotations(loadedAnnotations)
      const last = loadedAnnotations.map(item => item.updated_at).sort().at(-1)
      if (last) setLastSavedAt(last)
    }).catch(reason => { if (active) setError(String(reason)) })
    return () => { active = false }
  }, [media?.id])
  useEffect(() => { setAsrModel(source.preferences.default_model) }, [source.preferences.default_model])
  useEffect(() => {
    if (!mediaId) { setTimingIssues({}); return }
    let active = true
    void fetch(`/api/media/${mediaId}/alignment/diagnostics`).then(readJson<{ word_details: Array<{ word_id: string; issues: string[] }> }>)
      .then(result => { if (active) setTimingIssues(Object.fromEntries(result.word_details.map(item => [item.word_id, item.issues]))) })
      .catch(() => { if (active) setTimingIssues({}) })
    return () => { active = false }
  }, [mediaId, vad.run?.id, source.words.length, source.saveStatus])

  const sync = useCallback(() => {
    const video = videoRef.current
    if (!video || !media) return
    setTimeMs(toMs(video.currentTime) + media.media_offset_ms)
    if (playbackEnd.current !== null && toMs(video.currentTime) >= playbackEnd.current) { video.pause(); playbackEnd.current = null }
    const wave = waveRef.current
    if (wave && readyRef.current && Math.abs(wave.getCurrentTime() - video.currentTime) > 0.04) wave.setTime(video.currentTime)
  }, [media?.id, media?.media_offset_ms])
  useEffect(() => {
    if (!media || !waveformNode.current) return
    readyRef.current = false; setWaveReady(false)
    const wave = WaveSurfer.create({ container: waveformNode.current, url: media.audio_url,
      waveColor: '#6aa0d5', progressColor: '#81b5e3', cursorColor: 'transparent', cursorWidth: 0,
      height: 80, normalize: true, interact: true })
    waveRef.current = wave
    wave.on('ready', () => { readyRef.current = true; setWaveReady(true); sync() })
    wave.on('error', reason => setError(`Waveform: ${String(reason)}`))
    wave.on('interaction', seconds => { if (videoRef.current) { videoRef.current.currentTime = seconds; sync() } })
    return () => { readyRef.current = false; waveRef.current = null; wave.destroy() }
  }, [media?.id, sync])
  useEffect(() => {
    if (!playing) return
    const tick = () => { sync(); rafRef.current = requestAnimationFrame(tick) }
    rafRef.current = requestAnimationFrame(tick)
    return () => { if (rafRef.current !== null) cancelAnimationFrame(rafRef.current) }
  }, [playing, sync])
  useEffect(() => { if (videoRef.current) videoRef.current.playbackRate = speed }, [speed, media?.id])
  useEffect(() => {
    if (!timelineFollow || !playing || !timelineNode.current || !spokenWordIds.length) return
    const scroller = timelineNode.current
    const x = 148 + playheadMs / 1000 * pixelsPerSecond
    if (x < scroller.scrollLeft + scroller.clientWidth * .2 || x > scroller.scrollLeft + scroller.clientWidth * .8)
      scroller.scrollLeft = Math.max(0, x - scroller.clientWidth * .45)
  }, [spokenWordIds.join('|'), playing, timelineFollow, pixelsPerSecond])

  const persistDraft = (snapshot: Draft, annotation: HumanAnnotation, asset: MediaAsset, version: number): Promise<boolean> => {
    setSaveStatus('saving')
    const task = saveQueue.current.catch(() => false).then(async () => {
      if (version < editVersion.current) return true
      try {
        const updated = await fetch(`/api/media/${asset.id}/annotations/${annotation.id}`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            span: { start_ms: Math.round(snapshot.start_ms), end_ms: Math.round(snapshot.end_ms), precision: snapshot.precision },
            text: snapshot.text, event_label: snapshot.event_label || null,
            review_status: snapshot.review_status, metadata: annotation.metadata,
          }),
        }).then(readJson<HumanAnnotation>)
        setAnnotations(items => items.map(item => item.id === updated.id ? updated : item))
        if (version === editVersion.current) {
          dirty.current = false; setSaveStatus('saved'); setLastSavedAt(updated.updated_at); setError('')
        }
        return true
      } catch (reason) {
        if (version === editVersion.current) {
          setSaveStatus('failed'); setError(reason instanceof Error ? reason.message : String(reason))
        }
        return false
      }
    })
    saveQueue.current = task
    return task
  }
  const flushAnnotationDraft = async (): Promise<boolean> => {
    if (dirty.current && draft && selected && media) {
      return persistDraft(draft, selected, media, editVersion.current)
    }
    return saveQueue.current
  }
  const flushStateDraft = async (): Promise<boolean> => {
    if (!selectedState || !stateDraft) return true
    if (selectedState.text === stateDraft.text && selectedState.start_ms === stateDraft.start_ms &&
        selectedState.end_ms === stateDraft.end_ms && selectedState.review_status === stateDraft.review_status) return true
    try { await targetOCR.saveState(selectedState, stateDraft); return true }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); return false }
  }
  const flushDraft = async (): Promise<boolean> => (await flushAnnotationDraft()) && (await source.flush()) && (await flushStateDraft())
  const editDraft = (change: Partial<Draft>) => {
    if (!draft || !selected) return
    const next = { ...draft, ...change }
    undoStack.current.push({ id: selected.id, before: draft, after: next })
    redoStack.current = []
    editVersion.current += 1; dirty.current = true
    setDraft(next); setSaveStatus('unsaved')
  }
  const restoreDraft = (direction: 'undo' | 'redo') => {
    if (!selected) return
    const source = direction === 'undo' ? undoStack.current : redoStack.current
    const target = direction === 'undo' ? redoStack.current : undoStack.current
    const entry = source.pop()
    if (!entry || entry.id !== selected.id) return
    target.push(entry)
    editVersion.current += 1; dirty.current = true
    setDraft(direction === 'undo' ? entry.before : entry.after)
    setSaveStatus('unsaved')
  }
  useEffect(() => {
    if (!dirty.current || !draft || !selected || !media) return
    const timeout = window.setTimeout(() => { void persistDraft(draft, selected, media, editVersion.current) }, 650)
    return () => window.clearTimeout(timeout)
  }, [draft, selectedId, media?.id])
  useEffect(() => {
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      if (dirty.current || source.dirty.current || saveStatus === 'saving' || source.saveStatus === 'saving' ||
          (selectedState && stateDraft && (selectedState.text !== stateDraft.text || selectedState.start_ms !== stateDraft.start_ms || selectedState.end_ms !== stateDraft.end_ms || selectedState.review_status !== stateDraft.review_status))) { event.preventDefault(); event.returnValue = '' }
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [saveStatus, source.saveStatus, selectedState, stateDraft])

  const openProject = async (id: string) => {
    if (!(await flushDraft())) return false
    try {
      const opened = await fetch(`/api/projects/${id}/open`, { method: 'POST' }).then(readJson<Project>)
      setProjects(items => [opened, ...items.filter(item => item.id !== id)])
      setProjectId(id); setActiveDialog(null)
      setSaveStatus('idle'); setLastSavedAt(null)
      return true
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); return false }
  }
  const refreshProjects = async () => {
    const items = await fetch('/api/projects').then(readJson<Project[]>)
    setProjects(items)
    if (projectId && !items.some(item => item.id === projectId)) setProjectId(null)
  }
  const refreshMedia = async () => {
    if (!project) return
    const items = await fetch(`/api/projects/${project.id}/media`).then(readJson<MediaAsset[]>)
    setAssets(items)
    if (mediaId && !items.some(item => item.id === mediaId)) setMediaId(items[0]?.id ?? null)
  }
  const closeProject = async () => {
    if (!(await flushDraft())) return
    setProjectId(null); openDialog('projects'); setSaveStatus('idle')
  }
  const switchMedia = async (id: string) => {
    if (!(await flushDraft())) return false
    setMediaId(id)
    return true
  }
  const openFolder = async (id: string) => {
    try {
      await fetch(`/api/projects/${id}/open-folder`, { method: 'POST' }).then(readJson<{ status: string }>)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
  }
  const openDataFolder = async () => {
    try { await fetch('/api/storage/open-folder', { method: 'POST' }).then(readJson<{ status: string }>) }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
  }

  const importFile = async (file?: File) => {
    if (!file || !project || !(await flushDraft())) return
    setBusy(true); setError('')
    const form = new FormData(); form.append('file', file)
    try {
      const asset = await fetch(`/api/projects/${project.id}/media`, { method: 'POST', body: form }).then(readJson<MediaAsset>)
      setAssets(items => [asset, ...items]); setMediaId(asset.id)
      setSaveStatus('saved'); setLastSavedAt(asset.created_at)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false); if (fileInput.current) fileInput.current.value = '' }
  }
  const seek = (ms: number) => {
    if (!videoRef.current || !media) return
    videoRef.current.currentTime = clamp(ms, 0, durationMs) / 1000
    sync()
  }
  const playRange = (start: number, end: number) => {
    if (!videoRef.current) return
    playbackEnd.current = end
    seek(start)
    void videoRef.current.play().catch(() => setError('Video playback failed. Check the MP4 codec.'))
  }
  const togglePlay = async () => {
    if (!videoRef.current) return
    try { if (videoRef.current.paused) await videoRef.current.play(); else videoRef.current.pause() }
    catch { setError('Video playback failed. Check the MP4 codec.') }
  }
  const stepFrame = (direction: -1 | 1) => {
    if (!videoRef.current) return
    videoRef.current.pause()
    seek(toMs(videoRef.current.currentTime) + direction * 40)
  }
  const chooseAnnotation = async (annotation: HumanAnnotation) => {
    if (selectedId === annotation.id) { seek(draft?.start_ms ?? annotation.span.start_ms); return }
    if (!(await flushDraft())) return
    dirty.current = false
    undoStack.current = []; redoStack.current = []
    setSelectedWordId(null)
    setSelectedStateId(null)
    setSelectedId(annotation.id)
    setDraft({ start_ms: annotation.span.start_ms, end_ms: annotation.span.end_ms,
      text: annotation.text, event_label: annotation.event_label ?? '',
      review_status: annotation.review_status, precision: annotation.span.precision })
    seek(annotation.span.start_ms)
  }
  const save = async () => {
    setError('')
    await flushDraft()
  }
  const remove = async () => {
    if (!media || !selected) return
    try {
      editVersion.current += 1; dirty.current = false
      await saveQueue.current
      const response = await fetch(`/api/media/${media.id}/annotations/${selected.id}`, { method: 'DELETE' })
      if (!response.ok) throw new Error(`Delete failed (${response.status})`)
      setAnnotations(items => items.filter(item => item.id !== selected.id)); setSelectedId(null); setDraft(null)
      setSaveStatus('saved'); setLastSavedAt(new Date().toISOString())
    } catch (reason) { setSaveStatus('failed'); setError(reason instanceof Error ? reason.message : String(reason)) }
  }
  const addTier = async () => {
    if (!media) return
    const name = window.prompt('Name for the new annotation tier')?.trim()
    if (!name) return
    try {
      const tier = await fetch(`/api/media/${media.id}/tiers`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) }).then(readJson<Tier>)
      setTiers(items => [...items, tier])
      setSaveStatus('saved'); setLastSavedAt(new Date().toISOString())
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
  }
  const toggleTier = (id: string) => {
    setHiddenTiers(current => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id); else next.add(id)
      if (media) localStorage.setItem(`simulst.hiddenTiers.${media.id}`, JSON.stringify([...next]))
      return next
    })
  }
  const renameTier = async (tier: Tier) => {
    if (!media) return
    const name = window.prompt('Rename tier', tier.name)?.trim()
    if (!name || name === tier.name) return
    try {
      const updated = await fetch(`/api/media/${media.id}/tiers/${encodeURIComponent(tier.id)}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }),
      }).then(readJson<Tier>)
      setTiers(items => items.map(item => item.id === tier.id ? updated : item))
    } catch (reason) { setError(String(reason)) }
  }
  const moveTier = async (tier: Tier, delta: number) => {
    if (!media) return
    const order = [...tiers].sort((a, b) => a.sort_order - b.sort_order)
    const index = order.findIndex(item => item.id === tier.id), nextIndex = index + delta
    if (nextIndex < 0 || nextIndex >= order.length) return
    ;[order[index], order[nextIndex]] = [order[nextIndex], order[index]]
    try {
      const updated = await fetch(`/api/media/${media.id}/tiers/order`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: order.map(item => item.id) }),
      }).then(readJson<Tier[]>)
      setTiers(updated)
    } catch (reason) { setError(String(reason)) }
  }
  const deleteTier = async (tier: Tier) => {
    if (!media || !window.confirm(`Delete the empty tier “${tier.name}”? Existing annotations cannot be deleted with a tier.`)) return
    try {
      const response = await fetch(`/api/media/${media.id}/tiers/${encodeURIComponent(tier.id)}?confirm_name=${encodeURIComponent(tier.name)}`, { method: 'DELETE' })
      if (!response.ok) { const body = await response.json().catch(() => null); throw new Error(body?.detail || `Delete failed (${response.status})`) }
      setTiers(items => items.filter(item => item.id !== tier.id))
    } catch (reason) { setError(String(reason)) }
  }
  const pointerMs = (event: ReactPointerEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    return clamp(Math.round((event.clientX - rect.left) / pixelsPerSecond * 1000), 0, durationMs)
  }
  const startDrag = (event: ReactPointerEvent<HTMLDivElement>, tierId: string) => {
    if (!media || event.button !== 0) return
    const ms = pointerMs(event)
    event.currentTarget.setPointerCapture(event.pointerId)
    setDrag({ tierId, start: ms, end: ms })
  }
  const endDrag = async (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!drag || !media) return
    const end = pointerMs(event)
    const start_ms = Math.min(drag.start, end), end_ms = Math.max(drag.start, end)
    setDrag(null)
    if (end_ms - start_ms < 80) { seek(end); return }
    if (!(await flushDraft())) return
    if (tiers.find(tier => tier.id === drag.tierId)?.kind === 'source_words') {
      const word: SourceWord = { id: crypto.randomUUID(), media_id: media.id, start_ms, end_ms,
        text: '[word]', precision: 'estimated', review_status: 'unreviewed', speech_status: 'spoken', edit_status: 'inserted', attention: [], asr_run_id: null, provenance: { method: 'manual' } }
      source.mutate([...source.words, word].sort((a, b) => a.start_ms - b.start_ms))
      setSelectedWordId(word.id); setSelectedId(null); setDraft(null)
      requestAnimationFrame(() => textAreaRef.current?.focus())
      return
    }
    try {
      const annotation = await fetch(`/api/media/${media.id}/annotations`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tier_id: drag.tierId, span: { start_ms, end_ms, precision: 'estimated' },
          text: '', event_label: null, review_status: 'unreviewed' }),
      }).then(readJson<HumanAnnotation>)
      setAnnotations(items => [...items, annotation]); void chooseAnnotation(annotation)
      setSaveStatus('saved'); setLastSavedAt(annotation.created_at)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
  }

  const startToken = (event: ReactPointerEvent<HTMLButtonElement>, item: HumanAnnotation) => {
    if (event.button !== 0) return
    event.stopPropagation()
    const target = event.target as HTMLElement
    const mode = target.dataset.edge === 'start' ? 'start' : target.dataset.edge === 'end' ? 'end' : 'move'
    tokenStart.current = { item, clientX: event.clientX, mode }
    event.currentTarget.setPointerCapture(event.pointerId)
  }
  const tokenPosition = (clientX: number) => {
    const start = tokenStart.current
    if (!start) return null
    const delta = Math.round((clientX - start.clientX) / pixelsPerSecond * 1000)
    const originalStart = start.item.span.start_ms, originalEnd = start.item.span.end_ms
    const length = originalEnd - originalStart
    if (start.mode === 'move') {
      const begin = clamp(originalStart + delta, 0, durationMs - length)
      return { id: start.item.id, start_ms: begin, end_ms: begin + length }
    }
    if (start.mode === 'start') return { id: start.item.id, start_ms: clamp(originalStart + delta, 0, originalEnd - 1), end_ms: originalEnd }
    return { id: start.item.id, start_ms: originalStart, end_ms: clamp(originalEnd + delta, originalStart + 1, durationMs) }
  }
  const endToken = async (event: ReactPointerEvent<HTMLButtonElement>) => {
    const start = tokenStart.current
    if (!start) return
    const moved = Math.abs(event.clientX - start.clientX) >= 3
    const next = tokenPosition(event.clientX)
    tokenStart.current = null; setTokenDrag(null)
    if (!moved || !next || !media) return
    suppressTokenClick.current = true
    window.setTimeout(() => { suppressTokenClick.current = false }, 0)
    if (!(await flushDraft())) return
    const currentDraft = selectedId === start.item.id ? draft : null
    const before: Draft = currentDraft ?? { start_ms: start.item.span.start_ms, end_ms: start.item.span.end_ms,
      text: start.item.text, event_label: start.item.event_label ?? '', review_status: start.item.review_status,
      precision: start.item.span.precision }
    try {
      const updated = await fetch(`/api/media/${media.id}/annotations/${start.item.id}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ span: { start_ms: next.start_ms, end_ms: next.end_ms, precision: before.precision },
          text: before.text, event_label: before.event_label || null, review_status: before.review_status,
          metadata: start.item.metadata }),
      }).then(readJson<HumanAnnotation>)
      setAnnotations(items => items.map(item => item.id === updated.id ? updated : item))
      const after = { ...before, start_ms: updated.span.start_ms, end_ms: updated.span.end_ms }
      undoStack.current = [{ id: updated.id, before, after }]; redoStack.current = []
      setSelectedId(updated.id); setDraft(after); dirty.current = false
      setSaveStatus('saved'); setLastSavedAt(updated.updated_at); setError('')
    } catch (reason) {
      setSelectedId(start.item.id); setDraft({ ...before, start_ms: next.start_ms, end_ms: next.end_ms })
      dirty.current = true; editVersion.current += 1
      setSaveStatus('failed'); setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  const chooseWord = async (word: SourceWord) => {
    if (!(await flushDraft())) return
    setSelectedId(null); setDraft(null); setSelectedStateId(null); setSelectedWordId(word.id)
  }
  const chooseState = async (state: SubtitleState, skipSave = false) => {
    if (selectedStateId === state.id) { seek(stateDraft?.start_ms ?? state.start_ms); return }
    if (!(await flushAnnotationDraft()) || !(await source.flush())) return
    if (!skipSave && !(await flushStateDraft())) return
    setSelectedId(null); setSelectedWordId(null); setDraft(null)
    setSelectedStateId(state.id)
    setStateDraft({ start_ms: state.start_ms, end_ms: state.end_ms, text: state.text, review_status: state.review_status })
    seek(state.start_ms)
  }
  const saveState = async (reviewNext = false) => {
    if (!selectedState || !stateDraft) return
    try {
      await targetOCR.saveState(selectedState, reviewNext ? { ...stateDraft, review_status: 'reviewed' } : stateDraft)
      if (reviewNext) setStateDraft(current => current ? { ...current, review_status: 'reviewed' } : current)
      setError('')
      if (reviewNext) {
        const ordered = [...targetOCR.states].sort((a, b) => a.start_ms - b.start_ms || a.id.localeCompare(b.id))
        const next = ordered[ordered.findIndex(item => item.id === selectedState.id) + 1]
        if (next) void chooseState(next, true)
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
  }
  const stepState = (direction: -1 | 1) => {
    if (!selectedState) return
    const ordered = [...targetOCR.states].sort((a, b) => a.start_ms - b.start_ms || a.id.localeCompare(b.id))
    const next = ordered[ordered.findIndex(item => item.id === selectedState.id) + direction]
    if (next) void chooseState(next)
  }
  const editWord = (id: string, change: Partial<SourceWord>) => {
    source.mutate(source.words.map(word => word.id === id ? { ...word, ...change, edit_status: 'edited' } : word))
  }
  const insertWord = (start = playheadMs, end = Math.min(playheadMs + 300, durationMs), text = '[word]') => {
    if (!media || end <= start) return
    const word: SourceWord = { id: crypto.randomUUID(), media_id: media.id, start_ms: start,
      end_ms: end, text, precision: 'estimated', review_status: 'unreviewed',
      edit_status: 'inserted', speech_status: 'spoken', attention: [], asr_run_id: null, provenance: { method: 'manual' } }
    source.mutate([...source.words, word].sort((a, b) => a.start_ms - b.start_ms))
    setSelectedWordId(word.id); setSelectedId(null); setDraft(null)
    requestAnimationFrame(() => textAreaRef.current?.focus())
  }
  const deleteWord = () => {
    if (!selectedWord) return
    source.mutate(source.words.filter(word => word.id !== selectedWord.id)); setSelectedWordId(null)
  }
  const splitWord = () => {
    if (!selectedWord || selectedWord.end_ms - selectedWord.start_ms < 2 || selectedWord.text.length < 2) return
    const boundary = Math.round((selectedWord.start_ms + selectedWord.end_ms) / 2)
    const pieces = selectedWord.text.trim().split(/\s+/, 2)
    const cut = Math.max(1, Math.floor(selectedWord.text.length / 2))
    const left = pieces.length > 1 ? pieces[0] : selectedWord.text.slice(0, cut)
    const right = pieces.length > 1 ? selectedWord.text.slice(selectedWord.text.indexOf(pieces[1])) : selectedWord.text.slice(cut)
    const second: SourceWord = { ...selectedWord, id: crypto.randomUUID(), start_ms: boundary,
      text: right || '[word]', review_status: 'unreviewed', speech_status: 'spoken', edit_status: 'inserted', asr_run_id: null, provenance: { method: 'manual' } }
    source.mutate(source.words.flatMap(word => word.id === selectedWord.id ? [
      { ...word, end_ms: boundary, text: left || '[word]', review_status: 'unreviewed' as const, edit_status: 'edited' }, second,
    ] : [word]))
    setSelectedWordId(second.id)
  }
  const mergeNextWord = () => {
    if (!selectedWord) return
    const sorted = [...source.words].sort((a, b) => a.start_ms - b.start_ms)
    const index = sorted.findIndex(word => word.id === selectedWord.id)
    const next = sorted[index + 1]
    if (!next) return
    source.mutate(source.words.filter(word => word.id !== next.id).map(word => word.id === selectedWord.id ? {
      ...word, start_ms: Math.min(word.start_ms, next.start_ms), end_ms: Math.max(word.end_ms, next.end_ms),
      text: `${word.text.trimEnd()} ${next.text.trimStart()}`, review_status: 'unreviewed', edit_status: 'edited',
    } : word))
  }
  const nextUnreviewedWord = () => {
    const pending = [...source.words].filter(word => word.review_status === 'unreviewed' && word.speech_status !== 'not_spoken').sort((a, b) => a.start_ms - b.start_ms)
    const next = pending.find(word => word.start_ms > (selectedWord?.start_ms ?? -1)) ?? pending[0]
    if (next) void chooseWord(next)
  }
  const startWordToken = (event: ReactPointerEvent<HTMLButtonElement>, item: SourceWord) => {
    if (event.button !== 0) return
    event.stopPropagation()
    const edge = (event.target as HTMLElement).dataset.edge
    wordStart.current = { item, clientX: event.clientX, mode: edge === 'start' ? 'start' : edge === 'end' ? 'end' : 'move' }
    event.currentTarget.setPointerCapture(event.pointerId)
  }
  const wordPosition = (clientX: number) => {
    const origin = wordStart.current
    if (!origin) return null
    const delta = Math.round((clientX - origin.clientX) / pixelsPerSecond * 1000)
    const { start_ms, end_ms } = origin.item
    const length = end_ms - start_ms
    if (origin.mode === 'move') { const begin = clamp(start_ms + delta, 0, durationMs - length); return { id: origin.item.id, start_ms: begin, end_ms: begin + length } }
    if (origin.mode === 'start') return { id: origin.item.id, start_ms: clamp(start_ms + delta, 0, end_ms - 1), end_ms }
    return { id: origin.item.id, start_ms, end_ms: clamp(end_ms + delta, start_ms + 1, durationMs) }
  }
  const endWordToken = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const origin = wordStart.current
    if (!origin) return
    const moved = Math.abs(event.clientX - origin.clientX) >= 3
    const position = wordPosition(event.clientX)
    wordStart.current = null; setWordDrag(null)
    if (!moved || !position) return
    suppressTokenClick.current = true
    window.setTimeout(() => { suppressTokenClick.current = false }, 0)
    source.mutate(source.words.map(word => word.id === position.id ? { ...word, ...position, edit_status: 'edited' } : word))
    setSelectedWordId(position.id); setSelectedId(null); setDraft(null)
  }

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (activeDialog) return
      const element = event.target as HTMLElement
      const editing = element.matches('input, textarea, select') || element.isContentEditable
      const inWorkspace = Boolean(element.closest('.timeline-section, .inspector'))
      const key = keyFromEvent(event)
      const action = (Object.entries(shortcutSettings.bindings).find(([, binding]) => binding === key)?.[0] ?? null) as ShortcutAction | null
      if (activeMenu) return
      if (action === 'save' && (!editing || key === 'Ctrl+S')) { event.preventDefault(); void save(); return }
      if (editing) return
      if (event.key === 'Escape') { if (selectedId || selectedWordId) { event.preventDefault(); void flushDraft().then(ok => { if (ok) { setSelectedId(null); setSelectedWordId(null); setDraft(null) } }) } return }
      if (!action && key === 'Ctrl+Shift+Z' && inWorkspace) { event.preventDefault(); if (selectedWordId) source.redo(); else restoreDraft('redo'); return }
      if (!action) return
      if (action === 'help') { event.preventDefault(); openDialog('shortcuts'); return }
      if (action === 'playPause' && (!element.matches('button') || element.classList.contains('interval'))) { event.preventDefault(); void togglePlay(); return }
      if (action === 'seekBack' || action === 'seekForward') { event.preventDefault(); seek(playheadMs + (action === 'seekBack' ? -1000 : 1000)); return }
      if ((action === 'undo' || action === 'redo') && inWorkspace) { event.preventDefault(); if (selectedWordId) source[action](); else restoreDraft(action); return }
      if (['nudgeBack', 'nudgeForward', 'nudgeBackLarge', 'nudgeForwardLarge'].includes(action) && selectedWord && inWorkspace) {
        event.preventDefault(); const delta = (action === 'nudgeBack' || action === 'nudgeBackLarge' ? -1 : 1) * (action.endsWith('Large') ? 100 : 10)
        const length = selectedWord.end_ms - selectedWord.start_ms
        const begin = clamp(selectedWord.start_ms + delta, 0, durationMs - length)
        editWord(selectedWord.id, { start_ms: begin, end_ms: begin + length }); return
      }
      if (['nudgeBack', 'nudgeForward', 'nudgeBackLarge', 'nudgeForwardLarge'].includes(action) && selected && draft && inWorkspace) {
        event.preventDefault(); const delta = (action === 'nudgeBack' || action === 'nudgeBackLarge' ? -1 : 1) * (action.endsWith('Large') ? 100 : 10)
        const length = draft.end_ms - draft.start_ms
        const begin = clamp(draft.start_ms + delta, 0, durationMs - length)
        editDraft({ start_ms: begin, end_ms: begin + length }); return
      }
      if (action === 'deleteToken' && selectedWord && inWorkspace) { event.preventDefault(); deleteWord(); return }
      if (action === 'deleteToken' && selected && inWorkspace) { event.preventDefault(); void remove(); return }
      if (action === 'editToken' && element.classList.contains('source-word')) {
        event.preventDefault()
        const word = source.words.find(item => item.id === element.dataset.wordId)
        if (word) { void chooseWord(word).then(() => requestAnimationFrame(() => textAreaRef.current?.focus())) }
        return
      }
      if (action === 'editToken' && element.classList.contains('interval')) {
        event.preventDefault()
        const item = annotations.find(annotation => annotation.id === element.dataset.annotationId)
        if (item) { void chooseAnnotation(item).then(() => requestAnimationFrame(() => textAreaRef.current?.focus())) }
        return
      }
      if (action === 'zoomIn') { event.preventDefault(); changeZoom(Math.round(zoom * 1.4)); return }
      if (action === 'zoomOut') { event.preventDefault(); changeZoom(Math.round(zoom / 1.4)) }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  })

  const tickSeconds = pixelsPerSecond >= 100 ? 1 : pixelsPerSecond >= 55 ? 2 : 5
  const ticks = Array.from({ length: Math.min(Math.floor(durationMs / (tickSeconds * 1000)) + 1, 4000) }, (_, index) => index * tickSeconds * 1000)
  return <div className="research-app">
    <header className="research-toolbar">
      <div className="research-brand"><span className="brand-symbol">▤</span><strong>SimulST Annotator</strong></div>
      <div className="toolbar-group">
        <input ref={fileInput} hidden type="file" accept="video/mp4,.mp4" onChange={event => importFile(event.target.files?.[0])} />
        <div className="menu-anchor" ref={node => { menuAnchors.current.project = node }}>
          <button className="ui-button subtle toolbar-selector" aria-haspopup="true" aria-expanded={activeMenu === 'project'} aria-controls={activeMenu === 'project' ? 'project-menu' : undefined} onClick={() => toggleMenu('project')}>{project ? project.name : 'Projects'} ▾</button>
          {activeMenu === 'project' && <div id="project-menu" className="popover project-popover"><strong>Recent projects</strong>
            {projects.map(item => <button key={item.id} onClick={() => { setActiveMenu(null); void openProject(item.id) }}>{item.name}</button>)}
            <div className="popover-divider" />
            <button onClick={() => { setActiveMenu(null); openDialog('projects') }}>Manage projects…</button>
            {project && <><button onClick={() => { setActiveMenu(null); openDialog('storage') }}>Storage Overview</button><button onClick={() => { setActiveMenu(null); void closeProject() }}>Close project</button></>}
          </div>}
        </div>
        {project && <div className="menu-anchor" ref={node => { menuAnchors.current.media = node }}>
          <button className="ui-button subtle toolbar-selector" aria-haspopup="true" aria-expanded={activeMenu === 'media'} aria-controls={activeMenu === 'media' ? 'media-menu' : undefined} onClick={() => toggleMenu('media')}>{media ? media.display_name : 'Select media'} ▾</button>
          {activeMenu === 'media' && <div id="media-menu" className="popover media-popover"><strong>Media in {project.name}</strong>
            {assets.map(item => <button key={item.id} onClick={() => { setActiveMenu(null); void switchMedia(item.id) }}>{item.display_name}</button>)}
            <div className="popover-divider" />
            <button onClick={() => { setActiveMenu(null); fileInput.current?.click() }} disabled={busy}>{busy ? 'Processing…' : '＋ Add Video'}</button>
            <button onClick={() => { setActiveMenu(null); openDialog('media') }}>Manage media…</button>
          </div>}
        </div>}
        {project && <button className="ui-button subtle" onClick={() => openDialog('export')}>Export…</button>}
        {project && <div className="menu-anchor" ref={node => { menuAnchors.current.save = node }}><button className={`save-indicator ${saveStatus}`} aria-haspopup="true" aria-expanded={activeMenu === 'save'} onClick={() => toggleMenu('save')}>{saveStatus === 'saving' ? 'Saving…' : saveStatus === 'unsaved' ? 'Unsaved' : saveStatus === 'failed' ? 'Save failed' : saveStatus === 'saved' ? 'Saved' : 'Ready'} ▾</button>{activeMenu === 'save' && <div className="popover save-popover right"><strong>Local storage</strong><p>Database: {project.database_path}</p><p>Project: {project.name}</p><p>Last successful save: {lastSavedAt ? new Date(lastSavedAt).toLocaleString() : 'This session: none'}</p><p>Current media annotations: {annotations.length}</p><button onClick={() => { setActiveMenu(null); void openDataFolder() }}>Open Data Folder</button>{saveStatus === 'failed' && <button onClick={() => { setActiveMenu(null); void save() }}>Retry save</button>}</div>}</div>}
        <button className="toolbar-icon" title="Settings" aria-label="Settings" aria-haspopup="dialog" onClick={() => openDialog('settings')}>⚙</button>
      </div>
    </header>
    <main className="research-main">
      {activeDialog === 'projects' && <ProjectManager onClose={closeDialog} onOpen={openProject} onChanged={refreshProjects} openFolder={openFolder} beforeChange={flushDraft} />}
      {activeDialog === 'media' && project && <MediaManager project={project} onClose={closeDialog} onChanged={refreshMedia} onSelect={switchMedia} onImport={() => fileInput.current?.click()} beforeChange={flushDraft} />}
      {activeDialog === 'storage' && project && <StorageOverview project={project} onClose={closeDialog} openFolder={openFolder} />}
      {activeDialog === 'export' && project && <ExportDialog project={project} media={media} onClose={closeDialog} beforeExport={async () => (await flushDraft()) && (await source.flush())}  />}
      {activeDialog === 'shortcuts' && <ShortcutHelp bindings={shortcutSettings.bindings} onClose={closeDialog} />}
      {activeDialog === 'settings' && <SettingsDialog project={project} shortcuts={shortcutSettings} onApplyShortcuts={setShortcutSettings} onStorage={() => openDialog('storage')} onClose={closeDialog} asrPreferences={source.preferences} onSaveASR={source.updatePreferences} />}
      {error && <div className="app-message error" role="alert">{error}<button onClick={() => setError('')} aria-label="Dismiss error">×</button></div>}
      <div className="upper-workspace" style={window.innerWidth > 800 ? { height: effectiveSplitHeight } : undefined}>
        <section className="video-section" aria-label="Video preview">
          <div className="video-box" ref={videoBoxRef} style={{ width: window.innerWidth > 800 ? Math.min(658, (effectiveSplitHeight - 51) * videoAspect) : undefined, aspectRatio: videoAspect }}>{media ? <video key={media.id} ref={videoRef} src={media.video_url} preload="metadata" onLoadedMetadata={event => { if (event.currentTarget.videoWidth && event.currentTarget.videoHeight) setVideoAspect(event.currentTarget.videoWidth / event.currentTarget.videoHeight) }} onTimeUpdate={sync} onSeeking={sync} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} onError={() => setError('Video cannot be played. Check its codec.')} onClick={togglePlay} /> : <div className="video-placeholder"><span className="placeholder-play">▶</span><strong>Video preview</strong><p>{project ? 'Select or add media above' : 'Create or open a project to begin'}</p></div>}{media && <VideoROI videoRef={videoRef} boxRef={videoBoxRef} active={ocrSetup} roi={ocrROI} onChange={setOCRROI} />}</div>
          <div className="transport"><button title="Previous frame (approx. 40 ms)" onClick={() => stepFrame(-1)} disabled={!media}>|◀</button><button title={playing ? 'Pause' : 'Play'} onClick={togglePlay} disabled={!media}>{playing ? 'Ⅱ' : '▶'}</button><button title="Next frame (approx. 40 ms)" onClick={() => stepFrame(1)} disabled={!media}>▶|</button><span className="time-display">{timeText(timeMs)} <span>/ {media ? timeText(durationMs) : '--:--.---'}</span></span><label className="speed-control">Speed <select value={speed} onChange={event => setSpeed(Number(event.target.value))}><option value={0.5}>0.5×</option><option value={0.75}>0.75×</option><option value={1}>1×</option><option value={1.25}>1.25×</option><option value={1.5}>1.5×</option><option value={2}>2×</option></select></label></div>
        </section>
        <section className="inspector" aria-label="Annotation editor"><div className="inspector-heading"><div><h2>{selectedState ? 'Target subtitle editor' : selectedWord ? 'Source transcript editor' : 'Annotation editor'}</h2><p>{media ? media.display_name : project ? 'No media selected' : 'No project open'}</p></div>{(selected || selectedWord || selectedState) && <span className="review-pill">{selectedState?.review_status ?? selectedWord?.review_status ?? draft?.review_status}</span>}</div>
          {media && source.words.length > 0 && !selectedWord && !selected && !selectedState && <SourceTranscriptReview words={displayWords} timeMs={playheadMs} selectedId={selectedWordId} durationMs={durationMs}
            onChoose={word => { void chooseWord(word) }} onEdit={word => { void chooseWord(word).then(() => requestAnimationFrame(() => textAreaRef.current?.focus())) }}
            onPlayRange={playRange} onNext={nextUnreviewedWord} onPreview={(start, end) => source.previewAlignment(start, end, asrModel)}
            onApply={source.applyAlignment} onReject={source.rejectAlignment} />}
          {selectedState && stateDraft ? <div className="editor-fields target-state-editor"><label>Complete visible subtitle<textarea value={stateDraft.text} onChange={event => setStateDraft(current => current ? { ...current, text: event.target.value } : current)} placeholder="No visible subtitle" /></label><div className="field-grid"><label>Start (ms)<input type="number" min="0" max={durationMs} value={stateDraft.start_ms} onChange={event => setStateDraft(current => current ? { ...current, start_ms: Number(event.target.value) } : current)} /></label><label>End (ms)<input type="number" min="1" max={durationMs} value={stateDraft.end_ms} onChange={event => setStateDraft(current => current ? { ...current, end_ms: Number(event.target.value) } : current)} /></label></div><label>Review status<select value={stateDraft.review_status} onChange={event => setStateDraft(current => current ? { ...current, review_status: event.target.value as ReviewStatus } : current)}><option value="unreviewed">Unreviewed</option><option value="reviewed">Reviewed</option><option value="rejected">Rejected</option></select></label><p className="editor-footnote">Raw OCR remains separate · confidence {selectedState.provenance.confidence === null ? 'unknown' : `${Math.round(selectedState.provenance.confidence * 100)}%`}{selectedState.provenance.needs_review ? ' · check recognition' : ''}</p><div className="editor-actions"><button className="ui-button primary" onClick={() => void saveState()}>Save state</button><button className="ui-button subtle" onClick={() => stepState(-1)}>Previous</button><button className="ui-button subtle" onClick={() => stepState(1)}>Next</button><button className="ui-button subtle" onClick={() => void saveState(true)}>Review &amp; Next</button></div></div> : selectedWord ? <SourceWordInspector word={displayWords.find(word => word.id === selectedWord.id) ?? selectedWord} durationMs={durationMs} textRef={textAreaRef}
            onEdit={change => editWord(selectedWord.id, change)} onSave={() => void source.saveNow()} onSeek={() => seek(selectedWord.start_ms)}
            onPlay={() => { seek(Math.max(0, selectedWord.start_ms - 200)); void videoRef.current?.play().catch(() => setError('Video playback failed. Check the MP4 codec.')) }}
            onSplit={splitWord} onMerge={mergeNextWord} onDelete={deleteWord}
            onUndo={source.undo} onRedo={source.redo} canUndo={source.canUndo} canRedo={source.canRedo}
            onNext={nextUnreviewedWord} /> : selected && draft ? <div className="editor-fields"><label>Text / transcription<textarea ref={textAreaRef} value={draft.text} onChange={event => editDraft({ text: event.target.value })} placeholder="Word, character, subtitle, or note" /></label><div className="field-grid"><label>Start (ms)<input type="number" min="0" max={durationMs} value={draft.start_ms} onChange={event => editDraft({ start_ms: Number(event.target.value) })} /></label><label>End (ms)<input type="number" min="0" max={durationMs} value={draft.end_ms} onChange={event => editDraft({ end_ms: Number(event.target.value) })} /></label></div><div className="field-grid"><label>Event label<input value={draft.event_label} onChange={event => editDraft({ event_label: event.target.value })} placeholder="Optional" /></label><label>Review status<select value={draft.review_status} onChange={event => editDraft({ review_status: event.target.value as ReviewStatus })}><option value="unreviewed">Unreviewed</option><option value="reviewed">Reviewed</option><option value="rejected">Rejected</option></select></label></div><div className="field-grid"><label>Time precision<select value={draft.precision} onChange={event => editDraft({ precision: event.target.value as TimePrecision })}><option value="estimated">Estimated</option><option value="frame">Frame</option><option value="exact">Exact</option></select></label><label>Tier<input readOnly value={tiers.find(tier => tier.id === selected.tier_id)?.name ?? ''} /></label></div><div className="editor-actions"><button className="ui-button primary" onClick={save}>Save annotation</button><button className="ui-button subtle" onClick={() => restoreDraft('undo')} disabled={!undoStack.current.length}>Undo</button><button className="ui-button subtle" onClick={() => restoreDraft('redo')} disabled={!redoStack.current.length}>Redo</button><button className="ui-button danger" onClick={remove}>Delete</button></div><p className="editor-footnote">Manual annotation · ID {selected.id.slice(0, 8)}</p></div> : <div className="inspector-empty"><span>⌖</span><strong>{media ? 'Select or create an interval' : 'Open a video to annotate'}</strong><p>{media ? 'Drag across a tier below, then edit its text and timestamps here.' : 'Video and annotation details appear in this workspace.'}</p></div>}
        </section>
      </div>
      <div className="workspace-splitter" role="separator" aria-label="Resize video and timeline" aria-orientation="horizontal" aria-valuemin={240} aria-valuemax={Math.max(260, window.innerHeight - 240)} aria-valuenow={effectiveSplitHeight} tabIndex={0}
        onPointerDown={event => { splitStart.current = { y: event.clientY, height: effectiveSplitHeight }; event.currentTarget.setPointerCapture(event.pointerId); event.preventDefault() }}
        onPointerMove={event => { if (splitStart.current) setSplitHeight(clamp(splitStart.current.height + event.clientY - splitStart.current.y, 240, Math.max(260, window.innerHeight - 240))) }}
        onPointerUp={event => { if (!splitStart.current) return; const next = clamp(splitStart.current.height + event.clientY - splitStart.current.y, 240, Math.max(260, window.innerHeight - 240)); splitStart.current = null; setSplitHeight(next); localStorage.setItem('simulst.upperHeight', String(next)) }}
        onPointerCancel={() => { splitStart.current = null }}
        onKeyDown={event => { if (event.key === 'ArrowUp' || event.key === 'ArrowDown') { event.preventDefault(); const next = clamp(effectiveSplitHeight + (event.key === 'ArrowDown' ? 20 : -20), 240, Math.max(260, window.innerHeight - 240)); setSplitHeight(next); localStorage.setItem('simulst.upperHeight', String(next)) } }} />
      <section className="timeline-section" aria-label="Annotation timeline"><div className="timeline-toolbar"><div><h2>Timeline</h2><p>{media ? 'Drag an empty tier to create · select an interval to edit' : 'Import a video to begin annotating'}</p></div><div className="timeline-controls"><button className="ui-button subtle" onClick={addTier} disabled={!media}>＋ Add tier</button><button className="ui-button subtle" onClick={() => setTierManagerOpen(value => !value)} disabled={!media} aria-expanded={tierManagerOpen}>Manage tiers</button><label className="review-follow"><input type="checkbox" checked={timelineFollow} onChange={event => setTimelineFollow(event.target.checked)} /> Follow playback</label><span>Zoom</span><button className="zoom-button" title="Zoom out" onClick={() => changeZoom(Math.round(zoom / 1.4))}>−</button><span className="zoom-value">{Math.round(pixelsPerSecond)} px/s</span><button className="zoom-button" title="Zoom in" onClick={() => changeZoom(Math.round(zoom * 1.4))}>＋</button></div></div>
        {tierManagerOpen && media && <div className="tier-manager" aria-label="Tier management"><div><strong>Target Subtitle States</strong><button onClick={() => setShowTargetStates(value => !value)}>{showTargetStates ? 'Hide' : 'Show'}</button><button disabled title="OCR states are protected">Delete</button></div><div><strong>Source VAD</strong><button onClick={() => setShowVAD(value => !value)}>{showVAD ? 'Hide' : 'Show'}</button><button disabled title="Source VAD data is protected">Delete</button></div>{[...tiers].sort((a, b) => a.sort_order - b.sort_order).map(tier => <div key={tier.id}><strong>{tier.name}</strong><button onClick={() => toggleTier(tier.id)}>{hiddenTiers.has(tier.id) ? 'Show' : 'Hide'}</button><button onClick={() => void renameTier(tier)}>Rename</button><button onClick={() => void moveTier(tier, -1)} disabled={tier.sort_order === 0} aria-label={`Move ${tier.name} up`}>↑</button><button onClick={() => void moveTier(tier, 1)} disabled={tier.sort_order === tiers.length - 1} aria-label={`Move ${tier.name} down`}>↓</button><button onClick={() => void deleteTier(tier)} disabled={tier.kind !== 'custom'} title={tier.kind !== 'custom' ? 'Built-in tiers are protected' : 'Delete empty custom tier'}>Delete</button></div>)}</div>}
        {media && <div className="source-transcription-strip"><strong>Source words</strong><span className={`asr-state ${source.job?.status ?? 'idle'}`}>{source.job ? `${source.job.status}${source.job.cancel_requested && source.job.status === 'running' ? ' · cancelling' : ''}${source.job.device ? ` · ${source.job.device.toUpperCase()}` : ''}` : source.words.length ? `${source.words.length} editable words` : 'No source transcript'}</span>
          <label>Model <select value={asrModel} onChange={event => setAsrModel(event.target.value as 'small' | 'medium')} disabled={source.job?.status === 'queued' || source.job?.status === 'running'}><option value="small">small</option><option value="medium">medium</option></select></label>
          {source.job?.status === 'queued' || source.job?.status === 'running' ? <button onClick={() => void source.cancelJob()} disabled={!!source.job.cancel_requested}>Cancel job</button> : <button onClick={() => void source.startJob(asrModel, !!source.job || source.words.length > 0)}>{source.job?.status === 'failed' || source.job?.status === 'cancelled' ? 'Retry transcription' : source.words.length ? 'Re-transcribe' : 'Transcribe Source Speech'}</button>}
          <button onClick={() => insertWord()} disabled={durationMs < 1}>Insert word</button><button onClick={nextUnreviewedWord} disabled={!source.words.some(word => word.review_status === 'unreviewed' && word.speech_status !== 'not_spoken')}>Next unreviewed</button>
          {source.saveStatus !== 'idle' && <span className={`word-save-state ${source.saveStatus}`}>{source.saveStatus === 'saving' ? 'Saving words…' : source.saveStatus === 'unsaved' ? 'Unsaved words' : source.saveStatus === 'failed' ? 'Word save failed' : 'Words saved'}</span>}
          {source.job?.status === 'completed' && !source.job.applied && <small>New raw ASR result saved; current edits retained.</small>}
          {source.job?.status === 'failed' && <small role="alert">{source.job.error}</small>}
          {source.error && <small role="alert">{source.error}</small>}
          {source.candidates.length > 0 && <details className="asr-attention-list"><summary>{source.candidates.length} ASR word(s) need timing review</summary><div>{source.candidates.map(candidate => <p key={candidate.id}><strong>{candidate.text || '(empty)'}</strong> · {candidate.issues.join(', ').replaceAll('_', ' ')} {candidate.raw_start_seconds === null || candidate.raw_end_seconds === null ? <button onClick={() => insertWord(playheadMs, Math.min(playheadMs + 300, durationMs), candidate.text.trim() || '[word]')}>Insert with manual timing</button> : null}</p>)}</div></details>}
        </div>}
        {media && <div className="source-vad-strip"><strong>Source VAD</strong><span>{vad.job ? vad.job.status : vad.run ? 'ready' : 'not generated'}{vad.run ? ` · ${vad.intervals.filter(item => item.kind === 'speech').length} speech intervals` : ''}</span><label><input type="checkbox" checked={showVAD} onChange={event => setShowVAD(event.target.checked)} /> Show tier</label>{vad.job?.status === 'queued' || vad.job?.status === 'running' ? <button onClick={() => void vad.cancel()} disabled={!!vad.job.cancel_requested}>Cancel VAD</button> : <button onClick={() => void vad.start()}>{vad.run ? 'Re-run VAD' : 'Detect source speech'}</button>}{vad.error && <small role="alert">{vad.error}</small>}{vad.job?.status === 'failed' && <small role="alert">{vad.job.error}</small>}</div>}
        {media && <div className="target-ocr-strip"><strong>Target OCR</strong><span>{targetOCR.job?.status ?? (targetOCR.run ? 'ready' : 'not generated')}{targetOCR.run ? ` · ${targetOCR.states.length} states` : ''}</span><button onClick={() => setOCRSetup(value => !value)} aria-expanded={ocrSetup}>{ocrSetup ? 'Close ROI' : 'Set ROI / range'}</button><label><input type="checkbox" checked={showTargetStates} onChange={event => setShowTargetStates(event.target.checked)} /> Show tier</label>{(targetOCR.job?.status === 'queued' || targetOCR.job?.status === 'running') ? <button onClick={() => void targetOCR.cancel()}>Cancel OCR</button> : <button onClick={() => void targetOCR.start({ start_ms: ocrStart, end_ms: ocrEnd, fps: ocrFPS, roi: ocrROI })} disabled={ocrEnd <= ocrStart}>Run selected range</button>}{targetOCR.error && <small role="alert">{targetOCR.error}</small>}{targetOCR.job?.status === 'failed' && <small role="alert">{targetOCR.job.error}</small>}</div>}
        {media && ocrSetup && <div className="ocr-setup"><span>Drag or resize the box on the original video frame.</span><label>Start ms <input type="number" min="0" max={durationMs} value={ocrStart} onChange={event => setOCRStart(Number(event.target.value))} /></label><label>End ms <input type="number" min="1" max={durationMs} value={ocrEnd} onChange={event => setOCREnd(Number(event.target.value))} /></label><label>fps <input type="number" min="1" max="15" value={ocrFPS} onChange={event => setOCRFPS(Number(event.target.value))} /></label><button onClick={() => setOCRROI(defaultSubtitleROI)}>Reset ROI</button><small>ROI {ocrROI.x.toFixed(2)}, {ocrROI.y.toFixed(2)}, {ocrROI.width.toFixed(2)}, {ocrROI.height.toFixed(2)} · frame-relative</small></div>}
        <div className="timeline-scroll" ref={timelineNode} tabIndex={0} role="region" aria-label="Scrollable timeline tiers" onWheel={() => setTimelineFollow(false)} onTouchMove={() => setTimelineFollow(false)}><div className="timeline-content" style={{ width: timelineWidth + 148 }}><div className="timeline-ruler"><div className="tier-label">Time</div><div className="ruler-track" style={{ width: timelineWidth }}>{ticks.map(ms => <div className="ruler-tick" key={ms} style={{ left: ms / 1000 * pixelsPerSecond }}><span>{timeText(ms).slice(0, 5)}</span></div>)}</div></div><div className="wave-row"><div className="tier-label">Audio waveform</div><div className="wave-track" style={{ width: timelineWidth }}>{media ? <div ref={waveformNode} className="wave-container" style={{ width: timelineWidth }} /> : <div className="wave-empty">Waveform appears after import</div>}{media && !waveReady && <div className="wave-loading">Loading waveform…</div>}</div></div>
          {media && showTargetStates && <div className="tier-row"><div className="tier-label"><span className="tier-dot target-ocr" />Target Subtitle States</div><div className="tier-track target-ocr-track" style={{ width: timelineWidth }}>{targetOCR.states.map(state => <button key={state.id} data-ocr-state-id={state.id} className={`interval target-ocr ${selectedStateId === state.id ? 'selected' : ''} ${state.review_status === 'reviewed' ? 'reviewed' : ''}`} style={position(state.start_ms, state.end_ms, pixelsPerSecond)} onClick={() => void chooseState(state)} title={`${state.text || '(no subtitle)'} · ${timeText(state.start_ms)}–${timeText(state.end_ms)} · ${state.review_status}`}>{state.text || '∅'}</button>)}{!targetOCR.run && <span className="source-empty">Run Target OCR on a selected range</span>}</div></div>}
          {media && showVAD && <div className="tier-row source-vad-row"><div className="tier-label"><span className="tier-dot source-vad" />Source VAD</div><div className="tier-track source-vad-track" style={{ width: timelineWidth }}>{vad.intervals.filter(item => item.kind === 'speech').map(item => <div key={item.id} className="vad-interval" style={position(item.start_ms, item.end_ms, pixelsPerSecond)} title={`Estimated speech · ${timeText(item.start_ms)}–${timeText(item.end_ms)} · Silero VAD ${vad.run?.model_version ?? ''}`} />)}{!vad.run && <span className="source-empty">Run Source VAD to see estimated speech activity</span>}</div></div>}          {shownTiers.map(tier => <div className="tier-row" key={tier.id}><div className="tier-label"><span className={`tier-dot ${tier.kind}`} />{tier.name}</div><div className={`tier-track ${tier.kind}`} style={{ width: timelineWidth }} onPointerDown={event => startDrag(event, tier.id)} onPointerMove={event => { if (drag?.tierId === tier.id) setDrag({ ...drag, end: pointerMs(event) }) }} onPointerUp={endDrag} onPointerCancel={() => setDrag(null)}>
            {tier.kind === 'source_words' && source.words.length === 0 && <span className="source-empty">{source.job?.status === 'running' || source.job?.status === 'queued' ? 'Transcribing source speech…' : 'No source words yet · use Transcribe Source Speech or drag to insert a word'}</span>}
            {tier.kind === 'source_words' && displayWords.map(word => {
              const span = wordDrag?.id === word.id ? wordDrag : word
              return <button key={word.id} data-word-id={word.id} className={`interval source_words source-word ${spokenWordIds.includes(word.id) ? 'spoken' : ''} ${selectedWordId === word.id ? 'selected' : ''} ${word.speech_status === 'not_spoken' || word.review_status === 'rejected' ? 'not-spoken' : ''} ${word.attention.length && word.review_status !== 'reviewed' ? 'needs-review' : ''}`}
                style={position(span.start_ms, span.end_ms, pixelsPerSecond)}
                onPointerDown={event => startWordToken(event, word)}
                onPointerMove={event => { if (wordStart.current?.item.id === word.id) setWordDrag(wordPosition(event.clientX)) }}
                onPointerUp={endWordToken} onPointerCancel={() => { wordStart.current = null; setWordDrag(null) }}
                onClick={() => { if (suppressTokenClick.current) { suppressTokenClick.current = false; return } void chooseWord(word) }}
                onDoubleClick={() => { void chooseWord(word).then(() => requestAnimationFrame(() => textAreaRef.current?.focus())) }}
                title={`${word.text} · ${timeText(word.start_ms)}–${timeText(word.end_ms)}${word.attention.length ? ' · review timing' : ''}`}>
                <span className="interval-handle" data-edge="start" aria-hidden="true" />{word.text}<span className="interval-handle" data-edge="end" aria-hidden="true" />
              </button>
            })}
            {annotations.filter(item => item.tier_id === tier.id).map(item => {
              const span = tokenDrag?.id === item.id ? tokenDrag : item.span
              return <button key={item.id} data-annotation-id={item.id} className={`interval ${tier.kind} ${selectedId === item.id ? 'selected' : ''}`} style={position(span.start_ms, span.end_ms, pixelsPerSecond)}
                onPointerDown={event => startToken(event, item)} onPointerMove={event => { if (tokenStart.current?.item.id === item.id) setTokenDrag(tokenPosition(event.clientX)) }}
                onPointerUp={event => void endToken(event)} onPointerCancel={() => { tokenStart.current = null; setTokenDrag(null) }}
                onClick={() => { if (suppressTokenClick.current) { suppressTokenClick.current = false; return } void chooseAnnotation(item) }}
                title={`${item.text || 'Untitled interval'} · ${timeText(item.span.start_ms)}–${timeText(item.span.end_ms)}`}>
                <span className="interval-handle" data-edge="start" aria-hidden="true" />{item.text || 'Untitled'}<span className="interval-handle" data-edge="end" aria-hidden="true" />
              </button>
            })}{drag?.tierId === tier.id && <div className="drag-interval" style={position(Math.min(drag.start, drag.end), Math.max(drag.start, drag.end), pixelsPerSecond)} />}
          </div></div>)}
          <div className="playhead" style={{ left: 148 + playheadMs / 1000 * pixelsPerSecond }} /></div></div>
      </section>
      <div className="status-bar"><span>{media ? `${annotations.length} manual intervals · ${media.display_name}` : project ? 'No media selected' : 'No project open'}</span><span>{project ? project.name : 'Private local storage'}</span></div>
    </main>
  </div>
}






