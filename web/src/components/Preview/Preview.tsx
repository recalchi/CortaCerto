import { Play, Pause, SkipBack, SkipForward, Volume2, VolumeX, Maximize2, Repeat, RefreshCw, Grid3X3, Copy, Scissors, Trash2, ArrowUp, ArrowDown, Type } from 'lucide-react'
import { useState, useRef, useEffect, useCallback, useMemo, type CSSProperties } from 'react'
import { useStore, aspectRatioToCss, Clip, ProjectState } from '../../store/useStore'

const API = 'http://127.0.0.1:7472'

function withPreviewReload(url: string, nonce: number): string {
  const sep = url.includes('?') ? '&' : '?'
  return `${url}${sep}preview_reload=${nonce}`
}

function aspectRatioNumber(ar: string | undefined): number {
  const [w, h] = aspectRatioToCss(ar as any).split('/').map((part) => Number(part.trim()))
  return w > 0 && h > 0 ? w / h : 16 / 9
}

export function resolvePreviewClipSource(
  clip: Pick<Clip, 'source_path'> | null | undefined,
  projectVideoPath: string | null | undefined
): string | null {
  if (!clip) return null
  const source = (clip.source_path ?? '').trim()
  if (source) return source
  return projectVideoPath || null
}

/** Return visible video clips across the main track and extra video tracks. */
function getVisibleVideoClips(
  proj: { video_track: { clips: Clip[] }; extra_video_tracks?: { clips: Clip[] }[] } | null | undefined,
  trackStates: Record<string, { hidden?: boolean; solo?: boolean }> | undefined
): Clip[] {
  if (!proj) return []
  const soloIds = ['video', ...(proj.extra_video_tracks ?? []).map((_, index) => `video-${index + 1}`)]
    .filter((id) => isTrackSolo(trackStates, id))
  const soloOn = soloIds.length > 0
  const clips: Clip[] = []
  if (!isTrackHidden(trackStates, 'video') && (!soloOn || soloIds.includes('video'))) clips.push(...proj.video_track.clips)
  ;(proj.extra_video_tracks ?? []).forEach((track, index) => {
    const stateId = `video-${index + 1}`
    if (!isTrackHidden(trackStates, stateId) && (!soloOn || soloIds.includes(stateId))) clips.push(...track.clips)
  })
  return clips.sort((a, b) => a.start_s - b.start_s)
}

export function nextVideoClipAfter(
  clips: Clip[],
  active: Pick<Clip, 'id' | 'end_s'>,
  projectVideoPath?: string | null,
  tolerance_s = 0.08
): Clip | null {
  return clips
    .filter((clip) => resolvePreviewClipSource(clip, projectVideoPath))
    .slice()
    .sort((a, b) => a.start_s - b.start_s)
    .find((clip) => (
      clip.id !== active.id
      && clip.start_s >= active.end_s - tolerance_s
      && clip.start_s <= active.end_s + tolerance_s
    )) ?? null
}

export function nextVisibleVideoClipAfter(
  proj: ProjectState | null | undefined,
  trackStates: Record<string, { hidden?: boolean; solo?: boolean }> | undefined,
  active: Pick<Clip, 'id' | 'end_s'>,
  tolerance_s = 0.08
): Clip | null {
  return nextVideoClipAfter(getVisibleVideoClips(proj, trackStates), active, proj?.videoPath, tolerance_s)
}

export function clipInteriorStartTime(clip: Pick<Clip, 'start_s' | 'end_s'>, epsilon_s = 0.035): number {
  const start = Number(clip.start_s ?? 0)
  const end = Number(clip.end_s ?? start)
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return Math.max(0, start)
  return Math.min(end - 0.001, start + Math.max(0.001, epsilon_s))
}

/** Clips that should keep transport moving, even when there is no active video. */
function getPlayableTimelineClips(
  proj: ProjectState | null | undefined,
  trackStates: Record<string, { hidden?: boolean; muted?: boolean; solo?: boolean }> | undefined
): Clip[] {
  if (!proj) return []
  const clips: Clip[] = [...getVisibleVideoClips(proj, trackStates)]

  if (!isTrackMuted(trackStates, 'audio')) clips.push(...proj.audio_track.clips)
  ;(proj.extra_audio_tracks ?? []).forEach((track, index) => {
    const stateId = `audio-${index + 1}`
    if (!isTrackMuted(trackStates, stateId)) clips.push(...track.clips)
  })

  if (!isTrackHidden(trackStates, 'text')) clips.push(...proj.text_track.clips)
  if (!isTrackHidden(trackStates, 'overlay')) clips.push(...proj.overlay_track.clips)
  ;(proj.extra_overlay_tracks ?? []).forEach((track, index) => {
    const stateId = `overlay-${index + 1}`
    if (!isTrackHidden(trackStates, stateId)) clips.push(...track.clips)
  })

  return clips
    .filter((c) => Number.isFinite(c.start_s) && Number.isFinite(c.end_s) && c.end_s > c.start_s)
    .sort((a, b) => a.start_s - b.start_s)
}

function isTrackHidden(trackStates: Record<string, { hidden?: boolean }> | undefined, stateId: string): boolean {
  return !!trackStates?.[stateId]?.hidden
}

function isTrackMuted(trackStates: Record<string, { muted?: boolean }> | undefined, stateId: string): boolean {
  return !!trackStates?.[stateId]?.muted
}

function isTrackSolo(trackStates: Record<string, { solo?: boolean }> | undefined, stateId: string): boolean {
  return !!trackStates?.[stateId]?.solo
}

