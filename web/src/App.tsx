import { useEffect, useState, useCallback } from 'react'
import { Film, Upload, Loader2, FolderOpen, Clock } from 'lucide-react'
import { Header } from './components/Header'
import { LeftRail } from './components/LeftRail/LeftRail'
import { Preview } from './components/Preview/Preview'
import { Timeline } from './components/Timeline/Timeline'
import { Inspector } from './components/Inspector/Inspector'
import { api } from './api/client'
import { useStore, type ProjectState } from './store/useStore'
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
      setProject(proj.data)
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
              <p className="text-xs text-text-dim">Editor com corte automÃ¡tico</p>
            </div>
          </div>

          {/* Features summary */}
          <div className="w-full space-y-1.5 text-xs text-text-muted">
            {['âœ‚ï¸  Remove silÃªncio automaticamente', 'ðŸŽ¬  Preview em tempo real com filtros', 'ðŸŽµ  ImportaÃ§Ã£o de trilha sonora', 'âš¡  ExportaÃ§Ã£o rÃ¡pida com ffmpeg'].map((f) => (
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
                ? <><Loader2 size={16} className="animate-spin" /> Analisandoâ€¦</>
                : <><Upload size={16} /> Abrir VÃ­deo</>
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
            {dragging ? 'Solte para abrir' : 'ou arraste um arquivo de vÃ­deo aqui'}
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
                      {f.type === 'project' ? 'projeto' : 'vÃ­deo'}
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

function beginResize(e: React.MouseEvent<HTMLDivElement>, onDrag: (dx: number, dy: number) => void) {
  e.preventDefault()
  const startX = e.clientX
  const startY = e.clientY
  const previousCursor = document.body.style.cursor
  const previousSelect = document.body.style.userSelect

  const onMove = (ev: MouseEvent) => onDrag(ev.clientX - startX, ev.clientY - startY)
  const onUp = () => {
    document.body.style.cursor = previousCursor
    document.body.style.userSelect = previousSelect
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
  }

  document.body.style.cursor = e.currentTarget.dataset.axis === 'y' ? 'row-resize' : 'col-resize'
  document.body.style.userSelect = 'none'
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
}

function ResizeHandle({
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
      onMouseDown={(e) => beginResize(e, onDrag)}
      className={`group flex items-center justify-center flex-shrink-0 ${isY ? 'h-2 cursor-row-resize' : 'w-2 cursor-col-resize'} ${className}`}
      title={isY ? 'Arraste para ajustar a altura da timeline' : 'Arraste para ajustar a largura do painel'}
    >
      <div className={`${isY ? 'h-px w-full' : 'h-full w-px'} bg-border group-hover:bg-accent transition-colors`} />
    </div>
  )
}

// â”€â”€ App root â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
export default function App() {
  const { project, setProject, workspaceLayout, setWorkspaceLayout } = useStore()

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

  // On mount: restore project from backend, or fall back to localStorage autosave.
  //
  // RACE GUARD (bug 3.5): if the user opens a video before this async restore
  // completes, we'd otherwise OVERWRITE their fresh selection with whatever was
  // in /api/project (stale server state) or localStorage (stale autosave).
  // We check useStore.getState().project at the moment of resolution â€” if a
  // project is already loaded, we silently abort the restore.
  useEffect(() => {
    const restoreIfNoProjectLoaded = (data: ProjectState | null) => {
      if (!data?.loaded || !data?.videoPath) return
      if (useStore.getState().project) return   // user already loaded something â€” don't clobber
      setProject(data)
    }

    api.get('/api/project')
      .then((res) => {
        if (res.data?.loaded) {
          restoreIfNoProjectLoaded(res.data)
        } else {
          try {
            const saved = localStorage.getItem(AUTOSAVE_KEY)
            if (saved) restoreIfNoProjectLoaded(JSON.parse(saved))
          } catch { /* corrupt autosave â€” ignore */ }
        }
      })
      .catch(() => {
        try {
          const saved = localStorage.getItem(AUTOSAVE_KEY)
          if (saved) restoreIfNoProjectLoaded(JSON.parse(saved))
        } catch { /* ignore */ }
      })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-save project state to localStorage (debounced 2 s)
  useEffect(() => {
    if (!project?.videoPath) return
    const timer = setTimeout(() => {
      try { localStorage.setItem(AUTOSAVE_KEY, JSON.stringify(project)) } catch { /* quota full */ }
    }, 2000)
    return () => clearTimeout(timer)
  }, [project])

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
      // WebView2 sometimes omits 'Files' from types â€” be permissive: show overlay
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
      // Always prevent default first â€” otherwise WebView2 navigates to file://
      e.preventDefault()
      dragEnterCount = 0
      overlayEl.style.display = 'none'

      if (!e.dataTransfer) return
      // Internal clip drags (MediaTab â†’ Timeline) use a custom MIME type;
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
            // Decode file:///C:/path â†’ C:\path (Windows) or /path (Unix)
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
    </div>
  )
}
function DefaultWorkspace({ project }: { project: ProjectState | null }) {
  const [sizes, setSizes] = useLayoutSizes()

  return (
    <div className="flex flex-1 min-h-0 relative">
      <LeftRail width={sizes.defaultLeft} />
      <ResizeHandle
        axis="x"
        onDrag={(dx) => setSizes((current) => ({
          ...current,
          defaultLeft: clamp(sizes.defaultLeft + dx, 180, 460),
        }))}
      />
      <div className="flex flex-col flex-1 min-w-0 min-h-0 overflow-hidden relative">
        <div className="flex-1 min-h-0 overflow-hidden">
          <Preview />
        </div>
        <ResizeHandle
          axis="y"
          onDrag={(_dx, dy) => setSizes((current) => ({
            ...current,
            defaultTimeline: clamp(sizes.defaultTimeline - dy, 190, 560),
          }))}
        />
        <Timeline height={sizes.defaultTimeline} />
        {!project && <WelcomeOverlay />}
      </div>
      <ResizeHandle
        axis="x"
        onDrag={(dx) => setSizes((current) => ({
          ...current,
          defaultInspector: clamp(sizes.defaultInspector - dx, 220, 520),
        }))}
      />
      <InspectorPanel className="border-l" width={sizes.defaultInspector} />
    </div>
  )
}

function CapCutWorkspace({ project }: { project: ProjectState | null }) {
  const [sizes, setSizes] = useLayoutSizes()

  return (
    <div className="flex flex-col flex-1 min-h-0 relative bg-bg gap-2 p-2">
      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 min-h-0 overflow-hidden" style={{ width: sizes.capcutLeft }}>
          <LeftRail panel />
        </div>
        <ResizeHandle
          axis="x"
          className="mx-1"
          onDrag={(dx) => setSizes((current) => ({
            ...current,
            capcutLeft: clamp(sizes.capcutLeft + dx, 260, 560),
          }))}
        />
        <div className="flex-1 min-w-0 min-h-0 overflow-hidden rounded-lg border border-border bg-bg-panel">
          <Preview />
        </div>
        <ResizeHandle
          axis="x"
          className="mx-1"
          onDrag={(dx) => setSizes((current) => ({
            ...current,
            capcutInspector: clamp(sizes.capcutInspector - dx, 260, 560),
          }))}
        />
        <InspectorPanel className="rounded-lg border" width={sizes.capcutInspector} />
      </div>
      <ResizeHandle
        axis="y"
        onDrag={(_dx, dy) => setSizes((current) => ({
          ...current,
          capcutTimeline: clamp(sizes.capcutTimeline - dy, 190, 560),
        }))}
      />
      <div className="flex-shrink-0 overflow-hidden rounded-lg border border-border">
        <Timeline height={sizes.capcutTimeline} />
      </div>
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
      <div className="flex-1 overflow-hidden">
        <Inspector />
      </div>
    </aside>
  )
}

