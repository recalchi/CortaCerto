import { useEffect, useState, useCallback } from 'react'
import { Film, Upload, Loader2, FolderOpen, Clock } from 'lucide-react'
import { Header } from './components/Header'
import { LeftRail } from './components/LeftRail/LeftRail'
import { Preview } from './components/Preview/Preview'
import { Timeline } from './components/Timeline/Timeline'
import { Inspector } from './components/Inspector/Inspector'
import { api } from './api/client'
import { useStore, buildPersistableProject, type ProjectState } from './store/useStore'
import { getRecentFiles, addRecentFile } from './utils/recentFiles'

// â”€â”€ Welcome overlay (shown when no project is loaded) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function WelcomeOverlay() {
  const { setProject, setPreviewTime } = useStore()
  const [loading,  setLoading]  = useState(false)
  const [dragging, setDragging] = useState(false)
  const [recents,  setRecents]  = useState(() => getRecentFiles())

  const loadVideoPath = useCallback(async (filePath: string) => {
    setLoading(true)
    try {
      const { exportSettings } = useStore.getState()
      const proj = await api.post('/api/open-project', {
        path: filePath,
        silence_style: exportSettings.silenceStyle,
        auto_cut: exportSettings.silenceEnabled,
      })
      setProject(proj.data)
      setPreviewTime(0)
      addRecentFile(filePath, 'video')
      setRecents(getRecentFiles())
    } catch { /* ignore */ } finally {
      setLoading(false)
    }
  }, [setProject, setPreviewTime])

  const loadProjectPath = useCallback(async (filePath: string) => {
    setLoading(true)
    try {
      const proj = await api.post('/api/load-project', { path: filePath })
      setProject({ ...proj.data, _projectPath: filePath })
      setPreviewTime(0)
      addRecentFile(filePath, 'project')
      setRecents(getRecentFiles())
    } catch { /* ignore */ } finally {
      setLoading(false)
    }
  }, [setProject, setPreviewTime])

  const handleOpen = useCallback(async () => {
    if (loading) return
    try {
      const res = await api.post('/api/open-file-dialog', { type: 'video' })
      if (res.data.path) await loadVideoPath(res.data.path)
    } catch { /* ignore */ }
  }, [loading, loadVideoPath])

  const handleOpenProject = useCallback(async () => {
    if (loading) return
    try {
      const res = await api.post('/api/open-file-dialog', { type: 'project' })
      if (res.data.path) await loadProjectPath(res.data.path)
    } catch { /* ignore */ }
  }, [loading, loadProjectPath])

  // Drop processing is done by the global App-level handler.
  // This local handler only resets the drag highlight.
  const onDrop = useCallback((_e: React.DragEvent) => {
    setDragging(false)
  }, [])

  return (
    <div
      className="absolute inset-0 z-20 flex items-center justify-center"
      style={{ background: 'rgba(10,10,20,0.92)', backdropFilter: 'blur(6px)' }}
      onDrop={onDrop}
      onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
    >
      <div className="flex gap-5 items-start max-w-2xl w-full mx-4">
        {/* Left: main card */}
        <div
          className={`flex flex-col items-center gap-5 p-10 rounded-2xl border-2 transition-all flex-1 ${
            dragging
              ? 'border-accent bg-accent/10 scale-105'
              : 'border-border bg-bg-panel'
          }`}
        >
          {/* Logo */}
          <div className="flex items-center gap-3">
            <div className="w-12 h-12 rounded-xl bg-accent/20 flex items-center justify-center">
              <Film size={26} className="text-accent" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-white tracking-tight">CortaCerto</h1>
              <p className="text-xs text-text-dim">Editor com corte automatico</p>
            </div>
          </div>

          {/* Features summary */}
          <div className="w-full space-y-1.5 text-xs text-text-muted">
            {['Remove silencio automaticamente', 'Preview em tempo real com filtros', 'Importacao de trilha sonora', 'Exportacao rapida com ffmpeg'].map((f) => (
              <div key={f} className="flex items-center gap-2">{f}</div>
            ))}
          </div>

          {/* CTA buttons */}
          <div className="w-full space-y-2">
            <button
              onClick={handleOpen}
              disabled={loading}
              className="w-full flex items-center justify-center gap-2 bg-accent hover:bg-accent-hover disabled:opacity-60 disabled:cursor-wait text-white font-semibold text-sm px-6 py-3 rounded-xl transition-colors shadow-lg shadow-accent/20"
            >
              {loading
                ? <><Loader2 size={16} className="animate-spin" /> Analisando...</>
                : <><Upload size={16} /> Abrir Video</>
              }
            </button>
            <button
              onClick={handleOpenProject}
              disabled={loading}
              className="w-full flex items-center justify-center gap-2 bg-bg-surface hover:bg-border disabled:opacity-60 disabled:cursor-wait text-text-muted hover:text-white text-sm px-6 py-2.5 rounded-xl transition-colors border border-border"
            >
              <FolderOpen size={15} /> Abrir Projeto
            </button>
          </div>

          <p className="text-[11px] text-text-dim text-center">
            {dragging ? 'Solte para abrir' : 'ou arraste um arquivo de video aqui'}
          </p>
        </div>

        {/* Right: recent files panel (only when there are recents) */}
        {recents.length > 0 && (
          <div className="w-56 bg-bg-panel border border-border rounded-2xl p-4 flex flex-col gap-2 self-stretch">
            <p className="text-[10px] text-text-dim uppercase tracking-wider flex items-center gap-1.5 font-semibold">
              <Clock size={10} /> Recentes
            </p>
            <div className="flex-1 space-y-0.5 overflow-y-auto max-h-64">
              {recents.map((f) => (
                <button
                  key={f.path}
                  onClick={() => f.type === 'project' ? loadProjectPath(f.path) : loadVideoPath(f.path)}
                  disabled={loading}
                  className="w-full text-left px-2 py-2 rounded-lg hover:bg-bg-surface disabled:opacity-50 transition-colors group flex items-start gap-2"
                  title={f.path}
                >
                  <span className="flex-shrink-0 mt-0.5 opacity-50 group-hover:opacity-100">
                    {f.type === 'project'
                      ? <FolderOpen size={12} className="text-accent" />
                      : <Film size={12} className="text-text-muted" />
                    }
                  </span>
                  <span className="min-w-0">
                    <p className="text-[11px] text-text-muted group-hover:text-white truncate transition-colors leading-tight">
                      {f.name}
                    </p>
                    <p className="text-[9px] text-text-dim truncate mt-0.5 opacity-60">
                      {f.type === 'project' ? 'projeto' : 'video'}
                    </p>
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

const AUTOSAVE_KEY = 'cortacerto_autosave'
const LAYOUT_SIZES_KEY = 'cortacerto_layout_sizes_v1'

type LayoutSizes = {
  defaultLeft: number
  defaultInspector: number
  defaultTimeline: number
  capcutLeft: number
  capcutInspector: number
  capcutTimeline: number
}

const DEFAULT_LAYOUT_SIZES: LayoutSizes = {
  defaultLeft: 224,
  defaultInspector: 280,
  defaultTimeline: 320,
  capcutLeft: 360,
  capcutInspector: 340,
  capcutTimeline: 300,
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value))
}

function loadLayoutSizes(): LayoutSizes {
  try {
    const saved = localStorage.getItem(LAYOUT_SIZES_KEY)
    if (!saved) return DEFAULT_LAYOUT_SIZES
    return { ...DEFAULT_LAYOUT_SIZES, ...JSON.parse(saved) }
  } catch {
    return DEFAULT_LAYOUT_SIZES
  }
}

function useLayoutSizes() {
  const [sizes, setSizesState] = useState<LayoutSizes>(() => loadLayoutSizes())
  const setSizes = useCallback((next: LayoutSizes | ((current: LayoutSizes) => LayoutSizes)) => {
    setSizesState((current) => {
      const resolved = typeof next === 'function' ? next(current) : next
      try { localStorage.setItem(LAYOUT_SIZES_KEY, JSON.stringify(resolved)) } catch { /* ignore */ }
      return resolved
    })
  }, [])
  return [sizes, setSizes] as const
}

function beginResize(e: React.PointerEvent<HTMLDivElement>, onDrag: (dx: number, dy: number) => void) {
  e.preventDefault()
  e.stopPropagation()
  const startX = e.clientX
  const startY = e.clientY
  const previousCursor = document.body.style.cursor
  const previousSelect = document.body.style.userSelect

  const onMove = (ev: PointerEvent) => onDrag(ev.clientX - startX, ev.clientY - startY)
  const onUp = () => {
    document.body.style.cursor = previousCursor
    document.body.style.userSelect = previousSelect
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
    window.removeEventListener('pointercancel', onUp)
  }

  document.body.style.cursor = e.currentTarget.dataset.axis === 'y' ? 'row-resize' : 'col-resize'
  document.body.style.userSelect = 'none'
  e.currentTarget.setPointerCapture?.(e.pointerId)
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp)
  window.addEventListener('pointercancel', onUp)
}

type ResizeEdgeName = 'left' | 'right' | 'top' | 'bottom'

function ResizeEdge({
  edge,
  onDrag,
  className = '',
}: {
  edge: ResizeEdgeName
  onDrag: (dx: number, dy: number) => void
  className?: string
}) {
  const isY = edge === 'top' || edge === 'bottom'
  const positionClass =
    edge === 'left' ? 'left-[-7px] top-0 bottom-0 w-3.5 cursor-col-resize' :
    edge === 'right' ? 'right-[-7px] top-0 bottom-0 w-3.5 cursor-col-resize' :
    edge === 'top' ? 'top-[-7px] left-0 right-0 h-3.5 cursor-row-resize' :
    'bottom-[-7px] left-0 right-0 h-3.5 cursor-row-resize'
  const lineClass =
    edge === 'left' ? 'left-[6px] top-0 bottom-0 w-px' :
    edge === 'right' ? 'right-[6px] top-0 bottom-0 w-px' :
    edge === 'top' ? 'top-[6px] left-0 right-0 h-px' :
    'bottom-[6px] left-0 right-0 h-px'
  return (
    <div
      data-axis={isY ? 'y' : 'x'}
      data-layout-resize-handle="true"
      onPointerDown={(e) => beginResize(e, onDrag)}
      className={`group absolute ${positionClass} z-40 flex items-center justify-center touch-none select-none ${className}`}
      title={isY ? 'Arraste para ajustar a altura da timeline' : 'Arraste para ajustar a largura do painel'}
    >
      <div
        className={`absolute ${lineClass} bg-border group-hover:bg-accent transition-colors`}
      />
      <div
        className={`rounded-full bg-border/70 opacity-0 group-hover:opacity-100 group-hover:bg-accent transition-all ${
          isY ? 'h-1 w-12' : 'h-12 w-1'
        }`}
      />
    </div>
  )
}

function SplitHandle({
  axis,
  onDrag,
  className = '',
}: {
  axis: 'x' | 'y'
  onDrag: (dx: number, dy: number) => void
  className?: string
}) {
  const isY = axis === 'y'
  return (
    <div
      data-axis={axis}
      data-layout-resize-handle="true"
      onPointerDown={(e) => beginResize(e, onDrag)}
      className={`group relative z-50 flex flex-shrink-0 items-center justify-center touch-none select-none ${
        isY ? 'h-2 cursor-row-resize' : 'w-2 cursor-col-resize'
      } ${className}`}
      title={isY ? 'Arraste para redimensionar os campos acima/abaixo' : 'Arraste para redimensionar os campos laterais'}
    >
      <div
        className={`${isY ? 'h-px w-full' : 'h-full w-px'} bg-border/70 group-hover:bg-accent/60 group-active:bg-accent/70 transition-colors`}
      />
      <div
        className={`absolute rounded-full bg-accent/70 opacity-0 group-hover:opacity-80 group-active:opacity-90 transition-opacity ${
          isY ? 'h-0.5 w-10' : 'h-10 w-0.5'
        }`}
      />
    </div>
  )
}

function ResizablePane({
  children,
  className = '',
  style,
  edges,
}: {
  children: React.ReactNode
  className?: string
  style?: React.CSSProperties
  edges?: Partial<Record<ResizeEdgeName, (dx: number, dy: number) => void>>
}) {
  return (
    <div className={`relative min-w-0 min-h-0 ${className}`} style={style}>
      {children}
      {edges?.left && <ResizeEdge edge="left" onDrag={edges.left} />}
      {edges?.right && <ResizeEdge edge="right" onDrag={edges.right} />}
      {edges?.top && <ResizeEdge edge="top" onDrag={edges.top} />}
      {edges?.bottom && <ResizeEdge edge="bottom" onDrag={edges.bottom} />}
    </div>
  )
}

// â”€â”€ App root â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
export default function App() {
  const {
    project,
    setProject,
    setPreviewTime,
    workspaceLayout,
    setWorkspaceLayout,
    projectName,
    projectFilePath,
    isDirty,
    markSaved,
  } = useStore()

  useEffect(() => {
    let cancelled = false
    api.get('/api/settings')
      .then((res) => {
        if (cancelled) return
        const theme = res.data?.general?.ui_theme || 'violet'
        document.body.classList.remove('theme-violet', 'theme-graphite', 'theme-midnight', 'theme-emerald')
        document.body.classList.add(`theme-${theme}`)
      })
      .catch(() => undefined)
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    let cancelled = false
    api.get('/api/settings')
      .then((res) => {
        if (cancelled) return
        const preset = res.data?.general?.startup_layout
        if (preset === 'default' || preset === 'capcut') {
          setWorkspaceLayout(preset)
          return
        }
        const saved = localStorage.getItem('cortacerto_workspace_layout')
        if (saved === 'default' || saved === 'capcut') setWorkspaceLayout(saved)
      })
      .catch(() => {
        const saved = localStorage.getItem('cortacerto_workspace_layout')
        if (saved === 'default' || saved === 'capcut') setWorkspaceLayout(saved)
      })
    return () => { cancelled = true }
  }, [setWorkspaceLayout])

  useEffect(() => {
    localStorage.setItem('cortacerto_workspace_layout', workspaceLayout)
  }, [workspaceLayout])

  // On mount: bootstrap from startup target, else restore project from backend
  // or localStorage autosave.
  //
  // RACE GUARD (bug 3.5): if the user opens a video before this async restore
  // completes, we'd otherwise OVERWRITE their fresh selection with whatever was
  // in /api/project (stale server state) or localStorage (stale autosave).
  // We check useStore.getState().project at the moment of resolution; if a
  // project is already loaded, we silently abort the restore.
  useEffect(() => {
    const restoreIfNoProjectLoaded = (data: ProjectState | null) => {
      if (!data?.loaded || !data?.videoPath) return
      if (useStore.getState().project) return   // user already loaded something; don't clobber
      setProject(data)
    }

    const boot = new URLSearchParams(window.location.search)
    const bootKind = (boot.get('boot_kind') || '').trim()
    const bootPath = (boot.get('boot_path') || '').trim()
    const bootName = (boot.get('boot_name') || '').trim()

    const clearBootParams = () => {
      if (!boot.has('boot_kind') && !boot.has('boot_path') && !boot.has('boot_name')) return
      const next = new URLSearchParams(window.location.search)
      next.delete('boot_kind')
      next.delete('boot_path')
      next.delete('boot_name')
      const qs = next.toString()
      const nextUrl = `${window.location.pathname}${qs ? `?${qs}` : ''}`
      window.history.replaceState({}, '', nextUrl)
    }

    if (bootPath && (bootKind === 'video' || bootKind === 'project' || bootKind === 'new')) {
      const bootstrap = async () => {
        try {
          if (bootKind === 'video') {
            const { exportSettings } = useStore.getState()
            const res = await api.post('/api/open-project', {
              path: bootPath,
              silence_style: exportSettings.silenceStyle,
              auto_cut: exportSettings.silenceEnabled,
            })
            if (!useStore.getState().project) {
              setProject(res.data)
              setPreviewTime(0)
              addRecentFile(bootPath, 'video')
            }
          } else if (bootKind === 'project') {
            const res = await api.post('/api/load-project', { path: bootPath })
            if (!useStore.getState().project) {
              setProject({ ...res.data, _projectPath: bootPath })
              setPreviewTime(0)
              addRecentFile(bootPath, 'project')
            }
          } else {
            const name = bootName || bootPath.replace(/\\/g, '/').split('/').pop()?.replace(/\.[^.]+$/, '') || 'Projeto sem nome'
            const blankProject: ProjectState = {
              loaded: true,
              videoPath: null,
              duration_s: 0,
              waveform: [],
              aspect_ratio: '16:9',
              video_track: { name: 'Video', clips: [] },
              audio_track: { name: 'Audio', clips: [] },
              text_track: { name: 'Texto', clips: [] },
              overlay_track: { name: 'Overlay', clips: [] },
              extra_video_tracks: [],
              extra_audio_tracks: [],
              extra_overlay_tracks: [],
              removed_ranges: [],
              saved_time_s: 0,
            }
            const projectWithName = { ...blankProject, _projectName: name, _projectPath: bootPath }
            await api.post('/api/save-project', { path: bootPath, project: projectWithName })
            if (!useStore.getState().project) {
              setProject(projectWithName as ProjectState)
              setPreviewTime(0)
              addRecentFile(bootPath, 'project')
            }
          }
        } catch {
          // fall through to standard restore below
          api.get('/api/project')
            .then((res) => {
              if (res.data?.loaded) {
                restoreIfNoProjectLoaded(res.data)
              } else {
                try {
                  const saved = localStorage.getItem(AUTOSAVE_KEY)
                  if (saved) restoreIfNoProjectLoaded(JSON.parse(saved))
                } catch { /* ignore */ }
              }
            })
            .catch(() => {
              try {
                const saved = localStorage.getItem(AUTOSAVE_KEY)
                if (saved) restoreIfNoProjectLoaded(JSON.parse(saved))
              } catch { /* ignore */ }
            })
        } finally {
          clearBootParams()
        }
      }
      bootstrap()
      return
    }

    api.get('/api/project')
      .then((res) => {
        if (res.data?.loaded) {
          restoreIfNoProjectLoaded(res.data)
        } else {
          try {
            const saved = localStorage.getItem(AUTOSAVE_KEY)
            if (saved) restoreIfNoProjectLoaded(JSON.parse(saved))
              } catch { /* corrupt autosave; ignore */ }
        }
      })
      .catch(() => {
        try {
          const saved = localStorage.getItem(AUTOSAVE_KEY)
          if (saved) restoreIfNoProjectLoaded(JSON.parse(saved))
        } catch { /* ignore */ }
      })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-save project state to localStorage and, when the project already has
  // a .ccproj path, silently persist to disk too. This keeps "Salvar rapido"
  // and reopen behavior aligned with the actual editor state.
  useEffect(() => {
    if (!project?.loaded) return
    const snapshot = buildPersistableProject(project, projectName, projectFilePath)
    const timer = setTimeout(() => {
      try { localStorage.setItem(AUTOSAVE_KEY, JSON.stringify(snapshot)) } catch { /* quota full */ }
    }, 1200)
    return () => clearTimeout(timer)
  }, [project, projectName, projectFilePath])

  useEffect(() => {
    if (!project?.loaded || !projectFilePath || !isDirty) return
    const snapshot = buildPersistableProject(project, projectName, projectFilePath)
    let cancelled = false
    const timer = window.setTimeout(() => {
      api.post('/api/save-project', { path: projectFilePath, project: snapshot })
        .then(() => {
          if (!cancelled) markSaved()
        })
        .catch((err) => {
          console.warn('[CortaCerto] autosave .ccproj failed:', err)
        })
    }, 3500)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [project, projectName, projectFilePath, isDirty, markSaved])

  useEffect(() => {
    if (!project?.loaded || !projectFilePath) return
    let cancelled = false
    const ping = () => {
      if (cancelled) return
      api.post('/api/project-usage/ping', {
        path: projectFilePath,
        name: projectName || projectFilePath.replace(/\\/g, '/').split('/').pop()?.replace(/\.[^.]+$/, '') || 'Projeto',
      }).catch(() => {})
    }
    ping()
    const timer = window.setInterval(ping, 30000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [project?.loaded, projectFilePath, projectName])

  // Global drag-and-drop router.
  // WebView2 has erratic drop handling and our local React onDrop handlers
  // (MediaTab, Timeline) miss drops outside their elements. This single
  // document-level handler is the AUTHORITATIVE drop processor: it captures
  // every file drop, extracts the path (or falls back to native dialog), and
  // routes to the appropriate import action based on file extension and
  // current project state.
  //
  // We use the CAPTURE phase so this runs before any browser default behavior
  // (which in WebView2 would navigate to the file:// URL and replace the page).
  useEffect(() => {
    const AUDIO_EXTS = ['.mp3', '.wav', '.aac', '.ogg', '.m4a', '.flac', '.opus', '.wma']
    const IMAGE_EXTS = ['.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp']
    const overlayEl = (() => {
      const d = document.createElement('div')
      d.id = 'cc-drop-overlay'
      d.style.cssText = `
        position: fixed; inset: 0; z-index: 9999;
        background: rgba(13,13,24,0.85); backdrop-filter: blur(4px);
        display: none; align-items: center; justify-content: center;
        color: #4ade80; font-size: 18px; font-weight: 600;
        pointer-events: none;
        border: 4px dashed #4ade80; border-radius: 8px;
      `
      d.textContent = 'Solte o arquivo aqui'
      document.body.appendChild(d)
      return d
    })()

    let dragEnterCount = 0

    const onDragEnter = (e: DragEvent) => {
      // WebView2 sometimes omits 'Files' from types; be permissive: show overlay
      // for any drag that ISN'T our internal clip drag.
      if (e.dataTransfer?.types.includes('application/x-cortacerto-clip')) return
      dragEnterCount++
      overlayEl.style.display = 'flex'
    }
    const onDragLeave = (e: DragEvent) => {
      if (e.dataTransfer?.types.includes('application/x-cortacerto-clip')) return
      dragEnterCount = Math.max(0, dragEnterCount - 1)
      if (dragEnterCount === 0) overlayEl.style.display = 'none'
    }
    const onDragOver = (e: DragEvent) => {
      if (e.dataTransfer?.types.includes('application/x-cortacerto-clip')) return
      e.preventDefault()
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy'
    }

    const routeFile = async (filePath: string) => {
      const ext = ('.' + filePath.split('.').pop()).toLowerCase()
      const isAudio = AUDIO_EXTS.includes(ext)
      const isImage = IMAGE_EXTS.includes(ext)
      const proj = useStore.getState().project
      try {
        if (isAudio) {
          // Probe duration + waveform via backend
          const wfRes = await fetch(`http://127.0.0.1:7472/api/audio-waveform?path=${encodeURIComponent(filePath)}&bins=300`)
          const wfData = await wfRes.json().catch(() => ({ samples: [], duration_s: 60 }))
          useStore.getState().importAudio(filePath, wfData.duration_s ?? 60, wfData.samples ?? [])
          return
        }
        if (isImage) {
          useStore.getState().importImage(filePath, 5)
          return
        }
        // Video file: append if project loaded, otherwise open as new
        const { exportSettings } = useStore.getState()
        if (proj) {
          const r = await api.post('/api/analyze-video', { path: filePath, silence_style: exportSettings.silenceStyle, auto_cut: exportSettings.silenceEnabled })
          useStore.getState().appendVideo(
            r.data.clips ?? [],
            r.data.waveform ?? [],
            r.data.video_codec && r.data.proxy_status && r.data.proxy_status !== 'not_needed'
              ? { source_path: filePath, proxy_path: r.data.proxy_path ?? '', proxy_status: r.data.proxy_status }
              : undefined,
          )
        } else {
          const r = await api.post('/api/open-project', { path: filePath, silence_style: exportSettings.silenceStyle, auto_cut: exportSettings.silenceEnabled })
          useStore.getState().setProject(r.data)
          useStore.getState().setPreviewTime(0)
        }
      } catch (err) {
        console.error('[CortaCerto] Drop routing failed:', err)
      }
    }

    const onDrop = async (e: DragEvent) => {
      // Always prevent default first; otherwise WebView2 navigates to file://
      e.preventDefault()
      dragEnterCount = 0
      overlayEl.style.display = 'none'

      if (!e.dataTransfer) return
      // Internal clip drags (MediaTab -> Timeline) use a custom MIME type;
      // let the local Timeline drop handler process them.
      if (e.dataTransfer.types.includes('application/x-cortacerto-clip')) return

      // Try 1: File.path (rarely available in WebView2 but works in Electron-like wrappers)
      const file = e.dataTransfer.files[0] as any
      if (file?.path) { routeFile(file.path); return }

      // Try 2: file:// URI via text/uri-list
      const uriList = e.dataTransfer.getData('text/uri-list')
      if (uriList) {
        for (const line of uriList.split('\n').map((l) => l.trim()).filter(Boolean)) {
          if (line.startsWith('file:///')) {
            // Decode file:///C:/path -> C:\path (Windows) or /path (Unix)
            let p = decodeURIComponent(line.replace(/^file:\/\/\//, ''))
            if (/^[a-zA-Z]:/.test(p)) p = p.replace(/\//g, '\\')
            else p = '/' + p
            routeFile(p); return
          }
        }
      }

      // Try 3: plain text
      const text = e.dataTransfer.getData('text/plain')
      if (text?.startsWith('file:///')) {
        let p = decodeURIComponent(text.replace(/^file:\/\/\//, ''))
        if (/^[a-zA-Z]:/.test(p)) p = p.replace(/\//g, '\\')
        else p = '/' + p
        routeFile(p); return
      }

      // Final fallback: WebView2 stripped the path. Open native dialog using
      // the file's MIME / name (if any) to pick the right dialog type.
      const fname = (file?.name ?? '').toLowerCase()
      const mime  = (file?.type ?? '').toLowerCase()
      const isAudio = AUDIO_EXTS.some((x) => fname.endsWith(x)) || mime.startsWith('audio/')
      const isImage = IMAGE_EXTS.some((x) => fname.endsWith(x)) || mime.startsWith('image/')
      const dialogType = isAudio ? 'audio' : isImage ? 'image' : 'video'
      try {
        const res = await api.post('/api/open-file-dialog', { type: dialogType })
        const p: string = res.data.path
        if (p) routeFile(p)
      } catch (err) {
        console.error('[CortaCerto] Drop fallback dialog failed:', err)
      }
    }

    document.addEventListener('dragenter', onDragEnter, true)
    document.addEventListener('dragleave', onDragLeave, true)
    document.addEventListener('dragover',  onDragOver,  true)
    document.addEventListener('drop',      onDrop,      true)

    return () => {
      document.removeEventListener('dragenter', onDragEnter, true)
      document.removeEventListener('dragleave', onDragLeave, true)
      document.removeEventListener('dragover',  onDragOver,  true)
      document.removeEventListener('drop',      onDrop,      true)
      overlayEl.remove()
    }
  }, [])

  return (
    <div className="flex flex-col h-screen bg-bg overflow-hidden">
      {/* Top bar */}
      <Header />

      {workspaceLayout === 'capcut' ? <CapCutWorkspace project={project} /> : <DefaultWorkspace project={project} />}
      <RenderStatusFooter />
    </div>
  )
}

function RenderStatusFooter() {
  const { isRendering, renderProgress, renderMessage, renderError, renderOutputPath } = useStore()
  if (!isRendering && !renderError && !renderOutputPath) return null
  const statusText = isRendering
    ? (renderMessage || 'Exportando video...')
    : renderError
      ? renderError
      : renderOutputPath
        ? `Exportado: ${renderOutputPath.replace(/\\/g, '/').split('/').pop()}`
        : ''
  return (
    <div className="h-8 flex-shrink-0 border-t border-border bg-black/80 px-3 flex items-center gap-3 text-[11px] text-text-muted">
      <span className={`font-medium ${renderError ? 'text-red-300' : isRendering ? 'text-accent' : 'text-emerald-300'}`}>
        {renderError ? 'Erro no render' : isRendering ? 'Renderizando' : 'Concluido'}
      </span>
      <div className="h-1.5 w-44 rounded-full bg-bg-surface overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-300 ${renderError ? 'bg-red-400' : 'bg-accent'}`}
          style={{ width: `${renderError ? 100 : Math.max(0, Math.min(100, renderProgress))}%` }}
        />
      </div>
      <span className="w-10 text-right tabular-nums">{Math.round(renderError ? 100 : renderProgress)}%</span>
      <span className="min-w-0 flex-1 truncate" title={statusText}>{statusText}</span>
    </div>
  )
}

function DefaultWorkspace({ project }: { project: ProjectState | null }) {
  const [sizes, setSizes] = useLayoutSizes()

  return (
    <div className="flex flex-1 min-h-0 relative">
      <ResizablePane
        className="flex-shrink-0"
        style={{ width: sizes.defaultLeft }}
      >
        <LeftRail width={sizes.defaultLeft} />
      </ResizablePane>
      <SplitHandle
        axis="x"
        onDrag={(dx) => setSizes((current) => ({
          ...current,
          defaultLeft: clamp(sizes.defaultLeft + dx, 180, 520),
        }))}
      />
      <div className="flex flex-col flex-1 min-w-0 min-h-0 overflow-hidden relative">
        <ResizablePane
          className="flex-1 overflow-hidden"
        >
          <Preview />
        </ResizablePane>
        <SplitHandle
          axis="y"
          onDrag={(_dx, dy) => setSizes((current) => ({
            ...current,
            defaultTimeline: clamp(sizes.defaultTimeline - dy, 190, 620),
          }))}
        />
        <ResizablePane
          className="flex-shrink-0 overflow-hidden"
          style={{ height: sizes.defaultTimeline }}
        >
          <Timeline height={sizes.defaultTimeline} />
        </ResizablePane>
        {!project && <WelcomeOverlay />}
      </div>
      <SplitHandle
        axis="x"
        onDrag={(dx) => setSizes((current) => ({
          ...current,
          defaultInspector: clamp(sizes.defaultInspector - dx, 220, 560),
        }))}
      />
      <ResizablePane
        className="flex-shrink-0"
        style={{ width: sizes.defaultInspector }}
      >
        <InspectorPanel className="border-l w-full h-full" />
      </ResizablePane>
    </div>
  )
}

function CapCutWorkspace({ project }: { project: ProjectState | null }) {
  const [sizes, setSizes] = useLayoutSizes()

  return (
    <div className="flex flex-col flex-1 min-h-0 relative bg-bg gap-2 p-2">
      <div className="flex min-h-0 flex-1">
        <ResizablePane
          className="flex-shrink-0 overflow-hidden"
          style={{ width: sizes.capcutLeft }}
        >
          <LeftRail panel />
        </ResizablePane>
        <SplitHandle
          axis="x"
          onDrag={(dx) => setSizes((current) => ({
            ...current,
            capcutLeft: clamp(sizes.capcutLeft + dx, 220, 620),
          }))}
        />
        <ResizablePane
          className="flex-1 overflow-hidden rounded-lg border border-border bg-bg-panel"
        >
          <Preview />
        </ResizablePane>
        <SplitHandle
          axis="x"
          onDrag={(dx) => setSizes((current) => ({
            ...current,
            capcutInspector: clamp(sizes.capcutInspector - dx, 220, 620),
          }))}
        />
        <ResizablePane
          className="flex-shrink-0"
          style={{ width: sizes.capcutInspector }}
        >
          <InspectorPanel className="rounded-lg border w-full h-full" />
        </ResizablePane>
      </div>
      <SplitHandle
        axis="y"
        onDrag={(_dx, dy) => setSizes((current) => ({
          ...current,
          capcutTimeline: clamp(sizes.capcutTimeline - dy, 190, 620),
        }))}
      />
      <ResizablePane
        className="flex-shrink-0 overflow-hidden rounded-lg border border-border"
        style={{ height: sizes.capcutTimeline }}
      >
        <Timeline height={sizes.capcutTimeline} />
      </ResizablePane>
      {!project && <WelcomeOverlay />}
    </div>
  )
}

function InspectorPanel({ className = '', width }: { className?: string; width?: number }) {
  return (
    <aside className={`flex flex-col bg-bg-rail border-border flex-shrink-0 min-h-0 ${className}`} style={width ? { width } : undefined}>
      <div className="h-8 border-b border-border flex items-center px-3 bg-bg-panel">
        <span className="text-[10px] text-text-dim uppercase tracking-wider font-semibold">Propriedades</span>
      </div>
      <LayersPanel />
      <div className="flex-1 overflow-hidden">
        <Inspector />
      </div>
    </aside>
  )
}

function LayersPanel() {
  const {
    project,
    selectedClipId,
    setSelectedClip,
    updateClip,
    trackStates,
    setTrackState,
  } = useStore()
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('cc_layers_panel_collapsed') === '1')

  useEffect(() => {
    localStorage.setItem('cc_layers_panel_collapsed', collapsed ? '1' : '0')
  }, [collapsed])

  if (!project) return null

  const sections = [
    { id: 'overlay', stateId: 'overlay', label: 'Overlay', muted: false, hidden: trackStates.overlay?.hidden, clips: project.overlay_track.clips },
    ...(project.extra_overlay_tracks ?? []).map((t, ix) => {
      const stateId = `overlay-${ix + 1}`
      return { id: `overlay_${ix + 1}`, stateId, label: t.name || `Overlay ${ix + 2}`, muted: false, hidden: trackStates[stateId]?.hidden, clips: t.clips }
    }),
    { id: 'text', stateId: 'text', label: 'Texto', muted: false, hidden: trackStates.text?.hidden, clips: project.text_track.clips },
    { id: 'video', stateId: 'video', label: 'Video', muted: false, hidden: trackStates.video?.hidden, clips: project.video_track.clips },
    ...(project.extra_video_tracks ?? []).map((t, ix) => {
      const stateId = `video-${ix + 1}`
      return { id: `video_${ix + 1}`, stateId, label: t.name || `Video ${ix + 2}`, muted: false, hidden: trackStates[stateId]?.hidden, clips: t.clips }
    }),
    { id: 'audio', stateId: 'audio', label: 'Audio', muted: trackStates.audio?.muted, hidden: trackStates.audio?.hidden, clips: project.audio_track.clips },
    ...(project.extra_audio_tracks ?? []).map((t, ix) => {
      const stateId = `audio-${ix + 1}`
      return { id: `audio_${ix + 1}`, stateId, label: t.name || `Audio ${ix + 2}`, muted: trackStates[stateId]?.muted, hidden: trackStates[stateId]?.hidden, clips: t.clips }
    }),
  ]

  const allClips = sections.flatMap((section) => (
    section.clips.map((clip) => ({ ...clip, sectionId: section.id, sectionStateId: section.stateId, sectionLabel: section.label }))
  )).sort((a, b) => (b.z_order ?? 0) - (a.z_order ?? 0) || a.start_s - b.start_s)

  return (
    <div className="border-b border-border bg-bg-panel">
      <button
        onClick={() => setCollapsed((v) => !v)}
        className="flex h-8 w-full items-center justify-between px-3 text-left text-[10px] uppercase tracking-wider text-text-dim hover:text-white"
      >
        <span>Camadas</span>
        <span>{collapsed ? '+' : '-'}</span>
      </button>
      {!collapsed && (
        <div className="max-h-44 overflow-y-auto px-2 pb-2 space-y-1">
          <div className="grid grid-cols-2 gap-1 pb-1">
            {(['overlay', 'text', 'video', 'audio'] as const).map((id) => (
              <button
                key={id}
                onClick={() => setTrackState(id, id === 'audio'
                  ? { muted: !(trackStates[id]?.muted ?? false) }
                  : { hidden: !(trackStates[id]?.hidden ?? false) })}
                className="rounded border border-border bg-bg-surface px-2 py-1 text-[9px] text-text-muted hover:text-white"
              >
                {id === 'audio'
                  ? `${trackStates[id]?.muted ? 'Ativar' : 'Mutar'} audio`
                  : `${trackStates[id]?.hidden ? 'Mostrar' : 'Ocultar'} ${id}`}
              </button>
            ))}
          </div>
          {allClips.length === 0 ? (
            <p className="rounded border border-dashed border-border p-2 text-center text-[10px] text-text-dim">
              Sem camadas na timeline.
            </p>
          ) : allClips.map((clip) => {
            const selected = clip.id === selectedClipId
            return (
              <div
                key={clip.id}
                onClick={() => setSelectedClip(clip.id)}
                className={`group rounded border px-2 py-1 cursor-pointer ${selected ? 'border-accent bg-accent/15' : 'border-border bg-bg-surface hover:border-border-light'}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-[10px] text-white">{clip.label || clip.clip_type}</p>
                    <p className="truncate text-[9px] text-text-dim">
                      {clip.sectionLabel} | {clip.clip_type} | {clip.start_s.toFixed(1)}s
                    </p>
                  </div>
                  <div className="flex items-center gap-1 opacity-70 group-hover:opacity-100">
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        const state = trackStates[clip.sectionStateId]
                        if (clip.sectionStateId.startsWith('audio')) {
                          setTrackState(clip.sectionStateId, { muted: !(state?.muted ?? false) })
                        } else {
                          setTrackState(clip.sectionStateId, { hidden: !(state?.hidden ?? false) })
                        }
                      }}
                      className="rounded bg-bg-panel px-1.5 py-0.5 text-[9px] text-text-muted hover:text-white"
                      title={clip.sectionStateId.startsWith('audio') ? 'Mutar/ativar esta faixa' : 'Ocultar/mostrar esta faixa'}
                    >
                      {clip.sectionStateId.startsWith('audio')
                        ? (trackStates[clip.sectionStateId]?.muted ? 'M' : 'S')
                        : (trackStates[clip.sectionStateId]?.hidden ? 'V' : 'O')}
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        updateClip(clip.id, { z_order: (clip.z_order ?? 0) + 1 })
                      }}
                      className="rounded bg-bg-panel px-1.5 py-0.5 text-[9px] text-text-muted hover:text-white"
                      title="Subir camada"
                    >
                      ^
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        updateClip(clip.id, { z_order: (clip.z_order ?? 0) - 1 })
                      }}
                      className="rounded bg-bg-panel px-1.5 py-0.5 text-[9px] text-text-muted hover:text-white"
                      title="Descer camada"
                    >
                      v
                    </button>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

