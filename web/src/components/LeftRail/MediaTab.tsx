import {
  AlertCircle, Download, FilePlus, Film, FolderOpen, Heart, Image,
  Loader2, Music, RefreshCw, Search, Settings, Trash2, Upload, X,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../../api/client'
import { useStore } from '../../store/useStore'
import { addRecentFile, clearRecentFiles, getRecentFiles, RecentFile } from '../../utils/recentFiles'

const API = 'http://127.0.0.1:7472'

type MediaBinTab = 'files' | 'videos' | 'images' | 'audio' | 'sfx' | 'favorites' | 'downloads'
type MediaSort = 'recent' | 'name' | 'provider' | 'duration'

interface StockAsset {
  id: string
  provider: string
  type: 'video' | 'image' | 'audio'
  title: string
  author?: string
  license?: string
  source_url?: string
  download_url?: string
  thumbnail_url?: string
  duration_s?: number | null
  width?: number | null
  height?: number | null
  local_path?: string
}

const STOCK_TABS: Array<{ id: MediaBinTab; label: string; icon: ReactNode }> = [
  { id: 'files',     label: 'Meus',      icon: <FolderOpen size={12} /> },
  { id: 'videos',    label: 'Videos',    icon: <Film size={12} /> },
  { id: 'images',    label: 'Imagens',   icon: <Image size={12} /> },
  { id: 'audio',     label: 'Audio',     icon: <Music size={12} /> },
  { id: 'sfx',       label: 'Sons',      icon: <Search size={12} /> },
  { id: 'favorites', label: 'Favoritos', icon: <Heart size={12} /> },
  { id: 'downloads', label: 'Baixados',  icon: <Download size={12} /> },
]

const PROVIDERS = {
  videos: ['pexels', 'pixabay'],
  images: ['pexels', 'pixabay', 'unsplash'],
  audio:  ['freesound'],
  sfx:    ['freesound'],
} as const

const STOCK_ENV_NAMES = [
  'PEXELS_API_KEY',
  'PIXABAY_API_KEY',
  'UNSPLASH_APP_ID',
  'UNSPLASH_ACCESS_KEY',
  'UNSPLASH_SECRET_KEY',
  'FREESOUND_API_KEY',
  'FREESOUND_CLIENT_ID',
  'FREESOUND_CLIENT_SECRET',
]

function stockTypeForTab(tab: MediaBinTab): 'video' | 'image' | 'audio' {
  if (tab === 'videos') return 'video'
  if (tab === 'audio' || tab === 'sfx') return 'audio'
  return 'image'
}

function basename(path: string): string {
  return path.replace(/\\/g, '/').split('/').pop() || path
}

function recentThumbUrl(file: RecentFile): string | null {
  if (file.type === 'video') return `${API}/api/thumb?path=${encodeURIComponent(file.path)}&t=0&w=160`
  if (file.type === 'image') return `${API}/api/serve-file?path=${encodeURIComponent(file.path)}`
  return null
}

function recentKindLabel(type: RecentFile['type']): string {
  if (type === 'video') return 'Video'
  if (type === 'image') return 'Imagem'
  if (type === 'audio') return 'Audio'
  return 'Projeto'
}

async function probeAudioDuration(path: string): Promise<number> {
  return new Promise((resolve) => {
    const a = new Audio(`${API}/api/serve-file?path=${encodeURIComponent(path)}`)
    const cleanup = () => { a.src = '' }
    a.addEventListener('loadedmetadata', () => { resolve(a.duration || 60); cleanup() }, { once: true })
    a.addEventListener('error', () => { resolve(60); cleanup() }, { once: true })
    a.load()
  })
}

async function fetchAudioWaveform(path: string): Promise<{ samples: number[]; duration_s: number }> {
  try {
    const res = await fetch(`${API}/api/audio-waveform?path=${encodeURIComponent(path)}&bins=300`)
    if (!res.ok) return { samples: [], duration_s: 60 }
    const data = await res.json()
    return { samples: data.samples ?? [], duration_s: data.duration_s ?? 60 }
  } catch {
    return { samples: [], duration_s: 60 }
  }
}

export function MediaTab() {
  const { project, importAudio, importImage, appendVideo } = useStore()
  const [activeTab, setActiveTab] = useState<MediaBinTab>('files')
  const [provider, setProvider] = useState('pexels')
  const [query, setQuery] = useState('background')
  const [items, setItems] = useState<StockAsset[]>([])
  const [downloads, setDownloads] = useState<StockAsset[]>([])
  const [favorites, setFavorites] = useState<StockAsset[]>([])
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settings, setSettings] = useState<any>(null)
  const [keyDrafts, setKeyDrafts] = useState<Record<string, string>>({})
  const [mediaZoom, setMediaZoom] = useState(0.85)
  const [mediaSort, setMediaSort] = useState<MediaSort>('recent')
  const [loading, setLoading] = useState(false)
  const [appendLoading, setAppendLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [recents, setRecents] = useState<RecentFile[]>([])
  const [thumbUrl, setThumbUrl] = useState<string | null>(null)

  useEffect(() => { setRecents(getRecentFiles()) }, [])

  useEffect(() => {
    if (project?.videoPath) {
      setThumbUrl(`${API}/api/thumb?path=${encodeURIComponent(project.videoPath)}&t=0&w=240`)
    } else {
      setThumbUrl(null)
    }
  }, [project?.videoPath])

  const refreshSettings = useCallback(async () => {
    const res = await api.get('/api/stock/settings')
    setSettings(res.data)
  }, [])

  const refreshDownloads = useCallback(async () => {
    const res = await api.get('/api/stock/downloads')
    setDownloads(res.data.items ?? [])
  }, [])

  useEffect(() => {
    refreshSettings().catch(() => undefined)
    refreshDownloads().catch(() => undefined)
  }, [refreshSettings, refreshDownloads])

  useEffect(() => {
    const providers = (PROVIDERS as any)[activeTab] as string[] | undefined
    if (providers && !providers.includes(provider)) setProvider(providers[0])
  }, [activeTab, provider])

  const providerChoices = useMemo(() => ((PROVIDERS as any)[activeTab] as string[] | undefined) ?? [], [activeTab])

  const loadFile = useCallback(async (filePath: string) => {
    setLoading(true)
    setError(null)
    try {
      const { exportSettings } = useStore.getState()
      const proj = await api.post('/api/open-project', { path: filePath, silence_style: exportSettings.silenceStyle, auto_cut: exportSettings.silenceEnabled })
      useStore.getState().setProject(proj.data)
      useStore.getState().setPreviewTime(0)
      addRecentFile(filePath, 'video')
      setRecents(getRecentFiles())
    } catch (e: any) {
      const detail = e?.response?.data?.detail ?? e?.message ?? 'Erro ao carregar video'
      setError(typeof detail === 'string' ? detail.split('\n')[0] : 'Erro ao carregar video')
    } finally {
      setLoading(false)
    }
  }, [])

  const appendVideoFile = useCallback(async (filePath: string) => {
    if (!project) return loadFile(filePath)
    setAppendLoading(true)
    setError(null)
    try {
      const { exportSettings } = useStore.getState()
      const analysis = await api.post('/api/analyze-video', { path: filePath, silence_style: exportSettings.silenceStyle, auto_cut: exportSettings.silenceEnabled })
      appendVideo(
        analysis.data.clips ?? [],
        analysis.data.waveform ?? [],
        analysis.data.proxy_status && analysis.data.proxy_status !== 'not_needed'
          ? {
              source_path:  filePath,
              proxy_path:   analysis.data.proxy_path ?? '',
              proxy_status: analysis.data.proxy_status,
            }
          : undefined,
      )
      addRecentFile(filePath, 'video')
      setRecents(getRecentFiles())
    } catch (e: any) {
      const detail = e?.response?.data?.detail ?? e?.message ?? 'Erro ao adicionar video'
      setError(typeof detail === 'string' ? detail.split('\n')[0] : 'Erro ao adicionar video')
    } finally {
      setAppendLoading(false)
    }
  }, [appendVideo, loadFile, project])

  const loadProjectFile = useCallback(async (filePath: string) => {
    setLoading(true)
    setError(null)
    try {
      const proj = await api.post('/api/load-project', { path: filePath })
      useStore.getState().setProject(proj.data)
      useStore.getState().setPreviewTime(0)
      addRecentFile(filePath, 'project')
      setRecents(getRecentFiles())
    } catch (e: any) {
      const detail = e?.response?.data?.detail ?? e?.message ?? 'Erro ao carregar projeto'
      setError(typeof detail === 'string' ? detail.split('\n')[0] : 'Erro ao carregar projeto')
    } finally {
      setLoading(false)
    }
  }, [])

  const importAudioFile = useCallback(async (filePath: string) => {
    if (!project) return
    setAppendLoading(true)
    setError(null)
    try {
      const [duration, waveform] = await Promise.all([probeAudioDuration(filePath), fetchAudioWaveform(filePath)])
      importAudio(filePath, waveform.duration_s || duration, waveform.samples)
      addRecentFile(filePath, 'audio')
      setRecents(getRecentFiles())
    } catch {
      setError('Falha ao importar audio')
    } finally {
      setAppendLoading(false)
    }
  }, [importAudio, project])

  const importImageFile = useCallback((filePath: string) => {
    if (!project) return
    importImage(filePath, 5)
    addRecentFile(filePath, 'image')
    setRecents(getRecentFiles())
  }, [importImage, project])

  const useLocalAsset = useCallback((asset: StockAsset) => {
    const path = asset.local_path
    if (!path) return
    if (asset.type === 'audio') importAudioFile(path)
    else if (asset.type === 'image') importImageFile(path)
    else appendVideoFile(path)
  }, [appendVideoFile, importAudioFile, importImageFile])

  const handleOpenDialog = useCallback(async (type: 'video' | 'audio' | 'image' | 'project') => {
    setError(null)
    try {
      const res = await api.post('/api/open-file-dialog', { type })
      const path: string = res.data.path
      if (!path) return
      if (type === 'project') {
        await loadProjectFile(path)
      } else if (type === 'audio') {
        await importAudioFile(path)
      } else if (type === 'image') {
        importImageFile(path)
      } else {
        await appendVideoFile(path)
      }
    } catch {
      setError('Falha ao abrir arquivo')
    }
  }, [appendVideoFile, importAudioFile, importImageFile, loadProjectFile])

  const searchStock = useCallback(async () => {
    if (!providerChoices.length) return
    setLoading(true)
    setError(null)
    try {
      const res = await api.post('/api/stock/search', {
        provider,
        query,
        media_type: stockTypeForTab(activeTab),
        per_page: 12,
      })
      setItems(res.data.items ?? [])
    } catch (e: any) {
      const detail = e?.response?.data?.detail ?? e?.message ?? 'Erro ao buscar midia'
      setError(typeof detail === 'string' ? detail.split('\n')[0] : 'Erro ao buscar midia')
    } finally {
      setLoading(false)
    }
  }, [activeTab, provider, providerChoices.length, query])

  const downloadAsset = useCallback(async (asset: StockAsset) => {
    setAppendLoading(true)
    setError(null)
    try {
      const res = await api.post('/api/stock/download', { asset })
      const downloaded = res.data as StockAsset
      setDownloads((prev) => [downloaded, ...prev.filter((x) => x.local_path !== downloaded.local_path)])
      useLocalAsset(downloaded)
    } catch (e: any) {
      const detail = e?.response?.data?.detail ?? e?.message ?? 'Erro ao baixar asset'
      setError(typeof detail === 'string' ? detail.split('\n')[0] : 'Erro ao baixar asset')
    } finally {
      setAppendLoading(false)
    }
  }, [useLocalAsset])

  const saveSettings = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.post('/api/stock/settings', { values: keyDrafts })
      setSettings(res.data)
      setKeyDrafts({})
      setSettingsOpen(false)
    } catch {
      setError('Falha ao salvar chaves')
    } finally {
      setLoading(false)
    }
  }, [keyDrafts])

  const newProject = useCallback(() => {
    useStore.getState().setProject({
      loaded: false,
      videoPath: null,
      duration_s: 0,
      waveform: [],
      video_track: { name: 'Video', clips: [] },
      audio_track: { name: 'Audio', clips: [] },
      text_track: { name: 'Texto', clips: [] },
      overlay_track: { name: 'Overlay', clips: [] },
      removed_ranges: [],
      saved_time_s: 0,
    } as any)
  }, [])

  const visibleAssets = activeTab === 'downloads' ? downloads : activeTab === 'favorites' ? favorites : items
  const sortedAssets = useMemo(() => {
    const list = [...visibleAssets]
    if (mediaSort === 'name') {
      list.sort((a, b) => a.title.localeCompare(b.title))
    } else if (mediaSort === 'provider') {
      list.sort((a, b) => `${a.provider}-${a.title}`.localeCompare(`${b.provider}-${b.title}`))
    } else if (mediaSort === 'duration') {
      list.sort((a, b) => (b.duration_s ?? 0) - (a.duration_s ?? 0))
    }
    return list
  }, [mediaSort, visibleAssets])

  const sortedRecents = useMemo(() => {
    const list = [...recents]
    if (mediaSort === 'name') {
      list.sort((a, b) => a.name.localeCompare(b.name))
    } else if (mediaSort === 'provider') {
      list.sort((a, b) => `${a.type}-${a.name}`.localeCompare(`${b.type}-${b.name}`))
    } else {
      list.sort((a, b) => b.addedAt - a.addedAt)
    }
    return list
  }, [mediaSort, recents])

  const useRecentFile = useCallback((file: RecentFile) => {
    if (file.type === 'project') return loadProjectFile(file.path)
    if (file.type === 'audio') return importAudioFile(file.path)
    if (file.type === 'image') return importImageFile(file.path)
    if (project) return appendVideoFile(file.path)
    return loadFile(file.path)
  }, [appendVideoFile, importAudioFile, importImageFile, loadFile, loadProjectFile, project])

  return (
    <div className="p-2 space-y-2">
      <div className="grid grid-cols-4 gap-1">
        {STOCK_TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`flex flex-col items-center justify-center gap-0.5 px-1 py-1 rounded text-[9px] leading-tight transition-colors ${
              activeTab === tab.id ? 'bg-accent text-white' : 'bg-bg-surface text-text-muted hover:text-white'
            }`}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>

      <MediaBinControls
        zoom={mediaZoom}
        sort={mediaSort}
        onZoom={setMediaZoom}
        onSort={setMediaSort}
      />

      {activeTab === 'files' ? (
        <LocalFilesPanel
          project={project}
          loading={loading || appendLoading}
          thumbUrl={thumbUrl}
          recents={sortedRecents}
          zoom={mediaZoom}
          onOpenDialog={handleOpenDialog}
          onUseRecent={useRecentFile}
          onNewProject={newProject}
          onClearRecents={() => { clearRecentFiles(); setRecents([]) }}
        />
      ) : (
        <div className="space-y-2">
          <div className="flex items-center gap-1">
            <div className="flex-1 flex items-center gap-1 bg-bg-surface border border-border rounded px-2">
              <Search size={12} className="text-text-dim" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') searchStock() }}
                placeholder="Buscar conteudo"
                className="min-w-0 flex-1 bg-transparent outline-none text-xs text-white py-1.5"
              />
            </div>
            <button
              onClick={() => setSettingsOpen((v) => !v)}
              className="w-8 h-8 flex items-center justify-center rounded bg-bg-surface text-text-muted hover:text-white"
              title="Chaves de API"
            >
              <Settings size={13} />
            </button>
          </div>

          {providerChoices.length > 0 && (
            <div className="flex gap-1">
              {providerChoices.map((p) => (
                <button
                  key={p}
                  onClick={() => setProvider(p)}
                  className={`px-2 py-1 rounded text-[10px] capitalize ${
                    provider === p ? 'bg-accent/80 text-white' : 'bg-bg-surface text-text-muted hover:text-white'
                  }`}
                >
                  {p}
                </button>
              ))}
              <button
                onClick={searchStock}
                disabled={loading}
                className="ml-auto px-2 py-1 rounded bg-bg-surface text-text-muted hover:text-white disabled:opacity-50"
              >
                {loading ? <Loader2 size={12} className="animate-spin" /> : <Search size={12} />}
              </button>
            </div>
          )}

          {settingsOpen && (
            <SettingsPanel
              settings={settings}
              drafts={keyDrafts}
              onChange={(key, value) => setKeyDrafts((prev) => ({ ...prev, [key]: value }))}
              onSave={saveSettings}
              saving={loading}
            />
          )}

          <div className="space-y-1 max-h-[520px] overflow-y-auto">
            {activeTab === 'downloads' && (
              <button
                onClick={refreshDownloads}
                className="w-full flex items-center justify-center gap-1 px-2 py-1.5 rounded bg-bg-surface text-[10px] text-text-muted hover:text-white"
              >
                <RefreshCw size={11} />
                Recarregar baixados
              </button>
            )}
            {sortedAssets.length === 0 ? (
              <div className="text-center text-[11px] text-text-dim border border-dashed border-border rounded-lg p-5">
                {activeTab === 'downloads' ? 'Nenhum download ainda.' : 'Busque e baixe assets para usar na timeline.'}
              </div>
            ) : sortedAssets.map((asset) => (
              <AssetCard
                key={`${asset.provider}-${asset.id}-${asset.local_path ?? ''}`}
                asset={asset}
                zoom={mediaZoom}
                isFavorite={favorites.some((fav) => fav.provider === asset.provider && fav.id === asset.id)}
                busy={appendLoading}
                onDownload={() => asset.local_path ? useLocalAsset(asset) : downloadAsset(asset)}
                onFavorite={() => setFavorites((prev) => {
                  const exists = prev.some((fav) => fav.provider === asset.provider && fav.id === asset.id)
                  return exists ? prev.filter((fav) => !(fav.provider === asset.provider && fav.id === asset.id)) : [asset, ...prev]
                })}
              />
            ))}
          </div>
        </div>
      )}

      {error && (
        <div className="flex items-start gap-2 bg-red-900/30 border border-red-800/50 rounded-lg p-2">
          <AlertCircle size={12} className="text-red-400 flex-shrink-0 mt-0.5" />
          <p className="text-[10px] text-red-300 flex-1 leading-relaxed">{error}</p>
          <button onClick={() => setError(null)} className="text-red-400 hover:text-red-200 flex-shrink-0">
            <X size={10} />
          </button>
        </div>
      )}
    </div>
  )
}