export function Preview() {
  const {
    project, previewTime, setPreviewTime,
    updateClip, undo, redo,
    copyClip, pasteClip,
    splitClip, deleteClip, duplicateSelectionAtPlayhead, addTextClip,
    trackStates,
    setProxyStatus,
    setSourceProxy,
    selectedClipId, setSelectedClip,
    captionPreviewEnabled, captionPreviewText, captionPreviewStyle,
  } = useStore()
  const [playing,      setPlaying]      = useState(false)
  const [loop,         setLoop]         = useState(false)
  const [volume,       setVolume]       = useState(100)   // 0-100
  const [muted,        setMuted]        = useState(false)
  const [duration,     setDuration]     = useState(0)
  const [playbackRate, setPlaybackRate] = useState(1)
  const [videoLoading, setVideoLoading] = useState(false) // true while src is loading
  const [videoError,   setVideoError]   = useState<string | null>(null)
  const [previewRefreshNonce, setPreviewRefreshNonce] = useState(0)
  const [frameGuideVisible, setFrameGuideVisible] = useState(true)
  const [viewportSize, setViewportSize] = useState({ width: 0, height: 0 })
  const [overlaySnapGuides, setOverlaySnapGuides] = useState<{
    xKey: null | 'left' | 'center' | 'right' | 'overlay'
    yKey: null | 'top' | 'center' | 'bottom' | 'overlay'
    xValue: number | null
    yValue: number | null
  }>({ xKey: null, yKey: null, xValue: null, yValue: null })

  const videoRef        = useRef<HTMLVideoElement>(null)
  const containerRef    = useRef<HTMLDivElement>(null)
  const seekingByUser   = useRef(false)
  const lastExternal    = useRef(-1)

  // Tracks the user's *intent* to be playing across source remounts.
  // When the <video key={src}> remounts (source change), `playing` state is
  // still true but the new element is paused.  wasPlayingRef lets onLoadedMetadata
  // know it should call .play() automatically.
  const wasPlayingRef    = useRef(false)
  // Latest activeSrcPath in a ref so onTimeUpdate (stale closure) can compare
  // sources without going through React's render cycle.
  const activeSrcPathRef = useRef<string | null>(null)
  // Latest previewTime in a ref for onEnded (stale closure safe)
  const previewTimeRef   = useRef(0)
  const activeVideoClipRef = useRef<Clip | null>(null)
  const previewProbeRef = useRef<Set<string>>(new Set())
  const sourceTransitionRef = useRef<{
    fromClipId: string
    toClipId: string
    toStart_s: number
    toSrc: string | null
  } | null>(null)

  const visibleVideoClips = useMemo(() => {
    return getVisibleVideoClips(project, trackStates)
  }, [project, trackStates])

  // Resolve the visible video clip currently at the playhead.
  const activeVideoClip = visibleVideoClips.find(
    (c) => previewTime >= c.start_s && previewTime < c.end_s
  ) ?? null

  // ── P0-1: codec proxy (H.265 → H.264 background transcode) ──────────────────
  const proxyReady  = project?.proxy_status === 'ready' && !!project.proxy_path
  const proxyStatus = project?.proxy_status

  useEffect(() => {
    if (proxyStatus !== 'transcoding' || !project?.videoPath) return
    const timer = setInterval(async () => {
      try {
        const r = await fetch(`${API}/api/video-proxy-status?path=${encodeURIComponent(project.videoPath!)}`)
        const d = await r.json()
        if (d.status === 'ready' || d.status === 'error') {
          setProxyStatus(d.status, d.proxy_path || undefined)
          clearInterval(timer)
        }
      } catch { /* ignore */ }
    }, 2500)
    return () => clearInterval(timer)
  }, [proxyStatus, project?.videoPath]) // eslint-disable-line react-hooks/exhaustive-deps

  // Poll proxy status for APPENDED sources that are still transcoding.
  // When proxy becomes ready, write the path back into source_proxies so the
  // <video> element can switch to the H.264 proxy URL.
  const sourceProxies = project?.source_proxies
  useEffect(() => {
    if (!sourceProxies) return
    const pendingSources = Object.entries(sourceProxies)
      .filter(([, proxyPath]) => proxyPath === '')   // placeholder = still transcoding
      .map(([src]) => src)
    if (pendingSources.length === 0) return
    const timer = setInterval(async () => {
      for (const src of pendingSources) {
        try {
          const r = await fetch(`${API}/api/video-proxy-status?path=${encodeURIComponent(src)}`)
          const d = await r.json()
          if (d.status === 'ready' && d.proxy_path) {
            useStore.getState().setSourceProxy(src, d.proxy_path)
          }
        } catch { /* ignore */ }
      }
    }, 2500)
    return () => clearInterval(timer)
  }, [sourceProxies])

  // ── P2-1: multi-source video (appended clips from different files) ────────────
  // The active clip's source_path tells us which file to play; source_offset_s is
  // the amount by which that file's clock is shifted in the project timeline.
  //   project_time = source_time + source_offset_s
  //   source_time  = project_time - source_offset_s
  //
  // CRITICAL: do NOT fall back to project.videoPath when no clip is active.
  // That fallback was causing the "deleted clip keeps playing" bug — the <video>
  // element stayed mounted on the original source file even after the clip
  // referencing it was removed from the timeline.
  const activeSrcPath = resolvePreviewClipSource(activeVideoClip, project?.videoPath)
  const sourceOffset  = activeVideoClip?.source_offset_s ?? 0

  // Keep refs so event handlers always have the latest values without stale closures
  const sourceOffsetRef = useRef(0)
  useEffect(() => { sourceOffsetRef.current  = sourceOffset  }, [sourceOffset])
  useEffect(() => { activeSrcPathRef.current = activeSrcPath }, [activeSrcPath])
  useEffect(() => { previewTimeRef.current   = previewTime   }, [previewTime])
  useEffect(() => { activeVideoClipRef.current = activeVideoClip }, [activeVideoClip])
  const activeClipSpeed = Math.max(0.05, Number(activeVideoClip?.speed_factor ?? 1))
  const activeClipSpeedRef = useRef(1)
  useEffect(() => { activeClipSpeedRef.current = activeClipSpeed }, [activeClipSpeed])

  // When the active source PATH changes, the <video key={activeSrcPath}> remounts.
  // onLoadedMetadata then applies the seek using previewTimeRef + sourceOffsetRef
  // (always the latest values — no separate effect needed).

  // Build the actual src URL — prefer the per-source proxy if one exists.
  // Order of precedence:
  //   1. If the active source IS the main video AND its main proxy is ready → use main proxy
  //   2. If the active source has a per-source proxy registered → use that (appended H.265 files)
  //   3. Otherwise → original source path
  useEffect(() => {
    if (!activeSrcPath || activeSrcPath.startsWith('sticker:')) return
    const sourceProxiesNow = project?.source_proxies ?? {}
    const isMainSource = activeSrcPath === project?.videoPath
    const mainKnown = isMainSource && !!project?.proxy_status && project.proxy_status !== 'not_needed'
    const sourceKnown = !isMainSource && Object.prototype.hasOwnProperty.call(sourceProxiesNow, activeSrcPath)
    if (mainKnown || sourceKnown || previewProbeRef.current.has(activeSrcPath)) return
    previewProbeRef.current.add(activeSrcPath)
    let cancelled = false
    fetch(`${API}/api/video-proxy-ensure`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: activeSrcPath, force: false }),
    })
      .then((r) => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then((d) => {
        if (cancelled) return
        const status = d.proxy_status as ProjectState['proxy_status']
        const proxyPath = String(d.proxy_path || '')
        if (isMainSource) {
          setProxyStatus(status, proxyPath || undefined)
        } else if (status === 'ready' && proxyPath) {
          setSourceProxy(activeSrcPath, proxyPath)
        } else if (status === 'transcoding') {
          setSourceProxy(activeSrcPath, '')
        }
      })
      .catch(() => {
        previewProbeRef.current.delete(activeSrcPath)
      })
    return () => { cancelled = true }
  }, [activeSrcPath, project?.videoPath, project?.proxy_status, project?.source_proxies, setProxyStatus, setSourceProxy])

  const requestForcedPreviewProxy = useCallback((sourcePath: string | null) => {
    if (!sourcePath || sourcePath.startsWith('sticker:')) return
    const key = `${sourcePath}::force`
    if (previewProbeRef.current.has(key)) return
    previewProbeRef.current.add(key)
    const isMainSource = sourcePath === project?.videoPath
    fetch(`${API}/api/video-proxy-ensure`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: sourcePath, force: true }),
    })
      .then((r) => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then((d) => {
        const status = d.proxy_status as ProjectState['proxy_status']
        const proxyPath = String(d.proxy_path || '')
        if (isMainSource) {
          setProxyStatus(status, proxyPath || undefined)
        } else if (status === 'ready' && proxyPath) {
          setSourceProxy(sourcePath, proxyPath)
        } else if (status === 'transcoding') {
          setSourceProxy(sourcePath, '')
        }
      })
      .catch(() => {
        previewProbeRef.current.delete(key)
      })
  }, [project?.videoPath, setProxyStatus, setSourceProxy])

  const sourceProxiesMap = project?.source_proxies ?? {}
  const hasSourceProxyEntry = !!activeSrcPath && Object.prototype.hasOwnProperty.call(sourceProxiesMap, activeSrcPath)
  const sourceProxyPath = activeSrcPath ? (sourceProxiesMap[activeSrcPath] ?? '') : ''
  const mainProxyExpected = !!proxyStatus && proxyStatus !== 'not_needed'
  const mainProxyPending = !!activeSrcPath
    && activeSrcPath === project?.videoPath
    && mainProxyExpected
    && proxyStatus !== 'ready'
    && proxyStatus !== 'error'
  const mainProxyError = !!activeSrcPath
    && activeSrcPath === project?.videoPath
    && mainProxyExpected
    && proxyStatus === 'error'
  const sourceProxyPending = hasSourceProxyEntry && !sourceProxyPath
  const activeProxyPending = mainProxyPending || sourceProxyPending
  const activeProxyError = mainProxyError
  const videoSrc = activeSrcPath && !activeProxyPending && !activeProxyError
    ? ((activeSrcPath === project?.videoPath && proxyReady && project?.proxy_path)
        ? `${API}/api/serve-file?path=${encodeURIComponent(project.proxy_path)}`
        : (sourceProxyPath
            ? `${API}/api/serve-file?path=${encodeURIComponent(sourceProxyPath)}`
            : `${API}/api/serve-file?path=${encodeURIComponent(activeSrcPath)}`))
    : undefined
  const refreshedVideoSrc = videoSrc ? withPreviewReload(videoSrc, previewRefreshNonce) : undefined
  const nextVideoClip = activeVideoClip
    ? visibleVideoClips.find((clip) => clip.id !== activeVideoClip.id && clip.start_s >= activeVideoClip.end_s - 0.08)
    : visibleVideoClips.find((clip) => clip.start_s > previewTime)
  const nextVideoSrcPath = resolvePreviewClipSource(nextVideoClip, project?.videoPath)
  const nextVideoProxyPath = nextVideoSrcPath ? (sourceProxiesMap[nextVideoSrcPath] ?? '') : ''
  const nextVideoSrc = nextVideoSrcPath
    ? `${API}/api/serve-file?path=${encodeURIComponent(nextVideoProxyPath || nextVideoSrcPath)}`
    : undefined
  const transitionPreview = useMemo(() => {
    if (!activeVideoClip || !nextVideoClip || !nextVideoSrc) return null
    const transition = String(activeVideoClip.transition || 'Corte')
    if (transition === 'Corte') return null
    const touchGap = Math.abs(nextVideoClip.start_s - activeVideoClip.end_s)
    if (touchGap > 0.12) return null
    const durationS = Math.max(0.1, Math.min(1.5, Number(activeVideoClip.transition_duration_s ?? 0.4)))
    const start = activeVideoClip.end_s - durationS
    if (previewTime < start || previewTime >= activeVideoClip.end_s) return null
    return {
      clip: nextVideoClip,
      src: withPreviewReload(nextVideoSrc, previewRefreshNonce),
      transition,
      progress: Math.max(0, Math.min(1, (previewTime - start) / durationS)),
    }
  }, [activeVideoClip, nextVideoClip, nextVideoSrc, previewRefreshNonce, previewTime])

  // Track visibility/mute state from Timeline headers
  const videoHidden   = trackStates?.video?.hidden  ?? false
  const videoMuted    = trackStates?.video?.muted   ?? false
  const audioMuted    = trackStates?.audio?.muted   ?? false
  const textHidden    = trackStates?.text?.hidden   ?? false
  const overlayImageClips = useMemo(() => {
    if (!project) return [] as Clip[]
    const soloIds = ['overlay', ...(project.extra_overlay_tracks ?? []).map((_, index) => `overlay-${index + 1}`)]
      .filter((id) => isTrackSolo(trackStates, id))
    const soloOn = soloIds.length > 0
    const clips: Clip[] = []
    if (!isTrackHidden(trackStates, 'overlay') && (!soloOn || soloIds.includes('overlay'))) {
      clips.push(...project.overlay_track.clips)
    }
    ;(project.extra_overlay_tracks ?? []).forEach((track, index) => {
      const stateId = `overlay-${index + 1}`
      if (!isTrackHidden(trackStates, stateId) && (!soloOn || soloIds.includes(stateId))) clips.push(...track.clips)
    })
    return clips.sort((a, b) => {
      const za = Number(a.z_order ?? 0)
      const zb = Number(b.z_order ?? 0)
      if (za !== zb) return za - zb
      return Number(a.start_s ?? 0) - Number(b.start_s ?? 0)
    })
  }, [project, trackStates])

  const overlayVideoClips = useMemo(() => {
    return overlayImageClips.filter((c) =>
      (c.clip_type === 'video_overlay' || c.clip_type === 'video')
      && !!c.source_path
    )
  }, [overlayImageClips])

  const selectedActiveOverlayClip = useMemo(() => {
    if (!selectedClipId) return null
    const clip = overlayImageClips.find((c) => c.id === selectedClipId)
    if (!clip) return null
    if (!(previewTime >= clip.start_s && previewTime < clip.end_s)) return null
    if (!(clip.clip_type === 'image' || clip.clip_type === 'video_overlay' || clip.clip_type === 'video' || clip.clip_type === 'sticker')) return null
    return clip
  }, [overlayImageClips, previewTime, selectedClipId])

  const allProjectClips = useMemo(() => {
    if (!project) return [] as Clip[]
    return [
      ...project.video_track.clips,
      ...project.audio_track.clips,
      ...project.text_track.clips,
      ...project.overlay_track.clips,
      ...(project.extra_video_tracks ?? []).flatMap((t) => t.clips),
      ...(project.extra_audio_tracks ?? []).flatMap((t) => t.clips),
      ...(project.extra_overlay_tracks ?? []).flatMap((t) => t.clips),
    ]
  }, [project])

  const selectedClip = useMemo(() => {
    if (!selectedClipId) return null
    return allProjectClips.find((c) => c.id === selectedClipId) ?? null
  }, [allProjectClips, selectedClipId])

  const selectedClipActiveAtPlayhead = !!selectedClip
    && previewTime >= selectedClip.start_s
    && previewTime < selectedClip.end_s

  const selectedCanLayerMove = !!selectedClip && (
    selectedClip.clip_type === 'image'
    || selectedClip.clip_type === 'video_overlay'
    || selectedClip.clip_type === 'video'
    || selectedClip.clip_type === 'sticker'
    || selectedClip.clip_type === 'text'
  )

  const selectedCanSplit = !!selectedClip && (
    previewTime > selectedClip.start_s + 0.05
    && previewTime < selectedClip.end_s - 0.05
  )

  const bumpSelectedLayer = useCallback((delta: number) => {
    const st = useStore.getState()
    const id = st.selectedClipId
    if (!id || !st.project) return
    const all = [
      ...st.project.video_track.clips,
      ...st.project.audio_track.clips,
      ...st.project.text_track.clips,
      ...st.project.overlay_track.clips,
      ...(st.project.extra_video_tracks ?? []).flatMap((t) => t.clips),
      ...(st.project.extra_audio_tracks ?? []).flatMap((t) => t.clips),
      ...(st.project.extra_overlay_tracks ?? []).flatMap((t) => t.clips),
    ]
    const clip = all.find((c) => c.id === id)
    if (!clip) return
    if (!(clip.clip_type === 'image' || clip.clip_type === 'video_overlay' || clip.clip_type === 'video' || clip.clip_type === 'sticker' || clip.clip_type === 'text')) return
    const next = Math.max(-200, Math.min(200, Number(clip.z_order ?? 0) + delta))
    st.updateClip(id, { z_order: next })
  }, [])

  const activeOverlaySelectableIds = useMemo(() => {
    return overlayImageClips
      .filter((c) =>
        previewTime >= c.start_s && previewTime < c.end_s
        && (c.clip_type === 'image' || c.clip_type === 'video_overlay' || c.clip_type === 'video' || c.clip_type === 'sticker')
      )
      .sort((a, b) => {
        const za = Number(a.z_order ?? 0)
        const zb = Number(b.z_order ?? 0)
        if (za !== zb) return zb - za
        return Number(b.start_s ?? 0) - Number(a.start_s ?? 0)
      })
      .map((c) => c.id)
  }, [overlayImageClips, previewTime])

  const selectOrCycleOverlay = useCallback((clipId: string) => {
    const ids = activeOverlaySelectableIds
    if (ids.length === 0) {
      setSelectedClip(clipId)
      return
    }
    const current = useStore.getState().selectedClipId
    if (current === clipId && ids.length > 1) {
      const idx = ids.indexOf(clipId)
      if (idx >= 0) {
        const nextId = ids[(idx + 1) % ids.length]
        setSelectedClip(nextId)
        return
      }
    }
    setSelectedClip(clipId)
  }, [activeOverlaySelectableIds, setSelectedClip])

  const startOverlayPointerInteraction = useCallback((e: React.MouseEvent, clip: Clip) => {
    e.preventDefault()
    e.stopPropagation()
    const startX = e.clientX
    const startY = e.clientY
    const origX = Number(clip.position_x ?? 0)
    const origY = Number(clip.position_y ?? 0)
    const SNAP_PX = 10
    let didDrag = false
    setOverlaySnapGuides({ xKey: null, yKey: null, xValue: null, yValue: null })

    const scale = Math.max(0.1, Number(clip.scale_pct ?? 100) / 100)
    const edgeX = (viewportSize.width * (scale - 1)) / 2
    const edgeY = (viewportSize.height * (scale - 1)) / 2

    const pickSnap = <T extends string>(
      raw: number,
      candidates: Array<{ value: number; key: T }>,
      threshold: number,
    ): { value: number; key: T | null } => {
      let best: { value: number; key: T } | null = null
      let bestDist = Infinity
      for (const c of candidates) {
        const d = Math.abs(raw - c.value)
        if (d < bestDist) {
          best = c
          bestDist = d
        }
      }
      if (!best || bestDist > threshold) return { value: raw, key: null }
      return best
    }

    const onMove = (mv: MouseEvent) => {
      const dx = mv.clientX - startX
      const dy = mv.clientY - startY
      if (!didDrag && Math.abs(dx) < 3 && Math.abs(dy) < 3) return
      didDrag = true
      const rawX = Math.round(origX + dx)
      const rawY = Math.round(origY + dy)
      const xCandidates: Array<{ value: number; key: 'left' | 'center' | 'right' | 'overlay' }> = [
        { value: 0, key: 'center' },
      ]
      const yCandidates: Array<{ value: number; key: 'top' | 'center' | 'bottom' | 'overlay' }> = [
        { value: 0, key: 'center' },
      ]
      if (Math.abs(edgeX) > 4) {
        xCandidates.push({ value: edgeX, key: 'left' })
        xCandidates.push({ value: -edgeX, key: 'right' })
      }
      if (Math.abs(edgeY) > 4) {
        yCandidates.push({ value: edgeY, key: 'top' })
        yCandidates.push({ value: -edgeY, key: 'bottom' })
      }
      for (const sibling of overlayImageClips) {
        if (sibling.id === clip.id) continue
        if (!(previewTime >= sibling.start_s && previewTime < sibling.end_s)) continue
        if (!(sibling.clip_type === 'image' || sibling.clip_type === 'video_overlay' || sibling.clip_type === 'video' || sibling.clip_type === 'sticker')) continue
        xCandidates.push({ value: Number(sibling.position_x ?? 0), key: 'overlay' })
        yCandidates.push({ value: Number(sibling.position_y ?? 0), key: 'overlay' })
      }
      const sx = pickSnap(rawX, xCandidates, SNAP_PX)
      const sy = pickSnap(rawY, yCandidates, SNAP_PX)
      const nextX = Math.round(Number(sx.value))
      const nextY = Math.round(Number(sy.value))
      setOverlaySnapGuides({
        xKey: sx.key,
        yKey: sy.key,
        xValue: sx.key ? Number(sx.value) : null,
        yValue: sy.key ? Number(sy.value) : null,
      })
      updateClip(clip.id, {
        position_x: nextX,
        position_y: nextY,
      })
    }

    const onUp = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      setOverlaySnapGuides({ xKey: null, yKey: null, xValue: null, yValue: null })
      if (!didDrag) selectOrCycleOverlay(clip.id)
    }

    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [overlayImageClips, previewTime, selectOrCycleOverlay, updateClip, viewportSize.height, viewportSize.width])

  const musicClipEntries = useMemo(() => {
    if (!project) return [] as Array<{ clip: Clip; muted: boolean }>
    const soloIds = ['audio', ...(project.extra_audio_tracks ?? []).map((_, index) => `audio-${index + 1}`)]
      .filter((id) => isTrackSolo(trackStates, id))
    const soloOn = soloIds.length > 0
    const entries: Array<{ clip: Clip; muted: boolean }> = []
    const mainMuted = isTrackMuted(trackStates, 'audio')
    if (!soloOn || soloIds.includes('audio')) {
      for (const clip of project.audio_track.clips) {
        if (clip.clip_type === 'music' && clip.source_path) entries.push({ clip, muted: mainMuted })
      }
    }
    ;(project.extra_audio_tracks ?? []).forEach((track, index) => {
      const stateId = `audio-${index + 1}`
      if (soloOn && !soloIds.includes(stateId)) return
      const muted = isTrackMuted(trackStates, stateId)
      for (const clip of track.clips) {
        if (clip.clip_type === 'music' && clip.source_path) entries.push({ clip, muted })
      }
    })
    return entries
  }, [project, trackStates])

  // When no <video> element is active (audio-only, text-only or still image
  // region), drive the project clock manually so timeline playback keeps moving.
  useEffect(() => {
    if (!playing || activeVideoClip) return
    let raf = 0
    let last = performance.now()
    const tick = (now: number) => {
      const delta = Math.max(0, (now - last) / 1000) * playbackRate
      last = now
      const state = useStore.getState()
      const total = state.project?.duration_s ?? duration
      const clips = getPlayableTimelineClips(state.project, state.trackStates)
      const nextTime = state.previewTime + delta
      if (!Number.isFinite(nextTime) || nextTime >= total) {
        const sorted = clips.slice().sort((a, b) => a.start_s - b.start_s)
        if (loop && sorted.length > 0) {
          setPreviewTime(sorted[0].start_s)
          last = now
          raf = requestAnimationFrame(tick)
          return
        }
        setPreviewTime(Math.max(0, total))
        wasPlayingRef.current = false
        setPlaying(false)
        return
      }
      setPreviewTime(Math.max(0, nextTime))
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [playing, activeVideoClip, playbackRate, duration, loop, setPreviewTime])

  useEffect(() => {
    const vid = videoRef.current
    if (!vid) return
    vid.playbackRate = Math.max(0.05, playbackRate * activeClipSpeed)
  }, [playbackRate, activeClipSpeed, refreshedVideoSrc])

  const projectTimeToSourceTime = useCallback((projectTime: number, clip: Clip | null = activeVideoClipRef.current) => {
    if (!clip) return Math.max(0, projectTime - sourceOffsetRef.current)
    const speed = Math.max(0.05, Number(clip.speed_factor ?? 1))
    const sourceStart = clip.start_s - (clip.source_offset_s ?? 0)
    return Math.max(0, sourceStart + (projectTime - clip.start_s) * speed)
  }, [])

  const sourceTimeToProjectTime = useCallback((sourceTime: number, clip: Clip | null = activeVideoClipRef.current) => {
    if (!clip) return sourceTime + sourceOffsetRef.current
    const speed = Math.max(0.05, Number(clip.speed_factor ?? 1))
    const sourceStart = clip.start_s - (clip.source_offset_s ?? 0)
    return clip.start_s + (sourceTime - sourceStart) / speed
  }, [])

  const activeVideoClipFx = useMemo(
    () => applyMotionKeyframe(activeVideoClip, previewTime),
    [activeVideoClip, previewTime]
  )

  // Sync audio mute to the video element whenever it changes
  useEffect(() => {
    const vid = videoRef.current
    if (vid) vid.muted = audioMuted || videoMuted
  }, [audioMuted, videoMuted])

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const update = () => setViewportSize({ width: el.clientWidth, height: el.clientHeight })
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // Build live-preview CSS from that clip's Inspector values
  const pv = activeVideoClipFx
  const pvBrightness  = 1 + (pv?.brightness  ?? 0) / 100   // 0..2, 1 = neutral
  const pvContrast    = 1 + (pv?.contrast    ?? 0) / 100
  const pvSaturation  = 1 + (pv?.saturation  ?? 0) / 100
  const pvHue         = pv?.hue ?? 0                        // -180..180 degrees
  const pvExposure    = 1 + (pv?.exposure   ?? 0) / 100     // approximate via brightness multiplier
  const pvTemperature = (pv?.temperature  ?? 0) / 100       // -1..+1; approximated via sepia(+warm) / hue-rotate(-cool)
  const pvOpacity     = (pv?.opacity_pct  ?? 100) / 100
  const pvScale       = (pv?.scale_pct    ?? 100) / 100
  const pvRotation    =  pv?.rotation_deg ?? 0
  const pvPosX        = pv?.position_x ?? 0
  const pvPosY        = pv?.position_y ?? 0
  const pvCropTop     =  pv?.crop_top_pct    ?? 0
  const pvCropRight   =  pv?.crop_right_pct  ?? 0
  const pvCropBottom  =  pv?.crop_bottom_pct ?? 0
  const pvCropLeft    =  pv?.crop_left_pct   ?? 0

  // CSS filter string — combined color grading.
  // Temperature is approximated: warm (+) adds sepia tint; cool (-) shifts hue blueward.
  const pvFilterParts: string[] = []
  if (pvBrightness  !== 1)  pvFilterParts.push(`brightness(${(pvBrightness * pvExposure).toFixed(3)})`)
  else if (pvExposure !== 1) pvFilterParts.push(`brightness(${pvExposure.toFixed(3)})`)
  if (pvContrast    !== 1)  pvFilterParts.push(`contrast(${pvContrast.toFixed(3)})`)
  if (pvSaturation  !== 1)  pvFilterParts.push(`saturate(${pvSaturation.toFixed(3)})`)
  if (pvHue         !== 0)  pvFilterParts.push(`hue-rotate(${pvHue}deg)`)
  if (pvTemperature  >  0)  pvFilterParts.push(`sepia(${(pvTemperature * 0.5).toFixed(3)})`)
  if (pvTemperature  <  0)  pvFilterParts.push(`hue-rotate(${(pvTemperature * 30).toFixed(0)}deg)`)
  if (pv) appendBlurFilter(pvFilterParts, pv)
  // Compose visible adjustment layers. Hidden overlay tracks must not affect preview.
  for (const adj of overlayImageClips) {
    if (adj.clip_type !== 'adjustment') continue
    if (previewTime < adj.start_s || previewTime >= adj.end_s) continue
    appendAdjustmentFilter(pvFilterParts, adj)
  }
  const pvFilter = pvFilterParts.length > 0 ? pvFilterParts.join(' ') : undefined

  // Compose user transforms with the optional entry/exit animation transform
  const pvAnim = activeVideoClip ? computeAnimationStyle(previewTime, activeVideoClip) : null
  const baseTransform = (pvScale !== 1 || pvRotation !== 0 || pvPosX !== 0 || pvPosY !== 0)
    ? `translate(${pvPosX}px, ${pvPosY}px) scale(${pvScale.toFixed(4)}) rotate(${pvRotation}deg)`
    : ''
  const pvTransform = pvAnim
    ? `${pvAnim.transform} ${baseTransform}`.trim()
    : (baseTransform || undefined)
  const pvAnimOpacity = pvAnim ? pvAnim.opacity : 1

  // CSS clip-path for crop (inset order: top right bottom left)
  const pvClipPath = (pvCropTop + pvCropRight + pvCropBottom + pvCropLeft) > 0.01
    ? `inset(${pvCropTop}% ${pvCropRight}% ${pvCropBottom}% ${pvCropLeft}%)`
    : undefined

  // When project changes, reset state
  useEffect(() => {
    setPlaying(false)
    wasPlayingRef.current = false
    setDuration(project?.duration_s ?? 0)
  }, [project?.videoPath])

  // When project duration changes (e.g. appendVideo extends timeline) keep
  // the local `duration` in sync without resetting playback.
  useEffect(() => {
    setDuration(project?.duration_s ?? 0)
  }, [project?.duration_s])

  // Deletion guard: if there's no clip at the playhead, ALWAYS pause regardless
  // of `playing` state — protects against the <video> element continuing to
  // play or buffer the source file even after the clip referencing it is gone.
  useEffect(() => {
    if (!activeVideoClip) {
      const vid = videoRef.current
      if (vid) {
        if (!vid.paused) vid.pause()
        // Force-clear the src to stop background loading/decoding entirely
        try { vid.removeAttribute('src'); vid.load() } catch { /* ignore */ }
      }
      sourceTransitionRef.current = null
      const state = useStore.getState()
      const total = state.project?.duration_s ?? 0
      const hasTimelineAhead = state.previewTime < total - 0.01
      if (!hasTimelineAhead) {
        if (playing) setPlaying(false)
        wasPlayingRef.current = false
      }
    }
  }, [activeVideoClip, playing])

  // Clamp playhead when project duration shrinks (e.g. after deleting clips at the end).
  // Use (dur - 0.01) so previewTime lands INSIDE the last clip's range, not at its
  // boundary where no clip would be active.
  useEffect(() => {
    const dur = project?.duration_s ?? 0
    if (previewTime > dur + 0.01) {
      setPreviewTime(Math.max(0, dur - 0.01))
    }
  }, [project?.duration_s])  // eslint-disable-line react-hooks/exhaustive-deps

  // Show loading indicator whenever the video src URL changes
  useEffect(() => {
    setVideoError(null)
    if (refreshedVideoSrc) setVideoLoading(true)
    else          setVideoLoading(false)
  }, [refreshedVideoSrc])

  // External seek: previewTime changed from outside (ruler click, timeline click)
  // Translate project-time → source-file time using current sourceOffset.
  // Only sync if the difference is significant (avoids feedback loop during playback).
  useEffect(() => {
    const vid = videoRef.current
    if (!vid || seekingByUser.current) return
    const targetSrcTime = projectTimeToSourceTime(previewTime)
    if (Math.abs(vid.currentTime - targetSrcTime) > 0.15) {
      vid.currentTime = targetSrcTime
      lastExternal.current = previewTime
    }
  }, [previewTime, projectTimeToSourceTime])

  // During playback, push video currentTime → store.
  // Also implements skip-silence: if current position falls inside a gap between
  // speech clips, seek ahead to the next clip start (CapCut-style editing preview).
  //
  // Multi-source rule:
  //   • Gap → next clip SAME source  → seek vid.currentTime directly (no remount)
  //   • Gap → next clip DIFF source  → only call setPreviewTime; React re-renders,
  //     activeSrcPath changes, <video key=> remounts, onLoadedMetadata auto-plays.
  //     DO NOT seek vid.currentTime here — it's the wrong file.
  const onTimeUpdate = useCallback(() => {
    const vid = videoRef.current
    if (!vid || seekingByUser.current) return
    if (sourceTransitionRef.current) return
    const projTime = sourceTimeToProjectTime(vid.currentTime)

    const state = useStore.getState()
    const playableClips = getPlayableTimelineClips(state.project, state.trackStates)
    const videoClips = getVisibleVideoClips(state.project, state.trackStates)
    if (playableClips.length > 0 && !vid.paused) {
      const active = activeVideoClipRef.current
      if (active && projTime >= active.end_s - 0.03) {
        const next = nextVisibleVideoClipAfter(state.project, state.trackStates, active)
        if (next) {
          const nextSrc = resolvePreviewClipSource(next, state.project?.videoPath)
          const targetTime = clipInteriorStartTime(next)
          wasPlayingRef.current = true
          if (nextSrc === activeSrcPathRef.current) {
            vid.currentTime = projectTimeToSourceTime(targetTime, next)
          } else {
            sourceTransitionRef.current = {
              fromClipId: active.id,
              toClipId: next.id,
              toStart_s: targetTime,
              toSrc: nextSrc,
            }
            setVideoLoading(true)
            try { vid.pause() } catch { /* ignore */ }
          }
          setPreviewTime(targetTime)
          return
        }
      }
      const transportClips = active ? videoClips : playableClips
      const inClip = transportClips.some((c) => projTime >= c.start_s && projTime < c.end_s)
      if (!inClip) {
        const sorted = transportClips.slice().sort((a, b) => a.start_s - b.start_s)
        const next   = sorted.find((c) => c.start_s >= projTime - 0.08)
        if (next) {
          if (active && next.start_s > projTime + 0.12) {
            setPreviewTime(projTime)
            return
          }
          const nextSrc = resolvePreviewClipSource(next, state.project?.videoPath)
          const targetTime = active ? clipInteriorStartTime(next) : next.start_s
          if (nextSrc === activeSrcPathRef.current) {
            // Same source file — seek the video element directly (fast, no remount)
            const nextSrcTime = projectTimeToSourceTime(targetTime, next)
            vid.currentTime = nextSrcTime
          } else if (active) {
            sourceTransitionRef.current = {
              fromClipId: active.id,
              toClipId: next.id,
              toStart_s: targetTime,
              toSrc: nextSrc,
            }
            setVideoLoading(true)
            try { vid.pause() } catch { /* ignore */ }
          }
          // For different source: just update project time.
          // activeSrcPath will change → key= remounts video → onLoadedMetadata auto-plays.
          setPreviewTime(targetTime)
          return
        } else {
          // Past last clip — stop
          vid.pause()
          setPlaying(false)
          wasPlayingRef.current = false
          return
        }
      }
    }

    setPreviewTime(projTime)
  }, [projectTimeToSourceTime, setPreviewTime, sourceTimeToProjectTime])

  const onLoadedMetadata = useCallback(() => {
    const vid = videoRef.current
    if (!vid) return
    if (vid.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) {
      setVideoLoading(false)
      sourceTransitionRef.current = null
    }
    // Read CURRENT state directly from the store — refs may be stale during
    // the same render cycle as a <video key=> remount. The "ref sync" effects
    // run AFTER the new element fires loadedmetadata, so they can't be trusted here.
    const state  = useStore.getState()
    setDuration(state.project?.duration_s ?? vid.duration)
    const pt     = state.previewTime
    const clip   = getVisibleVideoClips(state.project, state.trackStates).find(
      (c) => pt >= c.start_s && pt < c.end_s
    )
    const targetSrcTime = Math.max(0, Math.min(projectTimeToSourceTime(pt, clip ?? null), vid.duration))
    vid.currentTime = targetSrcTime
    vid.playbackRate = Math.max(0.05, playbackRate * Math.max(0.05, Number(clip?.speed_factor ?? 1)))
    // Auto-resume if the user was playing when the source changed
    if (wasPlayingRef.current) {
      vid.play().catch(() => {
        setPlaying(false)
        wasPlayingRef.current = false
      })
    }
  }, [playbackRate, projectTimeToSourceTime])

  const onEnded = useCallback(() => {
    // onEnded fires when the SOURCE FILE reaches its natural end.
    // onTimeUpdate normally handles cross-clip transitions, but onEnded is the
    // safety net when the file ends exactly at (or before) the last expected frame.
    // If there are more clips in the project (possibly from different sources),
    // advance the project timeline to the next clip start.
    const state = useStore.getState()
    const clips = getVisibleVideoClips(state.project, state.trackStates)
    const sorted = clips.slice().sort((a, b) => a.start_s - b.start_s)
    const curTime = previewTimeRef.current  // latest project time

    // Find the first clip that starts after (or right at) the current project time.
    // Allow a 0.5s tolerance so floating-point drift at clip boundaries is handled.
    const nextClip = sorted.find((c) => c.start_s > curTime - 0.5)
    const isNewClip = nextClip && (
      nextClip.start_s > curTime + 0.05 ||                         // clearly ahead
      resolvePreviewClipSource(nextClip, state.project?.videoPath) !== (activeSrcPathRef.current ?? '') // same boundary, different source
    )

    if (isNewClip) {
      // Advance to next clip; React re-render handles any source change + auto-play
      setPreviewTime(nextClip!.start_s)
      return
    }

    // Nothing left — loop or stop
    if (loop && sorted.length > 0) {
      setPreviewTime(sorted[0].start_s)
      return
    }
    setPlaying(false)
    wasPlayingRef.current = false
  }, [loop, setPreviewTime])

  // Keyboard shortcuts
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return

      const vid = videoRef.current

      // Undo / Redo (must check before letter keys)
      if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {
        e.preventDefault(); undo(); return
      }
      if ((e.ctrlKey || e.metaKey) && (e.key === 'y' || (e.key === 'z' && e.shiftKey))) {
        e.preventDefault(); redo(); return
      }
      // Copy / Paste
      if ((e.ctrlKey || e.metaKey) && e.key === 'c') {
        const { selectedClipId: cid } = useStore.getState()
        if (cid) { e.preventDefault(); copyClip(cid) }
        return
      }
      if ((e.ctrlKey || e.metaKey) && e.key === 'v') {
        e.preventDefault(); pasteClip(); return
      }
      // Fullscreen
      if (e.key === 'f' && !e.ctrlKey && !e.metaKey) {
        e.preventDefault()
        if (document.fullscreenEnabled) {
          try {
            if (!document.fullscreenElement) containerRef.current?.requestFullscreen()
            else document.exitFullscreen()
          } catch { /* ignore */ }
        }
        return
      }

      // J/K/L — professional transport shortcuts
      if (e.key === 'j' && !e.ctrlKey && !e.metaKey) {
        e.preventDefault()
        if (vid) {
          const projT = Math.max(0, sourceTimeToProjectTime(vid.currentTime) - 10)
          vid.currentTime = projectTimeToSourceTime(projT)
          setPreviewTime(projT)
        } else {
          setPreviewTime(Math.max(0, useStore.getState().previewTime - 10))
        }
        return
      }
      if (e.key === 'k' && !e.ctrlKey && !e.metaKey) {
        e.preventDefault()
        if (vid) { wasPlayingRef.current = false; vid.pause(); setPlaying(false) }
        else { wasPlayingRef.current = false; setPlaying(false) }
        return
      }
      if (e.key === 'l' && !e.ctrlKey && !e.metaKey) {
        e.preventDefault()
        if (vid) {
          if (vid.paused) {
            wasPlayingRef.current = true
            vid.play().catch(() => { wasPlayingRef.current = false })
            setPlaying(true)
          } else {
            const rates = [1, 1.5, 2, 4]
            const next = rates.find((r) => r > playbackRate) ?? 1
            vid.playbackRate = next * activeClipSpeedRef.current
            setPlaybackRate(next)
          }
        } else {
          wasPlayingRef.current = true
          setPlaying(true)
        }
        return
      }

      if (e.code === 'Space') {
        e.preventDefault()
        if (vid) {
          if (vid.paused) {
            // Same gap-jump logic as togglePlay: if no clip at playhead, jump to next
            const state = useStore.getState()
            const clips = getPlayableTimelineClips(state.project, state.trackStates)
            const pt    = state.previewTime
            const here  = clips.find((c) => pt >= c.start_s && pt < c.end_s)
            if (!here) {
              const sorted = clips.slice().sort((a, b) => a.start_s - b.start_s)
              const next   = sorted.find((c) => c.start_s > pt)
              if (!next) return
              wasPlayingRef.current = true
              setPlaying(true)
              setPreviewTime(next.start_s)
              return
            }
            wasPlayingRef.current = true
            vid.play().catch(() => { wasPlayingRef.current = false })
            setPlaying(true)
          } else {
            wasPlayingRef.current = false
            vid.pause()
            setPlaying(false)
          }
        } else {
          const state = useStore.getState()
          if (playing) {
            wasPlayingRef.current = false
            setPlaying(false)
            return
          }
          const clips = getPlayableTimelineClips(state.project, state.trackStates)
          const pt = state.previewTime
          const here = clips.find((c) => pt >= c.start_s && pt < c.end_s)
          const next = here ?? clips.find((c) => c.start_s > pt) ?? clips[0]
          if (!next) return
          wasPlayingRef.current = true
          setPlaying(true)
          if (!here) setPreviewTime(next.start_s)
        }
      } else if (
        e.altKey
        && selectedActiveOverlayClip
        && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)
      ) {
        e.preventDefault()
        const step = e.shiftKey ? 10 : 1
        const curX = Number(selectedActiveOverlayClip.position_x ?? 0)
        const curY = Number(selectedActiveOverlayClip.position_y ?? 0)
        if (e.key === 'ArrowLeft') {
          updateClip(selectedActiveOverlayClip.id, { position_x: curX - step })
        } else if (e.key === 'ArrowRight') {
          updateClip(selectedActiveOverlayClip.id, { position_x: curX + step })
        } else if (e.key === 'ArrowUp') {
          updateClip(selectedActiveOverlayClip.id, { position_y: curY - step })
        } else if (e.key === 'ArrowDown') {
          updateClip(selectedActiveOverlayClip.id, { position_y: curY + step })
        }
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault()
        if (vid) {
          const projT = Math.max(0, sourceTimeToProjectTime(vid.currentTime) - (e.shiftKey ? 5 : 0.1))
          vid.currentTime = projectTimeToSourceTime(projT)
          setPreviewTime(projT)
        } else {
          setPreviewTime(Math.max(0, useStore.getState().previewTime - (e.shiftKey ? 5 : 0.1)))
        }
      } else if (e.key === 'ArrowRight') {
        e.preventDefault()
        if (vid) {
          const total = useStore.getState().project?.duration_s ?? duration
          const projT = Math.min(total, sourceTimeToProjectTime(vid.currentTime) + (e.shiftKey ? 5 : 0.1))
          vid.currentTime = projectTimeToSourceTime(projT)
          setPreviewTime(projT)
        } else {
          setPreviewTime(Math.min(dur, useStore.getState().previewTime + (e.shiftKey ? 5 : 0.1)))
        }
      } else if (e.key === 'Home') {
        e.preventDefault()
        if (vid) { vid.currentTime = projectTimeToSourceTime(0); setPreviewTime(0) }
        else setPreviewTime(0)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [duration, playbackRate, projectTimeToSourceTime, setPreviewTime, sourceTimeToProjectTime, undo, redo, copyClip, pasteClip, selectedActiveOverlayClip, updateClip])

  const togglePlay = useCallback(() => {
    if (playing) {
      const vid = videoRef.current
      if (vid) vid.pause()
      wasPlayingRef.current = false
      setPlaying(false)
      return
    }
    // Before playing: verify there's a clip at current playhead.
    // If we're in an intentional gap, keep playback moving through it.
    const state = useStore.getState()
    const clips = getVisibleVideoClips(state.project, state.trackStates)
    const pt    = state.previewTime
    const here  = clips.find((c) => pt >= c.start_s && pt < c.end_s)
    if (!here) {
      const sorted = clips.slice().sort((a, b) => a.start_s - b.start_s)
      const next   = sorted.find((c) => c.start_s > pt)
      const total = state.project?.duration_s ?? duration
      if (next && pt < total - 0.01) {
        wasPlayingRef.current = true
        setPlaying(true)
        return
      }
      if (!next) {
        // No content after current playhead — try the first clip (loop-back)
        const first = sorted[0]
        if (!first) return   // no clips at all
        wasPlayingRef.current = true
        setPlaying(true)
        setPreviewTime(first.start_s)
        return
      }
      // Jump to next clip start. React re-renders → activeVideoClip updates →
      // <video key=source> may remount → onLoadedMetadata fires + auto-plays
      // (because wasPlayingRef is true). For same-source jumps, the effect
      // below ([previewTime, playing]) calls vid.play() directly.
      wasPlayingRef.current = true
      setPlaying(true)
      setPreviewTime(next.start_s)
      return
    }
    // Clip at current time — just play
    const vid = videoRef.current
    if (!vid) {
      // Element not mounted yet (race) — set intent, the load effect handles it
      wasPlayingRef.current = true
      setPlaying(true)
      return
    }
    wasPlayingRef.current = true
    vid.play().catch(() => { setPlaying(false); wasPlayingRef.current = false })
    setPlaying(true)
  }, [duration, playing, setPreviewTime])

  // After a gap-jump (or any case where `playing=true` but vid is paused), kick play.
  // This runs after React re-renders following setPreviewTime+setPlaying,
  // and handles the same-source case where <video> doesn't remount.
  useEffect(() => {
    const vid = videoRef.current
    if (!vid) return
    if (!playing) return
    if (!wasPlayingRef.current) return
    if (!activeVideoClip) return
    if (!vid.paused) return
    vid.play().catch(() => {
      setPlaying(false)
      wasPlayingRef.current = false
    })
  }, [playing, activeVideoClip, previewTime])

  const skipBack = useCallback(() => {
    const vid = videoRef.current
    if (!vid) {
      setPreviewTime(Math.max(0, useStore.getState().previewTime - 5))
      return
    }
    const projT = Math.max(0, sourceTimeToProjectTime(vid.currentTime) - 5)
    vid.currentTime = projectTimeToSourceTime(projT)
    setPreviewTime(projT)
  }, [projectTimeToSourceTime, setPreviewTime, sourceTimeToProjectTime])

  const skipForward = useCallback(() => {
    const vid = videoRef.current
    if (!vid) {
      setPreviewTime(Math.min(duration, useStore.getState().previewTime + 5))
      return
    }
    const projT = Math.min(duration, sourceTimeToProjectTime(vid.currentTime) + 5)
    vid.currentTime = projectTimeToSourceTime(projT)
    setPreviewTime(projT)
  }, [duration, projectTimeToSourceTime, setPreviewTime, sourceTimeToProjectTime])

  const seekTo = useCallback((t: number) => {
    seekingByUser.current = true
    const vid = videoRef.current
    // Translate project time → source file time before seeking the video element
    if (vid) vid.currentTime = projectTimeToSourceTime(t)
    setPreviewTime(t)
    requestAnimationFrame(() => { seekingByUser.current = false })
  }, [projectTimeToSourceTime, setPreviewTime])

  const toggleMute = useCallback(() => {
    const vid = videoRef.current
    if (!vid) return
    vid.muted = !muted
    setMuted(!muted)
  }, [muted])

  const onVolumeChange = useCallback((v: number) => {
    const vid = videoRef.current
    if (vid) vid.volume = v / 100
    setVolume(v)
  }, [])

  const onRateChange = useCallback((rate: number) => {
    const vid = videoRef.current
    if (vid) vid.playbackRate = rate * activeClipSpeedRef.current
    setPlaybackRate(rate)
  }, [])

  const refreshPreview = useCallback(() => {
    const vid = videoRef.current
    const shouldResume = !!vid && !vid.paused
    wasPlayingRef.current = shouldResume
    if (refreshedVideoSrc) setVideoLoading(true)
    setPreviewRefreshNonce((n) => n + 1)
  }, [refreshedVideoSrc])

  const toggleFullscreen = useCallback(() => {
    // DOM Fullscreen API is unsupported in pywebview/WebView2 — guard before calling
    if (!document.fullscreenEnabled) return
    try {
      if (!document.fullscreenElement) {
        containerRef.current?.requestFullscreen()
      } else {
        document.exitFullscreen()
      }
    } catch { /* ignore — pywebview may not support this */ }
  }, [])

  const fmt = (s: number) => {
    const m = Math.floor(s / 60)
    const sec = (s % 60).toFixed(1)
    return `${m}:${sec.padStart(4, '0')}`
  }

  const dur = project?.duration_s || duration || 0
  const frameRatio = aspectRatioNumber(project?.aspect_ratio)
  const fitPad = 18
  const fitW = Math.max(1, viewportSize.width - fitPad * 2)
  const fitH = Math.max(1, viewportSize.height - fitPad * 2)
  const availableRatio = fitW / fitH
  const frameWidth = availableRatio > frameRatio ? fitH * frameRatio : fitW
  const frameHeight = availableRatio > frameRatio ? fitH : fitW / frameRatio
  const hasOutOfFrameTransform =
    Math.abs(pvScale - 1) > 0.001 ||
    Math.abs(pvPosX) > 0.5 ||
    Math.abs(pvPosY) > 0.5 ||
    Math.abs(pvRotation) > 0.5 ||
    (pvCropTop + pvCropRight + pvCropBottom + pvCropLeft) > 0.01

  return (
    <div className="flex flex-col bg-black h-full min-h-0 select-none">
      {/* Video canvas */}
      <div ref={containerRef} className="flex-1 flex items-center justify-center bg-bg min-h-0 overflow-hidden relative">
        {project ? (
          <div
            className="relative bg-black rounded overflow-hidden shadow-2xl"
            style={{
              aspectRatio: aspectRatioToCss(project.aspect_ratio),
              width:       frameWidth,
              height:      frameHeight,
              maxWidth:    `calc(100% - ${fitPad * 2}px)`,
              maxHeight:   `calc(100% - ${fitPad * 2}px)`,
              outline:     hasOutOfFrameTransform ? '2px solid rgba(250, 204, 21, 0.95)' : '1px solid rgba(255,255,255,0.18)',
              outlineOffset: hasOutOfFrameTransform ? 4 : 0,
              transition:  'width 0.16s ease, height 0.16s ease, outline-color 0.12s ease, outline-offset 0.12s ease',
            }}
          >
            {/* H.265 proxy transcoding indicator */}
            {activeProxyPending && (
              <div className="absolute top-2 right-2 z-30 flex items-center gap-1.5 bg-black/70 text-yellow-300 text-[10px] px-2 py-1 rounded-md border border-yellow-500/30">
                <svg className="animate-spin w-3 h-3" viewBox="0 0 24 24" fill="none">
                  <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeOpacity="0.3"/>
                  <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round"/>
                </svg>
                Preparando preview H.265…
              </div>
            )}
            {nextVideoSrc && (
              <video
                key={nextVideoSrc}
                src={nextVideoSrc}
                preload="auto"
                muted
                playsInline
                aria-hidden="true"
                style={{ display: 'none' }}
              />
            )}
            {activeProxyError && (
              <div className="absolute top-2 right-2 z-30 text-red-400 text-[10px] bg-black/70 px-2 py-1 rounded-md border border-red-500/30">
                Codec não suportado — instale HEVC Codec (Microsoft Store)
              </div>
            )}

            {refreshedVideoSrc ? (
              // Outer: handles scale + rotation + crop-clip
              <div
                className="w-full h-full flex items-center justify-center overflow-hidden"
                style={{
                  transform: pvTransform,
                  clipPath:  pvClipPath,
                  transition: 'transform 0.05s, clip-path 0.05s',
                  opacity:   videoHidden ? 0 : 1,
                }}
              >
                <video
                  key={refreshedVideoSrc}
                  ref={videoRef}
                  src={refreshedVideoSrc}
                  className="w-full h-full object-contain"
                  preload="metadata"
                  // preload="metadata" was correct — preload="auto" forced the
                  // browser to download the whole file upfront, freezing the
                  // preview for minutes on long videos. The backend already
                  // supports HTTP Range requests so seek/play streams on demand.
                  onTimeUpdate={onTimeUpdate}
                  onLoadedMetadata={onLoadedMetadata}
                  onEnded={onEnded}
                  onPlay={() => setPlaying(true)}
                  onPause={() => {
                    if (!sourceTransitionRef.current) setPlaying(false)
                  }}
                  onError={(e) => {
                    setVideoLoading(false)
                    sourceTransitionRef.current = null
                    const code = e.currentTarget.error?.code
                    if (code === 4 && activeSrcPath) {
                      requestForcedPreviewProxy(activeSrcPath)
                    }
                    setVideoError(`Falha ao carregar preview${code ? ` (codigo ${code})` : ''}. Tentando proxy quando disponivel.`)
                  }}
                  onCanPlay={() => { setVideoLoading(false); sourceTransitionRef.current = null; setVideoError(null) }}
                  onLoadedData={() => { setVideoLoading(false); sourceTransitionRef.current = null; setVideoError(null) }}
                  onPlaying={() => { setVideoLoading(false); sourceTransitionRef.current = null; setVideoError(null) }}
                  onSeeked={() => { setVideoLoading(false); sourceTransitionRef.current = null }}
                  onWaiting={(e) => {
                    if (e.currentTarget.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
                      setVideoLoading(true)
                    }
                  }}
                  style={{
                    filter:  pvFilter,
                    opacity: (pvOpacity * pvAnimOpacity !== 1)
                      ? pvOpacity * pvAnimOpacity
                      : undefined,
                    transition: 'filter 0.05s, opacity 0.05s',
                  }}
                />
              </div>
            ) : (
              <div className="absolute inset-0 flex items-center justify-center text-text-dim text-sm">
                Preview &mdash; {fmt(previewTime)}
              </div>
            )}

            {/* Loading spinner — shown while the video source is being fetched/decoded */}
            {transitionPreview && (
              <TransitionPreviewOverlay
                clip={transitionPreview.clip}
                src={transitionPreview.src}
                previewTime={previewTime}
                progress={transitionPreview.progress}
                transition={transitionPreview.transition}
              />
            )}

            {videoLoading && (
              <div className="absolute inset-0 z-40 flex flex-col items-center justify-center gap-2 bg-black/60 pointer-events-none">
                <svg className="w-8 h-8 animate-spin text-accent" viewBox="0 0 24 24" fill="none">
                  <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeOpacity="0.25"/>
                  <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round"/>
                </svg>
                <span className="text-[11px] text-text-muted">Carregando vídeo…</span>
              </div>
            )}

            {/* Imported music tracks — play independently of the <video>.
                Each music clip has its own <audio> element so deleting the
                video clip doesn't stop the background music. */}
            {videoError && !videoLoading && (
              <div className="absolute left-3 right-3 bottom-3 z-40 rounded-md border border-red-500/35 bg-black/80 px-3 py-2 text-[11px] text-red-200">
                {videoError}
              </div>
            )}

            {musicClipEntries
              .map(({ clip: c, muted }) => (
                <MusicPlayer
                  key={`${c.id}:${previewRefreshNonce}`}
                  clip={c}
                  previewTime={previewTime}
                  isPlaying={playing}
                  trackMuted={muted}
                  refreshNonce={previewRefreshNonce}
                />
              ))}

            {/* Overlay track image clips */}
            {overlayImageClips
              .filter((c) => previewTime >= c.start_s && previewTime < c.end_s && c.clip_type === 'image' && c.source_path)
              .map((c) => {
                const fx = applyMotionKeyframe(c, previewTime) ?? c
                return (
                <img
                  key={`${c.id}:${previewRefreshNonce}`}
                  src={withPreviewReload(`${API}/api/serve-file?path=${encodeURIComponent(c.source_path!)}`, previewRefreshNonce)}
                  draggable={false}
                  alt=""
                  onMouseDown={(e) => startOverlayPointerInteraction(e, c)}
                  style={{
                    position: 'absolute',
                    inset: 0,
                    width: '100%',
                    height: '100%',
                    objectFit: 'contain',
                    opacity: (fx.opacity_pct ?? 100) / 100,
                    transform: `translate(${Number(fx.position_x ?? 0)}px, ${Number(fx.position_y ?? 0)}px) scale(${(fx.scale_pct ?? 100) / 100}) rotate(${fx.rotation_deg ?? 0}deg)`,
                    zIndex: 15 + Math.max(0, Number(c.z_order ?? 0)),
                    pointerEvents: 'auto',
                    outline: selectedClipId === c.id ? '1px dashed rgba(139,107,255,0.7)' : 'none',
                    outlineOffset: selectedClipId === c.id ? '-1px' : undefined,
                  }}
                />
              )})
            }

            {/* Overlay track sticker clips (emoji-style figurinhas) */}
            {overlayImageClips
              .filter((c) => previewTime >= c.start_s && previewTime < c.end_s && c.clip_type === 'sticker')
              .map((c) => {
                const fx = applyMotionKeyframe(c, previewTime) ?? c
                const bgColor = c.text_background_color ?? '#111827'
                const bgAlpha = Number(c.text_background_alpha ?? 0.9)
                return (
                  <div
                    key={`${c.id}:${previewRefreshNonce}`}
                    onMouseDown={(e) => startOverlayPointerInteraction(e, c)}
                    style={{
                      position: 'absolute',
                      left: '50%',
                      top: '50%',
                      transform: `translate(-50%, -50%) translate(${Number(fx.position_x ?? 0)}px, ${Number(fx.position_y ?? 0)}px) scale(${(fx.scale_pct ?? 100) / 100}) rotate(${fx.rotation_deg ?? 0}deg)`,
                      zIndex: 15 + Math.max(0, Number(c.z_order ?? 0)),
                      opacity: (fx.opacity_pct ?? 100) / 100,
                      pointerEvents: 'auto',
                      outline: selectedClipId === c.id ? '1px dashed rgba(139,107,255,0.7)' : 'none',
                      outlineOffset: selectedClipId === c.id ? '1px' : undefined,
                      borderRadius: 10,
                      padding: '10px 12px',
                      background: `rgba(0,0,0,0.35), linear-gradient(145deg, ${bgColor}, #0f172a)`,
                      boxShadow: '0 10px 26px rgba(0,0,0,0.38)',
                    }}
                  >
                    <span
                      style={{
                        fontSize: `${Math.max(26, (c.text_size_pct ?? 120) * 0.36)}px`,
                        lineHeight: 1,
                        color: c.text_color ?? '#ffffff',
                        textShadow: '0 2px 6px rgba(0,0,0,0.4)',
                        filter: bgAlpha < 1 ? `opacity(${bgAlpha})` : undefined,
                      }}
                    >
                      {c.text_overlay || '✨'}
                    </span>
                  </div>
                )
              })}

            {/* Overlay track video clips */}
            {overlayVideoClips
              .filter((c) => previewTime >= c.start_s && previewTime < c.end_s)
              .map((c) => (
                <OverlayVideoClip
                  key={`${c.id}:${previewRefreshNonce}`}
                  clip={c}
                  project={project}
                  previewTime={previewTime}
                  isPlaying={playing}
                  refreshNonce={previewRefreshNonce}
                  isSelected={selectedClipId === c.id}
                  onPointerInteraction={startOverlayPointerInteraction}
                />
              ))
            }

            {selectedActiveOverlayClip && (
              <OverlayTransformHandles
                clip={selectedActiveOverlayClip}
                onUpdate={(patch) => updateClip(selectedActiveOverlayClip.id, patch)}
              />
            )}

            {(overlaySnapGuides.xKey || overlaySnapGuides.yKey) && (
              <div className="absolute inset-0 z-[95] pointer-events-none">
                {overlaySnapGuides.xKey && (
                  <div
                    className="absolute top-0 bottom-0"
                    style={{
                      width: 1,
                      background: overlaySnapGuides.xKey === 'overlay'
                        ? 'rgba(139,107,255,0.75)'
                        : 'rgba(34,212,188,0.65)',
                      left: overlaySnapGuides.xKey === 'overlay'
                        ? `calc(50% + ${Number(overlaySnapGuides.xValue ?? 0)}px)`
                        : overlaySnapGuides.xKey === 'left'
                          ? 0
                          : overlaySnapGuides.xKey === 'right'
                            ? '100%'
                            : '50%',
                      transform: 'translateX(-0.5px)',
                    }}
                  />
                )}
                {overlaySnapGuides.yKey && (
                  <div
                    className="absolute left-0 right-0"
                    style={{
                      height: 1,
                      background: overlaySnapGuides.yKey === 'overlay'
                        ? 'rgba(139,107,255,0.75)'
                        : 'rgba(34,212,188,0.65)',
                      top: overlaySnapGuides.yKey === 'overlay'
                        ? `calc(50% + ${Number(overlaySnapGuides.yValue ?? 0)}px)`
                        : overlaySnapGuides.yKey === 'top'
                          ? 0
                          : overlaySnapGuides.yKey === 'bottom'
                            ? '100%'
                            : '50%',
                      transform: 'translateY(-0.5px)',
                    }}
                  />
                )}
              </div>
            )}

            {selectedClip && (
              <div className="absolute left-2 top-2 z-[96] flex items-center gap-1.5 rounded-md border border-border bg-black/70 px-2 py-1">
                <button
                  title="Adicionar texto no playhead"
                  className="text-text-muted hover:text-white transition-colors"
                  onClick={() => {
                    const t = Math.max(0, previewTime)
                    addTextClip(t, t + 3, 'Novo texto')
                  }}
                >
                  <Type size={12} />
                </button>
                <button
                  title="Duplicar seleção no playhead (Ctrl+D)"
                  className="text-text-muted hover:text-white transition-colors"
                  onClick={() => duplicateSelectionAtPlayhead()}
                >
                  <Copy size={12} />
                </button>
                <button
                  title="Dividir no playhead"
                  className="text-text-muted hover:text-white transition-colors disabled:opacity-35 disabled:cursor-not-allowed"
                  disabled={!selectedCanSplit}
                  onClick={() => {
                    if (!selectedClip) return
                    splitClip(selectedClip.id, previewTime)
                  }}
                >
                  <Scissors size={12} />
                </button>
                <button
                  title="Excluir clip selecionado"
                  className="text-text-muted hover:text-red-300 transition-colors"
                  onClick={() => {
                    if (!selectedClip) return
                    deleteClip(selectedClip.id)
                  }}
                >
                  <Trash2 size={12} />
                </button>
                <div className="w-px h-4 bg-border mx-0.5" />
                <button
                  title="Trazer camada para frente (Alt+Seta cima)"
                  className="text-text-muted hover:text-white transition-colors disabled:opacity-35 disabled:cursor-not-allowed"
                  disabled={!selectedCanLayerMove}
                  onClick={() => bumpSelectedLayer(1)}
                >
                  <ArrowUp size={12} />
                </button>
                <button
                  title="Enviar camada para trás (Alt+Seta baixo)"
                  className="text-text-muted hover:text-white transition-colors disabled:opacity-35 disabled:cursor-not-allowed"
                  disabled={!selectedCanLayerMove}
                  onClick={() => bumpSelectedLayer(-1)}
                >
                  <ArrowDown size={12} />
                </button>
                {selectedClipActiveAtPlayhead && (
                  <span className="text-[10px] text-text-dim ml-1">
                    {selectedClip.label}
                  </span>
                )}
              </div>
            )}

            {/* Text track overlays — drag to reposition */}
            {!textHidden && project?.text_track.clips
              .filter((c) => previewTime >= c.start_s && previewTime < c.end_s && c.text_overlay)
              .map((c) => (
                <DraggableTextOverlay
                  key={`${c.id}:${previewRefreshNonce}`}
                  clip={c}
                  previewTime={previewTime}
                  onMoveTo={(x, y) => updateClip(c.id, {
                    text_position_x_pct: Math.max(-50, Math.min(50,  x)),
                    text_position_y_pct: Math.max(0,   Math.min(100, y)),
                  })}
                />
              ))
            }
            {!textHidden && captionPreviewEnabled && captionPreviewText?.trim() && (
              <CaptionDraftOverlay
                text={captionPreviewText}
                stylePatch={captionPreviewStyle ?? {}}
              />
            )}

            {/* Center play overlay on pause (hidden while loading) */}
            {!playing && refreshedVideoSrc && !videoLoading && (
              <button
                onClick={togglePlay}
                className="absolute inset-0 flex items-center justify-center bg-black/20 hover:bg-black/30 transition-colors group"
              >
                <div className="w-14 h-14 rounded-full bg-black/60 flex items-center justify-center group-hover:bg-black/80 transition-colors">
                  <Play size={24} className="text-white ml-1" />
                </div>
              </button>
            )}

            {frameGuideVisible && (
              <ExportFrameOverlay active={hasOutOfFrameTransform} label={project.aspect_ratio ?? '16:9'} />
            )}
          </div>
        ) : (
          <div className="text-center text-text-dim">
            <div className="text-6xl mb-3 opacity-20">&#9654;</div>
            <p className="text-sm">Abra um vídeo para começar</p>
          </div>
        )}
      </div>

      {/* Controls bar */}
      <div className="flex items-center gap-2 px-4 py-2 bg-bg-panel border-t border-border flex-shrink-0">
        {/* Transport */}
        <button onClick={skipBack} className="text-text-muted hover:text-white transition-colors" title="Voltar 5s">
          <SkipBack size={16} />
        </button>
        <button
          onClick={togglePlay}
          className="w-8 h-8 rounded-full bg-white text-black flex items-center justify-center hover:bg-gray-200 transition-colors flex-shrink-0"
          title={playing ? 'Pausar (Space)' : 'Reproduzir (Space)'}
        >
          {playing ? <Pause size={14} /> : <Play size={14} className="ml-0.5" />}
        </button>
        <button onClick={skipForward} className="text-text-muted hover:text-white transition-colors" title="Avançar 5s">
          <SkipForward size={16} />
        </button>

        {/* Time */}
        <span className="text-[11px] text-text-muted ml-2 tabular-nums">
          {fmt(previewTime)} / {fmt(dur)}
        </span>

        {/* Seek bar */}
        <div className="flex-1 mx-2">
          <input
            type="range" min={0} max={dur || 1} step={0.01} value={previewTime}
            onChange={(e) => seekTo(+e.target.value)}
            className="w-full h-1 accent-accent cursor-pointer"
          />
        </div>

        {/* Volume */}
        <button onClick={toggleMute} className="text-text-muted hover:text-white transition-colors" title={muted ? 'Ativar som' : 'Silenciar'}>
          {muted ? <VolumeX size={14} /> : <Volume2 size={14} />}
        </button>
        <input
          type="range" min={0} max={100} step={1} value={muted ? 0 : volume}
          onChange={(e) => onVolumeChange(+e.target.value)}
          className="w-16 h-1 accent-accent cursor-pointer"
          title="Volume"
        />

        {/* Playback speed */}
        <div className="flex items-center gap-0.5 ml-1">
          {([0.5, 1, 1.5, 2] as const).map((r) => (
            <button
              key={r}
              onClick={() => onRateChange(r)}
              className={`px-1.5 py-0.5 rounded text-[9px] font-medium transition-colors ${
                playbackRate === r
                  ? 'bg-accent text-white'
                  : 'text-text-dim hover:text-white hover:bg-bg-surface'
              }`}
              title={`Velocidade ${r}×`}
            >
              {r}×
            </button>
          ))}
        </div>

        {/* Loop toggle */}
        <button
          onClick={() => setLoop((v) => !v)}
          title={loop ? 'Loop ativo — clique para desativar' : 'Loop desativado (L para play, K para pausar)'}
          className={`transition-colors ${loop ? 'text-accent' : 'text-text-muted hover:text-white'}`}
        >
          <Repeat size={14} />
        </button>

        <button onClick={refreshPreview} title="Atualizar preview manualmente" className="text-text-muted hover:text-white transition-colors">
          <RefreshCw size={14} />
        </button>

        <button
          onClick={() => setFrameGuideVisible((v) => !v)}
          title={frameGuideVisible ? 'Ocultar grade do enquadro' : 'Mostrar grade do enquadro'}
          className={`transition-colors ${frameGuideVisible ? 'text-accent' : 'text-text-muted hover:text-white'}`}
        >
          <Grid3X3 size={14} />
        </button>

        <button onClick={toggleFullscreen} title="Tela cheia (F)" className="text-text-muted hover:text-white transition-colors">
          <Maximize2 size={14} />
        </button>
      </div>
    </div>
  )
}

