import { create } from 'zustand'

// ─────────────────────────────────────────────────────────────────────────────
// Linkage utility — derives audio↔video pairing from clip properties.
// Two clips are considered linked when they share the same source file AND
// the same time range (within 0.05s tolerance), and neither has been
// explicitly unlinked via right-click "Desvincular áudio".
//
// Exported so the Timeline (selection logic), store actions (delete/split/
// updateClip cascade) and tests all use the same definition of "linked".
// ─────────────────────────────────────────────────────────────────────────────
export function getLinkedClipIds(clipId: string, project: ProjectState | null): string[] {
  if (!project) return [clipId]
  const all = [
    ...project.video_track.clips,
    ...project.audio_track.clips,
    ...(project.extra_video_tracks ?? []).flatMap((t) => t.clips),
    ...(project.extra_audio_tracks ?? []).flatMap((t) => t.clips),
  ]
  const self = all.find((c) => c.id === clipId)
  if (!self || self.unlinked || !self.source_path) return [clipId]
  const paired = all
    .filter((c) =>
      c.id !== clipId &&
      !c.unlinked &&
      c.source_path === self.source_path &&
      Math.abs(c.start_s - self.start_s) < 0.05 &&
      Math.abs(c.end_s   - self.end_s)   < 0.05,
    )
    .map((c) => c.id)
  return [clipId, ...paired]
}

export interface Clip {
  id: string
  start_s: number
  end_s: number
  clip_type: string
  label: string
  source_path?: string
  volume_pct: number
  scale_pct: number
  opacity_pct: number
  transition: string
  // text clip fields
  text_overlay?:         string
  text_position_x_pct?: number
  text_position_y_pct?: number
  text_size_pct?:        number
  text_color?:           string
  text_bold?:            boolean
  text_italic?:          boolean
  text_underline?:       boolean
  text_align?:           string
  text_font?:            string
  // CapCut-style text styling (background pill, stroke, shadow)
  text_background_enabled?: boolean
  text_background_color?:   string   // hex "#rrggbb"
  text_background_alpha?:   number   // 0..1
  text_stroke_enabled?:     boolean
  text_stroke_color?:       string
  text_stroke_width?:       number   // px
  text_shadow_enabled?:     boolean
  // chroma key
  chroma_enabled?:   boolean
  chroma_color?:     string
  chroma_tolerance?: number
  // per-clip audio waveform (imported music tracks)
  source_waveform?:  number[]
  // time offset of this clip's source file relative to the project timeline (0 for the first video)
  source_offset_s?:  number
  // audio↔video pairing: by default paired clips with same source_path + time range
  // are linked. Setting unlinked=true opts out (right-click "Desvincular áudio").
  unlinked?:         boolean
  brightness: number
  contrast: number
  saturation: number
  // Extended color grading (Phase 5.5 — CapCut "Ajuste" tab)
  temperature?:  number   // -100..+100, 0 neutral; warm/cool color shift
  hue?:          number   // -180..+180 degrees
  exposure?:     number   // -100..+100, multiplies output
  sharpness?:    number   // 0..100, sharpness boost
  vignette?:     number   // 0..100, dark vignette intensity
  crop_top_pct: number
  crop_bottom_pct: number
  crop_left_pct: number
  crop_right_pct: number
  speed_factor: number
  rotation_deg: number
  blend_mode: string
  z_order: number
  // Audio-specific (Phase 5.4)
  fade_in_s?:        number   // seconds (0..5)
  fade_out_s?:       number   // seconds (0..5)
  normalize_audio?:  boolean  // normalize this clip's loudness
  position_x?:       number   // X position offset in pixels (transform)
  position_y?:       number   // Y position offset in pixels (transform)
  uniform_scale?:    boolean  // when true, X and Y scale are locked together
  // Clip enter/exit animations (Phase 6.2)
  animation_in?:           string   // 'none'|'fade'|'slide-left'|'slide-right'|'slide-up'|'slide-down'|'zoom-in'|'zoom-out'
  animation_out?:          string
  animation_in_duration_s?:  number   // seconds (0..2)
  animation_out_duration_s?: number
}

export interface Track {
  name: string
  clips: Clip[]
}

/** Aspect ratio identifier — controls preview viewport AND export dimensions. */
export type AspectRatio = '16:9' | '9:16' | '1:1' | '4:5' | '4:3' | '3:4' | 'original'