function MediaBinControls({
  zoom, sort, onZoom, onSort,
}: {
  zoom: number
  sort: MediaSort
  onZoom: (value: number) => void
  onSort: (value: MediaSort) => void
}) {
  const stepZoom = (delta: number) => onZoom(Math.max(0.7, Math.min(1.2, Number((zoom + delta).toFixed(2)))))
  return (
    <div className="flex items-center gap-1 rounded bg-bg-surface border border-border px-1.5 py-1">
      <span className="text-[9px] text-text-dim">Zoom</span>
      <button
        onClick={() => stepZoom(-0.05)}
        className="w-5 h-5 rounded bg-bg-panel text-text-muted hover:text-white"
        title="Diminuir thumbnails"
      >
        -
      </button>
      <input
        type="range"
        min="0.7"
        max="1.2"
        step="0.05"
        value={zoom}
        onChange={(e) => onZoom(Number(e.target.value))}
        className="min-w-12 flex-1"
        title="Zoom interno do Media Bin"
      />
      <button
        onClick={() => stepZoom(0.05)}
        className="w-5 h-5 rounded bg-bg-panel text-text-muted hover:text-white"
        title="Aumentar thumbnails"
      >
        +
      </button>
      <select
        value={sort}
        onChange={(e) => onSort(e.target.value as MediaSort)}
        className="w-24 rounded bg-bg-panel border border-border px-1 py-0.5 text-[9px] text-white outline-none"
        title="Classificacao"
      >
        <option value="recent">Recentes</option>
        <option value="name">Nome</option>
        <option value="provider">Tipo/Fonte</option>
        <option value="duration">Duracao</option>
      </select>
    </div>
  )
}

