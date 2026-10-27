import { useCallback, useEffect, useRef, useState } from 'react'
import WaveSurfer from 'wavesurfer.js'
import type { MediaAsset } from './contracts'

function formatTime(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  const minutes = Math.floor(total / 60).toString().padStart(2, '0')
  const seconds = (total % 60).toString().padStart(2, '0')
  const millis = Math.round(ms % 1000).toString().padStart(3, '0')
  return `${minutes}:${seconds}.${millis}`
}

function Player({ media }: { media: MediaAsset }) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const waveformRef = useRef<HTMLDivElement>(null)
  const waveRef = useRef<WaveSurfer | null>(null)
  const readyRef = useRef(false)
  const frameRef = useRef<number | null>(null)
  const [currentMs, setCurrentMs] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [waveReady, setWaveReady] = useState(false)
  const [waveError, setWaveError] = useState('')

  const sync = useCallback(() => {
    const video = videoRef.current
    const wave = waveRef.current
    if (!video) return
    const timelineMs = Math.round(video.currentTime * 1000) + media.media_offset_ms
    setCurrentMs(timelineMs)
    if (wave && readyRef.current && Math.abs(wave.getCurrentTime() - video.currentTime) > 0.04) {
      wave.setTime(video.currentTime)
    }
  }, [media.media_offset_ms])

  useEffect(() => {
    if (!waveformRef.current) return
    readyRef.current = false
    const wave = WaveSurfer.create({
      container: waveformRef.current,
      url: media.audio_url,
      waveColor: '#7890a5',
      progressColor: '#1db5a2',
      cursorColor: '#e7bb57',
      cursorWidth: 2,
      height: 112,
      normalize: true,
      interact: true,
    })
    waveRef.current = wave
    wave.on('ready', () => { readyRef.current = true; setWaveReady(true); sync() })
    wave.on('error', (error) => setWaveError(String(error)))
    wave.on('interaction', (seconds) => {
      const video = videoRef.current
      if (video) { video.currentTime = seconds; sync() }
    })
    return () => {
      readyRef.current = false
      waveRef.current = null
      setWaveReady(false)
      wave.destroy()
    }
  }, [media.id, media.audio_url, sync])

  useEffect(() => {
    if (!playing) return
    const tick = () => { sync(); frameRef.current = requestAnimationFrame(tick) }
    frameRef.current = requestAnimationFrame(tick)
    return () => { if (frameRef.current !== null) cancelAnimationFrame(frameRef.current) }
  }, [playing, sync])

  const togglePlayback = async () => {
    const video = videoRef.current
    if (!video) return
    if (video.paused) await video.play()
    else video.pause()
  }

  return (
    <section className="workspace" aria-label="Media workspace">
      <div className="workspace-topline">
        <div><span className="eyebrow">ACTIVE MEDIA</span><h2>{media.filename}</h2></div>
        <div className="media-meta"><span className="status-dot" /> LOCAL ASSET <span className="divider">/</span> {formatTime(media.duration_ms)}</div>
      </div>
      <div className="video-shell">
        <video
          key={media.id}
          ref={videoRef}
          src={media.video_url}
          controls
          preload="metadata"
          onTimeUpdate={sync}
          onSeeking={sync}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
          onError={() => setWaveError('Video could not be played. Check the MP4 codec.')}
        />
      </div>
      <div className="transport">
        <button className="play-button" onClick={togglePlayback} aria-label={playing ? 'Pause' : 'Play'}>{playing ? 'Ⅱ' : '▶'}</button>
        <div className="time-readout"><strong>{formatTime(currentMs)}</strong><span>/ {formatTime(media.duration_ms)}</span></div>
        <div className="transport-note">VIDEO CLOCK <span>→</span> WAVEFORM</div>
      </div>
      <div className="wave-panel">
        <div className="panel-heading"><div><span className="track-icon">◉</span><span>AUDIO WAVEFORM</span></div><span>{waveReady ? 'CLICK TO SEEK' : 'LOADING WAVEFORM…'}</span></div>
        <div className="waveform" ref={waveformRef} />
        {waveError && <p className="error">{waveError}</p>}
        <div className="wave-ruler"><span>00:00</span><span>{formatTime(media.duration_ms)}</span></div>
      </div>
      <div className="milestone-note"><span className="note-mark">i</span><div><strong>Milestone 1 · Media foundation</strong><p>Video playback and waveform seeking are available. Annotation tiers, OCR, and alignment arrive in later milestones.</p></div></div>
    </section>
  )
}

