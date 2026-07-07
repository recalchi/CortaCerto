import {
  AlertCircle,
  CheckCircle2,
  Download,
  Film,
  FolderOpen,
  Heart,
  Image,
  Loader2,
  Music,
  Search,
  Sparkles,
  Upload,
  Wand2,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../../api/client'
import { useStore } from '../../store/useStore'
import { addRecentFile, clearRecentFiles, getRecentFiles, type RecentFile } from '../../utils/recentFiles'

const API = 'http://127.0.0.1:7472'
const MEDIA_FAVORITES_KEY = 'cc_media_favorites_v2'

type MainCategory = 'mine' | 'ai' | 'space' | 'library'
type MediaSort = 'recent' | 'name' | 'type' | 'duration'
type SearchType = 'video' | 'image' | 'audio'

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
  size_bytes?: number
  modified_s?: number
}

const LIBRARY_PRESETS: Array<{ id: string; label: string; query: string; type: SearchType }> = [
  { id: 'popular', label: 'Populares', query: 'popular background', type: 'video' },
  { id: 'holiday', label: 'Natal e ano novo', query: 'christmas new year', type: 'video' },
  { id: 'green', label: 'Tela verde', query: 'green screen', type: 'video' },
  { id: 'bg', label: 'Plano de fundo', query: 'background abstract', type: 'image' },
  { id: 'intro', label: 'Introducao e fim', query: 'intro outro', type: 'video' },
  { id: 'transition', label: 'Transicoes', query: 'transition overlay', type: 'video' },
  { id: 'scene', label: 'Cenario', query: 'city street nature scene', type: 'image' },
  { id: 'atmosphere', label: 'Atmosfera', query: 'atmospheric particles light', type: 'video' },
]

const PROVIDERS_BY_TYPE: Record<SearchType, string[]> = {
  video: ['pexels', 'pixabay'],
  image: ['pexels', 'pixabay', 'unsplash'],
  audio: ['freesound'],
}

function formatDuration(s?: number | null): string {
  const sec = Math.max(0, Math.floor(Number(s || 0)))
  const mm = Math.floor(sec / 60)
  const ss = `${sec % 60}`.padStart(2, '0')
  return `${mm}:${ss}`
}

function basename(path: string): string {
  return path.replace(/\\/g, '/').split('/').pop() || path
}

function assetThumbUrl(asset: StockAsset): string {
  if (asset.thumbnail_url) return asset.thumbnail_url
  if (asset.local_path && asset.type === 'video') {
    return `${API}/api/thumb?path=${encodeURIComponent(asset.local_path)}&t=0&w=320`
  }
  if (asset.local_path && asset.type === 'image') {
    return `${API}/api/serve-file?path=${encodeURIComponent(asset.local_path)}`
  }
  return ''
}