function LocalAssetCard({
  title, subtitle, kind, thumbnailUrl, compact, disabled, draggable, dragType, dragPath, onClick,
}: {
  title: string
  subtitle: string
  kind: string
  thumbnailUrl: string | null
  compact: boolean
  disabled?: boolean
  draggable?: boolean
  dragType?: RecentFile['type']
  dragPath?: string
  onClick?: () => void
}) {
  const icon = kind === 'Audio'
    ? <Music size={16} />
    : kind === 'Imagem'
      ? <Image size={16} />
      : kind === 'Projeto'
        ? <FolderOpen size={16} />
        : <Film size={16} />

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      draggable={!!draggable && !!dragPath && dragType !== 'project'}
      onDragStart={(e) => {
        if (!dragPath || !dragType || dragType === 'project') return
        e.dataTransfer.setData('application/x-cortacerto-clip', JSON.stringify({
          type: dragType,
          path: dragPath,
        }))
        e.dataTransfer.effectAllowed = 'copy'
      }}
      className="group min-w-0 overflow-hidden rounded border border-border bg-bg-surface text-left hover:border-accent/70 disabled:opacity-60"
    >
      <div className={compact ? 'flex min-h-[54px]' : ''}>
        <div className={`${compact ? 'w-16 flex-shrink-0' : 'aspect-video'} bg-black/70 flex items-center justify-center text-text-dim overflow-hidden`}>
          {thumbnailUrl ? (
            <img src={thumbnailUrl} alt="" className="w-full h-full object-cover" loading="lazy" />
          ) : icon}
        </div>
        <div className="min-w-0 p-1.5">
          <div className="flex items-center gap-1">
            <span className="px-1 py-0.5 rounded bg-bg-panel text-[8px] text-accent">{kind}</span>
          </div>
          <p className="mt-1 text-[10px] text-text-muted group-hover:text-white truncate">{title}</p>
          <p className="text-[8px] text-text-dim truncate">{subtitle}</p>
        </div>
      </div>
    </button>
  )
}