export default function App() {
  const [media, setMedia] = useState<MediaAsset[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    fetch('/api/media').then(async response => {
      if (!response.ok) throw new Error('Could not load media library')
      return response.json() as Promise<MediaAsset[]>
    }).then(items => { setMedia(items); if (items.length) setSelectedId(items[0].id) })
      .catch(cause => setError(String(cause)))
  }, [])

  const importFile = async (file?: File) => {
    if (!file) return
    setBusy(true); setError('')
    const body = new FormData(); body.append('file', file)
    try {
      const response = await fetch('/api/media', { method: 'POST', body })
      const result = await response.json()
      if (!response.ok) throw new Error(result.detail || 'Import failed')
      const asset = result as MediaAsset
      setMedia(items => [asset, ...items]); setSelectedId(asset.id)
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setBusy(false); if (inputRef.current) inputRef.current.value = '' }
  }

  const selected = media.find(item => item.id === selectedId)
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark"><span /><span /><span /><span /></div><div><strong>SimulST</strong><small>ANNOTATOR</small></div></div>
        <div className="sidebar-section-title">WORKSPACE <span>01</span></div>
        <div className="nav-item active"><span className="nav-icon">▦</span> Media library</div>
        <div className="sidebar-section-title projects-title">MEDIA FILES <span>{media.length.toString().padStart(2, '0')}</span></div>
        <div className="media-list">{media.map(item => <button key={item.id} className={`media-item ${selectedId === item.id ? 'selected' : ''}`} onClick={() => setSelectedId(item.id)}><span className="file-icon">▣</span><span className="media-item-copy"><strong title={item.filename}>{item.filename}</strong><small>{formatTime(item.duration_ms)}</small></span></button>)}</div>
        <div className="sidebar-bottom"><span className="status-dot" /> LOCAL WORKSPACE <small>v0.1.0</small></div>
      </aside>
      <main className="main-area">
        <header className="topbar"><div><span className="breadcrumb">WORKSPACE</span><span className="chevron">/</span><span className="breadcrumb-current">Media library</span></div><button className="import-button" disabled={busy} onClick={() => inputRef.current?.click()}><span>＋</span>{busy ? 'Processing media…' : 'Import MP4'}</button><input ref={inputRef} type="file" accept="video/mp4,.mp4" hidden onChange={event => importFile(event.target.files?.[0])} /></header>
        <div className="content-area"><div className="page-intro"><div><span className="eyebrow">RESEARCH WORKSPACE / 01</span><h1>Media library</h1><p>Import a source video and inspect its synchronized audio timeline.</p></div><span className="intro-count">{media.length.toString().padStart(2, '0')} <small>ASSETS</small></span></div>
          {error && <div className="error-banner" role="alert">{error}</div>}
          {selected ? <Player key={selected.id} media={selected} /> : <div className="empty-state"><div className="empty-art"><span>▤</span><i /><i /><i /></div><span className="eyebrow">START A NEW SESSION</span><h2>Your timeline begins with a video.</h2><p>Import an MP4 to extract its audio and open the synchronized player.</p><button className="empty-button" onClick={() => inputRef.current?.click()}>＋ &nbsp; Import your first MP4</button></div>}
        </div>
      </main>
    </div>
  )
}