function TransitionPreviewOverlay({
  clip,
  src,
  previewTime,
  progress,
  transition,
}: {
  clip: Clip
  src: string
  previewTime: number
  progress: number
  transition: string
}) {
  const ref = useRef<HTMLVideoElement>(null)
  useEffect(() => {
    const vid = ref.current
    if (!vid) return
    const speed = Math.max(0.05, Number(clip.speed_factor ?? 1))
    const sourceStart = clip.start_s - (clip.source_offset_s ?? 0)
    const target = Math.max(0, sourceStart + Math.max(0, previewTime - clip.start_s) * speed)
    if (Number.isFinite(target) && Math.abs(vid.currentTime - target) > 0.12) {
      try { vid.currentTime = target } catch { /* ignore */ }
    }
  }, [clip, previewTime, src])

  const p = Math.max(0, Math.min(1, progress))
  const name = transition.toLowerCase()
  const style: CSSProperties = {
    position: 'absolute',
    inset: 0,
    pointerEvents: 'none',
    opacity: p,
    width: '100%',
    height: '100%',
    objectFit: 'contain',
    transformOrigin: 'center center',
  }
  if (name.includes('wipe')) {
    style.clipPath = name.includes('dir')
      ? `inset(0 0 0 ${Math.max(0, 100 - p * 100)}%)`
      : `inset(0 ${Math.max(0, 100 - p * 100)}% 0 0)`
  } else if (name.includes('zoom')) {
    style.transform = `scale(${1.08 - p * 0.08})`
    style.opacity = Math.min(1, p * 1.15)
  }

  return (
    <div className="absolute inset-0 z-20 overflow-hidden pointer-events-none">
      <video
        ref={ref}
        src={src}
        muted
        playsInline
        preload="metadata"
        style={style}
      />
    </div>
  )
}

