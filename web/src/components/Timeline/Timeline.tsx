/**
 * Timeline — CapCut-accurate implementation
 *
 * Based on direct CapCut desktop analysis:
 * • Standard moving playhead (NOT fixed-center) — moves right as time increases
 * • Wide header column (~130px) with horizontal icon layout
 * • CapCut teal (#16b3a0) for all video/overlay clips
 * • Dark navy (#0d1f35) with waveform for audio clips
 * • ~80px main video track, ~50px sub-tracks
 * • Near-zero border radius on clips (square-edged like CapCut)
 * • MM:SS time format, dense ruler ticks
 * • Toolbar: editing tools left, zoom right
 */

import React, { useRef, useEffect, useCallback, useState, useLayoutEffect, useMemo } from 'react'
import {
  Scissors, Trash2, Zap, Volume2, Undo2, Redo2,
  Lock, Unlock, Eye, EyeOff, VolumeX,
  Music, Plus, Minus, Magnet, Sliders, Headphones, Flag,
  ChevronDown, Pencil, Copy, Type, Layers, ArrowUp, ArrowDown, Mic, Square, ChevronsLeft,
} from 'lucide-react'
import { useStore, getLinkedClipIds, Clip, ProjectState, TrackState } from '../../store/useStore'
import { api } from '../../api/client'

// ── Layout constants (measured from CapCut) ─────────────────────────────────
const RULER_H     = 28    // px — time ruler (including toolbar-2nd row in CapCut)
const MAIN_H      = 80    // px — main video track
const SUB_H       = 50    // px — overlay / audio / text tracks
const HEADER_W    = 130   // px — left header column (wide enough for label + icons)
const TOOLBAR_H   = 38    // px — top toolbar row
/** Default Timeline height (no extra tracks). Dynamically grows when extras are added. */
const TIMELINE_H_BASE = TOOLBAR_H + RULER_H + SUB_H + MAIN_H + SUB_H + SUB_H  // 296
const ADD_TRACK_ROW_H = 28

// ── CapCut exact colors ──────────────────────────────────────────────────────
const TEAL        = '#16b3a0'   // CapCut video/overlay clip color
const TEAL_DARK   = '#0e8a7a'   // slightly darker teal for borders
const NAVY        = '#0d1f35'   // CapCut audio clip background
const NAVY_WAVE   = '#1e6fa0'   // CapCut waveform bar color
const BG_TRACK    = '#141420'   // track row background
const BG_HEADER   = '#0f0f18'   // header column background
const BG_RULER    = '#0a0a14'   // ruler background
const BORDER_CLR  = '#1e1e2e'   // divider between rows
const API_BASE    = 'http://127.0.0.1:7472'

// ── Track definitions (CapCut order: overlay above main, audio below) ────────
//
// Phase 4 made these dynamic — the timeline can have multiple parallel video
// and audio tracks. Each rendered row has a `getClips` thunk so we don't
// need to know about extra_video_tracks/extra_audio_tracks at every callsite.
interface TrackDef {
  /** Unique ID combining kind + index (e.g. "video", "video_1", "audio_2") */
  id:        string
  /** Category for the "+" button + drag-between-tracks logic */
  category:  'overlay' | 'video' | 'audio' | 'text'
  /** Display label, e.g. "Vídeo" or "Vídeo 2" */
  label:     string
  /** Index within its category (0 = main, 1+ = extras) */
  index:     number
  /** Row height in pixels */
  height:    number
  /** True for the primary main-video track (only one is "main") */
  isMain:    boolean
  /** True for any audio category track (main or extra) */
  isAudio:   boolean
  /** Pull the clips array out of the project for this track */
  getClips:  (p: ProjectState) => Clip[]
  /** State-id used for trackStates lookup. Main tracks have stable IDs;
   *  extras share their main's mute/lock/hide state (CapCut works the same way). */
  stateId:   string
}

type MarqueeRect = {
  x1: number
  y1: number
  x2: number
  y2: number
}