/** Translate an aspect ratio identifier to a CSS `aspect-ratio` value. */
export function aspectRatioToCss(ar: AspectRatio | undefined): string {
  switch (ar) {
    case '9:16':     return '9 / 16'
    case '1:1':      return '1 / 1'
    case '4:5':      return '4 / 5'
    case '4:3':      return '4 / 3'
    case '3:4':      return '3 / 4'
    case 'original': return '16 / 9'   // fallback; real value comes from video metadata
    case '16:9':
    default:         return '16 / 9'
  }
}

/** Aspect ratios shown in the toolbar dropdown — order matches CapCut. */
export const ASPECT_RATIO_OPTIONS: Array<{ id: AspectRatio; label: string; hint: string }> = [
  { id: '16:9', label: '16:9', hint: 'YouTube / paisagem' },
  { id: '9:16', label: '9:16', hint: 'Shorts / Reels / TikTok' },
  { id: '1:1',  label: '1:1',  hint: 'Quadrado / Instagram' },
  { id: '4:5',  label: '4:5',  hint: 'Instagram retrato' },
  { id: '4:3',  label: '4:3',  hint: 'Clássico' },
  { id: '3:4',  label: '3:4',  hint: 'Retrato' },
]

export interface ProjectState {
  loaded: boolean
  videoPath: string | null
  duration_s: number
  waveform: number[]
  /** Aspect ratio for preview and export. Default '16:9' (YouTube). */
  aspect_ratio?: AspectRatio
  video_track: Track
  audio_track: Track
  text_track: Track
  overlay_track: Track
  // Multi-track (Phase 2b): extra parallel tracks for layered video/audio.
  // Indexed alongside the main tracks; rendered in order in the timeline UI.
  extra_video_tracks?:   Track[]
  extra_audio_tracks?:   Track[]
  extra_overlay_tracks?: Track[]
  removed_ranges: [number, number][]
  saved_time_s: number
  // Codec / proxy fields (populated by backend) — for the MAIN video
  video_codec?:  string
  proxy_status?: 'not_needed' | 'transcoding' | 'ready' | 'error'
  proxy_path?:   string | null
  // Per-source proxy paths for appended videos (HEVC/VP9 etc. need transcoding).
  // Key = original source_path, value = local H.264 proxy path (or empty string).
  source_proxies?: Record<string, string>
}

export interface TrackState {
  locked: boolean
  hidden: boolean
  muted:  boolean
}

export interface ExportSettings {
  crf:             number
  preset:          'ultrafast' | 'fast' | 'medium' | 'slow'
  silenceEnabled:  boolean
  silenceStyle:    'aggressive' | 'natural' | 'light'
  platform:        'youtube' | 'reels' | 'tiktok' | 'shorts'
  normalizeAudio:  boolean
}

export type WorkspaceLayout = 'default' | 'capcut'

const DEFAULT_EXPORT_SETTINGS: ExportSettings = {
  crf:            18,
  preset:         'fast',
  silenceEnabled: false,
  silenceStyle:   'natural',
  platform:       'youtube',
  normalizeAudio: true,
}

const DEFAULT_TRACK_STATES: Record<string, TrackState> = {
  video:   { locked: false, hidden: false, muted: false },
  audio:   { locked: false, hidden: false, muted: false },
  text:    { locked: false, hidden: false, muted: false },
  overlay: { locked: false, hidden: false, muted: false },
}

interface AppStore {
  project:           ProjectState | null
  selectedClipId:    string | null
  selectedClipIds:   string[]       // multi-select (Ctrl+Click)
  snapEnabled:       boolean        // snap clips to edges/playhead when dragging
  activeLeftTab:     string
  isRendering:       boolean
  renderProgress:    number
  renderMessage:     string
  renderOutputPath:  string | null
  renderError:       string | null
  previewTime:       number
  timelineZoom:      number
  trackStates:    Record<string, TrackState>
  exportSettings: ExportSettings
  clipboardClip:  Clip | null
  isDirty:        boolean          // unsaved changes exist
  projectName:    string | null    // editable in Header
  workspaceLayout: WorkspaceLayout

  // Undo / redo history (structural edits only — split, delete)
  past:   ProjectState[]
  future: ProjectState[]