function OverlayVideoClip({
  clip,
  project,
  previewTime,
  isPlaying,
  refreshNonce,
  isSelected,
  onPointerInteraction,
}: {
  clip: Clip
  project: ProjectState | null
  previewTime: number
  isPlaying: boolean
  refreshNonce: number
  isSelected: boolean
  onPointerInteraction: (e: React.MouseEvent, clip: Clip) => void
}) {
  const ref = useRef<HTMLVideoElement>(null)
  const source = clip.source_path ? ((project?.source_proxies?.[clip.source_path] || clip.source_path)) : null
  const src = source ? withPreviewReload(`${API}/api/serve-file?path=${encodeURIComponent(source)}`, refreshNonce) : null
  const localTime = Math.max(0, previewTime - clip.start_s)
  const fx = applyMotionKeyframe(clip, previewTime) ?? clip

  useEffect(() => {
    const v = ref.current
    if (!v) return
    if (Math.abs(v.currentTime - localTime) > 0.08) {
      try { v.currentTime = localTime } catch { /* ignore */ }
    }
    if (isPlaying) v.play().catch(() => {})
    else v.pause()
  }, [localTime, isPlaying, src])

  if (!src) return null
  return (
    <video
      ref={ref}
      src={src}
      muted
      playsInline
      preload="metadata"
      onMouseDown={(e) => onPointerInteraction(e, clip)}
      style={{
        position: 'absolute',
        inset: 0,
        width: '100%',
        height: '100%',
        objectFit: 'contain',
        opacity: (fx.opacity_pct ?? 100) / 100,
        transform: `translate(${Number(fx.position_x ?? 0)}px, ${Number(fx.position_y ?? 0)}px) scale(${(fx.scale_pct ?? 100) / 100}) rotate(${fx.rotation_deg ?? 0}deg)`,
        zIndex: 16 + Math.max(0, Number(clip.z_order ?? 0)),
        pointerEvents: 'auto',
        outline: isSelected ? '1px dashed rgba(139,107,255,0.7)' : 'none',
        outlineOffset: isSelected ? '-1px' : undefined,
      }}
    />
  )
}