/** Build the ordered list of track rows from the current project. */
function buildTracks(project: ProjectState | null): TrackDef[] {
  const rows: TrackDef[] = []
  if (!project) {
    // Fallback skeleton when no project loaded (avoids crashes during transition)
    return [
      { id: 'overlay', category: 'overlay', label: 'Overlay', index: 0, height: SUB_H,  isMain: false, isAudio: false, getClips: () => [], stateId: 'overlay' },
      { id: 'video',   category: 'video',   label: 'Vídeo',   index: 0, height: MAIN_H, isMain: true,  isAudio: false, getClips: () => [], stateId: 'video' },
      { id: 'audio',   category: 'audio',   label: 'Áudio',   index: 0, height: SUB_H,  isMain: false, isAudio: true,  getClips: () => [], stateId: 'audio' },
      { id: 'text',    category: 'text',    label: 'Texto',   index: 0, height: SUB_H,  isMain: false, isAudio: false, getClips: () => [], stateId: 'text' },
    ]
  }
  // Overlay extras (rendered top-down, extras above main = CapCut)
  const overlayExtras = project.extra_overlay_tracks ?? []
  for (let i = overlayExtras.length - 1; i >= 0; i--) {
    rows.push({
      id: `overlay_${i + 1}`, category: 'overlay',
      label: overlayExtras[i].name || `Overlay ${i + 2}`,
      index: i + 1, height: SUB_H, isMain: false, isAudio: false,
      getClips: (p) => p.extra_overlay_tracks?.[i]?.clips ?? [],
      stateId: `overlay-${i + 1}`,
    })
  }
  rows.push({
    id: 'overlay', category: 'overlay', label: project.overlay_track.name || 'Overlay', index: 0,
    height: SUB_H, isMain: false, isAudio: false,
    getClips: (p) => p.overlay_track.clips,
    stateId: 'overlay',
  })
  // Main video (always height MAIN_H), then video extras below it
  rows.push({
    id: 'video', category: 'video', label: project.video_track.name || 'Vídeo', index: 0,
    height: MAIN_H, isMain: true, isAudio: false,
    getClips: (p) => p.video_track.clips,
    stateId: 'video',
  })
  const videoExtras = project.extra_video_tracks ?? []
  for (let i = 0; i < videoExtras.length; i++) {
    rows.push({
      id: `video_${i + 1}`, category: 'video',
      label: videoExtras[i].name || `Vídeo ${i + 2}`,
      index: i + 1, height: SUB_H, isMain: false, isAudio: false,
      getClips: (p) => p.extra_video_tracks?.[i]?.clips ?? [],
      stateId: `video-${i + 1}`,
    })
  }
  // Audio main, then extras
  rows.push({
    id: 'audio', category: 'audio', label: project.audio_track.name || 'Áudio', index: 0,
    height: SUB_H, isMain: false, isAudio: true,
    getClips: (p) => p.audio_track.clips,
    stateId: 'audio',
  })
  const audioExtras = project.extra_audio_tracks ?? []
  for (let i = 0; i < audioExtras.length; i++) {
    rows.push({
      id: `audio_${i + 1}`, category: 'audio',
      label: audioExtras[i].name || `Áudio ${i + 2}`,
      index: i + 1, height: SUB_H, isMain: false, isAudio: true,
      getClips: (p) => p.extra_audio_tracks?.[i]?.clips ?? [],
      stateId: `audio-${i + 1}`,
    })
  }
  // Text at the bottom
  rows.push({
    id: 'text', category: 'text', label: project.text_track.name || 'Texto', index: 0,
    height: SUB_H, isMain: false, isAudio: false,
    getClips: (p) => p.text_track.clips,
    stateId: 'text',
  })
  return rows
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtTime(s: number): string {
  if (!isFinite(s) || s < 0) return '00:00'
  const m   = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
}

function normalizeTransitionName(value: unknown): string {
  const v = String(value ?? 'Corte').trim()
  return v || 'Corte'
}

function transitionBadgeColor(name: string): string {
  const n = normalizeTransitionName(name).toLowerCase()
  if (n.includes('fade')) return '#8b5cf6'
  if (n.includes('dissolver')) return '#06b6d4'
  if (n.includes('wipe')) return '#10b981'
  if (n.includes('zoom')) return '#f97316'
  return '#64748b'
}

// ═══════════════════════════════════════════════════════════════════════════════
export function Timeline({ height }: { height?: number }) {
  const {
    project, previewTime, setPreviewTime,
    timelineZoom, setTimelineZoom,
    rippleMode, setRippleMode,
    selectedClipId, selectedClipIds, setSelectedClip, toggleClipSelection,
    snapEnabled, setSnapEnabled,
    updateClip, splitClip, deleteClip, rippleDelete, closeGapAfterClip, undo, redo,
    compileSelectedClips, uncompileSelectedClips, addTimelineMarker, removeTimelineMarker,
    past, future,
    trackStates, setTrackState,
    appendVideo, importAudio, importImage, importOverlayVideo, setSourceProxy,
    addTextClip, duplicateSelectionAtPlayhead,
  } = useStore()

  // P0-2: file drop handlers — called from TrackRow when a native file is dropped
  const handleVideoFileDrop = useCallback(async (filePath: string) => {
    if (!project) return
    try {
      const { exportSettings } = useStore.getState()
      const res = await fetch(`http://127.0.0.1:7472/api/analyze-video`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: filePath, silence_style: exportSettings.silenceStyle, auto_cut: exportSettings.silenceEnabled }),
      })
      if (!res.ok) return
      const d = await res.json()
      appendVideo(
        d.clips ?? [],
        d.waveform ?? [],
        d.proxy_status && d.proxy_status !== 'not_needed'
          ? { source_path: filePath, proxy_path: d.proxy_path ?? '', proxy_status: d.proxy_status }
          : undefined,
      )
    } catch { /* ignore */ }
  }, [project, appendVideo])

  const handleAudioFileDrop = useCallback(async (filePath: string) => {
    if (!project) return
    try {
      const [durRes] = await Promise.all([
        fetch(`http://127.0.0.1:7472/api/audio-waveform?path=${encodeURIComponent(filePath)}&bins=300`),
      ])
      const wfData = await durRes.json().catch(() => ({ samples: [], duration_s: 60 }))
      importAudio(filePath, wfData.duration_s ?? 60, wfData.samples ?? [])
    } catch { /* ignore */ }
  }, [project, importAudio])

  const handleImageFileDrop = useCallback((filePath: string) => {
    if (!project) return
    importImage(filePath, 5)
  }, [project, importImage])

  const handleOverlayVideoFileDrop = useCallback(async (filePath: string) => {
    if (!project) return
    try {
      const { exportSettings } = useStore.getState()
      const res = await fetch('http://127.0.0.1:7472/api/analyze-video', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: filePath,
          silence_style: exportSettings.silenceStyle,
          auto_cut: false,
        }),
      })
      if (!res.ok) return
      const d = await res.json()
      const duration = Math.max(0.1, Number(d.duration_s ?? 5))
      importOverlayVideo(filePath, duration)
      if (d.proxy_status === 'ready' && d.proxy_path) {
        setSourceProxy(filePath, d.proxy_path)
      }
    } catch { /* ignore */ }
  }, [project, importOverlayVideo, setSourceProxy])

  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const mediaStreamRef = useRef<MediaStream | null>(null)
  const recordingChunksRef = useRef<BlobPart[]>([])
  const recordingTimerRef = useRef<number | null>(null)
  const recordingStartedAtRef = useRef<number | null>(null)
  const [recording, setRecording] = useState(false)
  const [recordingSeconds, setRecordingSeconds] = useState(0)

  const stopRecordingTimer = useCallback(() => {
    if (recordingTimerRef.current !== null) {
      window.clearInterval(recordingTimerRef.current)
      recordingTimerRef.current = null
    }
  }, [])

  const detectRecordingMimeType = useCallback((): string => {
    const candidates = [
      'audio/webm;codecs=opus',
      'audio/webm',
      'audio/ogg;codecs=opus',
      'audio/ogg',
      'audio/mp4',
    ]
    for (const candidate of candidates) {
      try {
        if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(candidate)) {
          return candidate
        }
      } catch {
        // ignore
      }
    }
    return ''
  }, [])

  const extFromMime = useCallback((mime: string): string => {
    const m = mime.toLowerCase()
    if (m.includes('ogg')) return '.ogg'
    if (m.includes('mp4')) return '.m4a'
    if (m.includes('wav')) return '.wav'
    return '.webm'
  }, [])

  const waveformFromAudioBlob = useCallback(async (blob: Blob, bins = 300): Promise<{ samples: number[]; duration_s: number | null }> => {
    try {
      const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      if (!AudioCtx) return { samples: [], duration_s: null }
      const ctx = new AudioCtx()
      const buffer = await ctx.decodeAudioData(await blob.arrayBuffer())
      const length = buffer.length
      const channels = Math.max(1, buffer.numberOfChannels)
      const samples: number[] = []
      for (let i = 0; i < bins; i++) {
        const start = Math.floor((i / bins) * length)
        const end = Math.max(start + 1, Math.floor(((i + 1) / bins) * length))
        let sum = 0
        let count = 0
        for (let ch = 0; ch < channels; ch++) {
          const data = buffer.getChannelData(ch)
          for (let j = start; j < end; j++) {
            const v = data[j] || 0
            sum += v * v
            count++
          }
        }
        samples.push(Math.sqrt(sum / Math.max(1, count)))
      }
      await ctx.close?.()
      const max = Math.max(...samples, 0.0001)
      return {
        samples: samples.map((v) => Math.max(0.02, Math.min(1, v / max))),
        duration_s: Number.isFinite(buffer.duration) && buffer.duration > 0 ? buffer.duration : null,
      }
    } catch {
      return { samples: [], duration_s: null }
    }
  }, [])

  const startAudioRecording = useCallback(async () => {
    if (recording) return
    if (!project) {
      window.alert('Abra um projeto antes de gravar áudio.')
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mimeType = detectRecordingMimeType()
      const recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream)

      mediaStreamRef.current = stream
      mediaRecorderRef.current = recorder
      recordingChunksRef.current = []
      recordingStartedAtRef.current = performance.now()

      recorder.ondataavailable = (ev: BlobEvent) => {
        if (ev.data && ev.data.size > 0) recordingChunksRef.current.push(ev.data)
      }

      recorder.onstop = async () => {
        const recordedMs = recordingStartedAtRef.current === null
          ? recordingSeconds * 1000
          : performance.now() - recordingStartedAtRef.current
        const recordedDuration = Math.max(0.1, recordedMs / 1000)
        recordingStartedAtRef.current = null
        stopRecordingTimer()
        setRecording(false)
        setRecordingSeconds(0)
        try {
          mediaStreamRef.current?.getTracks().forEach((track) => track.stop())
        } catch {
          // ignore
        }
        mediaStreamRef.current = null

        const blob = new Blob(recordingChunksRef.current, { type: recorder.mimeType || 'audio/webm' })
        recordingChunksRef.current = []
        if (!blob.size) return
        const localAudio = await waveformFromAudioBlob(blob, 300)

        const ext = extFromMime(blob.type)
        const fileName = `timeline-recording${ext}`
        const form = new FormData()
        form.append('audio', blob, fileName)

        try {
          const saved = await api.post('/api/audio-recording/save', form, {
            headers: { 'Content-Type': 'multipart/form-data' },
          })
          const path = String(saved.data?.path || '')
          if (!path) return
          const wfRes = await fetch(`${API_BASE}/api/audio-waveform?path=${encodeURIComponent(path)}&bins=300`)
          const wfData = await wfRes.json().catch(() => ({ samples: [], duration_s: 60 }))
          const probedDuration = Number(wfData.duration_s ?? 0)
          const localDuration = Number(localAudio.duration_s ?? 0)
          const candidates = [localDuration, recordedDuration, probedDuration].filter((v) => Number.isFinite(v) && v > 0.05)
          const chosenDuration = candidates.length ? Math.min(...candidates) : 0.1
          const safeDuration = Math.max(0.1, Math.min(chosenDuration, recordedDuration + 0.25))
          const backendWaveform = Array.isArray(wfData.samples) ? wfData.samples : []
          importAudio(path, safeDuration, localAudio.samples.length ? localAudio.samples : backendWaveform)
        } catch {
          window.alert('Não foi possível salvar/importar a gravação.')
        }
      }

      recorder.start(250)
      setRecording(true)
      setRecordingSeconds(0)
      stopRecordingTimer()
      recordingTimerRef.current = window.setInterval(() => {
        setRecordingSeconds((prev) => prev + 1)
      }, 1000)
    } catch {
      window.alert('Permissão de microfone negada ou indisponível.')
    }
  }, [detectRecordingMimeType, extFromMime, importAudio, project, recording, stopRecordingTimer, waveformFromAudioBlob])

  const stopAudioRecording = useCallback(() => {
    try {
      const recorder = mediaRecorderRef.current
      if (recorder && recorder.state !== 'inactive') recorder.stop()
      else {
        stopRecordingTimer()
        setRecording(false)
        setRecordingSeconds(0)
        recordingStartedAtRef.current = null
      }
    } catch {
      stopRecordingTimer()
      setRecording(false)
      setRecordingSeconds(0)
      recordingStartedAtRef.current = null
    }
  }, [stopRecordingTimer])

  useEffect(() => {
    return () => {
      stopRecordingTimer()
      recordingStartedAtRef.current = null
      try {
        const recorder = mediaRecorderRef.current
        if (recorder && recorder.state !== 'inactive') recorder.stop()
      } catch {
        // ignore
      }
      try {
        mediaStreamRef.current?.getTracks().forEach((track) => track.stop())
      } catch {
        // ignore
      }
    }
  }, [stopRecordingTimer])

  const scrollRef  = useRef<HTMLDivElement>(null)
  const syncing    = useRef(false)
  const marqueeJustEnded = useRef(false)
  const [viewW, setViewW] = useState(800)
  const [marqueeRect, setMarqueeRect] = useState<MarqueeRect | null>(null)

  const duration = project?.duration_s ?? 0
  const pxPerSec = 80 * timelineZoom
  const totalW   = Math.max(duration * pxPerSec + viewW, viewW)
  const allClips = project
    ? [
      ...project.video_track.clips,
      ...project.audio_track.clips,
      ...project.text_track.clips,
      ...project.overlay_track.clips,
      ...(project.extra_video_tracks ?? []).flatMap((t) => t.clips),
      ...(project.extra_audio_tracks ?? []).flatMap((t) => t.clips),
      ...(project.extra_overlay_tracks ?? []).flatMap((t) => t.clips),
    ]
    : []
  const selectedClip = selectedClipId ? allClips.find((c) => c.id === selectedClipId) ?? null : null
  const canLayerMove = !!selectedClip && ['image', 'video_overlay', 'video', 'text'].includes(selectedClip.clip_type)
  const canCompileSelection = selectedClipIds.length > 1
  const canUncompileSelection = !!selectedClip?.compound_id

  // Measure scroll container width
  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setViewW(el.clientWidth))
    ro.observe(el)
    setViewW(el.clientWidth)
    return () => ro.disconnect()
  }, [])

  // Keyboard shortcuts: Delete = ripple-delete selected (all multi-selected), S = split
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
      if (e.key === 'Delete' || e.key === 'Backspace') {
        const state = useStore.getState()
        // Delete all multi-selected clips, or the single selected one
        const toDelete = state.selectedClipIds.length > 1
          ? [...state.selectedClipIds]
          : state.selectedClipId ? [state.selectedClipId] : []
        if (toDelete.length === 0) return
        e.preventDefault()
        if (state.rippleMode) state.rippleDelete(toDelete[0])
        else state.deleteClip(toDelete[0])
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'd') {
        e.preventDefault()
        useStore.getState().duplicateSelectionAtPlayhead()
        return
      }
      if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
        const state = useStore.getState()
        const id = state.selectedClipId
        if (!id || !state.project) return
        const all = [
          ...state.project.video_track.clips,
          ...state.project.audio_track.clips,
          ...state.project.text_track.clips,
          ...state.project.overlay_track.clips,
          ...(state.project.extra_video_tracks ?? []).flatMap((t) => t.clips),
          ...(state.project.extra_audio_tracks ?? []).flatMap((t) => t.clips),
          ...(state.project.extra_overlay_tracks ?? []).flatMap((t) => t.clips),
        ]
        const clip = all.find((c) => c.id === id)
        if (!clip) return
        if (!(clip.clip_type === 'image' || clip.clip_type === 'video_overlay' || clip.clip_type === 'video' || clip.clip_type === 'sticker' || clip.clip_type === 'text')) return
        e.preventDefault()
        const delta = e.key === 'ArrowUp' ? 1 : -1
        const next = Math.max(-200, Math.min(200, Number(clip.z_order ?? 0) + delta))
        state.updateClip(id, { z_order: next })
        return
      }
      if (e.key === 's' && !e.ctrlKey && !e.metaKey && selectedClipId) {
        e.preventDefault()
        useStore.getState().splitClip(selectedClipId, useStore.getState().previewTime)
      }
      // Escape = clear selection
      if (e.key === 'Escape') {
        useStore.getState().clearSelection()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selectedClipId])

  // (Auto-select-on-playhead removed — it was overwriting the user's manual
  // selection on the audio/text tracks. Selection now only changes when the
  // user explicitly clicks a clip.)

  // Auto-scroll to keep playhead visible when it's near the right edge
  useEffect(() => {
    const el = scrollRef.current
    if (!el || syncing.current) return
    const x = previewTime * pxPerSec
    const { scrollLeft, clientWidth } = el
    // If playhead is out of view, scroll to put it at 20% from left
    if (x < scrollLeft || x > scrollLeft + clientWidth - 40) {
      syncing.current = true
      el.scrollLeft = Math.max(0, x - clientWidth * 0.2)
      requestAnimationFrame(() => { syncing.current = false })
    }
  }, [previewTime, pxPerSec])

  // Ctrl+scroll = zoom
  const onWheel = useCallback((e: React.WheelEvent) => {
    if (!e.ctrlKey && !e.metaKey) return
    e.preventDefault()
    setTimelineZoom(Math.max(0.1, Math.min(10, timelineZoom - e.deltaY * 0.005)))
  }, [setTimelineZoom, timelineZoom])

  // Click ruler → seek
  const onRulerMouseDown = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const seek = (ev: MouseEvent | React.MouseEvent) => {
      const el = scrollRef.current
      if (!el) return
      const rect = el.getBoundingClientRect()
      const x = (ev as MouseEvent).clientX - rect.left + el.scrollLeft
      setPreviewTime(Math.max(0, Math.min(duration, x / pxPerSec)))
    }
    seek(e as unknown as MouseEvent)
    const onMove = (ev: MouseEvent) => seek(ev)
    const onUp   = () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp) }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [pxPerSec, duration, setPreviewTime])

  const bumpSelectedLayers = useCallback((delta: number) => {
    const state = useStore.getState()
    const proj = state.project
    if (!proj) return
    const ids = state.selectedClipIds.length > 0
      ? state.selectedClipIds
      : state.selectedClipId
        ? [state.selectedClipId]
        : []
    if (ids.length === 0) return
    const map = new Map<string, Clip>([
      ...proj.video_track.clips,
      ...proj.audio_track.clips,
      ...proj.text_track.clips,
      ...proj.overlay_track.clips,
      ...(proj.extra_video_tracks ?? []).flatMap((t) => t.clips),
      ...(proj.extra_audio_tracks ?? []).flatMap((t) => t.clips),
      ...(proj.extra_overlay_tracks ?? []).flatMap((t) => t.clips),
    ].map((c) => [c.id, c]))
    for (const id of ids) {
      const clip = map.get(id)
      if (!clip) continue
      if (!(clip.clip_type === 'image' || clip.clip_type === 'video_overlay' || clip.clip_type === 'video' || clip.clip_type === 'sticker' || clip.clip_type === 'text')) continue
      const next = Math.max(-200, Math.min(200, Number(clip.z_order ?? 0) + delta))
      state.updateClip(id, { z_order: next })
    }
  }, [])

  // Phase 4: dynamic track list derived from project (includes extras)
  const tracks = buildTracks(project)
  const allTracksH = tracks.reduce((s, t) => s + t.height, 0)
  const playheadX  = previewTime * pxPerSec
  const naturalHeight = TOOLBAR_H + RULER_H + allTracksH
  const timelineHeight = height ?? naturalHeight
  const trackAreaHeight = RULER_H + allTracksH
  const trackTopMap = useMemo(() => {
    let y = RULER_H
    const map = new Map<string, number>()
    for (const tr of tracks) {
      map.set(tr.id, y)
      y += tr.height
    }
    return map
  }, [tracks])

  const findClipsInRect = useCallback((rect: MarqueeRect): string[] => {
    if (!project) return []
    const left = Math.min(rect.x1, rect.x2)
    const right = Math.max(rect.x1, rect.x2)
    const top = Math.min(rect.y1, rect.y2)
    const bottom = Math.max(rect.y1, rect.y2)
    const ids: string[] = []
    for (const tr of tracks) {
      if (trackStates[tr.stateId]?.hidden) continue
      const rowTop = trackTopMap.get(tr.id) ?? RULER_H
      const rowBottom = rowTop + tr.height
      if (rowBottom < top || rowTop > bottom) continue
      for (const clip of tr.getClips(project)) {
        const clipLeft = clip.start_s * pxPerSec
        const clipRight = clip.end_s * pxPerSec
        if (clipRight >= left && clipLeft <= right) ids.push(clip.id)
      }
    }
    return Array.from(new Set(ids))
  }, [project, pxPerSec, trackStates, trackTopMap, tracks])

  const startMarqueeSelection = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (e.button !== 0 || !project) return
    const target = e.target as HTMLElement
    let node: HTMLElement | null = target
    while (node && node !== e.currentTarget) {
      if (node.getAttribute('data-clip') === 'true') return
      if (node.getAttribute('data-no-marquee') === 'true') return
      if (node.tagName === 'INPUT' || node.tagName === 'TEXTAREA' || node.tagName === 'BUTTON' || node.tagName === 'SELECT') return
      node = node.parentElement
    }
    const rect = e.currentTarget.getBoundingClientRect()
    const start = {
      x: Math.max(0, e.clientX - rect.left),
      y: Math.max(0, e.clientY - rect.top),
    }
    if (start.y < RULER_H) return
    const additive = e.ctrlKey || e.metaKey || e.shiftKey
    const originalIds = additive ? [...useStore.getState().selectedClipIds] : []
    let didDragMarquee = false

    const onMove = (ev: MouseEvent) => {
      const next = {
        x: Math.max(0, ev.clientX - rect.left),
        y: Math.max(0, ev.clientY - rect.top),
      }
      if (!didDragMarquee && Math.hypot(next.x - start.x, next.y - start.y) < 5) return
      didDragMarquee = true
      setMarqueeRect({ x1: start.x, y1: start.y, x2: next.x, y2: next.y })
    }

    const onUp = (ev: MouseEvent) => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      const end = {
        x: Math.max(0, ev.clientX - rect.left),
        y: Math.max(0, ev.clientY - rect.top),
      }
      if (didDragMarquee) {
        const picked = findClipsInRect({ x1: start.x, y1: start.y, x2: end.x, y2: end.y })
        const ids = additive ? Array.from(new Set([...originalIds, ...picked])) : picked
        useStore.setState({
          selectedClipIds: ids,
          selectedClipId: ids.length ? ids[ids.length - 1] : null,
        })
        marqueeJustEnded.current = true
        setTimeout(() => { marqueeJustEnded.current = false }, 0)
      }
      setMarqueeRect(null)
    }

    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [findClipsInRect, project])

  return (
    <div
      className="flex flex-col flex-shrink-0 min-h-0"
      style={{
        height: timelineHeight,
        minHeight: height ? 0 : TIMELINE_H_BASE,
        background: BG_TRACK, borderTop: `1px solid ${BORDER_CLR}`,
      }}
    >
      {/* ── Toolbar ── */}
      <Toolbar
        zoom={timelineZoom * 100}
        onZoomIn ={() => setTimelineZoom(Math.min(10,  timelineZoom + 0.25))}
        onZoomOut={() => setTimelineZoom(Math.max(0.1, timelineZoom - 0.25))}
        onZoomFit={() => {
          if (duration > 0 && viewW > 0) {
            // Base pxPerSec at zoom=1 is 80; solve for zoom: pxPerSec = 80 * zoom
            const fitZoom = (viewW - 20) / (duration * 80)
            setTimelineZoom(Math.max(0.1, Math.min(10, fitZoom)))
          } else {
            setTimelineZoom(1)
          }
        }}
        onAddMedia={() => useStore.getState().setActiveLeftTab('media')}
        onAddText={() => {
          const t = Math.max(0, previewTime)
          addTextClip(t, t + 3, 'Novo texto')
          useStore.getState().setActiveLeftTab('text')
        }}
        onDuplicate={() => duplicateSelectionAtPlayhead()}
        onSplit={() => { if (selectedClipId) splitClip(selectedClipId, previewTime) }}
        onDelete={() => {
          if (!selectedClipId) return
          if (rippleMode) rippleDelete(selectedClipId)
          else deleteClip(selectedClipId)
        }}
        onCloseGap={() => { if (selectedClipId) closeGapAfterClip(selectedClipId) }}
        onLayerUp={() => bumpSelectedLayers(1)}
        onLayerDown={() => bumpSelectedLayers(-1)}
        onCompile={() => compileSelectedClips()}
        onUncompile={() => uncompileSelectedClips()}
        canCompile={canCompileSelection}
        canUncompile={canUncompileSelection}
        onAddMarker={() => addTimelineMarker(previewTime)}
        onUndo={undo}
        onRedo={redo}
        canUndo={past.length > 0}
        canRedo={future.length > 0}
        hasSelection={!!selectedClipId}
        canLayerMove={canLayerMove}
        snapEnabled={snapEnabled}
        onToggleSnap={() => setSnapEnabled(!snapEnabled)}
        rippleMode={rippleMode}
        onToggleRipple={() => setRippleMode(!rippleMode)}
        onOpenAudioTab={() => useStore.getState().setActiveLeftTab('audio')}
        onOpenEffectsTab={() => useStore.getState().setActiveLeftTab('effects')}
        recording={recording}
        recordingSeconds={recordingSeconds}
        onToggleRecording={() => {
          if (recording) stopAudioRecording()
          else startAudioRecording().catch(() => undefined)
        }}
        onAddVideoTrack={() => useStore.getState().addExtraTrack('video')}
        onAddAudioTrack={() => useStore.getState().addExtraTrack('audio')}
      />

      {/* ── Track area ── */}
      <div className="flex flex-1 min-h-0 overflow-y-auto overflow-x-hidden">

        {/* ── Header column ── */}
        <div
          className="flex flex-col flex-shrink-0"
          data-no-deselect="true"
          style={{ width: HEADER_W, height: trackAreaHeight, background: BG_HEADER, borderRight: `1px solid ${BORDER_CLR}` }}
        >
          {/* ruler spacer */}
          <div style={{ height: RULER_H, borderBottom: `1px solid ${BORDER_CLR}` }} />
          {tracks.map((tr) => {
            return (
              <React.Fragment key={tr.id}>
                <TrackHeader
                  track={tr}
                  trackState={trackStates[tr.stateId] ?? { locked: false, hidden: false, muted: false, solo: false }}
                  onSetState={(patch) => setTrackState(tr.stateId, patch)}
                  onRename={(nextName) => useStore.getState().renameTrack({ kind: tr.category, index: tr.index }, nextName)}
                  onRemove={tr.index > 0
                    ? () => useStore.getState().removeExtraTrack(
                        tr.category === 'audio' ? 'audio' : 'video',
                        tr.index - 1,
                      )
                    : undefined}
                />
              </React.Fragment>
            )
          })}
        </div>

        {/* ── Scroll canvas ── */}
        <div className="relative flex-1 min-w-0 overflow-hidden" style={{ height: trackAreaHeight }}>

          {/* Scrollable content */}
          <div
            ref={scrollRef}
            className="absolute inset-0 overflow-x-auto overflow-y-hidden"
            onWheel={onWheel}
            style={{ scrollbarWidth: 'thin', scrollbarColor: `#2a2a3e ${BG_TRACK}` }}
          >
            <div
              style={{ width: totalW, position: 'relative', minWidth: '100%' }}
              onMouseDown={startMarqueeSelection}
              // Click in the timeline body that's NOT a clip → seek + clear selection.
              // Clips opt out via data-clip="true"; ruler still seeks via its own
              // onMouseDown.  Walks up DOM to detect clip clicks even when child
              // elements (label spans, resize handles) are the actual target.
              onClick={(e) => {
                if (marqueeJustEnded.current) {
                  e.preventDefault()
                  e.stopPropagation()
                  return
                }
                const el = e.target as HTMLElement
                let node: HTMLElement | null = el
                while (node && node !== e.currentTarget) {
                  if (node.getAttribute('data-clip') === 'true') return
                  if (node.tagName === 'INPUT' || node.tagName === 'TEXTAREA') return
                  node = node.parentElement
                }
                const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
                const x    = Math.max(0, e.clientX - rect.left)
                const t    = Math.max(0, Math.min(duration, x / pxPerSec))
                useStore.getState().clearSelection()
                useStore.getState().setPreviewTime(t)
              }}
            >

              {/* Ruler */}
              <Ruler
                duration={duration}
                pxPerSec={pxPerSec}
                onMouseDown={onRulerMouseDown}
              />
              <TimelineMarkerLayer
                markers={project?.timeline_markers ?? []}
                pxPerSec={pxPerSec}
                height={allTracksH + RULER_H}
                onRemove={removeTimelineMarker}
              />

              {/* Playhead line — design: purple with glow */}
              <div
                className="absolute z-20 pointer-events-none"
                style={{
                  left:   playheadX,
                  top:    RULER_H,
                  width:  1.5,
                  height: allTracksH,
                  background: '#8B6BFF',
                  boxShadow: '0 0 8px 1px rgba(139,107,255,0.55)',
                }}
              />

              {/* Clip-link connectors: thin bars at the video/audio boundary for paired clips */}
              {project && (
                <ClipLinkOverlay
                  videoClips={project.video_track.clips}
                  audioClips={project.audio_track.clips}
                  pxPerSec={pxPerSec}
                />
              )}

              {/* Track rows (dynamic — includes extra video/audio tracks).
                  We mirror the same spacer logic as the header column so the
                  "+ Faixa" buttons in the headers line up with empty space
                  here in the scroll canvas. */}
              {tracks.map((tr) => {
                const clips = project ? tr.getClips(project) : []
                return (
                  <React.Fragment key={tr.id}>
                    <TrackRow
                      track={tr}
                      clips={clips}
                      pxPerSec={pxPerSec}
                      selectedClipId={selectedClipId}
                      selectedClipIds={selectedClipIds}
                      onSelect={(id, t) => { setSelectedClip(id); setPreviewTime(t) }}
                      onToggleSelect={(id) => toggleClipSelection(id)}
                      updateClip={updateClip}
                      splitClip={splitClip}
                      deleteClip={deleteClip}
                      rippleDelete={rippleDelete}
                      closeGapAfterClip={closeGapAfterClip}
                      previewTime={previewTime}
                      waveform={project?.waveform ?? []}
                      totalDuration={project?.duration_s ?? 0}
                      hidden={trackStates[tr.stateId]?.hidden ?? false}
                      locked={trackStates[tr.stateId]?.locked ?? false}
                      onVideoFileDrop={tr.id === 'video' ? handleVideoFileDrop : (tr.category === 'overlay' ? handleOverlayVideoFileDrop : undefined)}
                      onAudioFileDrop={tr.category === 'audio' ? handleAudioFileDrop : undefined}
                      onImageFileDrop={tr.id === 'overlay' ? handleImageFileDrop : undefined}
                    />
                  </React.Fragment>
                )
              })}
              {marqueeRect && (
                <div
                  className="absolute z-40 pointer-events-none rounded-[2px]"
                  style={{
                    left: Math.min(marqueeRect.x1, marqueeRect.x2),
                    top: Math.min(marqueeRect.y1, marqueeRect.y2),
                    width: Math.abs(marqueeRect.x2 - marqueeRect.x1),
                    height: Math.abs(marqueeRect.y2 - marqueeRect.y1),
                    border: '1px solid rgba(139,107,255,0.95)',
                    background: 'rgba(139,107,255,0.16)',
                    boxShadow: '0 0 0 1px rgba(255,255,255,0.08) inset',
                  }}
                />
              )}
            </div>
          </div>

          {/* Playhead triangle on ruler (rendered outside scroll, so it's always visible) */}
          <PlayheadHead
            pxPerSec={pxPerSec}
            previewTime={previewTime}
            scrollRef={scrollRef}
          />
        </div>
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
// Playhead triangle (positioned above scroll content at ruler level)
// ═══════════════════════════════════════════════════════════════════════════════
function PlayheadHead({
  pxPerSec, previewTime, scrollRef,
}: {
  pxPerSec: number; previewTime: number; scrollRef: React.RefObject<HTMLDivElement>
}) {
  const [left, setLeft] = useState(0)

  // Recompute on every frame (lightweight since no RAF overhead here)
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const update = () => setLeft(previewTime * pxPerSec - el.scrollLeft)
    update()
    el.addEventListener('scroll', update, { passive: true })
    return () => el.removeEventListener('scroll', update)
  }, [previewTime, pxPerSec, scrollRef])

  return (
    <div
      className="absolute z-30 pointer-events-none"
      style={{ top: 0, left, width: 0, height: RULER_H }}
    >
      {/* Diamond marker — design spec */}
      <div
        style={{
          position: 'absolute',
          top: RULER_H - 10,
          left: -5,
          width: 10, height: 10,
          background: '#8B6BFF',
          transform: 'rotate(45deg)',
          boxShadow: '0 0 8px rgba(139,107,255,0.7)',
        }}
      />
      {/* Purple line in ruler */}
      <div
        style={{
          position: 'absolute',
          top: 0, left: 0,
          width: 1.5, height: RULER_H,
          background: '#8B6BFF',
          boxShadow: '0 0 6px rgba(139,107,255,0.5)',
        }}
      />
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
// Time ruler
// ═══════════════════════════════════════════════════════════════════════════════
function Ruler({
  duration, pxPerSec, onMouseDown,
}: {
  duration: number; pxPerSec: number
  onMouseDown: (e: React.MouseEvent<HTMLDivElement>) => void
}) {
  const [hoverTime, setHoverTime] = useState<number | null>(null)
  const [hoverX,    setHoverX]    = useState(0)
  // Choose label step so we get ~6-10 labels across a typical view
  const step = pxPerSec >= 200 ? 1
             : pxPerSec >= 80  ? 5
             : pxPerSec >= 30  ? 10
             : pxPerSec >= 10  ? 30
             : 60

  const marks: number[] = []
  for (let t = 0; t <= duration + step * 0.01; t = parseFloat((t + step).toFixed(4))) marks.push(t)

  return (
    <div
      className="relative select-none cursor-crosshair"
      style={{
        height:     RULER_H,
        background: BG_RULER,
        borderBottom: `1px solid ${BORDER_CLR}`,
      }}
      onMouseDown={onMouseDown}
      onMouseMove={(e) => {
        const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
        const x    = e.clientX - rect.left + (e.currentTarget.parentElement?.scrollLeft ?? 0)
        const t    = Math.max(0, Math.min(duration, x / pxPerSec))
        setHoverTime(t)
        setHoverX(e.clientX - rect.left)
      }}
      onMouseLeave={() => setHoverTime(null)}
    >
      {/* Hover time tooltip */}
      {hoverTime !== null && (
        <div
          style={{
            position: 'absolute',
            bottom: RULER_H + 2,
            left:   hoverX,
            transform: 'translateX(-50%)',
            background: '#1a1a2e',
            border: '1px solid #333350',
            borderRadius: 3,
            padding: '1px 5px',
            fontSize: 9,
            color: '#aaa',
            pointerEvents: 'none',
            whiteSpace: 'nowrap',
            zIndex: 100,
          }}
        >
          {fmtTime(hoverTime)}
        </div>
      )}
      {marks.map((t) => (
        <div
          key={t}
          className="absolute bottom-0 flex flex-col-reverse items-center"
          style={{ left: t * pxPerSec }}
        >
          <div style={{ width: 1, height: 10, background: '#333348' }} />
          <span
            style={{
              position: 'absolute',
              bottom: 12,
              fontSize: 9,
              color: '#4a4a62',
              whiteSpace: 'nowrap',
              transform: 'translateX(-50%)',
              userSelect: 'none',
            }}
          >
            {fmtTime(t)}
          </span>
        </div>
      ))}

      {/* Dense minor ticks */}
      {pxPerSec >= 20 && (() => {
        const minorStep = step >= 10 ? 1 : 0.5
        const minors: number[] = []
        for (let t = minorStep; t <= duration; t = parseFloat((t + minorStep).toFixed(4))) {
          if (t % step !== 0) minors.push(t)
        }
        return minors.slice(0, 500).map((t) => (
          <div
            key={`m${t}`}
            className="absolute bottom-0"
            style={{ left: t * pxPerSec, width: 1, height: 5, background: '#222234' }}
          />
        ))
      })()}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
// Track header — label + icon row (CapCut exact layout)
// ═══════════════════════════════════════════════════════════════════════════════
function TimelineMarkerLayer({
  markers, pxPerSec, height, onRemove,
}: {
  markers: NonNullable<ProjectState['timeline_markers']>
  pxPerSec: number
  height: number
  onRemove: (id: string) => void
}) {
  if (!markers.length) return null
  return (
    <div className="absolute left-0 top-0 z-[25] pointer-events-none" style={{ width: '100%', height }}>
      {markers.map((marker) => {
        const x = marker.time_s * pxPerSec
        return (
          <div
            key={marker.id}
            className="absolute top-0 pointer-events-auto"
            data-no-marquee="true"
            title={`${marker.label} - ${fmtTime(marker.time_s)}. Duplo clique para remover.`}
            onDoubleClick={(e) => {
              e.preventDefault()
              e.stopPropagation()
              onRemove(marker.id)
            }}
            style={{ left: x }}
          >
            <div
              className="absolute top-0 -translate-x-1/2 rounded-b px-1 py-0.5 text-[8px] font-semibold text-black"
              style={{ background: marker.color, minWidth: 18, textAlign: 'center' }}
            >
              {marker.label}
            </div>
            <div
              className="absolute top-0"
              style={{
                width: 1,
                height,
                background: marker.color,
                opacity: 0.72,
                boxShadow: `0 0 6px ${marker.color}77`,
              }}
            />
          </div>
        )
      })}
    </div>
  )
}

function TrackHeader({
  track,
  trackState,
  onSetState,
  onRename,
  onRemove,
}: {
  track: TrackDef
  trackState: TrackState
  onSetState: (patch: Partial<TrackState>) => void
  onRename: (name: string) => void
  /** When set, this is an EXTRA track and a delete button is shown */
  onRemove?: () => void
}) {
  const { locked, hidden, muted, solo } = trackState
  const [renaming, setRenaming] = useState(false)
  const [nameDraft, setNameDraft] = useState(track.label)

  useEffect(() => {
    if (!renaming) setNameDraft(track.label)
  }, [track.label, renaming])

  const commitRename = useCallback(() => {
    const clean = nameDraft.trim()
    if (clean && clean !== track.label) onRename(clean)
    setRenaming(false)
  }, [nameDraft, onRename, track.label])

  const cancelRename = useCallback(() => {
    setNameDraft(track.label)
    setRenaming(false)
  }, [track.label])
  // Category accent color — matches the clip color in the scroll area
  const accentColor = track.isAudio
    ? '#1e6fa0'
    : track.category === 'overlay' || track.category === 'text'
      ? '#8B6BFF'
      : TEAL

  return (
    <div
      className="flex flex-col flex-shrink-0"
      title={track.label}
      style={{
        height:       track.height,
        borderBottom: `1px solid ${BORDER_CLR}`,
        background:   track.isMain ? '#111120' : BG_HEADER,
        borderLeft:   `2px solid ${accentColor}44`,
      }}
    >
      {/* ── Top row: track label + optional remove button ── */}
      <div
        className="flex items-center flex-shrink-0"
        style={{ height: 20, paddingLeft: 6, paddingRight: 4, paddingTop: 3, gap: 4 }}
      >
        {renaming ? (
          <input
            autoFocus
            value={nameDraft}
            onChange={(e) => setNameDraft(e.target.value)}
            onBlur={commitRename}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitRename()
              if (e.key === 'Escape') cancelRename()
            }}
            style={{
              flex: 1,
              fontSize: 9,
              fontWeight: 700,
              color: '#c9c9e6',
              letterSpacing: '0.04em',
              background: '#161627',
              border: '1px solid #2a2a42',
              borderRadius: 3,
              padding: '1px 4px',
              outline: 'none',
            }}
          />
        ) : (
          <span
            onDoubleClick={() => setRenaming(true)}
            title="Duplo clique para renomear"
            style={{
              flex: 1,
              fontSize: 9,
              fontWeight: 700,
              color: track.isMain ? '#9999bb' : '#666680',
              textTransform: 'uppercase',
              letterSpacing: '0.06em',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              userSelect: 'none',
              cursor: 'text',
            }}
          >
            {track.label}
          </span>
        )}
        {onRemove && (
          <button
            onClick={onRemove}
            title="Remover faixa"
            style={{
              width: 13, height: 13, padding: 0, border: 'none',
              background: 'transparent', color: '#444460', cursor: 'pointer',
              fontSize: 13, lineHeight: 1, flexShrink: 0,
            }}
            onMouseEnter={(e) => (e.currentTarget.style.color = '#f03e3e')}
            onMouseLeave={(e) => (e.currentTarget.style.color = '#444460')}
          >×</button>
        )}
      </div>

      {/* ── Bottom row: control icons ── */}
      <div className="flex items-center flex-shrink-0 px-1 gap-0.5" style={{ flex: 1 }}>
        <HdrBtn
          icon={locked ? <Lock size={11} /> : <Unlock size={11} />}
          active={locked} activeColor="#f5c842"
          title={locked ? 'Desbloquear' : 'Bloquear'} onClick={() => onSetState({ locked: !locked })}
        />
        <HdrBtn
          icon={hidden ? <EyeOff size={11} /> : <Eye size={11} />}
          active={hidden} activeColor="#aaa"
          title={hidden ? 'Mostrar' : 'Ocultar'} onClick={() => onSetState({ hidden: !hidden })}
        />
        <HdrBtn
          icon={muted ? <VolumeX size={11} /> : <Volume2 size={11} />}
          active={muted} activeColor="#f03e3e"
          title={muted ? 'Ativar' : 'Silenciar'} onClick={() => onSetState({ muted: !muted })}
        />
        <HdrBtn
          icon={<Headphones size={11} />}
          active={solo} activeColor="#7c5cff"
          title={solo ? 'Desativar solo' : 'Solo'} onClick={() => onSetState({ solo: !solo })}
        />
      </div>
    </div>
  )
}

/** Row between track-category groups with a "+ Nova faixa" button.
 *  Lives in the HEADER column only; scroll canvas renders a plain matching spacer. */
export function AddTrackButton({
  category, onAdd,
}: {
  category: 'video' | 'audio'
  onAdd: () => void
}) {
  const accent = category === 'video' ? TEAL : '#1e6fa0'
  return (
    <div
      className="flex items-center px-2"
      style={{
        height: ADD_TRACK_ROW_H,
        borderBottom: `1px solid ${BORDER_CLR}`,
        background: '#0c0c18',
      }}
    >
      <button
        onClick={onAdd}
        title={category === 'video' ? 'Adicionar faixa de vídeo' : 'Adicionar faixa de áudio'}
        className="flex items-center justify-center gap-1 flex-1"
        style={{
          height: 20, padding: '0 6px',
          fontSize: 9, color: accent,
          background: `${accent}12`,
          border: `1px solid ${accent}44`,
          borderRadius: 3,
          cursor: 'pointer',
          letterSpacing: '0.04em',
          fontWeight: 700,
          transition: 'all 0.15s',
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = `${accent}28`
          e.currentTarget.style.borderColor = `${accent}88`
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = `${accent}12`
          e.currentTarget.style.borderColor = `${accent}44`
        }}
      >
        <Plus size={10} />
        <span>Nova faixa</span>
      </button>
    </div>
  )
}

function HdrBtn({
  icon, active, activeColor, title, onClick,
}: {
  icon: React.ReactNode; active: boolean; activeColor: string
  title: string; onClick: () => void
}) {
  return (
    <button
      title={title}
      onClick={onClick}
      className="flex items-center justify-center rounded flex-shrink-0 transition-all"
      style={{
        width: 20, height: 20,
        color:      active ? activeColor : '#3a3a52',
        background: active ? `${activeColor}22` : 'transparent',
        border: 'none', cursor: 'pointer', padding: 0,
      }}
      onMouseEnter={(e) => { if (!active) (e.currentTarget as HTMLElement).style.color = '#6a6a88' }}
      onMouseLeave={(e) => { if (!active) (e.currentTarget as HTMLElement).style.color = '#3a3a52' }}
    >
      {icon}
    </button>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
// Track row with clips
// ═══════════════════════════════════════════════════════════════════════════════
function TrackRow({
  track, clips, pxPerSec, selectedClipId, selectedClipIds, onSelect, onToggleSelect,
  updateClip, splitClip, deleteClip, rippleDelete, closeGapAfterClip, previewTime,
  waveform, totalDuration, hidden = false, locked = false,
  onVideoFileDrop, onAudioFileDrop, onImageFileDrop,
}: {
  track: TrackDef; clips: any[]; pxPerSec: number
  selectedClipId: string | null
  selectedClipIds: string[]
  onSelect: (id: string, t: number) => void
  onToggleSelect: (id: string) => void
  updateClip: (id: string, patch: Partial<Clip>) => void
  splitClip:     (id: string, at: number) => void
  deleteClip:    (id: string) => void
  rippleDelete:  (id: string) => void
  closeGapAfterClip: (id: string) => void
  previewTime: number
  waveform: number[]
  totalDuration: number
  hidden?: boolean
  locked?: boolean
  onVideoFileDrop?: (path: string) => void
  onAudioFileDrop?: (path: string) => void
  onImageFileDrop?: (path: string) => void
}) {
  // Track whether a drag moved far enough to suppress the click-to-select
  const didDrag = useRef(false)
  const didDragKeyframe = useRef(false)
  const [keyframeDragInfo, setKeyframeDragInfo] = useState<{
    clipId: string
    left: number
    t: number
  } | null>(null)
  const [selectedKeyframes, setSelectedKeyframes] = useState<string[]>([])

  // P0-2: file drop from native OS or internal MediaTab drag
  const [dropOver, setDropOver] = useState(false)
  const [resizeDraft, setResizeDraft] = useState<{
    clipId: string
    start_s: number
    end_s: number
    side: 'left' | 'right'
  } | null>(null)

  const handleFileDrop = useCallback((e: React.DragEvent) => {
    setDropOver(false)
    if (locked) return

    // Internal drag (clip from MediaTab list) — still handled locally,
    // because the data is in a custom MIME type the global handler ignores.
    const clipDataRaw = e.dataTransfer.getData('application/x-cortacerto-clip')
    if (clipDataRaw) {
      e.preventDefault()
      try {
        const info = JSON.parse(clipDataRaw) as { type: string; path: string }
        if (info.type === 'video' && onVideoFileDrop && info.path) onVideoFileDrop(info.path)
        if (info.type === 'audio' && onAudioFileDrop && info.path) onAudioFileDrop(info.path)
        if (info.type === 'image' && onImageFileDrop && info.path) onImageFileDrop(info.path)
      } catch { /* ignore bad JSON */ }
      return
    }

    // OS file drops are handled by the global App-level handler — fall through.
  }, [locked, onVideoFileDrop, onAudioFileDrop, onImageFileDrop])

  // Context menu state
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; clipId: string } | null>(null)

  // Inline rename state
  const [renaming, setRenaming] = useState<{ clipId: string; value: string } | null>(null)
  const renameInputRef = useRef<HTMLInputElement>(null)

  // Focus input when rename starts
  useEffect(() => {
    if (renaming) setTimeout(() => renameInputRef.current?.select(), 10)
  }, [renaming?.clipId])

  // Close context menu on outside click
  useEffect(() => {
    if (!ctxMenu) return
    const close = () => setCtxMenu(null)
    window.addEventListener('click',       close)
    window.addEventListener('contextmenu', close)
    return () => {
      window.removeEventListener('click',       close)
      window.removeEventListener('contextmenu', close)
    }
  }, [ctxMenu])

  // Transition markers between consecutive clips on this track.
  // Drawn only for non-audio tracks and only when clips touch at the boundary.
  const transitionMarkers = useMemo(() => {
    if (hidden || track.isAudio || clips.length < 2) return [] as Array<{
      id: string
      x: number
      width: number
      label: string
      color: string
      duration: number
    }>
    const sorted = [...clips]
      .filter((c) => Number.isFinite(c?.start_s) && Number.isFinite(c?.end_s))
      .sort((a, b) => (a.start_s as number) - (b.start_s as number))
    const markers: Array<{
      id: string
      x: number
      width: number
      label: string
      color: string
      duration: number
    }> = []
    for (let i = 0; i < sorted.length - 1; i++) {
      const left = sorted[i]
      const right = sorted[i + 1]
      const transition = normalizeTransitionName(left.transition)
      if (transition === 'Corte') continue
      // Show only if this is effectively a cut point (clips touch).
      const boundaryGap = Math.abs((right.start_s as number) - (left.end_s as number))
      if (boundaryGap > 0.12) continue
      const duration = Math.max(0.1, Math.min(1.5, Number(left.transition_duration_s ?? 0.4)))
      const width = Math.max(12, Math.min(38, duration * pxPerSec))
      markers.push({
        id: `${left.id}->${right.id}`,
        x: (left.end_s as number) * pxPerSec,
        width,
        label: transition,
        color: transitionBadgeColor(transition),
        duration,
      })
    }
    return markers
  }, [clips, hidden, pxPerSec, track.isAudio])

  // Snap a proposed clip-start time to nearby clip edges AND the playhead.
  // Snap is gated by the global `snapEnabled` toggle (magnet icon in toolbar).
  const SNAP_PX = 14   // forgiving snap radius for easier "fit into gap" behavior
  const snapStart = (proposed: number, clipId: string, dur: number): number => {
    if (!useStore.getState().snapEnabled) return proposed
    // Snap to playhead position
    if (Math.abs((proposed       - previewTime) * pxPerSec) < SNAP_PX) return previewTime
    if (Math.abs((proposed + dur - previewTime) * pxPerSec) < SNAP_PX) return previewTime - dur
    // Snap to other clip edges (this is what makes clips "fit into gaps":
    // the leading or trailing edge of the dragged clip aligns with the
    // surrounding clips, so a clip the size of the gap drops in perfectly)
    for (const other of clips) {
      if ((other as any).id === clipId) continue
      const os = (other as any).start_s as number
      const oe = (other as any).end_s   as number
      if (Math.abs((proposed          - os) * pxPerSec) < SNAP_PX) return os
      if (Math.abs((proposed          - oe) * pxPerSec) < SNAP_PX) return oe
      if (Math.abs((proposed + dur    - os) * pxPerSec) < SNAP_PX) return os - dur
      if (Math.abs((proposed + dur    - oe) * pxPerSec) < SNAP_PX) return oe - dur
    }
    return proposed
  }

  // Drag-reposition: move entire clip (both edges) while preserving duration
  const startDrag = (e: React.MouseEvent, clip: any) => {
    if (e.button !== 0) return
    if (locked) return          // track is locked — no drag
    e.preventDefault()
    didDrag.current = false
    const startX           = e.clientX
    const startY           = e.clientY
    const origStart        = clip.start_s as number
    const origEnd          = clip.end_s   as number
    const origSourceOffset = (clip.source_offset_s ?? 0) as number
    const dur              = origEnd - origStart
    const stateAtDrag = useStore.getState()
    const projectAtDrag = stateAtDrag.project
    const dragSelectionIds = stateAtDrag.selectedClipIds.includes(clip.id)
      ? stateAtDrag.selectedClipIds
      : [clip.id]
    const coveredIds = new Set<string>()
    const dragItems = projectAtDrag ? [
      ...projectAtDrag.video_track.clips,
      ...projectAtDrag.audio_track.clips,
      ...projectAtDrag.text_track.clips,
      ...projectAtDrag.overlay_track.clips,
      ...(projectAtDrag.extra_video_tracks ?? []).flatMap((t) => t.clips),
      ...(projectAtDrag.extra_audio_tracks ?? []).flatMap((t) => t.clips),
      ...(projectAtDrag.extra_overlay_tracks ?? []).flatMap((t) => t.clips),
    ].filter((item) => {
      if (!dragSelectionIds.includes(item.id)) return false
      if (coveredIds.has(item.id)) return false
      for (const linked of getLinkedClipIds(item.id, projectAtDrag)) coveredIds.add(linked)
      return true
    }).map((item) => ({
      id: item.id,
      start: item.start_s,
      end: item.end_s,
      sourceOffset: item.source_offset_s ?? 0,
    })) : []

    const onMove = (ev: MouseEvent) => {
      const dx = ev.clientX - startX
      const dy = ev.clientY - startY
      if (!didDrag.current && Math.abs(dx) < 4 && Math.abs(dy) < 4) return
      didDrag.current = true
      const dt       = dx / pxPerSec
      const raw      = Math.max(0, origStart + dt)
      const newStart = snapStart(raw, clip.id, dur)
      const actualDelta = newStart - origStart
      // CRITICAL: also shift source_offset_s by the same delta so the clip
      // continues to display the SAME source frames after being moved.
      //   project_time = source_time + source_offset_s
      // If start_s shifts by Δ but source_offset_s stays the same, the clip
      // ends up showing a different range of the source file (drifts out
      // of the trimmed region the user intended).
      if (dragItems.length > 1) {
        for (const item of dragItems) {
          updateClip(item.id, {
            start_s:         Math.round(Math.max(0, item.start + actualDelta) * 1000) / 1000,
            end_s:           Math.round(Math.max(0, item.end + actualDelta) * 1000) / 1000,
            source_offset_s: Math.round((item.sourceOffset + actualDelta) * 1000) / 1000,
          })
        }
      } else {
        updateClip(clip.id, {
          start_s:         Math.round(newStart * 1000) / 1000,
          end_s:           Math.round((newStart + dur) * 1000) / 1000,
          source_offset_s: Math.round((origSourceOffset + actualDelta) * 1000) / 1000,
        })
      }
    }
    const onUp = (ev: MouseEvent) => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup',   onUp)
      // 4.3 Cross-track drop: if mouse released over a DIFFERENT track of the
      // same kind (video↔video / audio↔audio), move the clip to that track.
      if (!didDrag.current) return
      if (track.category !== 'video' && track.category !== 'audio') return
      const elem = document.elementFromPoint(ev.clientX, ev.clientY)
      if (!elem) return
      let node: HTMLElement | null = elem as HTMLElement
      while (node && !node.getAttribute('data-track-id')) node = node.parentElement
      if (!node) return
      const droppedId = node.getAttribute('data-track-id')!
      if (droppedId === track.id) return   // same track — no move
      const droppedCategory = node.getAttribute('data-track-category')
      const droppedIndex = parseInt(node.getAttribute('data-track-index') ?? '-1', 10)
      if (droppedCategory === track.category && droppedIndex >= 0) {
        useStore.getState().moveClipToTrack(clip.id, {
          kind: track.category as 'video' | 'audio',
          index: droppedIndex,
        })
      }
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup',   onUp)
  }

  // Drag-resize a clip edge
  const startResize = (
    e: React.MouseEvent,
    side: 'left' | 'right',
    clip: any,
  ) => {
    e.stopPropagation()
    if (locked) return          // track is locked — no resize
    e.preventDefault()
    const startX   = e.clientX
    const origStart = clip.start_s as number
    const origEnd   = clip.end_s   as number
    let nextStart = origStart
    let nextEnd = origEnd

    const onMove = (ev: MouseEvent) => {
      const dt = (ev.clientX - startX) / pxPerSec
      if (side === 'left') {
        nextStart = Math.round(Math.max(0, Math.min(origStart + dt, origEnd - 0.1)) * 1000) / 1000
        nextEnd = origEnd
      } else {
        nextStart = origStart
        nextEnd = Math.round(Math.max(origStart + 0.1, origEnd + dt) * 1000) / 1000
      }
      setResizeDraft({ clipId: clip.id, start_s: nextStart, end_s: nextEnd, side })
    }
    const onUp = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup',   onUp)
      setResizeDraft(null)
      if (Math.abs(nextStart - origStart) > 0.001 || Math.abs(nextEnd - origEnd) > 0.001) {
        updateClip(clip.id, side === 'left'
          ? { start_s: nextStart }
          : { end_s: nextEnd })
        const current = useStore.getState().previewTime
        if (current < nextStart || current >= nextEnd) {
          useStore.getState().setPreviewTime(Math.max(nextStart, Math.min(nextEnd - 0.001, side === 'left' ? nextStart + 0.02 : nextEnd - 0.02)))
        }
      }
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup',   onUp)
  }

  const startKeyframeDrag = (
    e: React.MouseEvent,
    clip: any,
    keyframeIndex: number,
  ) => {
    e.stopPropagation()
    if (locked) return
    e.preventDefault()
    didDragKeyframe.current = false
    const startX = e.clientX
    const startT = (clip.motion_keyframes?.[keyframeIndex]?.t ?? 0) as number
    const clipDur = Math.max(0.01, (clip.end_s as number) - (clip.start_s as number))
    const SNAP_KF_PX = 10
    const FRAME_STEP = 1 / 30
    const kfKey = (ix: number) => `${clip.id}:${ix}`
    const selectedIdxs = selectedKeyframes
      .filter((k) => k.startsWith(`${clip.id}:`))
      .map((k) => parseInt(k.split(':')[1] ?? '-1', 10))
      .filter((ix) => ix >= 0 && ix < (clip.motion_keyframes?.length ?? 0))
    const dragIdxs = selectedIdxs.includes(keyframeIndex) && selectedIdxs.length > 1
      ? selectedIdxs
      : [keyframeIndex]
    const dragItems = dragIdxs.map((ix) => ({
      ix,
      start: (clip.motion_keyframes?.[ix]?.t ?? 0) as number,
    }))
    setSelectedKeyframes(dragIdxs.map(kfKey))

    const onMove = (ev: MouseEvent) => {
      const dx = ev.clientX - startX
      if (!didDragKeyframe.current && Math.abs(dx) < 3) return
      didDragKeyframe.current = true
      const dt = dx / pxPerSec
      let nextT = Math.max(0, Math.min(clipDur, startT + dt))
      // Hold Shift for frame-stepped precision while dragging.
      if (ev.shiftKey) nextT = Math.round(nextT / FRAME_STEP) * FRAME_STEP
      const playheadLocal = Math.max(0, Math.min(clipDur, previewTime - (clip.start_s as number)))
      if (Math.abs((nextT - playheadLocal) * pxPerSec) <= SNAP_KF_PX) nextT = playheadLocal
      nextT = Math.round(nextT * 100) / 100
      const delta = nextT - startT
      setKeyframeDragInfo({
        clipId: clip.id,
        left: (clip.start_s as number) * pxPerSec + nextT * pxPerSec,
        t: nextT,
      })

      const next = Array.isArray(clip.motion_keyframes) ? [...clip.motion_keyframes] : []
      for (const item of dragItems) {
        if (item.ix < 0 || item.ix >= next.length) continue
        let target = Math.max(0, Math.min(clipDur, item.start + delta))
        if (ev.shiftKey) target = Math.round(target / FRAME_STEP) * FRAME_STEP
        next[item.ix] = { ...next[item.ix], t: Math.round(target * 100) / 100 }
      }
      updateClip(clip.id, { motion_keyframes: next })
    }
    const onUp = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      setKeyframeDragInfo(null)
      const latest = useStore.getState().project
      if (!latest) return
      const all = [
        ...latest.video_track.clips,
        ...latest.audio_track.clips,
        ...latest.text_track.clips,
        ...latest.overlay_track.clips,
        ...(latest.extra_video_tracks ?? []).flatMap((t) => t.clips),
        ...(latest.extra_audio_tracks ?? []).flatMap((t) => t.clips),
        ...(latest.extra_overlay_tracks ?? []).flatMap((t) => t.clips),
      ]
      const after = all.find((c) => c.id === clip.id)
      if (!after?.motion_keyframes) return
      // click following mouseup should not trigger "jump to keyframe"
      if (didDragKeyframe.current) {
        setTimeout(() => { didDragKeyframe.current = false }, 0)
      }
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  return (
    <div
      className="relative"
      // Phase 4.3: data-track-* attributes let startDrag's mouseup handler
      // detect cross-track drops by querying the element under the cursor.
      data-track-id={track.id}
      data-track-category={track.category}
      data-track-index={track.index}
      style={{
        height:       track.height,
        borderBottom: `1px solid ${BORDER_CLR}`,
        background:   dropOver
          ? `${track.isAudio ? NAVY_WAVE : TEAL}22`
          : (track.isMain ? '#121222' : BG_TRACK),
        outline:      dropOver ? `1px dashed ${track.isAudio ? NAVY_WAVE : TEAL}` : undefined,
        opacity:      hidden ? 0.3 : 1,
        pointerEvents: hidden ? 'none' : undefined,
        transition:   'background 0.1s',
      }}
      onDragOver={(e) => {
        const hasInternalDrag = e.dataTransfer.types.includes('application/x-cortacerto-clip')
        if (hasInternalDrag || onVideoFileDrop || onAudioFileDrop || onImageFileDrop) {
          e.preventDefault()
          setDropOver(true)
        }
      }}
      onDragLeave={() => setDropOver(false)}
      onDrop={handleFileDrop}
    >
      {/* Lock overlay — dim and show lock icon when track is locked */}
      {locked && (
        <div
          style={{
            position: 'absolute', inset: 0, zIndex: 25,
            background: 'rgba(0,0,0,0.18)',
            display: 'flex', alignItems: 'center', justifyContent: 'flex-end',
            paddingRight: 8, pointerEvents: 'none',
          }}
        >
          <Lock size={12} color="#f5c842" opacity={0.5} />
        </div>
      )}

      {/* Empty guide line */}
      {!hidden && clips.length === 0 && (
        <div
          style={{
            position: 'absolute', top: '50%', left: 0, right: 0, height: 1,
            background: `repeating-linear-gradient(
              90deg, #1e1e30 0px, #1e1e30 6px, transparent 6px, transparent 14px
            )`,
          }}
        />
      )}

      {/* Only render clips that overlap the visible scroll viewport (virtualization) */}
      {!hidden && clips.filter((clip) => {
        const x = clip.start_s * pxPerSec
        const w = Math.max(4, (clip.end_s - clip.start_s) * pxPerSec)
        // Always render selected clip so resize handles work even if offscreen
        if (selectedClipId === clip.id) return true
        return x + w > -200 && x < 20000  // broad pass; parent scroll handles the rest
      }).map((clip) => {
        const draftForClip = resizeDraft?.clipId === clip.id ? resizeDraft : null
        const renderClip = draftForClip
          ? { ...clip, start_s: draftForClip.start_s, end_s: draftForClip.end_s }
          : clip
        const x         = renderClip.start_s * pxPerSec
        const w         = Math.max(4, (renderClip.end_s - renderClip.start_s) * pxPerSec)
        const isSel     = selectedClipId === clip.id
        const isMultiSel = selectedClipIds.includes(clip.id)
        const inset     = track.isMain ? 3 : 2
        const dur       = renderClip.end_s - renderClip.start_s

        return (
          <div
            key={clip.id}
            data-clip="true"
            onMouseDown={(e) => startDrag(e, clip)}
            onClick={(e) => {
              if (didDrag.current) { didDrag.current = false; return }
              // Ctrl+Click → multi-select (no seek)
              if (e.ctrlKey || e.metaKey) { onToggleSelect(clip.id); return }
              // Regular click → select + seek to click position within clip
              const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
              const clickOffset = Math.max(0, e.clientX - rect.left)
              const clickTime   = clip.start_s + clickOffset / pxPerSec
              const seekTime    = Math.max(clip.start_s, Math.min(clip.end_s - 0.001, clickTime))
              // Linked-clip selection: also select the paired audio/video clip
              const proj = useStore.getState().project
              const linkedIds = getLinkedClipIds(clip.id, proj)
              if (linkedIds.length > 1) {
                useStore.setState({ selectedClipId: clip.id, selectedClipIds: linkedIds })
                useStore.getState().setPreviewTime(seekTime)
              } else {
                onSelect(clip.id, seekTime)
              }
            }}
            onDoubleClick={(e) => {
              e.stopPropagation()
              if (locked) return
              setRenaming({ clipId: clip.id, value: clip.label })
            }}
            onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); setCtxMenu({ x: e.clientX, y: e.clientY, clipId: clip.id }) }}
            className={`absolute group overflow-hidden ${locked ? 'cursor-not-allowed' : 'cursor-grab active:cursor-grabbing'}`}
            style={{
              left:         x,
              width:        w,
              top:          inset,
              bottom:       inset,
              borderRadius: 2,
              background:    clip.clip_type === 'adjustment' ? '#d97706'    // amber for adjustment layers
                           : track.isAudio ? NAVY : TEAL,
              outline:       isSel        ? '2px solid #fff'
                           : isMultiSel  ? '2px solid #8B6BFF'
                           : clip.clip_type === 'adjustment'
                             ? '1px solid #f59e0b'
                             : `1px solid ${track.isAudio ? '#1a3a5a' : TEAL_DARK}`,
              outlineOffset: (isSel || isMultiSel) ? 1 : 0,
              zIndex:        isSel ? 10 : isMultiSel ? 8 : 1,
            }}
            title={clip.label}
          >
            {/* Top accent strip */}
            <div
              style={{
                position: 'absolute', top: 0, left: 0, right: 0, height: 2,
                background: clip.clip_type === 'adjustment' ? '#fbbf24'
                          : track.isAudio ? '#1e4a70' : '#22d4bc',
                borderRadius: '2px 2px 0 0',
              }}
            />

            {/* Clip keyframes (diamond markers) */}
            {Array.isArray(clip.motion_keyframes) && clip.motion_keyframes.length > 0 && w > 28 && (
              <div style={{ position: 'absolute', left: 0, right: 0, top: 6, height: 10, zIndex: 18, pointerEvents: 'none' }}>
                {clip.motion_keyframes
                  .filter((kf: { t: number }) => typeof kf.t === 'number' && kf.t >= 0 && kf.t <= dur + 0.001)
                  .map((kf: { t: number }, ix: number) => {
                    const left = Math.max(6, Math.min(w - 6, kf.t * pxPerSec))
                    const isNearPlayhead = Math.abs((clip.start_s + kf.t) - previewTime) <= 0.04
                    const keyId = `${clip.id}:${ix}`
                    const isSelectedKeyframe = selectedKeyframes.includes(keyId)
                    return (
                      <button
                        key={`${clip.id}-kf-${ix}-${kf.t}`}
                        onMouseDown={(e) => startKeyframeDrag(e, clip, ix)}
                        onClick={(e) => {
                          if (didDragKeyframe.current) {
                            e.stopPropagation()
                            return
                          }
                          if (e.ctrlKey || e.metaKey) {
                            e.stopPropagation()
                            setSelectedKeyframes((prev) => (
                              prev.includes(keyId)
                                ? prev.filter((k) => k !== keyId)
                                : [...prev, keyId]
                            ))
                            return
                          }
                          e.stopPropagation()
                          setSelectedKeyframes([keyId])
                          useStore.getState().setSelectedClip(clip.id)
                          useStore.getState().setPreviewTime(clip.start_s + kf.t)
                        }}
                        title={`Keyframe ${kf.t.toFixed(2)}s — arraste para mover (Shift = passo de frame)`}
                        style={{
                          position: 'absolute',
                          left: left - 4,
                          top: 0,
                          width: 8,
                          height: 8,
                          transform: 'rotate(45deg)',
                          borderRadius: 1,
                          border: `1px solid ${isSelectedKeyframe ? '#ffffff' : isNearPlayhead ? '#ffffff' : '#a2a2c8'}`,
                          background: isSelectedKeyframe ? '#22d4bc' : isNearPlayhead ? '#8B6BFF' : 'rgba(15,15,35,0.9)',
                          pointerEvents: 'auto',
                          cursor: 'pointer',
                          padding: 0,
                        }}
                      />
                    )
                  })}
              </div>
            )}

            {/* Video: real thumbnail frames (not for image or adjustment clips) */}
            {!track.isAudio && clip.clip_type !== 'image' && clip.clip_type !== 'adjustment' && w > 20 && (
              <ThumbStrip
                width={w}
                isMain={track.isMain}
                sourcePath={clip.source_path}
                clipStart={renderClip.start_s}
                sourceOffsetS={clip.source_offset_s ?? 0}
                pxPerSec={pxPerSec}
              />
            )}
            {/* Image clips: show a small image preview */}
            {clip.clip_type === 'image' && w > 20 && clip.source_path && (
              <div className="absolute inset-0 overflow-hidden" style={{ top: 2, borderRadius: '0 0 2px 2px' }}>
                <img
                  src={`${API}/api/serve-file?path=${encodeURIComponent(clip.source_path)}`}
                  className="w-full h-full object-cover opacity-70"
                  draggable={false}
                  alt=""
                />
              </div>
            )}

            {/* Audio: real waveform (uses per-clip waveform for imported music, global for project audio) */}
            {track.isAudio && w > 10 && (
              <WaveformBars
                width={w}
                height={track.height - inset * 2}
                waveform={clip.source_waveform ? [] : (clip.clip_type === 'music' ? [] : waveform)}
                clipStart={renderClip.start_s}
                clipEnd={renderClip.end_s}
                totalDuration={totalDuration}
                sourceWaveform={clip.source_waveform}
                volumePct={clip.volume_pct ?? 100}
              />
            )}

            {/* Clip label — double-click to rename */}
            {w > 30 && (
              renaming?.clipId === clip.id ? (
                <input
                  ref={renameInputRef}
                  value={renaming?.value ?? ''}
                  onChange={(e) => setRenaming({ clipId: clip.id, value: e.target.value })}
                  onBlur={() => {
                    if (renaming) updateClip(clip.id, { label: renaming.value || clip.label })
                    setRenaming(null)
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') { e.currentTarget.blur() }
                    if (e.key === 'Escape') { setRenaming(null) }
                    e.stopPropagation()
                  }}
                  onClick={(e) => e.stopPropagation()}
                  onMouseDown={(e) => e.stopPropagation()}
                  style={{
                    position: 'absolute',
                    top: 2, left: 4, right: 4,
                    fontSize: 10, height: 18,
                    background: 'rgba(0,0,0,0.7)',
                    color: '#fff',
                    border: '1px solid #8B6BFF',
                    borderRadius: 2,
                    padding: '0 3px',
                    outline: 'none',
                    zIndex: 30,
                  }}
                />
              ) : (
                <div
                  style={{
                    position: 'absolute',
                    top: 4, left: 5, right: 4,
                    fontSize: 10,
                    color: track.isAudio ? '#7fbfdf' : '#fff',
                    fontWeight: 500,
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    lineHeight: 1,
                    textShadow: '0 1px 2px rgba(0,0,0,0.6)',
                    zIndex: 5,
                  }}
                >
                  {clip.label}
                  {w > 80 && (
                    <span style={{ marginLeft: 6, opacity: 0.7, fontSize: 9 }}>
                      {fmtTime(dur)}
                    </span>
                  )}
                  {clip.compound_id && w > 96 && (
                    <span style={{
                      marginLeft: 6,
                      padding: '1px 4px',
                      borderRadius: 2,
                      background: 'rgba(139,107,255,0.72)',
                      color: '#fff',
                      fontSize: 8,
                      fontWeight: 700,
                    }}>
                      grupo
                    </span>
                  )}
                  {w > 96 && ((clip.animation_in && clip.animation_in !== 'none') || (clip.animation_out && clip.animation_out !== 'none')) && (
                    <span style={{
                      marginLeft: 6,
                      padding: '1px 4px',
                      borderRadius: 2,
                      background: 'rgba(34,212,188,0.7)',
                      color: '#031b1a',
                      fontSize: 8,
                      fontWeight: 700,
                    }}>
                      anim
                    </span>
                  )}
                </div>
              )
            )}

            {/* Resize handles — functional drag-to-trim */}
            {/* Resize handles — wider hit zone (12px) + a thin visible bar (3px white) */}
            <div
              className="absolute left-0 top-0 bottom-0 opacity-0 group-hover:opacity-100 transition-opacity"
              style={{ width: 12, cursor: 'w-resize', background: 'transparent', zIndex: 20 }}
              onMouseDown={(e) => startResize(e, 'left', clip)}
              onClick={(e) => e.stopPropagation()}
              title="Arrastar para aparar início"
            >
                  <div style={{
                    position: 'absolute', left: 0, top: 4, bottom: 4, width: 3,
                    background: '#fff', borderRadius: '0 2px 2px 0',
                    boxShadow: '0 0 4px rgba(255,255,255,0.5)',
                  }} />
                  {draftForClip?.side === 'left' && (
                    <span style={{ position: 'absolute', top: 4, left: 5, fontSize: 9, color: '#fff', background: 'rgba(0,0,0,0.65)', padding: '1px 3px', borderRadius: 2 }}>
                      {fmtTime(renderClip.start_s)}
                    </span>
                  )}
            </div>
            <div
              className="absolute right-0 top-0 bottom-0 opacity-0 group-hover:opacity-100 transition-opacity"
              style={{ width: 12, cursor: 'e-resize', background: 'transparent', zIndex: 20 }}
              onMouseDown={(e) => startResize(e, 'right', clip)}
              onClick={(e) => e.stopPropagation()}
              title="Arrastar para aparar fim"
            >
                  <div style={{
                    position: 'absolute', right: 0, top: 4, bottom: 4, width: 3,
                    background: '#fff', borderRadius: '2px 0 0 2px',
                    boxShadow: '0 0 4px rgba(255,255,255,0.5)',
                  }} />
                  {draftForClip?.side === 'right' && (
                    <span style={{ position: 'absolute', top: 4, right: 5, fontSize: 9, color: '#fff', background: 'rgba(0,0,0,0.65)', padding: '1px 3px', borderRadius: 2 }}>
                      {fmtTime(renderClip.end_s)}
                    </span>
                  )}
            </div>
          </div>
        )
      })} {/* end virtualized clip map */}

      {!hidden && transitionMarkers.map((marker) => (
        <div
          key={marker.id}
          title={`${marker.label} · ${marker.duration.toFixed(2)}s`}
          style={{
            position: 'absolute',
            left: marker.x - marker.width / 2,
            width: marker.width,
            top: 5,
            bottom: 5,
            zIndex: 22,
            pointerEvents: 'none',
            border: `1px solid ${marker.color}`,
            background: `${marker.color}22`,
            borderRadius: 3,
            boxShadow: `0 0 6px ${marker.color}55`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <div
            style={{
              width: 7,
              height: 7,
              transform: 'rotate(45deg)',
              border: `1px solid ${marker.color}`,
              background: '#0d1020',
              boxShadow: `0 0 4px ${marker.color}77`,
            }}
          />
          {marker.width >= 24 && (
            <span
              style={{
                position: 'absolute',
                bottom: -14,
                left: '50%',
                transform: 'translateX(-50%)',
                fontSize: 9,
                color: marker.color,
                whiteSpace: 'nowrap',
                textShadow: '0 1px 2px rgba(0,0,0,0.65)',
              }}
            >
              {marker.label}
            </span>
          )}
        </div>
      ))}

      {keyframeDragInfo && (
        <div
          style={{
            position: 'absolute',
            left: keyframeDragInfo.left,
            top: 2,
            transform: 'translate(-50%, -115%)',
            pointerEvents: 'none',
            zIndex: 45,
            background: 'rgba(8,8,20,0.96)',
            border: '1px solid #2a2a44',
            borderRadius: 4,
            padding: '1px 5px',
            fontSize: 10,
            lineHeight: 1.2,
            color: '#d8d8ef',
            whiteSpace: 'nowrap',
          }}
        >
          {keyframeDragInfo.t.toFixed(2)}s
        </div>
      )}

      {/* Context menu (right-click on clip) */}
      {ctxMenu && (() => {
        const ctxClip = clips.find((c: any) => c.id === ctxMenu.clipId) as any
        const ctxDur = ctxClip ? Math.max(0.01, (ctxClip.end_s as number) - (ctxClip.start_s as number)) : 0.01
        const ctxLocalPlayhead = ctxClip
          ? Math.max(0, Math.min(ctxDur, previewTime - (ctxClip.start_s as number)))
          : 0
        const ctxKfs = (ctxClip?.motion_keyframes ?? []) as Array<{
          t: number
          easing?: 'linear' | 'ease-in' | 'ease-out' | 'ease-in-out'
          position_x?: number
          position_y?: number
          scale_pct?: number
          opacity_pct?: number
          volume_pct?: number
        }>
        const ctxKfAtPlayheadIx = ctxKfs.findIndex((kf) => Math.abs(kf.t - ctxLocalPlayhead) <= 0.04)
        const ctxNearestKfIx = ctxKfs.length === 0
          ? -1
          : ctxKfs.reduce((best, kf, ix) => (
              best < 0 || Math.abs(kf.t - ctxLocalPlayhead) < Math.abs(ctxKfs[best].t - ctxLocalPlayhead) ? ix : best
            ), -1 as number)
        const ctxCanLayerMove = !!ctxClip && (
          ctxClip.clip_type === 'image'
          || ctxClip.clip_type === 'video_overlay'
          || ctxClip.clip_type === 'video'
          || ctxClip.clip_type === 'sticker'
          || ctxClip.clip_type === 'text'
        )

        return (
          <div
            className="fixed z-50 bg-bg-panel border border-border rounded-lg shadow-2xl py-1 min-w-[160px]"
            style={{ left: ctxMenu.x, top: ctxMenu.y }}
            onClick={(e) => e.stopPropagation()}
          >
            <button
              className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-text-muted hover:text-white hover:bg-bg-surface transition-colors"
              onClick={() => {
                if (ctxClip) setRenaming({ clipId: ctxMenu.clipId, value: ctxClip.label ?? '' })
                setCtxMenu(null)
              }}
            >
              <Pencil size={11} /> Renomear
            </button>
            <button
              className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-text-muted hover:text-white hover:bg-bg-surface transition-colors"
              onClick={() => {
                useStore.getState().copyClip(ctxMenu.clipId)
                setCtxMenu(null)
              }}
            >
              <Copy size={11} /> Copiar (Ctrl+C)
            </button>
            <button
              className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-text-muted hover:text-white hover:bg-bg-surface transition-colors"
              onClick={() => {
                useStore.getState().setSelectedClip(ctxMenu.clipId)
                useStore.getState().duplicateSelectionAtPlayhead()
                setCtxMenu(null)
              }}
            >
              <Copy size={11} /> Duplicar no playhead (Ctrl+D)
            </button>
            <button
              className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-text-muted hover:text-white hover:bg-bg-surface transition-colors"
              onClick={() => {
                splitClip(ctxMenu.clipId, previewTime)
                setCtxMenu(null)
              }}
            >
              <Scissors size={11} /> Dividir no playhead
            </button>
            <button
              className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-text-muted hover:text-white hover:bg-bg-surface transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              disabled={!ctxCanLayerMove}
              onClick={() => {
                if (!ctxClip || !ctxCanLayerMove) { setCtxMenu(null); return }
                const next = Math.max(-200, Math.min(200, Number(ctxClip.z_order ?? 0) + 1))
                updateClip(ctxMenu.clipId, { z_order: next })
                setCtxMenu(null)
              }}
            >
              <ArrowUp size={11} /> Trazer camada para frente
            </button>
            <button
              className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-text-muted hover:text-white hover:bg-bg-surface transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              disabled={!ctxCanLayerMove}
              onClick={() => {
                if (!ctxClip || !ctxCanLayerMove) { setCtxMenu(null); return }
                const next = Math.max(-200, Math.min(200, Number(ctxClip.z_order ?? 0) - 1))
                updateClip(ctxMenu.clipId, { z_order: next })
                setCtxMenu(null)
              }}
            >
              <ArrowDown size={11} /> Enviar camada para trás
            </button>
            <div className="border-t border-border my-1" />
            <button
              className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-text-muted hover:text-white hover:bg-bg-surface transition-colors"
              onClick={() => {
                if (!ctxClip) { setCtxMenu(null); return }
                const t = Math.round(ctxLocalPlayhead * 100) / 100
                const next = [...ctxKfs]
                const ix = next.findIndex((kf) => Math.abs(kf.t - t) <= 0.04)
                const frame = {
                  t,
                  easing: 'linear' as const,
                  position_x: ctxClip.position_x ?? 0,
                  position_y: ctxClip.position_y ?? 0,
                  scale_pct: ctxClip.scale_pct ?? 100,
                  opacity_pct: ctxClip.opacity_pct ?? 100,
                  volume_pct: ctxClip.volume_pct ?? 100,
                }
                if (ix >= 0) next[ix] = { ...next[ix], ...frame }
                else next.push(frame)
                next.sort((a, b) => a.t - b.t)
                updateClip(ctxMenu.clipId, { motion_keyframes: next })
                setCtxMenu(null)
              }}
            >
              <Plus size={11} /> Keyframe no playhead
            </button>
            <button
              className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-text-muted hover:text-white hover:bg-bg-surface transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              disabled={ctxNearestKfIx < 0}
              onClick={() => {
                if (!ctxClip || ctxNearestKfIx < 0) { setCtxMenu(null); return }
                const t = Math.round(ctxLocalPlayhead * 100) / 100
                const src = ctxKfs[ctxNearestKfIx]
                const next = [...ctxKfs]
                const ix = next.findIndex((kf) => Math.abs(kf.t - t) <= 0.04)
                const duplicated = { ...src, t }
                if (ix >= 0) next[ix] = duplicated
                else next.push(duplicated)
                next.sort((a, b) => a.t - b.t)
                updateClip(ctxMenu.clipId, { motion_keyframes: next })
                setCtxMenu(null)
              }}
            >
              <Copy size={11} /> Duplicar keyframe no playhead
            </button>
            <button
              className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-text-muted hover:text-white hover:bg-bg-surface transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              disabled={ctxNearestKfIx < 0}
              onClick={() => {
                if (!ctxClip || ctxNearestKfIx < 0) { setCtxMenu(null); return }
                const t = Math.round(ctxLocalPlayhead * 100) / 100
                const next = [...ctxKfs]
                next[ctxNearestKfIx] = { ...next[ctxNearestKfIx], t }
                next.sort((a, b) => a.t - b.t)
                updateClip(ctxMenu.clipId, { motion_keyframes: next })
                setCtxMenu(null)
              }}
            >
              <Pencil size={11} /> Mover keyframe p/ playhead
            </button>
            <button
              className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-text-muted hover:text-white hover:bg-bg-surface transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              disabled={ctxKfAtPlayheadIx < 0}
              onClick={() => {
                if (!ctxClip || ctxKfAtPlayheadIx < 0) { setCtxMenu(null); return }
                const next = ctxKfs.filter((_, ix) => ix !== ctxKfAtPlayheadIx)
                updateClip(ctxMenu.clipId, { motion_keyframes: next })
                setCtxMenu(null)
              }}
            >
              <Trash2 size={11} /> Remover keyframe no playhead
            </button>
            <button
              className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-text-muted hover:text-white hover:bg-bg-surface transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              disabled={ctxKfs.length === 0}
              onClick={() => {
                if (!ctxClip || ctxKfs.length === 0) { setCtxMenu(null); return }
                updateClip(ctxMenu.clipId, { motion_keyframes: [] })
                setCtxMenu(null)
              }}
            >
              <Trash2 size={11} /> Limpar keyframes
            </button>

            {/* Linked-clip toggle: only show if a pair exists */}
            {(() => {
              const proj = useStore.getState().project
              if (!proj || !ctxClip) return null
              const linked = getLinkedClipIds(ctxMenu.clipId, proj)
              if (linked.length <= 1) return null
              const isCurrentlyUnlinked = ctxClip.unlinked === true
              return (
                <button
                  className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-text-muted hover:text-white hover:bg-bg-surface transition-colors"
                  onClick={() => {
                    // Toggle unlinked flag on all paired clips so the link is broken/restored together
                    linked.forEach((id) => {
                      useStore.getState().updateClip(id, { unlinked: !isCurrentlyUnlinked })
                    })
                    setCtxMenu(null)
                  }}
                >
                  <Music size={11} /> {isCurrentlyUnlinked ? 'Vincular áudio' : 'Desvincular áudio'}
                </button>
              )
            })()}

            <div className="border-t border-border my-1" />
            <button
              className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-text-muted hover:text-white hover:bg-bg-surface transition-colors"
              onClick={() => {
                closeGapAfterClip(ctxMenu.clipId)
                setCtxMenu(null)
              }}
            >
              <ChevronsLeft size={11} /> Fechar gap a direita
            </button>
            <button
              className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-red-400 hover:text-red-300 hover:bg-bg-surface transition-colors"
              onClick={() => {
                deleteClip(ctxMenu.clipId)
                setCtxMenu(null)
              }}
            >
              <Trash2 size={11} /> Deletar clipe
            </button>
            <button
              className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-red-400 hover:text-red-300 hover:bg-bg-surface transition-colors"
              onClick={() => {
                rippleDelete(ctxMenu.clipId)
                setCtxMenu(null)
              }}
            >
              <Trash2 size={11} /> Deletar e fechar gap
            </button>
          </div>
        )
      })()}
    </div>
  )
}

// ── Thumbnail strip — real frames via /api/thumb ─────────────────────────────
const API = 'http://127.0.0.1:7472'

function ThumbStrip({
  width, isMain, sourcePath, clipStart, sourceOffsetS = 0, pxPerSec,
}: {
  width: number; isMain: boolean; sourcePath?: string
  clipStart: number; sourceOffsetS?: number; pxPerSec: number
}) {
  const frameW  = isMain ? 64 : 44
  const count   = Math.max(1, Math.ceil(width / frameW))

  // Generate source-file times evenly spread across the clip.
  // sourceOffsetS = how much this source file is shifted into the project timeline.
  // source_time = project_time - sourceOffsetS
  const frames  = Array.from({ length: count }, (_, i) => {
    const projT = clipStart + (i / count) * (width / pxPerSec)
    return Math.max(0, projT - sourceOffsetS)
  })

  if (!sourcePath) {
    // Fallback: gradient pattern
    return (
      <div className="absolute inset-0 overflow-hidden" style={{ top: 2, borderRadius: '0 0 2px 2px' }}>
        <div className="flex h-full" style={{ gap: 1 }}>
          {frames.map((_, i) => (
            <div key={i} className="flex-shrink-0 h-full" style={{
              width: frameW - 1,
              background: `linear-gradient(135deg, ${TEAL}44 0%, ${TEAL}22 50%, ${TEAL}44 100%)`,
              borderRight: `1px solid ${TEAL_DARK}55`,
            }} />
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="absolute inset-0 overflow-hidden" style={{ top: 2, borderRadius: '0 0 2px 2px' }}>
      <div className="flex h-full" style={{ gap: 0 }}>
        {frames.map((t, i) => (
          <ThumbFrame
            key={t.toFixed(3)}
            src={`${API}/api/thumb?path=${encodeURIComponent(sourcePath)}&t=${t.toFixed(2)}&w=${frameW * 2}`}
            width={Math.min(frameW, width - i * frameW)}
          />
        ))}
      </div>
    </div>
  )
}

function ThumbFrame({ src, width }: { src: string; width: number }) {
  const [loaded,    setLoaded]    = useState(false)
  const [retrySrc,  setRetrySrc]  = useState(src)
  const [retries,   setRetries]   = useState(0)
  const MAX_RETRIES = 3

  // O9: on error, retry up to MAX_RETRIES times with exponential back-off + cache-buster
  const handleError = () => {
    if (retries < MAX_RETRIES) {
      const delay = 500 * Math.pow(2, retries)  // 500ms, 1s, 2s
      setTimeout(() => {
        setLoaded(false)
        setRetrySrc(`${src}${src.includes('?') ? '&' : '?'}_r=${retries + 1}`)
        setRetries((r) => r + 1)
      }, delay)
    }
    // After MAX_RETRIES, leave as placeholder (retries stays at MAX_RETRIES)
  }

  // Reset when src changes (different timestamp / clip)
  useEffect(() => {
    setLoaded(false)
    setRetrySrc(src)
    setRetries(0)
  }, [src])

  const failed = retries >= MAX_RETRIES && !loaded

  return (
    <div className="flex-shrink-0 h-full overflow-hidden relative" style={{ width }}>
      {!failed && (
        <img
          src={retrySrc}
          loading="lazy"
          decoding="async"
          className="absolute inset-0 w-full h-full object-cover"
          style={{ opacity: loaded ? 1 : 0, transition: 'opacity 0.15s' }}
          onLoad={() => setLoaded(true)}
          onError={handleError}
        />
      )}
      {(!loaded || failed) && (
        <div className="absolute inset-0" style={{
          background: `linear-gradient(135deg, ${TEAL}44 0%, ${TEAL}22 100%)`,
        }} />
      )}
    </div>
  )
}

// ── Dense waveform (CapCut style: packed bars, uses real data when available) ──
function WaveformBars({
  width, height,
  waveform = [], clipStart = 0, clipEnd = 0, totalDuration = 0,
  sourceWaveform, volumePct = 100,
}: {
  width: number; height: number
  waveform?: number[]; clipStart?: number; clipEnd?: number; totalDuration?: number
  sourceWaveform?: number[]   // F5: per-clip waveform for imported music
  volumePct?: number
}) {
  const barW  = 2
  const gap   = 1
  const count = Math.max(1, Math.floor((width - 8) / (barW + gap)))
  const volumeScale = Math.max(0, Math.min(2, volumePct / 100))
  const applyVolume = (v: number) => Math.max(0.02, Math.min(1, v * volumeScale))

  // Prefer per-clip waveform (imported audio) over sliced global waveform
  let bars: number[]
  if (sourceWaveform && sourceWaveform.length > 0) {
    // F5: use the imported audio's own waveform directly
    bars = Array.from({ length: count }, (_, i) => {
      const idx = Math.floor((i / count) * sourceWaveform.length)
      return applyVolume(Math.max(0.05, Math.min(1, sourceWaveform[idx] ?? 0)))
    })
  } else if (waveform.length > 0 && totalDuration > 0 && clipEnd > clipStart) {
    const startRatio = clipStart / totalDuration
    const endRatio   = clipEnd   / totalDuration
    const startIdx   = Math.floor(startRatio * waveform.length)
    const endIdx     = Math.ceil (endRatio   * waveform.length)
    const slice      = waveform.slice(Math.max(0, startIdx), Math.min(waveform.length, endIdx))
    bars = Array.from({ length: count }, (_, i) => {
      const src = (i / count) * slice.length
      const val = slice[Math.floor(src)] ?? 0
      return applyVolume(Math.max(0.05, Math.min(1, val)))
    })
  } else {
    // Fallback: synthetic waveform
    bars = Array.from({ length: count }, (_, i) => {
      const v = (Math.sin(i * 1.7 + 0.3) * 0.45 + Math.sin(i * 0.5 + 2.1) * 0.4 + 0.5) * 0.85 + 0.15
      return applyVolume(Math.max(0.1, Math.min(1, v)))
    })
  }

  const innerH = height * 0.75

  return (
    <svg
      style={{ position: 'absolute', left: 4, top: 8, pointerEvents: 'none' }}
      width={width - 8}
      height={height - 10}
      viewBox={`0 0 ${width - 8} ${height - 10}`}
    >
      {bars.map((h, i) => {
        const bh = Math.max(2, h * innerH)
        const y  = ((height - 10) - bh) / 2
        return (
          <rect
            key={i}
            x={i * (barW + gap)}
            y={y}
            width={barW}
            height={bh}
            rx={0}
            fill={NAVY_WAVE}
            opacity={0.85}
          />
        )
      })}
    </svg>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
// Clip-link overlay — draws a thin connector bar between paired video & audio clips
// ═══════════════════════════════════════════════════════════════════════════════

// The overlay sits at the exact pixel boundary between the video track (bottom)
// and the audio track (top): RULER_H + SUB_H(overlay) + MAIN_H(video)
const LINK_BAR_Y = RULER_H + SUB_H + MAIN_H   // 28 + 50 + 80 = 158

function ClipLinkOverlay({
  videoClips, audioClips, pxPerSec,
}: {
  videoClips: Clip[]
  audioClips: Clip[]
  pxPerSec: number
}) {
  // Pair each video clip with an audio clip that shares the same source_path
  // and has the same start_s (created together by the backend / appendVideo).
  // Excludes pairs where either clip is `unlinked` (user broke the link via right-click).
  const pairs: Array<{ start_s: number; end_s: number }> = []
  for (const vc of videoClips) {
    if (vc.unlinked) continue
    const ac = audioClips.find(
      (a) =>
        !a.unlinked &&
        a.source_path === vc.source_path &&
        Math.abs(a.start_s - vc.start_s) < 0.05 &&
        Math.abs(a.end_s   - vc.end_s)   < 0.05
    )
    if (ac) pairs.push({ start_s: vc.start_s, end_s: vc.end_s })
  }

  if (pairs.length === 0) return null

  return (
    <>
      {pairs.map((p, i) => {
        const x = p.start_s * pxPerSec
        const w = Math.max(4, (p.end_s - p.start_s) * pxPerSec)
        // Thin solid teal bar across the boundary, plus a chain-link icon
        // at the start of each paired clip (only when there's room — > 30px wide)
        return (
          <div key={i} className="absolute pointer-events-none" style={{ left: x, top: LINK_BAR_Y - 2, width: w, height: 4, zIndex: 15 }}>
            <div style={{
              position: 'absolute', inset: '1px 0',
              background: `${TEAL}cc`, borderRadius: 1,
              boxShadow: `0 0 4px ${TEAL}88`,
            }} />
            {w > 30 && (
              <div style={{
                position: 'absolute', left: 4, top: -5,
                width: 10, height: 10,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                background: TEAL, borderRadius: '50%',
                color: '#0a0a14', fontSize: 8, fontWeight: 700,
              }} title="Áudio vinculado ao vídeo">⛓</div>
            )}
          </div>
        )
      })}
    </>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
// Toolbar (CapCut layout: tools left, zoom right)
// ═══════════════════════════════════════════════════════════════════════════════
function Toolbar({
  zoom, onZoomIn, onZoomOut, onZoomFit,
  onAddMedia, onAddText, onDuplicate, onSplit, onDelete, onCloseGap, onUndo, onRedo, canUndo, canRedo, hasSelection,
  onLayerUp, onLayerDown, canLayerMove,
  onCompile, onUncompile, canCompile, canUncompile, onAddMarker,
  snapEnabled, onToggleSnap,
  rippleMode, onToggleRipple,
  onOpenAudioTab, onOpenEffectsTab,
  recording, recordingSeconds, onToggleRecording,
  onAddVideoTrack, onAddAudioTrack,
}: {
  zoom: number; onZoomIn: () => void; onZoomOut: () => void; onZoomFit: () => void
  onAddMedia: () => void
  onAddText: () => void
  onDuplicate: () => void
  onSplit: () => void; onDelete: () => void
  onCloseGap: () => void
  onUndo: () => void; onRedo: () => void
  canUndo: boolean; canRedo: boolean; hasSelection: boolean
  onLayerUp: () => void; onLayerDown: () => void; canLayerMove: boolean
  onCompile: () => void; onUncompile: () => void; canCompile: boolean; canUncompile: boolean
  onAddMarker: () => void
  snapEnabled: boolean; onToggleSnap: () => void
  rippleMode: boolean; onToggleRipple: () => void
  onOpenAudioTab: () => void
  onOpenEffectsTab: () => void
  recording: boolean
  recordingSeconds: number
  onToggleRecording: () => void
  onAddVideoTrack: () => void
  onAddAudioTrack: () => void
}) {
  const [addMenuOpen, setAddMenuOpen] = useState(false)
  const chooseAdd = (action: () => void) => {
    action()
    setAddMenuOpen(false)
  }
  return (
    <div
      className="flex items-center flex-shrink-0 px-2 gap-0.5"
      data-no-deselect="true"
      style={{ height: TOOLBAR_H, borderBottom: `1px solid ${BORDER_CLR}`, background: '#0d0d18' }}
    >
      {/* Left: Add + cursor */}
      <TBtn icon={<Plus size={14} />}           title="Adicionar mídia"  onClick={onAddMedia} />
      <TBtn icon={<Type size={14} />}           title="Adicionar texto no cursor" onClick={onAddText} />
      <div className="relative">
        <TBtn icon={<ChevronDown size={13} />} title="Adicionar faixa" onClick={() => setAddMenuOpen((v) => !v)} />
        {addMenuOpen && (
          <div
            className="absolute left-0 top-8 z-50 min-w-40 rounded border border-border bg-bg-panel shadow-xl overflow-hidden"
            onMouseLeave={() => setAddMenuOpen(false)}
          >
            <button className="block w-full px-3 py-2 text-left text-[11px] text-text-muted hover:bg-bg-surface hover:text-white" onClick={() => chooseAdd(onAddMedia)}>
              Adicionar mídia
            </button>
            <button className="block w-full px-3 py-2 text-left text-[11px] text-text-muted hover:bg-bg-surface hover:text-white" onClick={() => chooseAdd(onAddVideoTrack)}>
              Nova faixa de vídeo
            </button>
            <button className="block w-full px-3 py-2 text-left text-[11px] text-text-muted hover:bg-bg-surface hover:text-white" onClick={() => chooseAdd(onAddAudioTrack)}>
              Nova faixa de áudio
            </button>
          </div>
        )}
      </div>
      <Sep />
      <TBtn icon={<Undo2 size={14} />}          title="Desfazer (Ctrl+Z)"  onClick={onUndo}   disabled={!canUndo} />
      <TBtn icon={<Redo2 size={14} />}          title="Refazer (Ctrl+Y)"   onClick={onRedo}   disabled={!canRedo} />
      <Sep />
      <TBtn icon={<Copy size={14} />}           title="Duplicar seleção no playhead" onClick={onDuplicate} disabled={!hasSelection} />
      <TBtn icon={<Scissors size={14} />}       title="Dividir no cursor (S)" onClick={onSplit}  disabled={!hasSelection} />
      <TBtn icon={<Trash2 size={14} />}         title="Excluir clipe (Del)"   onClick={onDelete} disabled={!hasSelection} />
      <TBtn icon={<ChevronsLeft size={14} />}   title="Fechar gap a direita do clipe selecionado" onClick={onCloseGap} disabled={!hasSelection} />
      <TBtn icon={<Layers size={14} />}         title="Ações de camada" disabled={!canLayerMove} />
      <TBtn icon={<Flag size={14} />}           title="Adicionar marcador no playhead" onClick={onAddMarker} />
      <TBtn icon={<Layers size={14} />}         title="Compilar selecao de clipes" onClick={onCompile} disabled={!canCompile} />
      <TBtn icon={<Minus size={13} />}          title="Descompilar grupo selecionado" onClick={onUncompile} disabled={!canUncompile} />
      <TBtn icon={<ArrowUp size={14} />}        title="Trazer camada para frente" onClick={onLayerUp} disabled={!canLayerMove} />
      <TBtn icon={<ArrowDown size={14} />}      title="Enviar camada para trás" onClick={onLayerDown} disabled={!canLayerMove} />
      <TBtn icon={<Zap size={14} />}            title="Abrir aba de efeitos" onClick={onOpenEffectsTab} />
      <TBtn icon={<Volume2 size={14} />}        title="Abrir aba de áudio" onClick={onOpenAudioTab} />
      <TBtn icon={<Sliders size={14} />}        title="Adicionar camada de ajuste (color grading global)"
            onClick={() => useStore.getState().addAdjustmentLayer()} />
      <Sep />
      {/* Snap toggle — when ON, clips snap to gap edges/playhead while dragging */}
      <button
        title={snapEnabled ? 'Encaixe magnético ATIVO — clipes grudam em bordas e gaps (clique para desativar)' : 'Encaixe magnético DESATIVADO (clique para ativar)'}
        onClick={onToggleSnap}
        className="flex items-center justify-center rounded flex-shrink-0 transition-colors"
        style={{
          width: 28, height: 26,
          color:      snapEnabled ? '#22d4bc' : '#3e3e58',
          background: snapEnabled ? 'rgba(34,212,188,0.12)' : 'transparent',
          border: 'none', cursor: 'pointer',
        }}
      >
        <Magnet size={14} />
      </button>
      <button
        title={rippleMode ? 'Ripple delete ATIVO — excluir fecha o gap automaticamente' : 'Ripple delete DESATIVADO — excluir mantém gap'}
        onClick={onToggleRipple}
        className="flex items-center justify-center rounded flex-shrink-0 transition-colors"
        style={{
          width: 28, height: 26,
          color:      rippleMode ? '#ffb454' : '#3e3e58',
          background: rippleMode ? 'rgba(255,180,84,0.14)' : 'transparent',
          border: 'none', cursor: 'pointer',
        }}
      >
        <Scissors size={14} />
      </button>
      <Sep />
      <button
        title={recording ? 'Parar gravação de áudio (microfone)' : 'Gravar áudio em tempo real (microfone)'}
        onClick={onToggleRecording}
        className="flex items-center justify-center rounded flex-shrink-0 transition-colors"
        style={{
          width: 28,
          height: 26,
          color: recording ? '#fda4af' : '#3e3e58',
          background: recording ? 'rgba(244,63,94,0.2)' : 'transparent',
          border: 'none',
          cursor: 'pointer',
        }}
      >
        {recording ? <Square size={13} /> : <Mic size={14} />}
      </button>
      {recording && (
        <div
          className="tabular-nums"
          title="Tempo de gravação"
          style={{
            marginLeft: 4,
            fontSize: 10,
            color: '#fda4af',
            background: '#1a0f16',
            border: '1px solid #3a1a27',
            borderRadius: 3,
            padding: '2px 6px',
          }}
        >
          REC {fmtTime(recordingSeconds)}
        </div>
      )}

      <div className="flex-1" />

      {/* Right: zoom controls */}
      <TBtn icon={<Minus size={13} />}          title="Zoom − (Ctrl+Scroll)" onClick={onZoomOut} />
      <div
        style={{
          width: 42, textAlign: 'center', fontSize: 10, color: '#44445e',
          background: '#12121e', borderRadius: 3, padding: '2px 0',
        }}
        className="tabular-nums"
      >
        {zoom.toFixed(0)}%
      </div>
      <TBtn icon={<Plus size={13} />}           title="Zoom + (Ctrl+Scroll)" onClick={onZoomIn} />
      <button
        onClick={onZoomFit}
        title="Encaixar"
        style={{
          marginLeft: 2, fontSize: 10, color: '#44445e',
          padding: '2px 7px', background: '#12121e',
          border: `1px solid #1e1e2e`, borderRadius: 3, cursor: 'pointer',
        }}
        onMouseEnter={(e) => (e.currentTarget.style.color = '#999')}
        onMouseLeave={(e) => (e.currentTarget.style.color = '#44445e')}
      >
        Encaixar
      </button>
    </div>
  )
}

function TBtn({
  icon, title, onClick, disabled = false,
}: { icon: React.ReactNode; title: string; onClick?: () => void; disabled?: boolean }) {
  return (
    <button
      title={title}
      onClick={onClick}
      disabled={disabled}
      className="flex items-center justify-center rounded flex-shrink-0 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
      style={{
        width: 28, height: 26, color: '#3e3e58',
        background: 'none', border: 'none', cursor: disabled ? 'not-allowed' : 'pointer',
      }}
      onMouseEnter={(e) => { if (!disabled) e.currentTarget.style.color = '#8888aa' }}
      onMouseLeave={(e) => { if (!disabled) e.currentTarget.style.color = '#3e3e58' }}
    >
      {icon}
    </button>
  )
}

function Sep() {
  return (
    <div
      style={{
        width: 1, height: 14, background: '#1e1e2e',
        margin: '0 3px', flexShrink: 0,
      }}
    />
  )
}
