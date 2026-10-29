import { useEffect, useRef, useState } from 'react'
import type { PointerEvent, RefObject } from 'react'
import type { SubtitleROI } from './useTargetOCR'

type FrameRect = { left: number; top: number; width: number; height: number }
type Drag = { mode: 'draw' | 'move' | 'start' | 'end'; x: number; y: number; roi: SubtitleROI }
const clamp = (value: number, low: number, high: number) => Math.max(low, Math.min(high, value))

export function VideoROI({ videoRef, boxRef, active, roi, onChange }: {
  videoRef: RefObject<HTMLVideoElement | null>; boxRef: RefObject<HTMLDivElement | null>
  active: boolean; roi: SubtitleROI; onChange: (value: SubtitleROI) => void
}) {
  const [frame, setFrame] = useState<FrameRect | null>(null)
  const drag = useRef<Drag | null>(null)
  useEffect(() => {
    const video = videoRef.current, box = boxRef.current
    if (!video || !box) return
    const measure = () => {
      const width = box.clientWidth, height = box.clientHeight
      const intrinsicWidth = video.videoWidth || 16, intrinsicHeight = video.videoHeight || 9
      const scale = Math.min(width / intrinsicWidth, height / intrinsicHeight)
      const frameWidth = intrinsicWidth * scale, frameHeight = intrinsicHeight * scale
      setFrame({ left: (width - frameWidth) / 2, top: (height - frameHeight) / 2,
                 width: frameWidth, height: frameHeight })
    }
    const observer = new ResizeObserver(measure)
    observer.observe(box)
    video.addEventListener('loadedmetadata', measure)
    measure()
    return () => { observer.disconnect(); video.removeEventListener('loadedmetadata', measure) }
  }, [videoRef, boxRef, active])
  if (!active || !frame) return null
  const point = (event: PointerEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    return { x: clamp((event.clientX - rect.left) / rect.width, 0, 1),
             y: clamp((event.clientY - rect.top) / rect.height, 0, 1) }
  }
  const down = (event: PointerEvent<HTMLDivElement>) => {
    const current = point(event)
    const handle = (event.target as HTMLElement).closest<HTMLElement>('[data-handle]')?.dataset.handle
    const inside = current.x >= roi.x && current.x <= roi.x + roi.width && current.y >= roi.y && current.y <= roi.y + roi.height
    const mode = handle === 'start' || handle === 'end' ? handle : inside ? 'move' : 'draw'
    drag.current = { mode, ...current, roi }
    if (mode === 'draw') onChange({ x: Math.min(.98, current.x), y: Math.min(.98, current.y), width: .02, height: .02 })
    event.currentTarget.setPointerCapture(event.pointerId)
    event.preventDefault()
  }
  const move = (event: PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return
    const current = point(event), start = drag.current
    if (start.mode === 'draw') {
      const x = Math.min(.98, Math.min(start.x, current.x)), y = Math.min(.98, Math.min(start.y, current.y))
      onChange({ x, y, width: Math.min(1 - x, Math.max(.02, Math.abs(current.x - start.x))),
                 height: Math.min(1 - y, Math.max(.02, Math.abs(current.y - start.y))) })
    } else if (start.mode === 'move') {
      onChange({ ...start.roi, x: clamp(start.roi.x + current.x - start.x, 0, 1 - start.roi.width),
                 y: clamp(start.roi.y + current.y - start.y, 0, 1 - start.roi.height) })
    } else if (start.mode === 'start') {
      const right = start.roi.x + start.roi.width, bottom = start.roi.y + start.roi.height
      const x = clamp(current.x, 0, right - .02), y = clamp(current.y, 0, bottom - .02)
      onChange({ x, y, width: right - x, height: bottom - y })
    } else {
      onChange({ ...start.roi, width: clamp(current.x - start.roi.x, .02, 1 - start.roi.x),
                 height: clamp(current.y - start.roi.y, .02, 1 - start.roi.y) })
    }
  }
  return <div className="ocr-frame" style={frame} onPointerDown={down} onPointerMove={move}
    onPointerUp={() => { drag.current = null }} onPointerCancel={() => { drag.current = null }}
    aria-label="Target subtitle region of interest">
    <div className="ocr-roi" style={{ left: `${roi.x * 100}%`, top: `${roi.y * 100}%`,
      width: `${roi.width * 100}%`, height: `${roi.height * 100}%` }}>
      <span className="ocr-roi-label">Target subtitle ROI</span>
      <span className="ocr-roi-handle start" data-handle="start" />
      <span className="ocr-roi-handle end" data-handle="end" />
    </div>
  </div>
}