// ── Draggable text overlay ─────────────────────────────────────────────────────
// ── Imported-music player ─────────────────────────────────────────────────────
// One <audio> element per imported music clip. Lives outside the <video> so
// deleting / pausing the video clip doesn't stop background music. Syncs its
// playback position from `previewTime` and obeys the global play/pause state.
function OverlayTransformHandles({
  clip,
  onUpdate,
}: {
  clip: Clip
  onUpdate: (patch: Partial<Clip>) => void
}) {
  const normalizeDeg = (v: number): number => {
    let a = v % 360
    if (a > 180) a -= 360
    if (a < -180) a += 360
    return Math.round(a * 10) / 10
  }

  const startScaleDrag = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    const rect = (e.currentTarget.parentElement as HTMLElement | null)?.getBoundingClientRect()
    if (!rect) return
    const startX = e.clientX
    const startY = e.clientY
    const startScale = Number(clip.scale_pct ?? 100)
    const centerX = rect.left + rect.width / 2 + Number(clip.position_x ?? 0)
    const centerY = rect.top + rect.height / 2 + Number(clip.position_y ?? 0)
    const startDist = Math.max(1, Math.hypot(startX - centerX, startY - centerY))

    const onMove = (mv: MouseEvent) => {
      const dist = Math.max(1, Math.hypot(mv.clientX - centerX, mv.clientY - centerY))
      const ratio = dist / startDist
      const next = Math.max(10, Math.min(400, Math.round(startScale * ratio)))
      onUpdate({ scale_pct: next })
    }
    const onUp = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [clip.position_x, clip.position_y, clip.scale_pct, onUpdate])

  const startRotateDrag = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    const rect = (e.currentTarget.parentElement as HTMLElement | null)?.getBoundingClientRect()
    if (!rect) return
    const startRot = Number(clip.rotation_deg ?? 0)
    const centerX = rect.left + rect.width / 2 + Number(clip.position_x ?? 0)
    const centerY = rect.top + rect.height / 2 + Number(clip.position_y ?? 0)
    const startA = Math.atan2(e.clientY - centerY, e.clientX - centerX)

    const onMove = (mv: MouseEvent) => {
      const currentA = Math.atan2(mv.clientY - centerY, mv.clientX - centerX)
      const deltaDeg = ((currentA - startA) * 180) / Math.PI
      onUpdate({ rotation_deg: normalizeDeg(startRot + deltaDeg) })
    }
    const onUp = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [clip.position_x, clip.position_y, clip.rotation_deg, onUpdate])

  return (
    <div
      className="absolute inset-0 pointer-events-none"
      style={{
        transform: `translate(${Number(clip.position_x ?? 0)}px, ${Number(clip.position_y ?? 0)}px) scale(${(clip.scale_pct ?? 100) / 100}) rotate(${clip.rotation_deg ?? 0}deg)`,
        transformOrigin: 'center center',
        zIndex: 120,
      }}
    >
      <div className="absolute inset-[8%] border border-dashed border-accent/70 rounded-[2px]" />
      <button
        onMouseDown={startRotateDrag}
        title="Girar overlay"
        className="pointer-events-auto absolute left-1/2 top-[6%] -translate-x-1/2 -translate-y-1/2 w-4 h-4 rounded-full bg-accent border border-white/70 shadow-sm cursor-grab active:cursor-grabbing"
      />
      <button
        onMouseDown={startScaleDrag}
        title="Escalar overlay"
        className="pointer-events-auto absolute right-[6%] bottom-[6%] translate-x-1/2 translate-y-1/2 w-4 h-4 rounded-full bg-white border border-accent shadow-sm cursor-nwse-resize"
      />
    </div>
  )
}

