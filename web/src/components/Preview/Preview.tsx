import { Play, Pause, SkipBack, SkipForward, Volume2, VolumeX, Maximize2, Repeat, RefreshCw, Grid3X3 } from 'lucide-react'
import { useState, useRef, useEffect, useCallback } from 'react'
import { useStore, aspectRatioToCss, Clip } from '../../store/useStore'

const API = 'http://127.0.0.1:7472'

function withPreviewReload(url: string, nonce: number): string {
  const sep = url.includes('?') ? '&' : '?'
  return `${url}${sep}preview_reload=${nonce}`
}

function aspectRatioNumber(ar: string | undefined): number {
  const [w, h] = aspectRatioToCss(ar as any).split('/').map((part) => Number(part.trim()))
  return w > 0 && h > 0 ? w / h : 16 / 9
}

/** Return all video clips across the main track AND every extra video track.
 *  Main track takes priority (it's always first), so `.find()` on the result
 *  will prefer the main track when clips overlap in time. */
function getAllVideoClips(proj: { video_track: { clips: Clip[] }; extra_video_tracks?: { clips: Clip[] }[] } | null | undefined): Clip[] {
  if (!proj) return []
  return [
    ...proj.video_track.clips,
    ...(proj.extra_video_tracks ?? []).flatMap((t) => t.clips),
  ]
}

