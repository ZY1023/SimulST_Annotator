import type { KeyboardEvent as ReactKeyboardEvent } from 'react'

export const shortcutGroups = {
  Playback: [
    ['playPause', 'Play / pause', 'Space'],
    ['seekBack', 'Seek back one second', 'Alt+ArrowLeft'],
    ['seekForward', 'Seek forward one second', 'Alt+ArrowRight'],
  ],
  Navigation: [
    ['zoomIn', 'Zoom in', '+'],
    ['zoomOut', 'Zoom out', '-'],
    ['help', 'Shortcut help', '?'],
  ],
  'Annotation Editing': [
    ['nudgeBack', 'Move token back 10 ms', 'ArrowLeft'],
    ['nudgeForward', 'Move token forward 10 ms', 'ArrowRight'],
    ['nudgeBackLarge', 'Move token back 100 ms', 'Shift+ArrowLeft'],
    ['nudgeForwardLarge', 'Move token forward 100 ms', 'Shift+ArrowRight'],
    ['editToken', 'Edit focused token', 'Enter'],
    ['deleteToken', 'Delete selected token', 'Delete'],
    ['undo', 'Undo annotation edit', 'Ctrl+Z'],
    ['redo', 'Redo annotation edit', 'Ctrl+Y'],
    ['save', 'Save pending edit', 'Ctrl+S'],
  ],
} as const

export type ShortcutAction = typeof shortcutGroups[keyof typeof shortcutGroups][number][0]
export type ShortcutBindings = Record<ShortcutAction, string>
export type ShortcutPreset = 'default' | 'annotation' | 'custom'
export type ShortcutSettingsValue = { preset: ShortcutPreset; bindings: ShortcutBindings }

const storageKey = 'simulst-shortcuts-v1'
const entries = Object.values(shortcutGroups).flat()
export const defaultBindings = Object.fromEntries(entries.map(([id, , key]) => [id, key])) as ShortcutBindings
export const annotationBindings: ShortcutBindings = {
  ...defaultBindings,
  playPause: 'P',
  seekBack: 'Alt+ArrowLeft', seekForward: 'Alt+ArrowRight',
  nudgeBack: 'ArrowLeft', nudgeForward: 'ArrowRight',
  nudgeBackLarge: 'Shift+ArrowLeft', nudgeForwardLarge: 'Shift+ArrowRight',
  zoomIn: 'Ctrl+ArrowUp', zoomOut: 'Ctrl+ArrowDown',
}
export const actionEntries = entries.map(([id, label]) => ({ id, label }))

export function keyFromEvent(event: KeyboardEvent | ReactKeyboardEvent): string | null {
  if (event.key === 'Tab' || event.key === 'Escape' || event.key === 'Dead' || event.key === 'Unidentified') return null
  const primary = event.ctrlKey || event.metaKey
  let key = event.key === ' ' ? 'Space' : event.key
  if (key === '=' || key === '+') key = '+'
  if (key === '_') key = '-'
  if (key.length === 1 && /[a-z]/i.test(key)) key = key.toUpperCase()
  if (key.length === 1 && !['+', '-', '?'].includes(key) && !/[A-Z0-9]/.test(key)) return null
  // Shift is already encoded in the printable characters + and ?.
  const shift = event.shiftKey && !['+', '?'].includes(key)
  return `${primary ? 'Ctrl+' : ''}${event.altKey ? 'Alt+' : ''}${shift ? 'Shift+' : ''}${key}`
}

export function reservedBinding(key: string): boolean {
  if (key === 'Space' || key === 'Enter' || key === 'Delete' || key === '+' || key === '-' || key === '?') return false
  if (/^(Shift\+)?Arrow(Left|Right)$/.test(key)) return false
  if (/^Alt\+Arrow(Left|Right)$/.test(key)) return false
  if (/^Ctrl\+(ArrowUp|ArrowDown|Space|S|Z|Y)$/.test(key)) return false
  // Keep browser navigation, reload, address-bar and OS shortcuts untouched.
  return key.startsWith('Ctrl+') || key.startsWith('Alt+') || key.startsWith('Shift+') || !/^[A-Z0-9]$/.test(key)
}

export function conflictFor(bindings: ShortcutBindings, action: ShortcutAction, key: string): ShortcutAction | null {
  return (Object.keys(bindings) as ShortcutAction[]).find(other => other !== action && bindings[other] === key) ?? null
}

export function loadShortcutSettings(): ShortcutSettingsValue {
  try {
    const raw = JSON.parse(localStorage.getItem(storageKey) ?? 'null')
    if (!raw || !['default', 'annotation', 'custom'].includes(raw.preset) || typeof raw.bindings !== 'object') throw new Error('Invalid settings')
    const bindings = { ...defaultBindings, ...raw.bindings } as ShortcutBindings
    if (Object.values(bindings).some(value => typeof value !== 'string' || reservedBinding(value))) throw new Error('Invalid binding')
    if (new Set(Object.values(bindings)).size !== Object.values(bindings).length) throw new Error('Conflicting binding')
    return { preset: raw.preset, bindings }
  } catch { return { preset: 'default', bindings: { ...defaultBindings } } }
}

export function saveShortcutSettings(value: ShortcutSettingsValue): void {
  localStorage.setItem(storageKey, JSON.stringify(value))
}