function ExportFrameOverlay({ active, label }: { active: boolean; label: string }) {
  const color = active ? 'rgba(250,204,21,0.9)' : 'rgba(255,255,255,0.38)'
  const line = active ? 'rgba(250,204,21,0.42)' : 'rgba(255,255,255,0.16)'
  const corner = active ? 18 : 14
  const thickness = active ? 2 : 1
  return (
    <div className="absolute inset-0 z-[70] pointer-events-none">
      <div
        className="absolute inset-0"
        style={{
          border: `1px solid ${color}`,
          backgroundImage:
            `linear-gradient(90deg, transparent calc(33.333% - 0.5px), ${line} calc(33.333% - 0.5px) calc(33.333% + 0.5px), transparent calc(33.333% + 0.5px)),
             linear-gradient(90deg, transparent calc(66.666% - 0.5px), ${line} calc(66.666% - 0.5px) calc(66.666% + 0.5px), transparent calc(66.666% + 0.5px)),
             linear-gradient(0deg, transparent calc(33.333% - 0.5px), ${line} calc(33.333% - 0.5px) calc(33.333% + 0.5px), transparent calc(33.333% + 0.5px)),
             linear-gradient(0deg, transparent calc(66.666% - 0.5px), ${line} calc(66.666% - 0.5px) calc(66.666% + 0.5px), transparent calc(66.666% + 0.5px))`,
          boxShadow: 'inset 0 0 0 1px rgba(0,0,0,0.35)',
        }}
      />
      {[
        'left-0 top-0 border-l border-t',
        'right-0 top-0 border-r border-t',
        'left-0 bottom-0 border-l border-b',
        'right-0 bottom-0 border-r border-b',
      ].map((klass) => (
        <div
          key={klass}
          className={`absolute ${klass}`}
          style={{
            width: corner,
            height: corner,
            borderColor: color,
            borderWidth: thickness,
          }}
        />
      ))}
      <div
        className="absolute left-2 top-2 rounded px-1.5 py-0.5 text-[8px] font-semibold tracking-wide"
        style={{
          color: active ? '#1f1600' : 'rgba(229,231,235,0.8)',
          background: active ? 'rgba(250,204,21,0.85)' : 'rgba(0,0,0,0.32)',
          border: active ? '1px solid rgba(0,0,0,0.28)' : '1px solid rgba(255,255,255,0.12)',
        }}
      >
        {active ? 'fora do enquadro' : label}
      </div>
    </div>
  )
}