export function Preview() {
  const {
    project, previewTime, setPreviewTime,
    updateClip, undo, redo,
    copyClip, pasteClip,
    trackStates,
    setProxyStatus,
  } = useStore()
  const [playing,      setPlaying]      = useState(false)
  const [loop,         setLoop]         = useState(false)
  const [volume,       setVolume]       = useState(100)   // 0-100
  const [muted,        setMuted]        = useState(false)
  const [duration,     setDuration]     = useState(0)
  const [playbackRate, setPlaybackRate] = useState(1)
  const [videoLoading, setVideoLoading] = useState(false) // true while src is loading
  const [previewRefreshNonce, setPreviewRefreshNonce] = useState(0)
  const [frameGuideVisible, setFrameGuideVisible] = useState(true)
  const [viewportSize, setViewportSize] = useState({ width: 0, height: 0 })

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

  // Resolve the video clip currently at the playhead.
  // Searches the main video track first (priority), then every extra video track
  // so clips on parallel/B-roll tracks are found and played too.
  const activeVideoClip = getAllVideoClips(project).find(
    (c) => previewTime >= c.start_s && previewTime < c.end_s
  ) ?? null

  // ── P0-1: codec proxy (H.265 → H.264 background transcode) ──────────────────
  const NEEDS_PROXY_CODECS = ['hevc', 'h265', 'vp9', 'vp8', 'av1', 'mpeg4', 'vc1']
  const needsProxy  = !!(project?.video_codec && NEEDS_PROXY_CODECS.includes(project.video_codec))
  const proxyReady  = project?.proxy_status === 'ready' && !!project.proxy_path
  const proxyStatus = project?.proxy_status

  useEffect(() => {
    if (!needsProxy || proxyStatus !== 'transcoding' || !project?.videoPath) return
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
  }, [needsProxy, proxyStatus, project?.videoPath]) // eslint-disable-line react-hooks/exhaustive-deps

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
  const activeSrcPath = activeVideoClip?.source_path ?? null
  const sourceOffset  = activeVideoClip?.source_offset_s ?? 0

  // Keep refs so event handlers always have the latest values without stale closures
  const sourceOffsetRef = useRef(0)
  useEffect(() => { sourceOffsetRef.current  = sourceOffset  }, [sourceOffset])
  useEffect(() => { activeSrcPathRef.current = activeSrcPath }, [activeSrcPath])
  useEffect(() => { previewTimeRef.current   = previewTime   }, [previewTime])
  useEffect(() => { activeVideoClipRef.current = activeVideoClip }, [activeVideoClip])

  // When the active source PATH changes, the <video key={activeSrcPath}> remounts.
  // onLoadedMetadata then applies the seek using previewTimeRef + sourceOffsetRef
  // (always the latest values — no separate effect needed).

  // Build the actual src URL — prefer the per-source proxy if one exists.
  // Order of precedence:
  //   1. If the active source IS the main video AND its main proxy is ready → use main proxy
  //   2. If the active source has a per-source proxy registered → use that (appended H.265 files)
  //   3. Otherwise → original source path
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

  // Track visibility/mute state from Timeline headers
  const videoHidden  = trackStates?.video?.hidden  ?? false
  const videoMuted   = trackStates?.video?.muted   ?? false
  const audioMuted   = trackStates?.audio?.muted   ?? false
  const textHidden   = trackStates?.text?.hidden   ?? false

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
  const pv = activeVideoClip
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
  // Compose adjustment layers — any clip_type='adjustment' on overlay track
  // whose [start_s, end_s) range covers previewTime adds its own filter.
  for (const adj of project?.overlay_track.clips ?? []) {
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
      if (playing) setPlaying(false)
      wasPlayingRef.current = false
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
    if (refreshedVideoSrc) setVideoLoading(true)
    else          setVideoLoading(false)
  }, [refreshedVideoSrc])

  // External seek: previewTime changed from outside (ruler click, timeline click)
  // Translate project-time → source-file time using current sourceOffset.
  // Only sync if the difference is significant (avoids feedback loop during playback).
  useEffect(() => {
    const vid = videoRef.current
    if (!vid || seekingByUser.current) return
    const targetSrcTime = Math.max(0, previewTime - sourceOffsetRef.current)
    if (Math.abs(vid.currentTime - targetSrcTime) > 0.15) {
      vid.currentTime = targetSrcTime
      lastExternal.current = previewTime
    }
  }, [previewTime])

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
    // source_time + offset = project_time
    const offset   = sourceOffsetRef.current
    const projTime = vid.currentTime + offset

    const state = useStore.getState()
    const clips = getAllVideoClips(state.project)
    if (clips.length > 0 && !vid.paused) {
      const active = activeVideoClipRef.current
      if (active && projTime >= active.end_s - 0.03) {
        const sorted = clips.slice().sort((a, b) => a.start_s - b.start_s)
        const next = sorted.find((c) => c.id !== active.id && c.start_s >= active.end_s - 0.08)
        if (next) {
          const nextSrc = next.source_path ?? state.project?.videoPath ?? null
          wasPlayingRef.current = true
          if (nextSrc === activeSrcPathRef.current) {
            vid.currentTime = Math.max(0, next.start_s - (next.source_offset_s ?? 0))
          }
          setPreviewTime(next.start_s)
          return
        }
      }
      const inClip = clips.some((c) => projTime >= c.start_s && projTime < c.end_s)
      if (!inClip) {
        const sorted = clips.slice().sort((a, b) => a.start_s - b.start_s)
        const next   = sorted.find((c) => c.start_s >= projTime - 0.08)
        if (next) {
          const nextSrc = next.source_path ?? state.project?.videoPath ?? null
          if (nextSrc === activeSrcPathRef.current) {
            // Same source file — seek the video element directly (fast, no remount)
            const nextSrcTime = Math.max(0, next.start_s - (next.source_offset_s ?? 0))
            vid.currentTime = nextSrcTime
          }
          // For different source: just update project time.
          // activeSrcPath will change → key= remounts video → onLoadedMetadata auto-plays.
          setPreviewTime(next.start_s)
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
  }, [setPreviewTime])

  const onLoadedMetadata = useCallback(() => {
    const vid = videoRef.current
    if (!vid) return
    setVideoLoading(false)
    // Read CURRENT state directly from the store — refs may be stale during
    // the same render cycle as a <video key=> remount. The "ref sync" effects
    // run AFTER the new element fires loadedmetadata, so they can't be trusted here.
    const state  = useStore.getState()
    setDuration(state.project?.duration_s ?? vid.duration)
    const pt     = state.previewTime
    const clip   = getAllVideoClips(state.project).find(
      (c) => pt >= c.start_s && pt < c.end_s
    )
    const offset = clip?.source_offset_s ?? 0
    const targetSrcTime = Math.max(0, Math.min(pt - offset, vid.duration))
    vid.currentTime = targetSrcTime
    // Auto-resume if the user was playing when the source changed
    if (wasPlayingRef.current) {
      vid.play().catch(() => {
        setPlaying(false)
        wasPlayingRef.current = false
      })
    }
  }, [])

  const onEnded = useCallback(() => {
    // onEnded fires when the SOURCE FILE reaches its natural end.
    // onTimeUpdate normally handles cross-clip transitions, but onEnded is the
    // safety net when the file ends exactly at (or before) the last expected frame.
    // If there are more clips in the project (possibly from different sources),
    // advance the project timeline to the next clip start.
    const clips  = getAllVideoClips(useStore.getState().project)
    const sorted = clips.slice().sort((a, b) => a.start_s - b.start_s)
    const curTime = previewTimeRef.current  // latest project time

    // Find the first clip that starts after (or right at) the current project time.
    // Allow a 0.5s tolerance so floating-point drift at clip boundaries is handled.
    const nextClip = sorted.find((c) => c.start_s > curTime - 0.5)
    const isNewClip = nextClip && (
      nextClip.start_s > curTime + 0.05 ||                         // clearly ahead
      (nextClip.source_path ?? '') !== (activeSrcPathRef.current ?? '') // same boundary, different source
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
          // Step 10 s in project time, translate to source time
          const offset = sourceOffsetRef.current
          const projT  = Math.max(0, (vid.currentTime + offset) - 10)
          vid.currentTime = Math.max(0, projT - offset); setPreviewTime(projT)
        }
        return
      }
      if (e.key === 'k' && !e.ctrlKey && !e.metaKey) {
        e.preventDefault()
        if (vid) { wasPlayingRef.current = false; vid.pause(); setPlaying(false) }
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
            const next = rates.find((r) => r > vid.playbackRate) ?? 1
            vid.playbackRate = next
            setPlaybackRate(next)
          }
        }
        return
      }

      if (e.code === 'Space') {
        e.preventDefault()
        if (vid) {
          if (vid.paused) {
            // Same gap-jump logic as togglePlay: if no clip at playhead, jump to next
            const state = useStore.getState()
            const clips = getAllVideoClips(state.project)
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
        }
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault()
        if (vid) {
          const offset = sourceOffsetRef.current
          const projT  = Math.max(0, (vid.currentTime + offset) - (e.shiftKey ? 5 : 0.1))
          vid.currentTime = Math.max(0, projT - offset); setPreviewTime(projT)
        }
      } else if (e.key === 'ArrowRight') {
        e.preventDefault()
        if (vid) {
          const offset  = sourceOffsetRef.current
          const projT   = Math.min(dur, (vid.currentTime + offset) + (e.shiftKey ? 5 : 0.1))
          vid.currentTime = Math.max(0, projT - offset); setPreviewTime(projT)
        }
      } else if (e.key === 'Home') {
        e.preventDefault()
        if (vid) { vid.currentTime = Math.max(0, -sourceOffsetRef.current); setPreviewTime(0) }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [setPreviewTime, undo, redo, copyClip, pasteClip])

  const togglePlay = useCallback(() => {
    if (playing) {
      const vid = videoRef.current
      if (vid) vid.pause()
      wasPlayingRef.current = false
      setPlaying(false)
      return
    }
    // Before playing: verify there's a clip at current playhead.
    // If we're in a gap (e.g. after delete), jump to next clip first.
    const state = useStore.getState()
    const clips = getAllVideoClips(state.project)
    const pt    = state.previewTime
    const here  = clips.find((c) => pt >= c.start_s && pt < c.end_s)
    if (!here) {
      const sorted = clips.slice().sort((a, b) => a.start_s - b.start_s)
      const next   = sorted.find((c) => c.start_s > pt)
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
  }, [playing, setPreviewTime])

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
    if (!vid) return
    const offset = sourceOffsetRef.current
    const projT  = Math.max(0, (vid.currentTime + offset) - 5)
    vid.currentTime = Math.max(0, projT - offset)
    setPreviewTime(projT)
  }, [setPreviewTime])

  const skipForward = useCallback(() => {
    const vid = videoRef.current
    if (!vid) return
    const offset = sourceOffsetRef.current
    const projT  = Math.min(duration, (vid.currentTime + offset) + 5)
    vid.currentTime = Math.max(0, projT - offset)
    setPreviewTime(projT)
  }, [duration, setPreviewTime])

  const seekTo = useCallback((t: number) => {
    seekingByUser.current = true
    const vid = videoRef.current
    // Translate project time → source file time before seeking the video element
    if (vid) vid.currentTime = Math.max(0, t - sourceOffsetRef.current)
    setPreviewTime(t)
    requestAnimationFrame(() => { seekingByUser.current = false })
  }, [setPreviewTime])

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
    if (vid) vid.playbackRate = rate
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
                  key={`${activeSrcPath ?? 'no-src'}:${previewRefreshNonce}`}
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
                  onPause={() => setPlaying(false)}
                  onError={() => setVideoLoading(false)}
                  onCanPlay={() => setVideoLoading(false)}
                  onWaiting={() => setVideoLoading(true)}
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
            {!audioMuted && project?.audio_track.clips
              .filter((c) => c.clip_type === 'music' && c.source_path)
              .map((c) => (
                <MusicPlayer
                  key={`${c.id}:${previewRefreshNonce}`}
                  clip={c}
                  previewTime={previewTime}
                  isPlaying={playing}
                  trackMuted={trackStates?.audio?.muted ?? false}
                  refreshNonce={previewRefreshNonce}
                />
              ))}

            {/* Overlay track image clips */}
            {!(trackStates?.overlay?.hidden) && project?.overlay_track.clips
              .filter((c) => previewTime >= c.start_s && previewTime < c.end_s && c.clip_type === 'image' && c.source_path)
              .map((c) => (
                <img
                  key={`${c.id}:${previewRefreshNonce}`}
                  src={withPreviewReload(`${API}/api/serve-file?path=${encodeURIComponent(c.source_path!)}`, previewRefreshNonce)}
                  draggable={false}
                  alt=""
                  style={{
                    position: 'absolute',
                    inset: 0,
                    width: '100%',
                    height: '100%',
                    objectFit: 'contain',
                    opacity: (c.opacity_pct ?? 100) / 100,
                    transform: c.scale_pct !== 100 || c.rotation_deg
                      ? `scale(${(c.scale_pct ?? 100) / 100}) rotate(${c.rotation_deg ?? 0}deg)`
                      : undefined,
                    zIndex: 15,
                    pointerEvents: 'none',
                  }}
                />
              ))
            }

            {/* Text track overlays — drag to reposition */}
            {!textHidden && project?.text_track.clips
              .filter((c) => previewTime >= c.start_s && previewTime < c.end_s && c.text_overlay)
              .map((c) => (
                <DraggableTextOverlay
                  key={`${c.id}:${previewRefreshNonce}`}
                  clip={c}
                  onMoveTo={(x, y) => updateClip(c.id, {
                    text_position_x_pct: Math.max(-50, Math.min(50,  x)),
                    text_position_y_pct: Math.max(0,   Math.min(100, y)),
                  })}
                />
              ))
            }

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