function readFavoriteKeys(): string[] {
  try {
    const raw = localStorage.getItem(MEDIA_FAVORITES_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter((item) => typeof item === 'string')
  } catch {
    return []
  }
}

function writeFavoriteKeys(keys: string[]) {
  try {
    localStorage.setItem(MEDIA_FAVORITES_KEY, JSON.stringify(keys.slice(0, 500)))
  } catch {
    // ignore
  }
}

function favoriteKey(asset: StockAsset): string {
  return `${asset.provider}|${asset.id}|${asset.local_path || ''}`
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
  const [mainCategory, setMainCategory] = useState<MainCategory>('mine')
  const [searchType, setSearchType] = useState<SearchType>('video')
  const [query, setQuery] = useState('')
  const [libraryPreset, setLibraryPreset] = useState<string>('popular')
  const [provider, setProvider] = useState<string>('pexels')
  const [mediaZoom, setMediaZoom] = useState(1)
  const [mediaSort, setMediaSort] = useState<MediaSort>('recent')
  const [loading, setLoading] = useState(false)
  const [actionBusy, setActionBusy] = useState(false)
  const [actionMessage, setActionMessage] = useState<string | null>(null)
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [recents, setRecents] = useState<RecentFile[]>([])
  const [stockItems, setStockItems] = useState<StockAsset[]>([])
  const [downloads, setDownloads] = useState<StockAsset[]>([])
  const [spacePath, setSpacePath] = useState('')
  const [spaceItems, setSpaceItems] = useState<StockAsset[]>([])
  const [favoriteKeys, setFavoriteKeys] = useState<string[]>(() => readFavoriteKeys())
  const [stockSettings, setStockSettings] = useState<any>(null)

  useEffect(() => {
    setRecents(getRecentFiles())
  }, [])

  const refreshDownloads = useCallback(async () => {
    const res = await api.get('/api/stock/downloads')
    setDownloads((res.data.items ?? []) as StockAsset[])
  }, [])

  const refreshStockSettings = useCallback(async () => {
    const res = await api.get('/api/stock/settings')
    setStockSettings(res.data)
  }, [])

  useEffect(() => {
    refreshDownloads().catch(() => undefined)
    refreshStockSettings().catch(() => undefined)
  }, [refreshDownloads, refreshStockSettings])

  useEffect(() => {
    const providers = PROVIDERS_BY_TYPE[searchType]
    if (!providers.includes(provider)) setProvider(providers[0])
  }, [provider, searchType])

  const loadFile = useCallback(async (filePath: string) => {
    setLoading(true)
    setError(null)
    try {
      const { exportSettings } = useStore.getState()
      const proj = await api.post('/api/open-project', {
        path: filePath,
        silence_style: exportSettings.silenceStyle,
        auto_cut: exportSettings.silenceEnabled,
      })
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
    setActionBusy(true)
    setActionMessage('Analisando video e preparando timeline...')
    setError(null)
    try {
      const { exportSettings } = useStore.getState()
      const analysis = await api.post('/api/analyze-video', {
        path: filePath,
        silence_style: exportSettings.silenceStyle,
        auto_cut: exportSettings.silenceEnabled,
      })
      appendVideo(
        analysis.data.clips ?? [],
        analysis.data.waveform ?? [],
        analysis.data.proxy_status && analysis.data.proxy_status !== 'not_needed'
          ? {
              source_path: filePath,
              proxy_path: analysis.data.proxy_path ?? '',
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
      setActionBusy(false)
      setActionMessage(null)
    }
  }, [appendVideo, loadFile, project])

  const loadProjectFile = useCallback(async (filePath: string) => {
    setLoading(true)
    setError(null)
    try {
      const proj = await api.post('/api/load-project', { path: filePath })
      useStore.getState().setProject({ ...proj.data, _projectPath: filePath })
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
    setActionBusy(true)
    setActionMessage('Importando audio e calculando onda sonora...')
    setError(null)
    try {
      const [duration, waveform] = await Promise.all([probeAudioDuration(filePath), fetchAudioWaveform(filePath)])
      importAudio(filePath, waveform.duration_s || duration, waveform.samples)
      addRecentFile(filePath, 'audio')
      setRecents(getRecentFiles())
    } catch {
      setError('Falha ao importar audio')
    } finally {
      setActionBusy(false)
      setActionMessage(null)
    }
  }, [importAudio, project])

  const importImageFile = useCallback((filePath: string) => {
    if (!project) return
    setActionMessage('Adicionando imagem na timeline...')
    importImage(filePath, 5)
    addRecentFile(filePath, 'image')
    setRecents(getRecentFiles())
    window.setTimeout(() => setActionMessage(null), 350)
  }, [importImage, project])

  const insertAsset = useCallback(async (asset: StockAsset) => {
    const path = asset.local_path
    if (!path) return
    if (asset.type === 'audio') await importAudioFile(path)
    else if (asset.type === 'image') importImageFile(path)
    else await appendVideoFile(path)
  }, [appendVideoFile, importAudioFile, importImageFile])

  const openFileDialog = useCallback(async (type: 'video' | 'audio' | 'image' | 'project') => {
    setError(null)
    try {
      const res = await api.post('/api/open-file-dialog', { type })
      const path: string = res.data.path
      if (!path) return
      if (type === 'project') await loadProjectFile(path)
      else if (type === 'audio') await importAudioFile(path)
      else if (type === 'image') importImageFile(path)
      else await appendVideoFile(path)
    } catch {
      setError('Falha ao abrir arquivo')
    }
  }, [appendVideoFile, importAudioFile, importImageFile, loadProjectFile])

  const openSpaceFolder = useCallback(async () => {
    setError(null)
    setLoading(true)
    try {
      const pick = await api.post('/api/open-folder-dialog')
      const chosen = String(pick.data?.path || '')
      if (!chosen) return
      setSpacePath(chosen)
      const listed = await api.post('/api/space/list-media', { path: chosen, recursive: true, limit: 1200 })
      setSpaceItems((listed.data?.items ?? []) as StockAsset[])
      setMainCategory('space')
    } catch (e: any) {
      const detail = e?.response?.data?.detail ?? e?.message ?? 'Falha ao listar pasta'
      setError(typeof detail === 'string' ? detail.split('\n')[0] : 'Falha ao listar pasta')
    } finally {
      setLoading(false)
    }
  }, [])

  const searchLibrary = useCallback(async (opts?: { q?: string; type?: SearchType; provider?: string }) => {
    const q = (opts?.q ?? query).trim()
    const type = opts?.type ?? searchType
    const prov = opts?.provider ?? provider
    if (!q) return
    setLoading(true)
    setError(null)
    try {
      const res = await api.post('/api/stock/search', {
        provider: prov,
        query: q,
        media_type: type,
        per_page: 24,
      })
      setStockItems((res.data?.items ?? []) as StockAsset[])
    } catch (e: any) {
      const detail = e?.response?.data?.detail ?? e?.message ?? 'Erro ao buscar biblioteca'
      setError(typeof detail === 'string' ? detail.split('\n')[0] : 'Erro ao buscar biblioteca')
    } finally {
      setLoading(false)
    }
  }, [provider, query, searchType])

  const runAiGenerate = useCallback(async () => {
    const prompt = query.trim()
    if (!prompt) return
    const decorated = `${prompt} cinematic high quality background`
    setMainCategory('ai')
    await searchLibrary({ q: decorated, type: searchType, provider: PROVIDERS_BY_TYPE[searchType][0] })
  }, [query, searchLibrary, searchType])

  const downloadAsset = useCallback(async (asset: StockAsset) => {
    setBusyKey(favoriteKey(asset))
    setActionMessage(`Baixando ${asset.type === 'audio' ? 'audio' : asset.type === 'image' ? 'imagem' : 'video'} para cache local...`)
    setError(null)
    try {
      const res = await api.post('/api/stock/download', { asset })
      const downloaded = res.data as StockAsset
      setDownloads((prev) => [downloaded, ...prev.filter((item) => item.local_path !== downloaded.local_path)])
      await insertAsset(downloaded)
    } catch (e: any) {
      const detail = e?.response?.data?.detail ?? e?.message ?? 'Erro ao baixar asset'
      setError(typeof detail === 'string' ? detail.split('\n')[0] : 'Erro ao baixar asset')
    } finally {
      setBusyKey(null)
      setActionMessage(null)
    }
  }, [insertAsset])

  const toggleFavorite = (asset: StockAsset) => {
    const key = favoriteKey(asset)
    setFavoriteKeys((prev) => {
      const next = prev.includes(key) ? prev.filter((item) => item !== key) : [key, ...prev]
      writeFavoriteKeys(next)
      return next
    })
  }

  const allAssets = useMemo(() => {
    const merged = [...downloads, ...stockItems, ...spaceItems]
    const map = new Map<string, StockAsset>()
    for (const item of merged) {
      const key = favoriteKey(item)
      if (!map.has(key)) map.set(key, item)
    }
    return Array.from(map.values())
  }, [downloads, spaceItems, stockItems])

  const favoriteAssets = useMemo(
    () => allAssets.filter((asset) => favoriteKeys.includes(favoriteKey(asset))),
    [allAssets, favoriteKeys],
  )

  const providerStatuses = useMemo(() => {
    const providers = stockSettings?.providers
    return Array.isArray(providers) ? providers : []
  }, [stockSettings])

  const visibleAssets = useMemo(() => {
    let list: StockAsset[] = []
    if (mainCategory === 'mine') {
      list = recents
        .filter((item) => item.type !== 'project')
        .map((item) => ({
          id: item.path,
          provider: 'local',
          type: item.type as 'video' | 'image' | 'audio',
          title: item.name,
          local_path: item.path,
          duration_s: null,
        }))
    } else if (mainCategory === 'space') {
      list = spaceItems
    } else if (mainCategory === 'library' || mainCategory === 'ai') {
      list = stockItems
    }
    const q = query.trim().toLowerCase()
    if (q && mainCategory !== 'library' && mainCategory !== 'ai') {
      list = list.filter((item) => `${item.title} ${item.author || ''}`.toLowerCase().includes(q))
    }
    const out = [...list]
    if (mediaSort === 'name') out.sort((a, b) => a.title.localeCompare(b.title))
    if (mediaSort === 'type') out.sort((a, b) => `${a.type}-${a.title}`.localeCompare(`${b.type}-${b.title}`))
    if (mediaSort === 'duration') out.sort((a, b) => (b.duration_s || 0) - (a.duration_s || 0))
    if (mediaSort === 'recent') out.sort((a, b) => (b.modified_s || 0) - (a.modified_s || 0))
    return out
  }, [mainCategory, mediaSort, query, recents, spaceItems, stockItems])

  const providerChoices = PROVIDERS_BY_TYPE[searchType]
  const thumbCols = mediaZoom <= 0.85 ? 'grid-cols-2' : mediaZoom <= 1.05 ? 'grid-cols-3' : 'grid-cols-4'

  return (
    <div className="p-2 space-y-2 text-xs">
      <div className="grid grid-cols-[102px_1fr] gap-2">
        <aside className="space-y-1">
          <button
            onClick={() => openFileDialog('video')}
            disabled={loading || actionBusy}
            className="w-full flex items-center justify-center gap-1 px-2 py-1.5 rounded bg-accent text-white text-[11px] disabled:opacity-60"
          >
            {loading ? <Loader2 size={12} className="animate-spin" /> : <Upload size={12} />}
            Importar
          </button>
          <button
            onClick={() => setMainCategory('mine')}
            className={`w-full text-left px-2 py-1 rounded ${mainCategory === 'mine' ? 'bg-bg-surface text-white' : 'text-text-muted hover:text-white'}`}
          >
            Seus
          </button>
          <button
            onClick={() => setMainCategory('ai')}
            className={`w-full text-left px-2 py-1 rounded ${mainCategory === 'ai' ? 'bg-bg-surface text-white' : 'text-text-muted hover:text-white'}`}
          >
            Midia de IA
          </button>
          <button
            onClick={() => { setMainCategory('space'); openSpaceFolder().catch(() => undefined) }}
            className={`w-full text-left px-2 py-1 rounded ${mainCategory === 'space' ? 'bg-bg-surface text-white' : 'text-text-muted hover:text-white'}`}
          >
            Espacos
          </button>
          <button
            onClick={() => setMainCategory('library')}
            className={`w-full text-left px-2 py-1 rounded ${mainCategory === 'library' ? 'bg-bg-surface text-white' : 'text-text-muted hover:text-white'}`}
          >
            Biblioteca
          </button>

          <div className="pt-1 border-t border-border space-y-1">
            <button
              onClick={() => openFileDialog('audio')}
              disabled={!project || loading || actionBusy}
              className="w-full text-left px-2 py-1 rounded text-[10px] text-text-muted hover:text-white disabled:opacity-50"
            >
              + Audio
            </button>
            <button
              onClick={() => openFileDialog('image')}
              disabled={!project || loading || actionBusy}
              className="w-full text-left px-2 py-1 rounded text-[10px] text-text-muted hover:text-white disabled:opacity-50"
            >
              + Imagem
            </button>
            <button
              onClick={() => openFileDialog('project')}
              disabled={loading || actionBusy}
              className="w-full text-left px-2 py-1 rounded text-[10px] text-text-muted hover:text-white disabled:opacity-50"
            >
              Abrir projeto
            </button>
          </div>
        </aside>

        <section className="space-y-2">
          <div className="flex items-center gap-1.5">
            <div className="min-w-0 flex-1 flex items-center gap-1 rounded bg-bg-surface border border-border px-2">
              <Search size={12} className="text-text-dim" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key !== 'Enter') return
                  if (mainCategory === 'library' || mainCategory === 'ai') searchLibrary().catch(() => undefined)
                }}
                placeholder="Pesquise videos e fotos"
                className="min-w-0 flex-1 bg-transparent outline-none text-[11px] text-white py-1.5"
              />
            </div>

            {(mainCategory === 'library' || mainCategory === 'ai') && (
              <button
                onClick={() => searchLibrary().catch(() => undefined)}
                disabled={loading}
                className="w-8 h-8 rounded bg-bg-surface border border-border text-text-muted hover:text-white flex items-center justify-center disabled:opacity-50"
                title="Buscar biblioteca"
              >
                {loading ? <Loader2 size={12} className="animate-spin" /> : <Search size={12} />}
              </button>
            )}
          </div>

          {(mainCategory === 'library' || mainCategory === 'ai') && (
            <div className="flex flex-wrap items-center gap-1">
              <select
                value={searchType}
                onChange={(e) => setSearchType(e.target.value as SearchType)}
                className="rounded bg-bg-surface border border-border px-2 py-1 text-[10px] text-white outline-none"
              >
                <option value="video">Videos</option>
                <option value="image">Imagens</option>
                <option value="audio">Audio</option>
              </select>
              {providerChoices.map((item) => (
                <button
                  key={item}
                  onClick={() => setProvider(item)}
                  className={`px-2 py-1 rounded text-[10px] capitalize ${provider === item ? 'bg-accent text-white' : 'bg-bg-surface text-text-muted hover:text-white'}`}
                >
                  {item}
                </button>
              ))}
              {mainCategory === 'ai' && (
                <button
                  onClick={() => runAiGenerate().catch(() => undefined)}
                  disabled={loading}
                  className="ml-auto px-2 py-1 rounded bg-accent/80 hover:bg-accent text-white text-[10px] disabled:opacity-60 flex items-center gap-1"
                >
                  <Wand2 size={11} />
                  Gerar busca IA
                </button>
              )}
            </div>
          )}

          {mainCategory === 'library' && (
            <div className="flex flex-wrap gap-1">
              {LIBRARY_PRESETS.map((preset) => (
                <button
                  key={preset.id}
                  onClick={() => {
                    setLibraryPreset(preset.id)
                    setSearchType(preset.type)
                    setQuery(preset.query)
                    setMainCategory('library')
                    searchLibrary({ q: preset.query, type: preset.type, provider: PROVIDERS_BY_TYPE[preset.type][0] }).catch(() => undefined)
                  }}
                  className={`px-2 py-1 rounded text-[10px] ${libraryPreset === preset.id ? 'bg-accent/30 text-accent border border-accent/40' : 'bg-bg-surface text-text-muted hover:text-white border border-transparent'}`}
                >
                  {preset.label}
                </button>
              ))}
            </div>
          )}

          {mainCategory === 'space' && (
            <div className="rounded border border-border bg-bg-surface px-2 py-1.5 flex items-center gap-2">
              <button
                onClick={() => openSpaceFolder().catch(() => undefined)}
                className="px-2 py-1 rounded bg-bg-panel text-[10px] text-text-muted hover:text-white flex items-center gap-1"
              >
                <FolderOpen size={11} />
                Escolher pasta
              </button>
              <p className="text-[10px] text-text-dim truncate">{spacePath || 'Nenhuma pasta selecionada'}</p>
            </div>
          )}

          {mainCategory === 'ai' && (
            <div className="rounded border border-border bg-bg-panel p-2 text-[10px] text-text-muted flex items-start gap-2">
              <Sparkles size={12} className="text-accent mt-0.5" />
              <p>Modo IA: usamos seu prompt para gerar uma busca inteligente na biblioteca e retornar assets prontos para timeline.</p>
            </div>
          )}

          {mainCategory === 'library' && (
            <div className="rounded border border-border bg-bg-panel p-2 space-y-1">
              <p className="text-[10px] text-text-dim uppercase tracking-wider">APIs encontradas</p>
              <div className="flex flex-wrap gap-1">
                {providerStatuses.length === 0 && (
                  <span className="text-[10px] text-text-dim">Carregando...</span>
                )}
                {providerStatuses.map((item: any) => (
                  <span
                    key={item.id}
                    className={`inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[10px] ${item.configured ? 'border-emerald-500/40 bg-emerald-500/15 text-emerald-200' : 'border-border bg-bg-surface text-text-muted'}`}
                  >
                    {item.configured ? <CheckCircle2 size={10} /> : <X size={10} />}
                    {item.label}
                  </span>
                ))}
              </div>
            </div>
          )}

          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1 text-[10px] text-text-dim">
              <span>Zoom</span>
              <input
                type="range"
                min={0.8}
                max={1.2}
                step={0.1}
                value={mediaZoom}
                onChange={(e) => setMediaZoom(Number(e.target.value))}
                className="w-20"
              />
            </div>
            <select
              value={mediaSort}
              onChange={(e) => setMediaSort(e.target.value as MediaSort)}
              className="rounded bg-bg-surface border border-border px-2 py-1 text-[10px] text-white outline-none"
            >
              <option value="recent">Recentes</option>
              <option value="name">Nome</option>
              <option value="type">Tipo</option>
              <option value="duration">Duracao</option>
            </select>
            <button
              onClick={() => {
                clearRecentFiles()
                setRecents([])
              }}
              className="ml-auto px-2 py-1 rounded bg-bg-surface border border-border text-[10px] text-text-muted hover:text-white"
            >
              Limpar recentes
            </button>
          </div>

          {(actionMessage || actionBusy) && (
            <div className="rounded border border-accent/30 bg-accent/10 px-2 py-1.5 text-[10px] text-accent flex items-center gap-2">
              <Loader2 size={12} className="animate-spin" />
              <span className="truncate">{actionMessage || 'Preparando midia para timeline...'}</span>
            </div>
          )}

          {mainCategory === 'mine' && favoriteAssets.length > 0 && (
            <div className="rounded border border-border bg-bg-panel p-2">
              <p className="text-[10px] text-text-dim uppercase tracking-wider mb-1">Favoritos</p>
              <div className="flex flex-wrap gap-1">
                {favoriteAssets.slice(0, 8).map((asset) => (
                  <button
                    key={favoriteKey(asset)}
                    onClick={() => {
                      if (asset.local_path) insertAsset(asset).catch(() => undefined)
                    }}
                    className="px-2 py-1 rounded bg-bg-surface text-[10px] text-text-muted hover:text-white truncate max-w-[140px]"
                  >
                    {asset.title}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className={`grid ${thumbCols} gap-2 max-h-[420px] overflow-y-auto pr-0.5`}>
            {visibleAssets.length === 0 ? (
              <div className="col-span-full rounded border border-dashed border-border p-5 text-center text-[11px] text-text-dim">
                Nenhuma midia para exibir nesta categoria.
              </div>
            ) : visibleAssets.map((asset) => {
              const key = favoriteKey(asset)
              const isFav = favoriteKeys.includes(key)
              const isBusy = busyKey === key
              const thumb = assetThumbUrl(asset)
              const dragPath = asset.local_path || ''
              return (
                <div
                  key={key}
                  draggable={!!dragPath}
                  onDragStart={(e) => {
                    if (!dragPath) return
                    e.dataTransfer.setData('application/x-cortacerto-clip', JSON.stringify({
                      type: asset.type,
                      path: dragPath,
                    }))
                    e.dataTransfer.effectAllowed = 'copy'
                  }}
                  className="rounded border border-border bg-bg-surface overflow-hidden"
                  style={{ fontSize: `${Math.round(11 * mediaZoom)}px` }}
                >
                  <div className="aspect-video bg-black/70 relative">
                    {thumb
                      ? <img src={thumb} alt="" className="w-full h-full object-cover" loading="lazy" />
                      : (
                        <div className="w-full h-full flex items-center justify-center text-text-dim">
                          {asset.type === 'video' && <Film size={18} />}
                          {asset.type === 'image' && <Image size={18} />}
                          {asset.type === 'audio' && <Music size={18} />}
                        </div>
                      )}
                    <span className="absolute top-1 right-1 rounded bg-black/70 px-1 py-0.5 text-[9px] text-white">
                      {asset.duration_s ? formatDuration(asset.duration_s) : asset.type.toUpperCase()}
                    </span>
                  </div>
                  <div className="p-1.5 space-y-1">
                    <p className="text-[10px] text-white truncate">{asset.title}</p>
                    <p className="text-[9px] text-text-dim truncate">
                      {asset.author || asset.provider} {asset.local_path ? `· ${basename(asset.local_path)}` : ''}
                    </p>
                    <div className="flex items-center justify-between gap-1">
                      <button
                        onClick={() => toggleFavorite(asset)}
                        className={`w-6 h-6 rounded flex items-center justify-center ${isFav ? 'text-rose-400' : 'text-text-dim hover:text-rose-300'}`}
                        title={isFav ? 'Desfavoritar' : 'Favoritar'}
                      >
                        <Heart size={12} fill={isFav ? 'currentColor' : 'none'} />
                      </button>
                      <button
                        onClick={() => {
                          if (!asset.local_path) downloadAsset(asset).catch(() => undefined)
                          else insertAsset(asset).catch(() => undefined)
                        }}
                        disabled={actionBusy || isBusy || !project}
                        className="px-2 py-1 rounded bg-accent/80 hover:bg-accent text-white text-[10px] disabled:opacity-50 flex items-center gap-1"
                        title={!project ? 'Abra um projeto primeiro' : asset.local_path ? 'Usar na timeline' : 'Baixar'}
                      >
                        {isBusy
                          ? <Loader2 size={10} className="animate-spin" />
                          : asset.local_path ? <Upload size={10} /> : <Download size={10} />}
                        {asset.local_path ? 'Usar' : 'Baixar'}
                      </button>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      </div>

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