function MusicPlayer({
  clip, previewTime, isPlaying, trackMuted, refreshNonce,
}: {
  clip: Clip
  previewTime: number
  isPlaying: boolean
  trackMuted: boolean
  refreshNonce: number
}) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const fxClip = applyMotionKeyframe(clip, previewTime) ?? clip
  const src = withPreviewReload(`${API}/api/serve-file?path=${encodeURIComponent(clip.source_path!)}`, refreshNonce)

  // Compute desired source-file time. Music clips have source_offset_s = clip.start_s
  // by default (imported at the playhead), but support source_offset_s explicitly.
  const offset = clip.source_offset_s ?? clip.start_s
  const inRange = previewTime >= clip.start_s && previewTime < clip.end_s
  const targetSrcTime = Math.max(0, previewTime - offset)

  // Apply mute + volume
  useEffect(() => {
    const a = audioRef.current
    if (!a) return
    a.muted  = trackMuted
    a.volume = Math.max(0, Math.min(1, (fxClip.volume_pct ?? 100) / 100))
  }, [trackMuted, fxClip.volume_pct])

  // Drive play/pause based on isPlaying + whether the playhead is inside the clip range
  useEffect(() => {
    const a = audioRef.current
    if (!a) return
    const shouldPlay = isPlaying && inRange && !trackMuted
    if (shouldPlay && a.paused) {
      a.play().catch(() => { /* user-gesture restriction etc. — ignore */ })
    } else if (!shouldPlay && !a.paused) {
      a.pause()
    }
  }, [isPlaying, inRange, trackMuted])

  // Keep <audio>.currentTime in sync with the playhead (only when far enough
  // off to be perceptible — avoid feedback loops with the audio's own timeupdate).
  useEffect(() => {
    const a = audioRef.current
    if (!a) return
    if (Math.abs(a.currentTime - targetSrcTime) > 0.2) {
      a.currentTime = targetSrcTime
    }
  }, [targetSrcTime])

  return (
    <audio
      ref={audioRef}
      src={src}
      preload="auto"
      style={{ display: 'none' }}
    />
  )
}

function applyMotionKeyframe(clip: Clip | null, previewTime: number): Clip | null {
  if (!clip) return null
  const kfs = (clip.motion_keyframes ?? []).slice().sort((a, b) => a.t - b.t)
  if (kfs.length === 0) return clip
  const localT = Math.max(0, Math.min(clip.end_s - clip.start_s, previewTime - clip.start_s))
  const pick = (v: number | undefined, fallback: number) => (typeof v === 'number' ? v : fallback)
  const mergeFrom = (kf: { position_x?: number; position_y?: number; scale_pct?: number; opacity_pct?: number; volume_pct?: number }) => ({
    ...clip,
    position_x: pick(kf.position_x, clip.position_x ?? 0),
    position_y: pick(kf.position_y, clip.position_y ?? 0),
    scale_pct: pick(kf.scale_pct, clip.scale_pct ?? 100),
    opacity_pct: pick(kf.opacity_pct, clip.opacity_pct ?? 100),
    volume_pct: pick(kf.volume_pct, clip.volume_pct ?? 100),
  })
  if (localT <= kfs[0].t) return mergeFrom(kfs[0])
  if (localT >= kfs[kfs.length - 1].t) return mergeFrom(kfs[kfs.length - 1])
  let a = kfs[0], b = kfs[kfs.length - 1]
  for (let i = 0; i < kfs.length - 1; i++) {
    if (localT >= kfs[i].t && localT <= kfs[i + 1].t) { a = kfs[i]; b = kfs[i + 1]; break }
  }
  const denom = Math.max(0.0001, b.t - a.t)
  const pRaw = (localT - a.t) / denom
  const p = applyEasingProgress(pRaw, b.easing ?? 'linear')
  const lerp = (x: number, y: number) => x + (y - x) * p
  const av = {
    position_x: pick(a.position_x, clip.position_x ?? 0),
    position_y: pick(a.position_y, clip.position_y ?? 0),
    scale_pct: pick(a.scale_pct, clip.scale_pct ?? 100),
    opacity_pct: pick(a.opacity_pct, clip.opacity_pct ?? 100),
    volume_pct: pick(a.volume_pct, clip.volume_pct ?? 100),
  }
  const bv = {
    position_x: pick(b.position_x, clip.position_x ?? 0),
    position_y: pick(b.position_y, clip.position_y ?? 0),
    scale_pct: pick(b.scale_pct, clip.scale_pct ?? 100),
    opacity_pct: pick(b.opacity_pct, clip.opacity_pct ?? 100),
    volume_pct: pick(b.volume_pct, clip.volume_pct ?? 100),
  }
  return {
    ...clip,
    position_x: lerp(av.position_x, bv.position_x),
    position_y: lerp(av.position_y, bv.position_y),
    scale_pct: lerp(av.scale_pct, bv.scale_pct),
    opacity_pct: lerp(av.opacity_pct, bv.opacity_pct),
    volume_pct: lerp(av.volume_pct, bv.volume_pct),
  }
}

function applyEasingProgress(p: number, easing: 'linear' | 'ease-in' | 'ease-out' | 'ease-in-out'): number {
  const x = Math.max(0, Math.min(1, p))
  if (easing === 'ease-in') return x * x
  if (easing === 'ease-out') return 1 - (1 - x) * (1 - x)
  if (easing === 'ease-in-out') return x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2
  return x
}

/** Append CSS filter strings for an adjustment-layer clip into `parts`.
 *  Only writes a function when the value is non-neutral, so the filter
 *  string stays short when the user only tweaks one slider. */
function appendAdjustmentFilter(parts: string[], adj: Clip): void {
  const br   = 1 + (adj.brightness  ?? 0) / 100
  const co   = 1 + (adj.contrast    ?? 0) / 100
  const sa   = 1 + (adj.saturation  ?? 0) / 100
  const hue  = adj.hue ?? 0
  const exp  = 1 + (adj.exposure   ?? 0) / 100
  const temp = (adj.temperature  ?? 0) / 100
  if (br !== 1) parts.push(`brightness(${(br * exp).toFixed(3)})`)
  else if (exp !== 1) parts.push(`brightness(${exp.toFixed(3)})`)
  if (co  !== 1) parts.push(`contrast(${co.toFixed(3)})`)
  if (sa  !== 1) parts.push(`saturate(${sa.toFixed(3)})`)
  if (hue !== 0) parts.push(`hue-rotate(${hue}deg)`)
  if (temp >  0) parts.push(`sepia(${(temp * 0.5).toFixed(3)})`)
  if (temp <  0) parts.push(`hue-rotate(${(temp * 30).toFixed(0)}deg)`)
  appendBlurFilter(parts, adj)
}

function appendBlurFilter(parts: string[], clip: Clip): void {
  const blurType = clip.blur_type ?? 'none'
  const intensity = Math.max(0, Math.min(100, Number(clip.blur_intensity ?? 0)))
  if (blurType === 'none' || intensity <= 0.5) return
  const px = blurType === 'pixelate'
    ? Math.min(2.4, 0.4 + intensity * 0.018)
    : Math.min(24, intensity * (blurType === 'box' ? 0.24 : 0.18))
  if (px <= 0.05) return
  parts.push(`blur(${px.toFixed(1)}px)`)
}