function LocalFilesPanel({
  project, loading, thumbUrl, recents, zoom, onOpenDialog, onUseRecent, onNewProject, onClearRecents,
}: {
  project: any
  loading: boolean
  thumbUrl: string | null
  recents: RecentFile[]
  zoom: number
  onOpenDialog: (type: 'video' | 'audio' | 'image' | 'project') => void
  onUseRecent: (file: RecentFile) => void
  onNewProject: () => void
  onClearRecents: () => void
}) {
  const compact = zoom <= 0.9
  return (
    <div className="space-y-1.5" style={{ fontSize: `${Math.round(12 * zoom)}px` }}>
      <button onClick={() => onOpenDialog('video')} disabled={loading}
        className="w-full flex items-center gap-2 px-2 py-1.5 bg-accent hover:bg-accent-hover disabled:opacity-60 text-white text-xs font-medium rounded">
        {loading ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
        Video
      </button>
      <button onClick={() => onOpenDialog('project')} disabled={loading}
        className="w-full flex items-center gap-2 px-2 py-1.5 bg-bg-surface hover:bg-border text-text-muted hover:text-white text-xs rounded">
        <FolderOpen size={13} /> Projeto
      </button>
      <div className="grid grid-cols-2 gap-1">
        <button onClick={() => onOpenDialog('audio')} disabled={!project || loading}
          className="flex items-center justify-center gap-1 px-2 py-1.5 bg-bg-surface hover:bg-border disabled:opacity-50 text-text-muted hover:text-white text-[10px] rounded">
          <Music size={12} /> Audio
        </button>
        <button onClick={() => onOpenDialog('image')} disabled={!project || loading}
          className="flex items-center justify-center gap-1 px-2 py-1.5 bg-bg-surface hover:bg-border disabled:opacity-50 text-text-muted hover:text-white text-[10px] rounded">
          <Image size={12} /> Imagem
        </button>
      </div>
      {project && (
        <button onClick={onNewProject} className="w-full flex items-center gap-2 px-3 py-1.5 text-text-dim hover:text-red-400 text-[10px] rounded-lg">
          <RefreshCw size={11} /> Novo projeto
        </button>
      )}
      {project?.videoPath && (
        <div className="space-y-1">
          <p className="px-1 text-[10px] text-text-dim uppercase tracking-wider">Projeto atual</p>
          <LocalAssetCard
            title={basename(project.videoPath)}
            subtitle={`${project.duration_s?.toFixed?.(1) ?? 0}s - ${project.video_track?.clips?.length ?? 0} clipes`}
            kind="Video"
            thumbnailUrl={thumbUrl}
            compact={compact}
          />
        </div>
      )}
      {recents.length > 0 && (
        <div className="space-y-1">
          <div className="flex items-center justify-between px-1">
            <p className="text-[10px] text-text-dim uppercase tracking-wider">Recentes</p>
            <button onClick={onClearRecents} className="text-text-dim hover:text-red-400"><Trash2 size={10} /></button>
          </div>
          <div className="grid grid-cols-2 gap-1 max-h-56 overflow-y-auto pr-0.5">
            {recents.map((f) => (
              <LocalAssetCard
                key={f.path}
                title={f.name}
                subtitle={f.path}
                kind={recentKindLabel(f.type)}
                thumbnailUrl={recentThumbUrl(f)}
                compact={compact}
                disabled={loading}
                draggable={f.type !== 'project'}
                dragType={f.type}
                dragPath={f.path}
                onClick={() => onUseRecent(f)}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function SettingsPanel({
  settings, drafts, onChange, onSave, saving,
}: {
  settings: any
  drafts: Record<string, string>
  onChange: (key: string, value: string) => void
  onSave: () => void
  saving: boolean
}) {
  return (
    <div className="border border-border rounded-lg p-2 bg-bg-panel space-y-1.5">
      <p className="text-[10px] text-text-dim uppercase tracking-wider">Chaves de API</p>
      {STOCK_ENV_NAMES.map((key) => (
        <div key={key} className="space-y-0.5">
          <div className="flex items-center justify-between gap-2">
            <label className="text-[9px] text-text-muted truncate">{key}</label>
            <span className="text-[9px] text-text-dim">{settings?.keys?.[key]?.masked || 'vazio'}</span>
          </div>
          <input
            type="password"
            value={drafts[key] ?? ''}
            onChange={(e) => onChange(key, e.target.value)}
            placeholder="novo valor"
            className="w-full bg-bg-surface border border-border rounded px-2 py-1 text-[10px] text-white outline-none"
          />
        </div>
      ))}
      <button onClick={onSave} disabled={saving}
        className="w-full flex items-center justify-center gap-1 px-2 py-1.5 rounded bg-accent text-white text-[10px] disabled:opacity-50">
        {saving ? <Loader2 size={11} className="animate-spin" /> : <Settings size={11} />}
        Salvar e recarregar
      </button>
    </div>
  )
}

function AssetCard({
  asset, zoom, isFavorite, busy, onDownload, onFavorite,
}: {
  asset: StockAsset
  zoom: number
  isFavorite: boolean
  busy: boolean
  onDownload: () => void
  onFavorite: () => void
}) {
  const localPath = asset.local_path || ''
  const compact = zoom <= 0.9
  return (
    <div
      draggable={!!localPath}
      onDragStart={(e) => {
        if (!localPath) return
        e.dataTransfer.setData('application/x-cortacerto-clip', JSON.stringify({
          type: asset.type,
          path: localPath,
        }))
        e.dataTransfer.effectAllowed = 'copy'
      }}
      className="border border-border bg-bg-surface rounded overflow-hidden"
      style={{ fontSize: `${Math.round(11 * zoom)}px` }}
    >
      <div className={compact ? 'flex min-h-[58px]' : ''}>
        {asset.thumbnail_url && (
          <div className={compact ? 'w-20 flex-shrink-0 bg-black' : 'aspect-video bg-black'}>
            <img src={asset.thumbnail_url} alt="" className="w-full h-full object-cover" loading="lazy" />
          </div>
        )}
        <div className={`${compact ? 'flex-1 min-w-0' : ''} p-2 space-y-1`}>
          <div className="flex items-start gap-2">
            <div className="min-w-0 flex-1">
              <p className="text-[11px] text-white truncate">{asset.title}</p>
              <p className="text-[9px] text-text-dim truncate">
                {asset.provider} {asset.author ? `- ${asset.author}` : ''}
              </p>
            </div>
            <button onClick={onFavorite} className={isFavorite ? 'text-red-400' : 'text-text-dim hover:text-red-300'}>
              <Heart size={12} fill={isFavorite ? 'currentColor' : 'none'} />
            </button>
          </div>
          <div className="flex items-center justify-between gap-1">
            <span className="text-[9px] text-text-dim truncate">
              {asset.duration_s ? `${asset.duration_s.toFixed(1)}s` : (asset.license || asset.type)}
            </span>
            <button onClick={onDownload} disabled={busy}
              className="flex items-center gap-1 px-2 py-1 rounded bg-accent/80 hover:bg-accent text-white text-[10px] disabled:opacity-50">
              {busy ? <Loader2 size={10} className="animate-spin" /> : localPath ? <FilePlus size={10} /> : <Download size={10} />}
              {localPath ? 'Usar' : 'Baixar'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
