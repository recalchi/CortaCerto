import { Film, Save, Undo2, Redo2, Download, Settings, CheckCircle, XCircle, X, Square, Pencil, Crop, Trash2, LayoutPanelTop, FolderOpen } from 'lucide-react'
import { useState, useCallback, useRef, useEffect } from 'react'
import { useStore, ASPECT_RATIO_OPTIONS, buildPersistableProject } from '../store/useStore'
import type { AspectRatio, Clip } from '../store/useStore'
import { api, ws_url } from '../api/client'

const API_SETTING_KEYS = [
  'OPENAI_API_KEY',
  'OPENAI_MONTHLY_BUDGET_USD',
  'OPENAI_GPT_INPUT_USD_PER_1K',
  'OPENAI_GPT_OUTPUT_USD_PER_1K',
  'OPENAI_WHISPER_USD_PER_MIN',
  'CORTACERTO_DEFAULT_SAVE_DIR',
  'PEXELS_API_KEY',
  'PIXABAY_API_KEY',
  'UNSPLASH_ACCESS_KEY',
  'UNSPLASH_SECRET_KEY',
  'FREESOUND_API_KEY',
  'FREESOUND_CLIENT_ID',
  'FREESOUND_CLIENT_SECRET',
] as const

export function Header() {
  const {
    project, isRendering,
    setIsRendering, setRenderProgress, setRenderMessage,
    setRenderOutputPath, setRenderError,
    trackStates, exportSettings, undo, redo, past, future,
    isDirty, projectName, projectFilePath, setProjectName, setProjectFilePath, markSaved,
    workspaceLayout, setWorkspaceLayout,
  } = useStore()

  const [editingName, setEditingName] = useState(false)
  const [nameValue,   setNameValue]   = useState('')
  const nameInputRef = useRef<HTMLInputElement>(null)

  const [toast, setToast] = useState<{ type: 'ok' | 'err'; msg: string } | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsTab, setSettingsTab] = useState<'general' | 'keys' | 'usage'>('general')
  const [settingsData, setSettingsData] = useState<any>(null)
  const [usageData, setUsageData] = useState<any>(null)
  const [settingsDraft, setSettingsDraft] = useState<Record<string, string>>({})
  const [settingsLoading, setSettingsLoading] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const handleSaveRef = useRef<() => void>(() => {})

  const showToast = (type: 'ok' | 'err', msg: string) => {
    setToast({ type, msg })
    setTimeout(() => setToast(null), 5000)
  }

  // Focus name input when editing starts
  useEffect(() => {
    if (editingName) {
      setTimeout(() => nameInputRef.current?.select(), 10)
    }
  }, [editingName])

  const commitName = useCallback(() => {
    const trimmed = nameValue.trim()
    if (trimmed) setProjectName(trimmed)
    setEditingName(false)
  }, [nameValue, setProjectName])

  // Ctrl+S global shortcut — must be registered after handleSave is defined
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's' && !e.shiftKey) {
        e.preventDefault()
        handleSaveRef.current()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const saveProjectTo = useCallback(async (savePath: string) => {
    if (!project) return
    const projectWithName = buildPersistableProject(project, projectName, savePath)
    await api.post('/api/save-project', { path: savePath, project: projectWithName })
    setProjectFilePath(savePath)
    markSaved()
    showToast('ok', `Salvo: ${savePath.split('\\').pop()?.split('/').pop()}`)
  }, [project, projectName, setProjectFilePath, markSaved])

  const handleSaveAs = useCallback(async () => {
    if (!project) return
    try {
      const basename = projectName
        ?? project.videoPath?.split('\\').pop()?.split('/').pop()?.replace(/\.[^.]+$/, '')
        ?? 'projeto'
      const res = await api.post('/api/open-save-dialog', { default_name: `${basename}.ccproj`, type: 'project' })
      const savePath: string = res.data.path
      if (!savePath) return
      await saveProjectTo(savePath)
    } catch {
      showToast('err', 'Erro ao salvar projeto')
    }
  }, [project, projectName, saveProjectTo])

  // Save project as JSON. If a .ccproj path is already known, Ctrl+S is silent.
  const handleSave = useCallback(async () => {
    if (!project) return
    if (projectFilePath) {
      try {
        await saveProjectTo(projectFilePath)
      } catch {
        showToast('err', 'Erro ao salvar projeto')
      }
      return
    }
    await handleSaveAs()
  }, [project, projectFilePath, saveProjectTo, handleSaveAs])

  // Keep ref in sync so Ctrl+S always calls the latest closure
  useEffect(() => { handleSaveRef.current = handleSave }, [handleSave])

  const loadSettings = useCallback(async () => {
    setSettingsLoading(true)
    try {
      const [settingsRes, usageRes] = await Promise.all([
        api.get('/api/settings'),
        api.get('/api/openai/usage'),
      ])
      setSettingsData(settingsRes.data)
      setUsageData(usageRes.data)
    } catch {
      showToast('err', 'Falha ao carregar configuracoes')
    } finally {
      setSettingsLoading(false)
    }
  }, [])

  const openSettings = useCallback(() => {
    setSettingsOpen(true)
    loadSettings()
  }, [loadSettings])

  const saveSettings = useCallback(async () => {
    setSettingsLoading(true)
    try {
      const res = await api.post('/api/settings', { values: settingsDraft })
      setSettingsData(res.data)
      const theme = res.data?.general?.ui_theme || 'violet'
      document.body.classList.remove('theme-violet', 'theme-graphite', 'theme-midnight', 'theme-emerald')
      document.body.classList.add(`theme-${theme}`)
      setSettingsDraft({})
      const usageRes = await api.get('/api/openai/usage')
      setUsageData(usageRes.data)
      showToast('ok', 'Configuracoes salvas')
    } catch {
      showToast('err', 'Falha ao salvar configuracoes')
    } finally {
      setSettingsLoading(false)
    }
  }, [settingsDraft])

  const clearCache = useCallback(async () => {
    setSettingsLoading(true)
    try {
      await api.post('/api/cache/clear')
      await loadSettings()
      showToast('ok', 'Cache limpo')
    } catch {
      showToast('err', 'Falha ao limpar cache')
    } finally {
      setSettingsLoading(false)
    }
  }, [loadSettings])

  const openLogsFolder = useCallback(async () => {
    try {
      const res = await api.post('/api/logs/open-dir')
      showToast('ok', `Pasta de logs aberta: ${res.data?.path ?? ''}`)
    } catch {
      showToast('err', 'Falha ao abrir pasta de logs')
    }
  }, [])

  // Cancel ongoing render
  const handleCancelExport = useCallback(() => {
    wsRef.current?.close()
    wsRef.current = null
    setIsRendering(false)
    setRenderProgress(0)
    setRenderMessage('')
    showToast('ok', 'Exportação cancelada')
  }, [setIsRendering, setRenderProgress, setRenderMessage])

  const handleExport = useCallback(async () => {
    if (!project) return

    // 1. Open native save dialog
    let savePath: string
    try {
      const baseName = projectName
        ?? project.videoPath?.split('\\').pop()?.split('/').pop()?.replace(/\.[^.]+$/, '')
        ?? 'output'
      const filename = `${baseName}_cortado.mp4`
      const res = await api.post('/api/open-save-dialog', { default_name: filename })
      savePath = res.data.path
    } catch {
      showToast('err', 'Falha ao abrir diálogo de salvar')
      return
    }
    if (!savePath) return  // user cancelled

    // 2. Connect WebSocket and start render
    setIsRendering(true)
    setRenderProgress(0)
    setRenderMessage('Iniciando render…')
    setRenderOutputPath(null)
    setRenderError(null)

    const ws = new WebSocket(ws_url('/ws/render'))
    wsRef.current = ws

    ws.onopen = () => {
      const audioTracks = [
        { id: 'audio', clips: project.audio_track.clips },
        ...(project.extra_audio_tracks ?? []).map((track, index) => ({ id: `audio-${index + 1}`, clips: track.clips })),
      ]
      const videoTracks = [
        { id: 'video', clips: project.video_track.clips },
        ...(project.extra_video_tracks ?? []).map((track, index) => ({ id: `video-${index + 1}`, clips: track.clips })),
      ]
      const visibleVideoClips = videoTracks
        .filter((track) => !trackStates?.[track.id]?.hidden && !trackStates?.[track.id]?.muted)
        .flatMap((track) => track.clips)
        .sort((a, b) => a.start_s - b.start_s)
      const isLinkedBaseAudio = (clip: Clip) =>
        visibleVideoClips.some((videoClip) =>
          videoClip.source_path &&
          clip.source_path === videoClip.source_path &&
          Math.abs(Number(clip.start_s ?? 0) - Number(videoClip.start_s ?? 0)) < 0.05 &&
          Math.abs(Number(clip.end_s ?? 0) - Number(videoClip.end_s ?? 0)) < 0.05
        )
      const audioClips = audioTracks
        .filter((track) => !trackStates?.[track.id]?.muted)
        .flatMap((track) => track.clips)
        .filter((c) =>
          (c.clip_type === 'music' || c.clip_type === 'audio') &&
          Boolean(c.source_path) &&
          !isLinkedBaseAudio(c)
        )
        .sort((a, b) => a.start_s - b.start_s)
        .map((c) => ({
          id:              c.id,
          source_path:     c.source_path,
          start_s:         c.start_s,
          end_s:           c.end_s,
          source_offset_s: c.source_offset_s ?? c.start_s,
          volume_pct:      c.volume_pct ?? 100,
          fade_in_s:       c.fade_in_s ?? 0,
          fade_out_s:      c.fade_out_s ?? 0,
        }))
      const clips = visibleVideoClips.map((c) => ({
        id:               c.id,
        start_s:          c.start_s,
        end_s:            c.end_s,
        source_path:      c.source_path,     // per-clip source file (multi-source support)
        source_offset_s:  c.source_offset_s ?? 0,  // project-time offset of this source file
        speed_factor:     c.speed_factor,
        volume_pct:       c.volume_pct,
        transition:       c.transition,
        transition_duration_s: c.transition_duration_s ?? 0.4,
        brightness:       c.brightness,
        contrast:         c.contrast,
        saturation:       c.saturation,
        temperature:      c.temperature,
        hue:              c.hue,
        exposure:         c.exposure,
        sharpness:        c.sharpness,
        vignette:         c.vignette,
        blur_type:        c.blur_type,
        blur_intensity:   c.blur_intensity,
        blur_direction:   c.blur_direction,
        crop_top_pct:     c.crop_top_pct,
        crop_bottom_pct:  c.crop_bottom_pct,
        crop_left_pct:    c.crop_left_pct,
        crop_right_pct:   c.crop_right_pct,
        opacity_pct:      c.opacity_pct,
        scale_pct:        c.scale_pct,
        position_x:       c.position_x ?? 0,
        position_y:       c.position_y ?? 0,
        rotation_deg:     c.rotation_deg,
        chroma_enabled:   c.chroma_enabled,
        chroma_color:     c.chroma_color,
        chroma_tolerance: c.chroma_tolerance,
        person_remove_enabled:  c.person_remove_enabled,
        person_remove_strength: c.person_remove_strength,
        person_remove_feather:  c.person_remove_feather,
      }))
      const mutedTracks = Object.entries(trackStates)
        .filter(([, s]) => s.muted)
        .map(([id]) => id)

      // Text overlay clips from text_track (for drawtext burn-in)
      const textClips = (trackStates?.text?.hidden || trackStates?.text?.muted)
        ? []
        : project.text_track.clips
            .filter((c) => c.text_overlay?.trim())
            .map((c) => ({
              text_overlay:        c.text_overlay,
              start_s:             c.start_s,
              end_s:               c.end_s,
              text_position_x_pct: c.text_position_x_pct ?? 0,
              text_position_y_pct: c.text_position_y_pct ?? 72,
              text_size_pct:       c.text_size_pct       ?? 100,
              text_side_margin_pct: c.text_side_margin_pct ?? 5,
              text_line_spacing:    c.text_line_spacing    ?? 1.25,
              text_color:          c.text_color          ?? '#ffffff',
              text_bold:           c.text_bold           ?? false,
              text_italic:         c.text_italic         ?? false,
              text_underline:      c.text_underline      ?? false,
              text_align:          c.text_align          ?? 'center',
              text_font:           c.text_font           ?? 'Sistema',
              text_background_enabled: c.text_background_enabled ?? false,
              text_background_color:   c.text_background_color   ?? '#000000',
              text_background_alpha:   c.text_background_alpha   ?? 0.65,
              text_stroke_enabled:     c.text_stroke_enabled     ?? false,
              text_stroke_color:       c.text_stroke_color       ?? '#000000',
              text_stroke_width:       c.text_stroke_width       ?? 2,
              text_shadow_enabled:     c.text_shadow_enabled     ?? true,
            }))

      // Visual overlay clips (image + video) from overlay tracks
      const visualOverlaySources = [
        ...(trackStates?.overlay?.hidden || trackStates?.overlay?.muted ? [] : project.overlay_track.clips),
        ...(project.extra_overlay_tracks ?? []).flatMap((track, index) => {
          const state = trackStates?.[`overlay-${index + 1}`]
          return state?.hidden || state?.muted ? [] : track.clips
        }),
      ]
      const visualOverlayClips = visualOverlaySources
            .filter((c) => (c.clip_type === 'image' || c.clip_type === 'video_overlay' || c.clip_type === 'video') && c.source_path)
            .sort((a, b) => {
              const za = Number(a.z_order ?? 0)
              const zb = Number(b.z_order ?? 0)
              if (za !== zb) return za - zb
              return Number(a.start_s ?? 0) - Number(b.start_s ?? 0)
            })
            .map((c) => ({
              clip_type:   c.clip_type,
              source_path: c.source_path,
              start_s:     c.start_s,
              end_s:       c.end_s,
              opacity_pct: c.opacity_pct ?? 100,
              z_order:     c.z_order ?? 0,
              position_x:  c.position_x ?? 0,
              position_y:  c.position_y ?? 0,
              scale_pct:   c.scale_pct ?? 100,
              rotation_deg: c.rotation_deg ?? 0,
            }))

      ws.send(JSON.stringify({
        output_path:     savePath,
        clips,
        crf:             exportSettings.crf,
        preset:          exportSettings.preset,
        normalize_audio: exportSettings.normalizeAudio,
        // aspect_ratio (project-level) takes precedence over the legacy
        // exportSettings.platform when the user explicitly picked one
        platform:        exportSettings.platform,
        aspect_ratio:    project?.aspect_ratio ?? '16:9',
        project_snapshot: project,
        project_path: projectFilePath,
        video_path: project.videoPath || visibleVideoClips.find((c) => c.source_path)?.source_path || '',
        ...(audioClips.length > 0 ? { audio_clips: audioClips } : {}),
        ...(mutedTracks.length > 0  ? { muted_tracks: mutedTracks } : {}),
        ...(textClips.length > 0    ? { text_clips:   textClips   } : {}),
        ...(visualOverlayClips.length > 0   ? { image_clips:  visualOverlayClips  } : {}),
      }))
    }

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data)
        if (msg.type === 'progress') {
          setRenderProgress(msg.value ?? 0)
          setRenderMessage(msg.message ?? '')
        } else if (msg.type === 'done') {
          setIsRendering(false)
          setRenderProgress(100)
          setRenderOutputPath(msg.path)
          wsRef.current = null
          showToast('ok', `Exportado: ${msg.path.split('\\').pop()?.split('/').pop()}`)
          ws.close()
        } else if (msg.type === 'error') {
          setIsRendering(false)
          setRenderError(msg.detail ?? 'Erro desconhecido')
          wsRef.current = null
          showToast('err', msg.detail ?? 'Erro ao exportar')
          ws.close()
        }
      } catch { /* ignore parse errors */ }
    }

    ws.onerror = () => {
      setIsRendering(false)
      setRenderError('Falha na conexão WebSocket')
      wsRef.current = null
      showToast('err', 'Falha na conexão com o servidor')
    }

    ws.onclose = () => {
      if (useStore.getState().isRendering) {
        setIsRendering(false)
        setRenderError('Conexão encerrada inesperadamente')
        wsRef.current = null
      }
    }
  }, [project, projectName, projectFilePath, trackStates, exportSettings, setIsRendering, setRenderProgress, setRenderMessage, setRenderOutputPath, setRenderError])

  return (
    <header className="flex items-center h-12 bg-bg-panel border-b border-border px-4 gap-3 flex-shrink-0 relative">
      {/* Logo */}
      <div className="flex items-center gap-2 mr-4">
        <Film className="text-accent w-5 h-5" />
        <span className="font-bold text-sm tracking-wide">CortaCerto</span>
      </div>

      {/* Undo / Redo */}
      <div className="flex items-center gap-1">
        <HeaderBtn
          icon={<Undo2 size={15} />}
          title="Desfazer (Ctrl+Z)"
          onClick={undo}
          disabled={past.length === 0}
        />
        <HeaderBtn
          icon={<Redo2 size={15} />}
          title="Refazer (Ctrl+Y)"
          onClick={redo}
          disabled={future.length === 0}
        />
      </div>

      <div className="w-px h-6 bg-border mx-1" />

      {/* Save */}
      <div className="flex items-center gap-1">
        <HeaderBtn
          icon={<Save size={15} />}
          title={projectFilePath ? 'Salvar projeto (Ctrl+S)' : 'Salvar projeto como...'}
          onClick={handleSave}
          disabled={!project}
        />
        <HeaderBtn
          icon={<FolderOpen size={15} />}
          title="Salvar como..."
          onClick={handleSaveAs}
          disabled={!project}
        />
      </div>

      <div className="w-px h-6 bg-border mx-1" />

      {/* Aspect ratio selector */}
      {project && <AspectRatioPicker />}

      <div className="flex-1" />

      {/* Project name — click to rename, dirty dot when unsaved */}
      {project && (
        <div className="flex items-center gap-1.5 max-w-xs">
          {/* Unsaved-changes indicator */}
          {isDirty && (
            <span className="w-1.5 h-1.5 rounded-full bg-accent flex-shrink-0" title="Alterações não salvas" />
          )}
          {editingName ? (
            <input
              ref={nameInputRef}
              value={nameValue}
              onChange={(e) => setNameValue(e.target.value)}
              onBlur={commitName}
              onKeyDown={(e) => {
                if (e.key === 'Enter')  { e.preventDefault(); commitName() }
                if (e.key === 'Escape') { setEditingName(false) }
                e.stopPropagation()
              }}
              className="bg-bg-surface border border-accent/50 rounded px-1.5 py-0.5 text-xs text-white outline-none w-40"
            />
          ) : (
            <button
              onClick={() => { setNameValue(projectName ?? ''); setEditingName(true) }}
              className="flex items-center gap-1 text-text-muted hover:text-white text-xs truncate group transition-colors"
              title="Clique para renomear o projeto"
            >
              <span className="truncate">
                {projectName ?? project.videoPath?.split('\\').pop()?.split('/').pop() ?? 'Projeto'}
              </span>
              <Pencil size={10} className="opacity-0 group-hover:opacity-60 flex-shrink-0 transition-opacity" />
            </button>
          )}
        </div>
      )}

      <div className="flex-1" />

      {/* Export / cancel action. Detailed render progress lives in the footer. */}
      {isRendering ? (
        <div className="flex items-center gap-2">
          <button
            onClick={handleCancelExport}
            className="flex items-center gap-1 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-1.5 text-xs font-medium text-red-300 hover:bg-red-500/20 transition-colors"
            title="Cancelar exportação"
          >
            <Square size={10} className="fill-current" /> Cancelar
          </button>
        </div>
      ) : (
        <button
          onClick={handleExport}
          disabled={!project}
          className="flex items-center gap-2 bg-accent hover:bg-accent-hover disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-medium px-4 py-1.5 rounded-lg transition-colors"
          title={project ? 'Exportar vídeo' : 'Abra um vídeo primeiro'}
        >
          <Download size={14} />
          Exportar
        </button>
      )}

      <HeaderBtn icon={<Settings size={15} />} title="Configuracoes" onClick={openSettings} />
      <HeaderBtn
        icon={<LayoutPanelTop size={15} />}
        title={workspaceLayout === 'capcut' ? 'Layout padrao' : 'Layout CapCut'}
        onClick={() => setWorkspaceLayout(workspaceLayout === 'capcut' ? 'default' : 'capcut')}
      />

      {settingsOpen && (
        <SettingsModal
          activeTab={settingsTab}
          onTab={setSettingsTab}
          data={settingsData}
          usage={usageData}
          drafts={settingsDraft}
          loading={settingsLoading}
          onDraft={(key, value) => setSettingsDraft((prev) => ({ ...prev, [key]: value }))}
          onClose={() => setSettingsOpen(false)}
          onRefresh={loadSettings}
          onSave={saveSettings}
          onClearCache={clearCache}
          onOpenLogs={openLogsFolder}
        />
      )}

      {/* Toast notification */}
      {toast && (
        <div
          className={`absolute right-4 top-14 z-50 flex items-center gap-2 px-3 py-2 rounded-lg shadow-xl text-sm font-medium border ${
            toast.type === 'ok'
              ? 'bg-green-900/80 border-green-700 text-green-200'
              : 'bg-red-900/80 border-red-700 text-red-200'
          }`}
        >
          {toast.type === 'ok'
            ? <CheckCircle size={14} className="text-green-400 flex-shrink-0" />
            : <XCircle   size={14} className="text-red-400 flex-shrink-0" />
          }
          <span className="truncate max-w-[260px]">{toast.msg}</span>
          <button onClick={() => setToast(null)} className="ml-1 opacity-60 hover:opacity-100">
            <X size={12} />
          </button>
        </div>
      )}
    </header>
  )
}