/** Compute live preview transform + opacity contribution from clip animations.
 *
 *  Returns null when the clip isn't currently in an animation window.
 *  Otherwise returns { transform, opacity } strings that callers compose
 *  with their own transform/opacity values.
 *
 *  We use easeOutQuad / easeInQuad for natural motion.
 */
export function computeAnimationStyle(
  previewTime: number,
  clip: { start_s: number; end_s: number; animation_in?: string; animation_out?: string;
          animation_in_duration_s?: number; animation_out_duration_s?: number },
): { transform: string; opacity: number } | null {
  const animIn  = clip.animation_in ?? 'none'
  const animOut = clip.animation_out ?? 'none'
  const durIn   = clip.animation_in_duration_s  ?? 0.5
  const durOut  = clip.animation_out_duration_s ?? 0.5

  const tFromStart = previewTime - clip.start_s
  const tFromEnd   = clip.end_s   - previewTime

  // Active animation: entrance has priority (when both windows overlap on short clips)
  if (animIn !== 'none' && tFromStart >= 0 && tFromStart < durIn) {
    const p = Math.max(0, Math.min(1, tFromStart / durIn))     // 0 → 1
    return applyAnim(animIn, 1 - p)   // amount 1 (offscreen) → 0 (in place)
  }
  if (animOut !== 'none' && tFromEnd >= 0 && tFromEnd < durOut) {
    const p = Math.max(0, Math.min(1, tFromEnd / durOut))      // 1 → 0 as clip ends
    return applyAnim(animOut, 1 - p)
  }
  return null
}

function applyAnim(kind: string, amount: number): { transform: string; opacity: number } {
  // amount: 0 = animation finished (clip fully in place), 1 = animation start
  // easeOutQuad for smoother motion: y = 1 - (1-x)²
  const eased = 1 - (1 - (1 - amount)) ** 2   // == 1 - amount², increasing
  const offsetPct = amount * 100               // 0..100%
  switch (kind) {
    case 'fade':         return { transform: 'none',                                opacity: 1 - amount }
    case 'typewriter':   return { transform: 'none',                                opacity: 1 }
    case 'draw-reveal':  return { transform: `translateY(${amount * 8}px)`,          opacity: 1 - amount * 0.35 }
    case 'magic-sparkle': return { transform: `scale(${0.92 + 0.08 * eased})`,       opacity: eased }
    case 'slide-left':   return { transform: `translateX(${-offsetPct}%)`,          opacity: 1 }
    case 'slide-right':  return { transform: `translateX(${ offsetPct}%)`,          opacity: 1 }
    case 'slide-up':     return { transform: `translateY(${-offsetPct}%)`,          opacity: 1 }
    case 'slide-down':   return { transform: `translateY(${ offsetPct}%)`,          opacity: 1 }
    case 'zoom-in':      return { transform: `scale(${0.3 + 0.7 * eased})`,         opacity: eased }
    case 'zoom-out':     return { transform: `scale(${1.5 - 0.5 * eased})`,         opacity: eased }
    default:             return { transform: 'none', opacity: 1 }
  }
}

function visibleTextForAnimation(clip: Clip, previewTime: number): string {
  const text = clip.text_overlay || ''
  if (!text) return text
  const localIn = previewTime - clip.start_s
  const localOut = clip.end_s - previewTime
  const inKind = clip.animation_in ?? 'none'
  const outKind = clip.animation_out ?? 'none'
  const inDur = Math.max(0.1, Number(clip.animation_in_duration_s ?? 0.5))
  const outDur = Math.max(0.1, Number(clip.animation_out_duration_s ?? 0.5))
  const revealKinds = new Set(['typewriter', 'draw-reveal'])
  if (revealKinds.has(inKind) && localIn >= 0 && localIn < inDur) {
    const p = Math.max(0, Math.min(1, localIn / inDur))
    return text.slice(0, Math.max(1, Math.ceil(text.length * p)))
  }
  if (revealKinds.has(outKind) && localOut >= 0 && localOut < outDur) {
    const p = Math.max(0, Math.min(1, localOut / outDur))
    return text.slice(0, Math.max(0, Math.ceil(text.length * p)))
  }
  return text
}

function isTextMagicActive(clip: Clip, previewTime: number): boolean {
  const inDur = Math.max(0.1, Number(clip.animation_in_duration_s ?? 0.5))
  const outDur = Math.max(0.1, Number(clip.animation_out_duration_s ?? 0.5))
  return (
    clip.animation_in === 'magic-sparkle'
    && previewTime >= clip.start_s
    && previewTime < clip.start_s + inDur
  ) || (
    clip.animation_out === 'magic-sparkle'
    && previewTime <= clip.end_s
    && previewTime > clip.end_s - outDur
  )
}

/** Map text_font value (saved in clip) to a real CSS font-family stack. */
function fontFamilyFor(name: string | undefined): string {
  switch ((name || '').toLowerCase()) {
    case 'arial':       return 'Arial, sans-serif'
    case 'helvetica':   return 'Helvetica, Arial, sans-serif'
    case 'georgia':     return 'Georgia, serif'
    case 'courier':     return 'Courier New, monospace'
    case 'impact':      return 'Impact, sans-serif'
    case 'verdana':     return 'Verdana, sans-serif'
    case 'times':
    case 'times new roman': return 'Times New Roman, serif'
    case 'sistema':
    default:            return 'system-ui, -apple-system, sans-serif'
  }
}

function DraggableTextOverlay({
  clip, previewTime, onMoveTo,
}: {
  clip: Clip
  previewTime: number
  onMoveTo: (xPct: number, yPct: number) => void
}) {
  const elRef        = useRef<HTMLDivElement>(null)
  const selectedClipId = useStore((s) => s.selectedClipId)
  const setSelectedClip = useStore((s) => s.setSelectedClip)
  const isSel        = selectedClipId === clip.id

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    if (e.button !== 0) return
    e.preventDefault()
    e.stopPropagation()
    setSelectedClip(clip.id)

    const parent = elRef.current?.parentElement
    if (!parent) return

    const startX  = e.clientX
    const startY  = e.clientY
    // Snapshot original position at mousedown — stable across moves
    const origX   = clip.text_position_x_pct ?? 0
    const origY   = clip.text_position_y_pct ?? 72

    const onMove_ = (mv: MouseEvent) => {
      const rect  = parent.getBoundingClientRect()
      const dxPct = ((mv.clientX - startX) / rect.width)  * 100
      const dyPct = ((mv.clientY - startY) / rect.height) * 100
      onMoveTo(origX + dxPct, origY + dyPct)
    }
    const onUp = () => {
      window.removeEventListener('mousemove', onMove_)
      window.removeEventListener('mouseup',   onUp)
    }
    window.addEventListener('mousemove', onMove_)
    window.addEventListener('mouseup',   onUp)
  }, [setSelectedClip, clip.id, onMoveTo, clip.text_position_x_pct, clip.text_position_y_pct])

  // Compose style fields from CapCut-like text presets (background pill, stroke, shadow).
  // Background and stroke default to OFF when unset; shadow defaults to ON to preserve the
  // previous always-shadowed look for pre-existing text clips.
  const bgEnabled     = clip.text_background_enabled ?? false
  const strokeEnabled = clip.text_stroke_enabled ?? false
  const shadowEnabled = clip.text_shadow_enabled ?? !bgEnabled  // default: shadow on unless pill
  const bgColor       = clip.text_background_color ?? '#000000'
  const bgAlpha       = clip.text_background_alpha ?? 0.65
  const strokeColor   = clip.text_stroke_color ?? '#000000'
  const strokeWidth   = clip.text_stroke_width ?? 2
  const sideMarginPct = Math.max(0, Math.min(25, Number(clip.text_side_margin_pct ?? 5)))
  const maxWidthPct   = Math.max(50, 100 - (sideMarginPct * 2))
  const animStyle = computeAnimationStyle(previewTime, clip)
  const animText = visibleTextForAnimation(clip, previewTime)
  const magicActive = isTextMagicActive(clip, previewTime)
  return (
    <div
      ref={elRef}
      onMouseDown={onMouseDown}
      className="absolute select-none"
      style={{
        left:           `${50 + (clip.text_position_x_pct ?? 0)}%`,
        top:            `${clip.text_position_y_pct ?? 72}%`,
        transform:      `translateX(-50%) ${animStyle?.transform && animStyle.transform !== 'none' ? animStyle.transform : ''}`,
        textAlign:      (clip.text_align ?? 'center') as 'left' | 'center' | 'right',
        fontSize:       `${1.4 * ((clip.text_size_pct ?? 100) / 100)}rem`,
        fontFamily:     fontFamilyFor(clip.text_font),
        color:          clip.text_color ?? '#ffffff',
        fontWeight:     clip.text_bold   ? 700 : 400,
        fontStyle:      clip.text_italic ? 'italic' : 'normal',
        textDecoration: clip.text_underline ? 'underline' : 'none',
        textShadow:     magicActive
          ? '0 0 7px rgba(255,255,255,0.95), 0 0 18px rgba(139,107,255,0.95), 0 0 34px rgba(250,204,21,0.65)'
          : shadowEnabled ? `0 2px 6px ${hexWithAlpha(clip.text_shadow_color ?? '#000000', 0.85)}` : 'none',
        WebkitTextStroke: strokeEnabled ? `${strokeWidth}px ${strokeColor}` : undefined,
        background:     bgEnabled ? hexWithAlpha(bgColor, bgAlpha) : 'transparent',
        opacity:    Math.min((clip.opacity_pct ?? 100) / 100, animStyle?.opacity ?? 1),
        width:      `${maxWidthPct}%`,
        lineHeight: Number(clip.text_line_spacing ?? 1.25),
        zIndex:     50,
        whiteSpace: 'pre-wrap',
        wordBreak:  'break-word',
        cursor:     'move',
        outline:    isSel ? '1px dashed rgba(139,107,255,0.7)' : 'none',
        outlineOffset: '4px',
        borderRadius: bgEnabled ? 6 : 2,
        padding:    bgEnabled ? '6px 12px' : '2px 4px',
      }}
    >
      {animText}
    </div>
  )
}

function CaptionDraftOverlay({
  text,
  stylePatch,
}: {
  text: string
  stylePatch: Partial<Clip>
}) {
  const bgEnabled     = stylePatch.text_background_enabled ?? false
  const strokeEnabled = stylePatch.text_stroke_enabled ?? false
  const shadowEnabled = stylePatch.text_shadow_enabled ?? !bgEnabled
  const bgColor       = stylePatch.text_background_color ?? '#000000'
  const bgAlpha       = stylePatch.text_background_alpha ?? 0.65
  const strokeColor   = stylePatch.text_stroke_color ?? '#000000'
  const strokeWidth   = stylePatch.text_stroke_width ?? 2
  const yPos          = Number(stylePatch.text_position_y_pct ?? 82)
  const sizePct       = Number(stylePatch.text_size_pct ?? 92)
  const sideMarginPct = Math.max(0, Math.min(25, Number(stylePatch.text_side_margin_pct ?? 5)))
  const maxWidthPct   = Math.max(50, 100 - (sideMarginPct * 2))
  return (
    <div
      className="absolute select-none pointer-events-none"
      style={{
        left:           '50%',
        top:            `${Math.max(0, Math.min(100, yPos))}%`,
        transform:      'translateX(-50%)',
        width:          `${maxWidthPct}%`,
        zIndex:     52,
      }}
    >
      <div
        aria-hidden
        className="absolute inset-0 rounded-md border border-dashed"
        style={{ borderColor: 'rgba(255,255,255,0.25)' }}
      />
      <div
        style={{
          position:        'relative',
          textAlign:       (stylePatch.text_align ?? 'center') as 'left' | 'center' | 'right',
          fontSize:        `${1.4 * (sizePct / 100)}rem`,
          fontFamily:      fontFamilyFor(stylePatch.text_font),
          color:           stylePatch.text_color ?? '#ffffff',
          fontWeight:      stylePatch.text_bold ? 700 : 400,
          fontStyle:       stylePatch.text_italic ? 'italic' : 'normal',
          textDecoration:  stylePatch.text_underline ? 'underline' : 'none',
          textShadow:      shadowEnabled ? '0 2px 6px rgba(0,0,0,0.85)' : 'none',
          WebkitTextStroke: strokeEnabled ? `${strokeWidth}px ${strokeColor}` : undefined,
          background:      bgEnabled ? hexWithAlpha(bgColor, bgAlpha) : 'transparent',
          width:           '100%',
          margin:          0,
          lineHeight:      1.45,
          whiteSpace:      'pre-wrap',
          wordBreak:       'break-word',
          borderRadius:    bgEnabled ? 6 : 2,
          padding:         bgEnabled ? '6px 12px' : '2px 4px',
        }}
      >
        {text}
      </div>
    </div>
  )
}

// Convert "#rrggbb" + alpha to "rgba(r,g,b,a)". Returns the hex unchanged if it's not a
// valid 6-digit hex (the caller's fallback alpha is applied via opacity in those cases).
function hexWithAlpha(hex: string, alpha: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex)
  if (!m) return hex
  const n = parseInt(m[1], 16)
  const r = (n >> 16) & 0xff
  const g = (n >> 8)  & 0xff
  const b =  n        & 0xff
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}