  setProject:          (p: ProjectState) => void
  setWorkspaceLayout:  (layout: WorkspaceLayout) => void
  setProjectName:      (name: string) => void
  setProxyStatus:      (status: ProjectState['proxy_status'], path?: string) => void
  markSaved:           () => void
  setTrackState:       (trackId: string, patch: Partial<TrackState>) => void
  setExportSetting:    <K extends keyof ExportSettings>(key: K, val: ExportSettings[K]) => void
  copyClip:            (id: string) => void
  pasteClip:           () => void
  addTextClip:         (startS: number, endS: number, text: string, style?: Partial<Clip>) => void
  updateClip:          (id: string, patch: Partial<Clip>) => void
  splitClip:           (id: string, atTime: number) => void
  deleteClip:          (id: string) => void
  rippleDelete:        (id: string) => void
  importAudio:         (path: string, durationS: number, waveform?: number[]) => void
  importImage:         (path: string, durationS?: number) => void
  /** Add a transparent Adjustment Layer (clip_type='adjustment') on the overlay
   *  track. Affects clips below it on the timeline via color grading. */
  addAdjustmentLayer:  (durationS?: number) => void
  appendVideo:         (clips: Array<{start_s:number;end_s:number;source_path:string;label:string}>, waveform: number[], proxyInfo?: { source_path: string; proxy_path: string; proxy_status: string }) => void
  setSourceProxy:      (sourcePath: string, proxyPath: string) => void
  undo:                () => void
  redo:                () => void
  setSelectedClip:       (id: string | null) => void
  toggleClipSelection:   (id: string) => void
  clearSelection:        () => void
  setSnapEnabled:        (v: boolean) => void
  setAspectRatio:        (ratio: AspectRatio) => void
  // Multi-track (Phase 2b)
  addExtraTrack:         (type: 'video' | 'audio') => void
  removeExtraTrack:      (type: 'video' | 'audio', index: number) => void
  moveClipToTrack:       (clipId: string, target: { kind: 'video' | 'audio'; index: number }) => void
  setActiveLeftTab:    (tab: string) => void
  setRenderProgress:   (p: number) => void
  setRenderMessage:    (m: string) => void
  setIsRendering:      (v: boolean) => void
  setRenderOutputPath: (path: string | null) => void
  setRenderError:      (err: string | null) => void
  setPreviewTime:      (t: number) => void
  setTimelineZoom:     (z: number) => void
}

// (patchTrack was the legacy single-clip patcher; updateClip now uses patchAll
// inline to cascade time patches to linked pairs.)

const MAX_HISTORY = 50

function clipEndDuration(project: ProjectState | null): number {
  if (!project) return 0
  return Math.max(
    ...project.video_track.clips.map((c) => c.end_s),
    ...project.audio_track.clips.map((c) => c.end_s),
    ...project.text_track.clips.map((c) => c.end_s),
    ...project.overlay_track.clips.map((c) => c.end_s),
    ...(project.extra_video_tracks ?? []).flatMap((t) => t.clips.map((c) => c.end_s)),
    ...(project.extra_audio_tracks ?? []).flatMap((t) => t.clips.map((c) => c.end_s)),
    ...(project.extra_overlay_tracks ?? []).flatMap((t) => t.clips.map((c) => c.end_s)),
    0,
  )
}

function withTimelineDuration(project: ProjectState, minimum = 0): ProjectState {
  return {
    ...project,
    duration_s: Math.max(minimum, clipEndDuration(project)),
  }
}

// O10: Adaptive undo — fewer history entries for large projects to save memory.
function maxHistory(project: ProjectState | null): number {
  if (!project) return MAX_HISTORY
  const clips = project.video_track.clips.length
              + project.audio_track.clips.length
              + project.text_track.clips.length
  if (clips > 200) return 10
  if (clips > 100) return 20
  if (clips > 50)  return 30
  return MAX_HISTORY
}