// Aspect ratio dropdown — shows a "Proporção: NxM" button that opens a small
// menu with all available ratios. Selecting one updates project.aspect_ratio,
// which flows to the Preview viewport and the export pipeline.
function AspectRatioPicker() {
  const { project, setAspectRatio } = useStore()
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)
  const current: AspectRatio = project?.aspect_ratio ?? '16:9'

  // Close on outside click
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false)
    }
    window.addEventListener('mousedown', onDown)
    return () => window.removeEventListener('mousedown', onDown)
  }, [open])

  return (
    <div ref={wrapRef} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        title="Proporção do vídeo"
        className="flex items-center gap-1.5 px-2.5 py-1 rounded text-xs text-text-muted hover:text-white hover:bg-bg-surface transition-colors"
      >
        <Crop size={13} />
        <span className="tabular-nums">{current}</span>
      </button>
      {open && (
        <div
          className="absolute right-0 mt-1 z-50 bg-bg-panel border border-border rounded-lg shadow-2xl py-1 min-w-[180px]"
          style={{ top: '100%' }}
        >
          {ASPECT_RATIO_OPTIONS.map((opt) => (
            <button
              key={opt.id}
              onClick={() => { setAspectRatio(opt.id); setOpen(false) }}
              className={`w-full flex items-center justify-between px-3 py-1.5 text-xs transition-colors ${
                current === opt.id
                  ? 'bg-accent/15 text-accent'
                  : 'text-text-muted hover:text-white hover:bg-bg-surface'
              }`}
            >
              <span className="font-medium tabular-nums">{opt.label}</span>
              <span className="text-[10px] text-text-dim">{opt.hint}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function SettingsModal({
  activeTab, onTab, data, usage, drafts, loading, onDraft, onClose, onRefresh, onSave, onClearCache, onOpenLogs,
}: {
  activeTab: 'general' | 'keys' | 'usage'
  onTab: (tab: 'general' | 'keys' | 'usage') => void
  data: any
  usage: any
  drafts: Record<string, string>
  loading: boolean
  onDraft: (key: string, value: string) => void
  onClose: () => void
  onRefresh: () => void
  onSave: () => void
  onClearCache: () => void
  onOpenLogs: () => void
}) {
  const valueInfo = (key: string) => {
    const openai = data?.openai?.keys?.[key]
    const stock = data?.stock?.keys?.[key]
    return openai ?? stock ?? {}
  }

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="w-[640px] max-w-[calc(100vw-32px)] max-h-[calc(100vh-48px)] overflow-hidden rounded-xl border border-border bg-bg-panel shadow-2xl">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div>
            <p className="text-sm font-semibold text-white">Configuracoes</p>
            <p className="text-[10px] text-text-dim">Chaves locais, uso estimado e limites da API</p>
          </div>
          <button onClick={onClose} className="text-text-muted hover:text-white">
            <X size={16} />
          </button>
        </div>

        <div className="flex border-b border-border">
          <button
            onClick={() => onTab('general')}
            className={`flex-1 px-3 py-2 text-xs ${activeTab === 'general' ? 'bg-bg-surface text-white' : 'text-text-muted hover:text-white'}`}
          >
            Geral
          </button>
          <button
            onClick={() => onTab('keys')}
            className={`flex-1 px-3 py-2 text-xs ${activeTab === 'keys' ? 'bg-bg-surface text-white' : 'text-text-muted hover:text-white'}`}
          >
            Chaves e campos
          </button>
          <button
            onClick={() => onTab('usage')}
            className={`flex-1 px-3 py-2 text-xs ${activeTab === 'usage' ? 'bg-bg-surface text-white' : 'text-text-muted hover:text-white'}`}
          >
            Uso GPT
          </button>
        </div>

        <div className="max-h-[58vh] overflow-y-auto p-4">
          {activeTab === 'general' ? (
            <GeneralSettingsPanel
              data={data?.general}
              drafts={drafts}
              loading={loading}
              onDraft={onDraft}
              onClearCache={onClearCache}
              onOpenLogs={onOpenLogs}
            />
          ) : activeTab === 'keys' ? (
            <div className="grid grid-cols-2 gap-3">
              {API_SETTING_KEYS.map((key) => {
                const info = valueInfo(key)
                const isSecret = key.includes('KEY') || key.includes('SECRET')
                return (
                  <label key={key} className="space-y-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[10px] text-text-muted">{key}</span>
                      <span className="text-[9px] text-text-dim truncate">
                        {info.masked || (info.configured ? 'configurado' : 'vazio')}
                      </span>
                    </div>
                    <input
                      type={isSecret ? 'password' : 'text'}
                      value={drafts[key] ?? ''}
                      onChange={(e) => onDraft(key, e.target.value)}
                      placeholder={isSecret ? 'novo valor' : (info.value || 'valor')}
                      className="w-full rounded border border-border bg-bg-surface px-2 py-1.5 text-xs text-white outline-none focus:border-accent"
                    />
                  </label>
                )
              })}
            </div>
          ) : (
            <div className="space-y-3">
              <div className="grid grid-cols-4 gap-2">
                <UsageStat label="Chamadas" value={usage?.calls ?? 0} />
                <UsageStat label="Estimado US$" value={(usage?.estimated_cost_usd ?? 0).toFixed?.(4) ?? '0.0000'} />
                <UsageStat label="Orcamento" value={`US$ ${(usage?.monthly_budget_usd ?? 0).toFixed?.(2) ?? '0.00'}`} />
                <UsageStat label="Uso" value={`${usage?.budget_used_pct ?? 0}%`} />
              </div>
              <div className="grid grid-cols-3 gap-2">
                <UsageStat label="Input tokens" value={usage?.input_tokens ?? 0} />
                <UsageStat label="Output tokens" value={usage?.output_tokens ?? 0} />
                <UsageStat label="Audio seg." value={(usage?.audio_seconds ?? 0).toFixed?.(1) ?? '0.0'} />
              </div>
              <div className="rounded-lg border border-border bg-bg-surface p-3">
                <div className="mb-2 flex items-center justify-between">
                  <p className="text-[10px] uppercase tracking-wider text-text-dim">Eventos recentes</p>
                  <button onClick={onRefresh} className="text-[10px] text-text-muted hover:text-white">Recarregar</button>
                </div>
                <div className="space-y-1 max-h-48 overflow-y-auto">
                  {(usage?.events ?? []).length === 0 ? (
                    <p className="text-xs text-text-dim">Nenhum uso registrado ainda.</p>
                  ) : usage.events.map((event: any, idx: number) => (
                    <div key={`${event.ts}-${idx}`} className="flex items-center justify-between gap-2 rounded bg-bg-panel px-2 py-1">
                      <span className="text-[10px] text-text-muted truncate">
                        {event.ts} - {event.feature} - {event.model}
                      </span>
                      <span className="text-[10px] text-accent">
                        US$ {(event.estimated_cost_usd ?? 0).toFixed?.(4) ?? '0.0000'}
                      </span>
                    </div>
                  ))}
                </div>
                <p className="mt-2 truncate text-[9px] text-text-dim" title={usage?.log_path}>
                  Log local: {usage?.log_path || 'aguardando backend'}
                </p>
              </div>
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
          <button onClick={onRefresh} disabled={loading} className="rounded bg-bg-surface px-3 py-1.5 text-xs text-text-muted hover:text-white disabled:opacity-50">
            Recarregar
          </button>
          <button onClick={onSave} disabled={loading || Object.keys(drafts).length === 0} className="rounded bg-accent px-3 py-1.5 text-xs text-white disabled:opacity-50">
            {loading ? 'Salvando...' : 'Salvar'}
          </button>
        </div>
      </div>
    </div>
  )
}

function GeneralSettingsPanel({
  data, drafts, loading, onDraft, onClearCache, onOpenLogs,
}: {
  data: any
  drafts: Record<string, string>
  loading: boolean
  onDraft: (key: string, value: string) => void
  onClearCache: () => void
  onOpenLogs: () => void
}) {
  const boolValue = (key: string, fallback: boolean) => {
    if (drafts[key] !== undefined) return drafts[key] === 'true'
    if (key === 'CORTACERTO_AUTO_UPDATES') return Boolean(data?.auto_updates ?? fallback)
    if (key === 'CORTACERTO_UPDATE_NOTIFICATIONS') return Boolean(data?.update_notifications ?? fallback)
    if (key === 'CORTACERTO_UI_GPU_RENDERING') return Boolean(data?.ui_gpu_rendering ?? fallback)
    return fallback
  }
  const defaultSaveDir = drafts.CORTACERTO_DEFAULT_SAVE_DIR ?? data?.default_save_dir ?? ''
  const startupLayout = drafts.CORTACERTO_STARTUP_LAYOUT ?? data?.startup_layout ?? 'last'
  const uiTheme = drafts.CORTACERTO_UI_THEME ?? data?.ui_theme ?? 'violet'
  const themeOptions = [
    { id: 'violet', label: 'Violeta', colors: ['#0b0712', '#8b5cf6', '#22d4bc'] },
    { id: 'graphite', label: 'Grafite', colors: ['#08090b', '#6f7f95', '#a8b3c5'] },
    { id: 'midnight', label: 'Midnight', colors: ['#050812', '#2f8cff', '#69b7ff'] },
    { id: 'emerald', label: 'Emerald', colors: ['#050c0a', '#13a075', '#50d9ad'] },
  ]
  const cacheMb = data?.cache?.total_mb ?? 0

  return (
    <div className="space-y-4">
      <section className="rounded-lg border border-border bg-bg-surface p-3 space-y-2">
        <p className="text-[10px] uppercase tracking-wider text-text-dim">Visual do programa</p>
        <div className="grid grid-cols-4 gap-1.5">
          {themeOptions.map((theme) => (
            <button
              key={theme.id}
              onClick={() => onDraft('CORTACERTO_UI_THEME', theme.id)}
              className={`rounded-md border px-2 py-1.5 text-left transition-colors ${uiTheme === theme.id ? 'border-accent bg-accent/10 text-white' : 'border-border bg-bg-panel text-text-muted hover:text-white'}`}
              title={`Tema visual do editor: ${theme.label}`}
            >
              <div className="mb-1 flex gap-0.5">
                {theme.colors.map((color) => (
                  <span key={color} className="h-2 flex-1 rounded-sm" style={{ background: color }} />
                ))}
              </div>
              <span className="block truncate text-[9px]">{theme.label}</span>
            </button>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <label className="space-y-1">
            <span className="text-[10px] text-text-muted">Layout ao abrir</span>
            <select
              value={startupLayout}
              onChange={(e) => onDraft('CORTACERTO_STARTUP_LAYOUT', e.target.value)}
              className="w-full rounded border border-border bg-bg-panel px-2 py-1.5 text-xs text-white outline-none focus:border-accent"
            >
              <option value="last">Ultima escolha</option>
              <option value="default">Layout padrao</option>
              <option value="capcut">Layout CapCut</option>
            </select>
          </label>
          <label className="space-y-1">
            <span className="text-[10px] text-text-muted">Tema visual do editor</span>
            <select
              value={uiTheme}
              onChange={(e) => onDraft('CORTACERTO_UI_THEME', e.target.value)}
              className="w-full rounded border border-border bg-bg-panel px-2 py-1.5 text-xs text-white outline-none focus:border-accent"
            >
              <option value="violet">Violeta profissional</option>
              <option value="graphite">Grafite neutro</option>
              <option value="midnight">Midnight azul</option>
              <option value="emerald">Emerald cinema</option>
            </select>
          </label>
        </div>
        <p className="text-[9px] text-text-dim">
          Define organizacao de paineis e paleta principal da interface.
        </p>
      </section>

      <section className="rounded-lg border border-border bg-bg-surface p-3 space-y-2">
        <p className="text-[10px] uppercase tracking-wider text-text-dim">Atualizacoes</p>
        <CheckRow
          label="Atualizacoes automaticas"
          checked={boolValue('CORTACERTO_AUTO_UPDATES', false)}
          onChange={(checked) => onDraft('CORTACERTO_AUTO_UPDATES', String(checked))}
        />
        <CheckRow
          label="Receber notificacao sobre atualizacoes"
          checked={boolValue('CORTACERTO_UPDATE_NOTIFICATIONS', true)}
          onChange={(checked) => onDraft('CORTACERTO_UPDATE_NOTIFICATIONS', String(checked))}
        />
      </section>

      <section className="rounded-lg border border-border bg-bg-surface p-3 space-y-2">
        <p className="text-[10px] uppercase tracking-wider text-text-dim">Renderizacao da interface</p>
        <CheckRow
          label="Renderizacao de interface por GPU"
          checked={boolValue('CORTACERTO_UI_GPU_RENDERING', false)}
          onChange={(checked) => onDraft('CORTACERTO_UI_GPU_RENDERING', String(checked))}
        />
        <div className="rounded bg-bg-panel px-2 py-1.5">
          <p className="text-[10px] text-text-muted">GPU identificada</p>
          <p className="mt-0.5 text-xs text-white truncate" title={data?.gpu?.label}>
            {data?.gpu?.label || 'GPU nao identificada'}
          </p>
          <p className="mt-1 text-[9px] text-text-dim">
            Padrao: desmarcado. Use apenas se a interface estiver estavel no seu driver.
          </p>
        </div>
      </section>

      <section className="rounded-lg border border-border bg-bg-surface p-3 space-y-2">
        <p className="text-[10px] uppercase tracking-wider text-text-dim">Salvar arquivos</p>
        <input
          type="text"
          value={defaultSaveDir}
          onChange={(e) => onDraft('CORTACERTO_DEFAULT_SAVE_DIR', e.target.value)}
          placeholder="Ex.: C:\\Users\\renan\\Videos\\CortaCerto"
          className="w-full rounded border border-border bg-bg-panel px-2 py-1.5 text-xs text-white outline-none focus:border-accent"
        />
        <p className="text-[9px] text-text-dim">
          Esta pasta sera usada como sugestao inicial ao salvar projetos e exports.
        </p>
      </section>

      <section className="rounded-lg border border-border bg-bg-surface p-3">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[10px] uppercase tracking-wider text-text-dim">Cache</p>
            <p className="mt-1 text-sm font-semibold text-white">{Number(cacheMb).toFixed(2)} MB</p>
            <p className="mt-0.5 truncate text-[9px] text-text-dim" title={data?.cache?.stock_root}>
              Stock: {data?.cache?.stock_root || 'sem cache'}
            </p>
          </div>
          <button
            onClick={onClearCache}
            disabled={loading}
            title="Limpar cache"
            className="flex h-9 w-9 items-center justify-center rounded bg-red-900/40 text-red-300 hover:bg-red-900/70 disabled:opacity-50"
          >
            <Trash2 size={15} />
          </button>
        </div>
      </section>

      <section className="rounded-lg border border-border bg-bg-surface p-3">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[10px] uppercase tracking-wider text-text-dim">Logs de erro</p>
            <p className="mt-1 text-[11px] text-white truncate" title={data?.logs?.file}>
              {data?.logs?.file || 'errors.jsonl'}
            </p>
            <p className="mt-0.5 text-[9px] text-text-dim truncate" title={data?.logs?.dir}>
              {data?.logs?.dir || 'pasta de logs'}
            </p>
          </div>
          <button
            onClick={onOpenLogs}
            title="Abrir pasta de logs"
            className="flex h-9 w-9 items-center justify-center rounded bg-bg-panel text-text-muted hover:text-white"
          >
            <FolderOpen size={15} />
          </button>
        </div>
      </section>
    </div>
  )
}

function CheckRow({
  label, checked, onChange,
}: {
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <label className="flex items-center justify-between gap-3 rounded bg-bg-panel px-2 py-1.5">
      <span className="text-xs text-text-muted">{label}</span>
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 accent-accent"
      />
    </label>
  )
}

function UsageStat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-border bg-bg-surface p-2">
      <p className="text-[9px] uppercase tracking-wider text-text-dim">{label}</p>
      <p className="mt-1 truncate text-sm font-semibold text-white">{value}</p>
    </div>
  )
}

function HeaderBtn({
  icon, title, onClick, disabled = false,
}: {
  icon: React.ReactNode; title: string; onClick?: () => void; disabled?: boolean
}) {
  return (
    <button
      title={title}
      onClick={onClick}
      disabled={disabled}
      className="p-2 rounded-md text-text-muted hover:text-white hover:bg-bg-surface transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
    >
      {icon}
    </button>
  )
}