// ── Draggable text overlay ─────────────────────────────────────────────────────
// ── Imported-music player ─────────────────────────────────────────────────────
// One <audio> element per imported music clip. Lives outside the <video> so
// deleting / pausing the video clip doesn't stop background music. Syncs its
// playback position from `previewTime` and obeys the global play/pause state.
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
    a.volume = Math.max(0, Math.min(1, (clip.volume_pct ?? 100) / 100))
  }, [trackMuted, clip.volume_pct])

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
    case 'slide-left':   return { transform: `translateX(${-offsetPct}%)`,          opacity: 1 }
    case 'slide-right':  return { transform: `translateX(${ offsetPct}%)`,          opacity: 1 }
    case 'slide-up':     return { transform: `translateY(${-offsetPct}%)`,          opacity: 1 }
    case 'slide-down':   return { transform: `translateY(${ offsetPct}%)`,          opacity: 1 }
    case 'zoom-in':      return { transform: `scale(${0.3 + 0.7 * eased})`,         opacity: eased }
    case 'zoom-out':     return { transform: `scale(${1.5 - 0.5 * eased})`,         opacity: eased }
    default:             return { transform: 'none', opacity: 1 }
  }
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
  clip, onMoveTo,
}: {
  clip: Clip
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
  return (
    <div
      ref={elRef}
      onMouseDown={onMouseDown}
      className="absolute select-none"
      style={{
        left:           `${50 + (clip.text_position_x_pct ?? 0)}%`,
        top:            `${clip.text_position_y_pct ?? 72}%`,
        transform:      'translateX(-50%)',
        textAlign:      (clip.text_align ?? 'center') as 'left' | 'center' | 'right',
        fontSize:       `${1.4 * ((clip.text_size_pct ?? 100) / 100)}rem`,
        fontFamily:     fontFamilyFor(clip.text_font),
        color:          clip.text_color ?? '#ffffff',
        fontWeight:     clip.text_bold   ? 700 : 400,
        fontStyle:      clip.text_italic ? 'italic' : 'normal',
        textDecoration: clip.text_underline ? 'underline' : 'none',
        textShadow:     shadowEnabled ? '0 2px 6px rgba(0,0,0,0.85)' : 'none',
        WebkitTextStroke: strokeEnabled ? `${strokeWidth}px ${strokeColor}` : undefined,
        background:     bgEnabled ? hexWithAlpha(bgColor, bgAlpha) : 'transparent',
        maxWidth:   '82%',
        lineHeight: 1.35,
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
      {clip.text_overlay}
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