export const useStore = create<AppStore>((set) => ({
  project:           null,
  selectedClipId:    null,
  selectedClipIds:   [],
  snapEnabled:       true,
  activeLeftTab:     'media',
  isRendering:       false,
  renderProgress:    0,
  renderMessage:     '',
  renderOutputPath:  null,
  renderError:       null,
  previewTime:       0,
  timelineZoom:      1,
  trackStates:       { ...DEFAULT_TRACK_STATES },
  exportSettings:    { ...DEFAULT_EXPORT_SETTINGS },
  clipboardClip:     null,
  isDirty:           false,
  projectName:       null,
  workspaceLayout:   'default',
  past:              [],
  future:            [],

  setProject: (p) => set(() => {
    const { _projectName, ...rest } = p as any
    const cleanProjectBase: ProjectState = {
      loaded:         rest.loaded ?? true,
      videoPath:      rest.videoPath ?? null,
      duration_s:     rest.duration_s ?? 0,
      waveform:       rest.waveform ?? [],
      video_track:    rest.video_track    ?? { name: 'Video',   clips: [] },
      audio_track:    rest.audio_track    ?? { name: 'Audio',   clips: [] },
      text_track:     rest.text_track     ?? { name: 'Texto',   clips: [] },
      overlay_track:  rest.overlay_track  ?? { name: 'Overlay', clips: [] },
      extra_video_tracks:   rest.extra_video_tracks   ?? [],
      extra_audio_tracks:   rest.extra_audio_tracks   ?? [],
      extra_overlay_tracks: rest.extra_overlay_tracks ?? [],
      removed_ranges: rest.removed_ranges ?? [],
      saved_time_s:   rest.saved_time_s   ?? 0,
      video_codec:    rest.video_codec,
      proxy_status:   rest.proxy_status,
      proxy_path:     rest.proxy_path,
      source_proxies: rest.source_proxies ?? {},
      aspect_ratio:   rest.aspect_ratio   ?? '16:9',
    }
    const cleanProject = withTimelineDuration(cleanProjectBase, cleanProjectBase.duration_s)
    const derivedName = cleanProject.videoPath?.replace(/\\/g, '/').split('/').pop()?.replace(/\.[^.]+$/, '') ?? null
    return {
      project: cleanProject,
      past: [],
      future: [],
      selectedClipId: null,
      selectedClipIds: [],
      trackStates: { ...DEFAULT_TRACK_STATES },
      isDirty: false,
      projectName: (_projectName as string | undefined) ?? derivedName,
    }
  }),

  setWorkspaceLayout: (layout) => set({ workspaceLayout: layout }),
  setProjectName: (name) => set({ projectName: name, isDirty: true }),
  setProxyStatus: (status, path) => set((state) => state.project ? ({
    project: { ...state.project, proxy_status: status, proxy_path: path ?? state.project.proxy_path },
  }) : {}),
  markSaved: () => set({ isDirty: false }),
  setTrackState: (trackId, patch) => set((state) => ({
    trackStates: {
      ...state.trackStates,
      [trackId]: {
        ...(state.trackStates[trackId] ?? DEFAULT_TRACK_STATES[trackId] ?? { locked: false, hidden: false, muted: false }),
        ...patch,
      },
    },
  })),
  setExportSetting: (key, val) => set((state) => ({ exportSettings: { ...state.exportSettings, [key]: val } })),

  addTextClip: (startS, endS, text, style = {}) => set((state) => {
    if (!state.project) return {}
    const newClip: Clip = {
      id: `text_${Date.now().toString(36)}`,
      start_s: startS,
      end_s: endS,
      clip_type: 'text',
      label: text || 'Novo Texto',
      text_overlay: text,
      volume_pct: 100,
      scale_pct: 100,
      opacity_pct: 100,
      transition: 'Corte',
      brightness: 0,
      contrast: 0,
      saturation: 0,
      crop_top_pct: 0,
      crop_bottom_pct: 0,
      crop_left_pct: 0,
      crop_right_pct: 0,
      speed_factor: 1,
      rotation_deg: 0,
      blend_mode: 'Normal',
      z_order: 10,
      text_position_x_pct: 0,
      text_position_y_pct: 72,
      text_size_pct: 100,
      text_color: '#ffffff',
      text_bold: false,
      text_italic: false,
      text_align: 'center',
      chroma_enabled: false,
      chroma_color: '#00ff00',
      chroma_tolerance: 45,
      ...style,
    }
    return {
      past: [...state.past, state.project].slice(-maxHistory(state.project)),
      future: [],
      isDirty: true,
      selectedClipId: newClip.id,
      selectedClipIds: [newClip.id],
      project: withTimelineDuration({
        ...state.project,
        text_track: { ...state.project.text_track, clips: [...state.project.text_track.clips, newClip] },
      }, state.project.duration_s),
    }
  }),

  updateClip: (id, patch) => set((state) => {
    if (!state.project) return {}
    const isTimePatch = 'start_s' in patch || 'end_s' in patch
    const ids = new Set<string>(isTimePatch ? getLinkedClipIds(id, state.project) : [id])
    const patchTrack = (track: Track): Track => ({
      ...track,
      clips: track.clips.map((c) => ids.has(c.id) ? { ...c, ...patch } : c),
    })
    const patchedProject: ProjectState = {
      ...state.project,
      video_track: patchTrack(state.project.video_track),
      audio_track: patchTrack(state.project.audio_track),
      text_track: patchTrack(state.project.text_track),
      overlay_track: patchTrack(state.project.overlay_track),
      extra_video_tracks: state.project.extra_video_tracks?.map(patchTrack),
      extra_audio_tracks: state.project.extra_audio_tracks?.map(patchTrack),
      extra_overlay_tracks: state.project.extra_overlay_tracks?.map(patchTrack),
    }
    return { isDirty: true, project: isTimePatch ? withTimelineDuration(patchedProject) : patchedProject }
  }),

  splitClip: (id, atTime) => set((state) => {
    if (!state.project) return {}
    const linkedIds = new Set(getLinkedClipIds(id, state.project))
    const splitTrack = (track: Track): Track => ({
      ...track,
      clips: track.clips.flatMap((c) => {
        if (!linkedIds.has(c.id) || atTime <= c.start_s || atTime >= c.end_s) return [c]
        const suffix = Date.now().toString(36)
        return [{ ...c, end_s: atTime }, { ...c, id: `${c.id}_${suffix}`, start_s: atTime }]
      }),
    })
    const project = withTimelineDuration({
      ...state.project,
      video_track: splitTrack(state.project.video_track),
      audio_track: splitTrack(state.project.audio_track),
      text_track: splitTrack(state.project.text_track),
      overlay_track: splitTrack(state.project.overlay_track),
      extra_video_tracks: state.project.extra_video_tracks?.map(splitTrack),
      extra_audio_tracks: state.project.extra_audio_tracks?.map(splitTrack),
      extra_overlay_tracks: state.project.extra_overlay_tracks?.map(splitTrack),
    }, state.project.duration_s)
    return { past: [...state.past, state.project].slice(-maxHistory(state.project)), future: [], isDirty: true, project }
  }),

  deleteClip: (id) => set((state) => {
    if (!state.project) return {}
    const ids = new Set<string>()
    for (const cid of (state.selectedClipIds.length > 1 ? state.selectedClipIds : [id])) {
      for (const linked of getLinkedClipIds(cid, state.project)) ids.add(linked)
    }
    const removeFromTrack = (track: Track): Track => ({ ...track, clips: track.clips.filter((c) => !ids.has(c.id)) })
    const project = withTimelineDuration({
      ...state.project,
      video_track: removeFromTrack(state.project.video_track),
      audio_track: removeFromTrack(state.project.audio_track),
      text_track: removeFromTrack(state.project.text_track),
      overlay_track: removeFromTrack(state.project.overlay_track),
      extra_video_tracks: state.project.extra_video_tracks?.map(removeFromTrack),
      extra_audio_tracks: state.project.extra_audio_tracks?.map(removeFromTrack),
      extra_overlay_tracks: state.project.extra_overlay_tracks?.map(removeFromTrack),
    })
    return {
      past: [...state.past, state.project].slice(-maxHistory(state.project)),
      future: [],
      isDirty: true,
      selectedClipId: null,
      selectedClipIds: [],
      project,
    }
  }),

  rippleDelete: (id) => set((state) => {
    if (!state.project) return {}
    const ids = new Set(getLinkedClipIds(id, state.project))
    const rippleTrack = (track: Track): Track => {
      const removed = track.clips.find((c) => ids.has(c.id))
      if (!removed) return track
      const gap = removed.end_s - removed.start_s
      return {
        ...track,
        clips: track.clips
          .filter((c) => !ids.has(c.id))
          .map((c) => c.start_s >= removed.end_s ? { ...c, start_s: c.start_s - gap, end_s: c.end_s - gap } : c),
      }
    }
    const project = withTimelineDuration({
      ...state.project,
      video_track: rippleTrack(state.project.video_track),
      audio_track: rippleTrack(state.project.audio_track),
      text_track: rippleTrack(state.project.text_track),
      overlay_track: rippleTrack(state.project.overlay_track),
      extra_video_tracks: state.project.extra_video_tracks?.map(rippleTrack),
      extra_audio_tracks: state.project.extra_audio_tracks?.map(rippleTrack),
      extra_overlay_tracks: state.project.extra_overlay_tracks?.map(rippleTrack),
    })
    return {
      past: [...state.past, state.project].slice(-maxHistory(state.project)),
      future: [],
      isDirty: true,
      selectedClipId: state.selectedClipId === id ? null : state.selectedClipId,
      selectedClipIds: state.selectedClipIds.filter((x) => x !== id),
      project,
    }
  }),

  importAudio: (path, durationS, waveform) => set((state) => {
    if (!state.project) return {}
    const label = path.replace(/\\/g, '/').split('/').pop() ?? 'Audio'
    const start = Math.max(...state.project.audio_track.clips.map((c) => c.end_s), 0)
    const newClip: Clip = {
      id: `audio_${Date.now().toString(36)}`,
      start_s: start,
      end_s: start + durationS,
      clip_type: 'music',
      label,
      source_path: path,
      source_offset_s: start,
      source_waveform: waveform && waveform.length > 0 ? waveform : undefined,
      volume_pct: 100,
      scale_pct: 100,
      opacity_pct: 100,
      transition: 'Corte',
      brightness: 0,
      contrast: 0,
      saturation: 0,
      crop_top_pct: 0,
      crop_bottom_pct: 0,
      crop_left_pct: 0,
      crop_right_pct: 0,
      speed_factor: 1,
      rotation_deg: 0,
      blend_mode: 'Normal',
      z_order: 0,
    }
    return {
      past: [...state.past, state.project].slice(-maxHistory(state.project)),
      future: [],
      isDirty: true,
      project: withTimelineDuration({
        ...state.project,
        audio_track: { ...state.project.audio_track, clips: [...state.project.audio_track.clips, newClip] },
      }, state.project.duration_s),
    }
  }),

  importImage: (path, durationS = 5) => set((state) => {
    if (!state.project) return {}
    const label = path.replace(/\\/g, '/').split('/').pop() ?? 'Imagem'
    const start = state.previewTime ?? 0
    const newClip: Clip = {
      id: `img_${Date.now().toString(36)}`,
      start_s: start,
      end_s: start + durationS,
      clip_type: 'image',
      label,
      source_path: path,
      volume_pct: 100,
      scale_pct: 100,
      opacity_pct: 100,
      transition: 'Corte',
      brightness: 0,
      contrast: 0,
      saturation: 0,
      crop_top_pct: 0,
      crop_bottom_pct: 0,
      crop_left_pct: 0,
      crop_right_pct: 0,
      speed_factor: 1,
      rotation_deg: 0,
      blend_mode: 'Normal',
      z_order: 0,
    }
    return {
      past: [...state.past, state.project].slice(-maxHistory(state.project)),
      future: [],
      isDirty: true,
      selectedClipId: newClip.id,
      selectedClipIds: [newClip.id],
      project: withTimelineDuration({
        ...state.project,
        overlay_track: { ...state.project.overlay_track, clips: [...state.project.overlay_track.clips, newClip] },
      }, state.project.duration_s),
    }
  }),

  addAdjustmentLayer: (durationS = 5) => set((state) => {
    if (!state.project) return {}
    const start = state.previewTime ?? 0
    const id = `adj_${Date.now().toString(36)}`
    const newClip: Clip = {
      id,
      start_s: start,
      end_s: start + durationS,
      clip_type: 'adjustment',
      label: 'Ajuste',
      volume_pct: 0,
      scale_pct: 100,
      opacity_pct: 100,
      transition: 'Corte',
      brightness: 0,
      contrast: 0,
      saturation: 0,
      crop_top_pct: 0,
      crop_bottom_pct: 0,
      crop_left_pct: 0,
      crop_right_pct: 0,
      speed_factor: 1,
      rotation_deg: 0,
      blend_mode: 'Normal',
      z_order: 20,
    }
    return {
      past: [...state.past, state.project].slice(-maxHistory(state.project)),
      future: [],
      isDirty: true,
      selectedClipId: id,
      selectedClipIds: [id],
      project: withTimelineDuration({
        ...state.project,
        overlay_track: { ...state.project.overlay_track, clips: [...state.project.overlay_track.clips, newClip] },
      }, state.project.duration_s),
    }
  }),

  appendVideo: (newClips, newWaveform, proxyInfo) => set((state) => {
    if (!state.project) return {}
    const proj = state.project
    const ts = Date.now().toString(36)
    const makeClip = (c: typeof newClips[0], i: number, idPrefix: string, offset: number): Clip => ({
      id: `${idPrefix}_${ts}_${i}`,
      start_s: c.start_s + offset,
      end_s: c.end_s + offset,
      clip_type: 'speech',
      label: c.label,
      source_path: c.source_path,
      source_offset_s: offset,
      volume_pct: 100,
      scale_pct: 100,
      opacity_pct: 100,
      transition: 'Corte',
      brightness: 0,
      contrast: 0,
      saturation: 0,
      crop_top_pct: 0,
      crop_bottom_pct: 0,
      crop_left_pct: 0,
      crop_right_pct: 0,
      speed_factor: 1,
      rotation_deg: 0,
      blend_mode: 'Normal',
      z_order: 0,
    })
    const updatedProxies = { ...(proj.source_proxies ?? {}) }
    if (proxyInfo && proxyInfo.source_path && proxyInfo.proxy_path) {
      updatedProxies[proxyInfo.source_path] = proxyInfo.proxy_status === 'ready' ? proxyInfo.proxy_path : ''
    }
    const offset = Math.max(
      ...proj.video_track.clips.map((c) => c.end_s),
      ...proj.audio_track.clips.map((c) => c.end_s),
      0,
    )
    const totalNewDuration = newClips.reduce((max, c) => Math.max(max, c.end_s), 0)
    const newDuration = offset + totalNewDuration
    const videoClips = newClips.map((c, i) => makeClip(c, i, 'video_append', offset))
    const audioClips = newClips.map((c, i) => ({
      ...makeClip(c, i, 'audio_append', offset),
      id: `audio_append_${ts}_${i}`,
      source_waveform: newWaveform.length > 0 ? newWaveform : undefined,
    }))
    const W = 500
    const existing = proj.waveform
    const existingBins = Math.round(W * (newDuration > 0 ? offset / newDuration : 0))
    const newBins = W - existingBins
    const resampledExisting = Array.from({ length: existingBins }, (_, i) => existing[Math.floor((i / Math.max(1, existingBins)) * existing.length)] ?? 0)
    const resampledNew = Array.from({ length: newBins }, (_, i) => newWaveform[Math.floor((i / Math.max(1, newBins)) * newWaveform.length)] ?? 0)
    return {
      past: [...state.past, proj].slice(-maxHistory(proj)),
      future: [],
      isDirty: true,
      project: {
        ...proj,
        duration_s: newDuration,
        waveform: [...resampledExisting, ...resampledNew],
        video_track: { ...proj.video_track, clips: [...proj.video_track.clips, ...videoClips] },
        audio_track: { ...proj.audio_track, clips: [...proj.audio_track.clips, ...audioClips] },
        source_proxies: updatedProxies,
      },
    }
  }),

  setSourceProxy: (sourcePath, proxyPath) => set((state) => state.project ? ({
    project: { ...state.project, source_proxies: { ...(state.project.source_proxies ?? {}), [sourcePath]: proxyPath } },
  }) : {}),

  undo: () => set((state) => {
    if (state.past.length === 0 || !state.project) return {}
    const prev = state.past[state.past.length - 1]
    return { past: state.past.slice(0, -1), future: [state.project, ...state.future].slice(0, maxHistory(state.project)), project: prev }
  }),
  redo: () => set((state) => {
    if (state.future.length === 0 || !state.project) return {}
    const next = state.future[0]
    return { past: [...state.past, state.project].slice(-maxHistory(state.project)), future: state.future.slice(1), project: next }
  }),

  copyClip: (id) => set((state) => {
    if (!state.project) return {}
    const allClips = [
      ...state.project.video_track.clips,
      ...state.project.audio_track.clips,
      ...state.project.text_track.clips,
      ...state.project.overlay_track.clips,
      ...(state.project.extra_video_tracks ?? []).flatMap((t) => t.clips),
      ...(state.project.extra_audio_tracks ?? []).flatMap((t) => t.clips),
      ...(state.project.extra_overlay_tracks ?? []).flatMap((t) => t.clips),
    ]
    return { clipboardClip: allClips.find((c) => c.id === id) ?? null }
  }),
  pasteClip: () => set((state) => {
    if (!state.project || !state.clipboardClip) return {}
    const src = state.clipboardClip
    const dur = src.end_s - src.start_s
    const newClip = { ...src, id: `${src.id}_copy_${Date.now().toString(36)}`, start_s: state.previewTime, end_s: state.previewTime + dur }
    const isAudio = src.clip_type === 'music' || src.clip_type === 'audio'
    const isText = src.clip_type === 'text'
    const appendTo = (track: Track, match: boolean): Track => match ? { ...track, clips: [...track.clips, newClip] } : track
    const project = withTimelineDuration({
      ...state.project,
      video_track: appendTo(state.project.video_track, !isAudio && !isText),
      audio_track: appendTo(state.project.audio_track, isAudio),
      text_track: appendTo(state.project.text_track, isText),
    }, state.project.duration_s)
    return { past: [...state.past, state.project].slice(-maxHistory(state.project)), future: [], isDirty: true, selectedClipId: newClip.id, project }
  }),

  setSelectedClip: (id) => set({ selectedClipId: id, selectedClipIds: id ? [id] : [] }),
  toggleClipSelection: (id) => set((state) => {
    const ids = state.selectedClipIds.includes(id) ? state.selectedClipIds.filter((x) => x !== id) : [...state.selectedClipIds, id]
    return { selectedClipIds: ids, selectedClipId: ids.length ? ids[ids.length - 1] : null }
  }),
  clearSelection: () => set({ selectedClipIds: [], selectedClipId: null }),
  setSnapEnabled: (v) => set({ snapEnabled: v }),
  setAspectRatio: (ratio) => set((state) => state.project ? ({ isDirty: true, project: { ...state.project, aspect_ratio: ratio } }) : {}),

  addExtraTrack: (type) => set((state) => {
    if (!state.project) return {}
    if (type === 'video') {
      const tracks = [...(state.project.extra_video_tracks ?? [])]
      tracks.push({ name: `Video ${tracks.length + 2}`, clips: [] })
      return { past: [...state.past, state.project].slice(-maxHistory(state.project)), future: [], isDirty: true, project: { ...state.project, extra_video_tracks: tracks } }
    }
    const tracks = [...(state.project.extra_audio_tracks ?? [])]
    tracks.push({ name: `Audio ${tracks.length + 2}`, clips: [] })
    return { past: [...state.past, state.project].slice(-maxHistory(state.project)), future: [], isDirty: true, project: { ...state.project, extra_audio_tracks: tracks } }
  }),
  removeExtraTrack: (type, index) => set((state) => {
    if (!state.project) return {}
    if (type === 'video') {
      const tracks = [...(state.project.extra_video_tracks ?? [])]
      if (index < 0 || index >= tracks.length) return {}
      tracks.splice(index, 1)
      return { past: [...state.past, state.project].slice(-maxHistory(state.project)), future: [], isDirty: true, project: withTimelineDuration({ ...state.project, extra_video_tracks: tracks }) }
    }
    const tracks = [...(state.project.extra_audio_tracks ?? [])]
    if (index < 0 || index >= tracks.length) return {}
    tracks.splice(index, 1)
    return { past: [...state.past, state.project].slice(-maxHistory(state.project)), future: [], isDirty: true, project: withTimelineDuration({ ...state.project, extra_audio_tracks: tracks }) }
  }),
  moveClipToTrack: (clipId, target) => set((state) => {
    if (!state.project) return {}
    const trackKey = target.kind === 'video' ? 'video_track' : 'audio_track'
    const extraKey = target.kind === 'video' ? 'extra_video_tracks' : 'extra_audio_tracks'
    const allTracks: Track[] = [state.project[trackKey], ...(state.project[extraKey] ?? [])]
    if (target.index < 0 || target.index >= allTracks.length) return {}
    let movedClip: Clip | null = null
    const updated = allTracks.map((track) => ({
      ...track,
      clips: track.clips.filter((clip) => {
        if (clip.id === clipId) { movedClip = clip; return false }
        return true
      }),
    }))
    if (!movedClip) return {}
    updated[target.index] = { ...updated[target.index], clips: [...updated[target.index].clips, movedClip].sort((a, b) => a.start_s - b.start_s) }
    const project = withTimelineDuration({ ...state.project, [trackKey]: updated[0], [extraKey]: updated.slice(1) } as ProjectState, state.project.duration_s)
    return { past: [...state.past, state.project].slice(-maxHistory(state.project)), future: [], isDirty: true, project }
  }),

  setActiveLeftTab:    (tab)  => set({ activeLeftTab: tab }),
  setRenderProgress:   (p)    => set({ renderProgress: p }),
  setRenderMessage:    (m)    => set({ renderMessage: m }),
  setIsRendering:      (v)    => set({ isRendering: v }),
  setRenderOutputPath: (path) => set({ renderOutputPath: path }),
  setRenderError:      (err)  => set({ renderError: err }),
  setPreviewTime:      (t)    => set({ previewTime: t }),
  setTimelineZoom:     (z)    => set({ timelineZoom: z }),
}))
