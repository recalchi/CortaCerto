import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  CircleHelp,
  Download,
  FilePlus,
  Heart,
  Loader2,
  Music,
  Pause,
  Play,
  ShieldCheck,
  Trash2,
  Volume2,
  VolumeX,
} from 'lucide-react'
import { useStore } from '../../store/useStore'
import { api } from '../../api/client'

const API = 'http://127.0.0.1:7472'
const AUDIO_FAVORITES_KEY = 'cc_audio_asset_favorites_v1'

type AudioSourceTab = 'music' | 'sfx' | 'imported' | 'recorded' | 'downloads' | 'favorites'

interface AudioAsset {
  id: string
  provider: string
  type: 'audio'
  title: string
  author?: string
  license?: string
  source_url?: string
  download_url?: string
  thumbnail_url?: string
  duration_s?: number | null
  local_path?: string
  downloaded_at?: string
  from_clip_id?: string
}

type RightsLevel = 'ok' | 'caution' | 'risk' | 'unknown'

type RightsAudit = {
  level: RightsLevel
  label: string
  guidance: string
  requiresAttribution: boolean
}

const MUSIC_CATEGORIES = [
  { id: 'hits', label: 'Sucessos', query: 'pop upbeat background music' },
  { id: 'trending', label: 'Trending', query: 'trending upbeat beat' },
  { id: 'vlog', label: 'Vlog', query: 'vlog acoustic background' },
  { id: 'phonk', label: 'Phonk', query: 'phonk drift beat' },
  { id: 'reggaeton', label: 'Reggaeton', query: 'reggaeton latin beat' },
  { id: 'marketing', label: 'Marketing', query: 'corporate promo advertising' },
  { id: 'birthday', label: 'Aniversario', query: 'happy birthday party' },
  { id: 'travel', label: 'Viagem', query: 'travel cinematic ambient' },
]

const SFX_CATEGORIES = [
  { id: 'text-openers', label: 'Texto / Abertura', query: 'magic typing paper draw text reveal' },
  { id: 'magic', label: 'Magic', query: 'magic sparkle wand shimmer' },
  { id: 'typing', label: 'Digitando', query: 'typing keyboard text typewriter' },
  { id: 'paper', label: 'Papel', query: 'paper page turn sheet whoosh' },
  { id: 'transitions', label: 'Transicoes', query: 'whoosh transition' },
  { id: 'impacts', label: 'Impactos', query: 'impact hit boom' },
  { id: 'ui', label: 'UI', query: 'ui click notification' },
  { id: 'glitch', label: 'Glitch', query: 'glitch digital error' },
  { id: 'camera', label: 'Camera', query: 'camera shutter zoom' },
  { id: 'nature', label: 'Natureza', query: 'nature birds wind' },
]

function formatDuration(s?: number | null): string {
  const sec = Math.max(0, Math.floor(Number(s || 0)))
  const m = Math.floor(sec / 60)
  const ss = `${sec % 60}`.padStart(2, '0')
  return `${m}:${ss}`
}

function basename(path: string): string {
  return path.replace(/\\/g, '/').split('/').pop() || path
}

function readFavoriteIds(): string[] {
  try {
    const raw = localStorage.getItem(AUDIO_FAVORITES_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter((item) => typeof item === 'string')
  } catch {
    return []
  }
}

function writeFavoriteIds(ids: string[]) {
  try {
    localStorage.setItem(AUDIO_FAVORITES_KEY, JSON.stringify(ids.slice(0, 300)))
  } catch {
    // ignore
  }
}

function audioAssetKey(asset: AudioAsset): string {
  return `${asset.provider}|${asset.id}|${asset.local_path || ''}`
}

function isStockPath(path: string): boolean {
  return path.toLowerCase().includes('\\cortacerto\\assets\\stock\\') || path.toLowerCase().includes('/cortacerto/assets/stock/')
}

function isRecordingPath(path: string): boolean {
  const p = path.toLowerCase().replace(/\\/g, '/')
  return p.includes('/cortacerto/assets/recordings/') || basename(path).toLowerCase().startsWith('rec-')
}

function validateRights(asset: AudioAsset): RightsAudit {
  const license = `${asset.license || ''}`.toLowerCase()
  const provider = `${asset.provider || ''}`.toLowerCase()
  const hasSource = Boolean(asset.source_url)
  const localPath = `${asset.local_path || ''}`

  if (!license && localPath && !isStockPath(localPath)) {
    return {
      level: 'unknown',
      label: 'Sem metadados de licenca',
      guidance: 'Arquivo local sem origem/licenca registrada. Valide os direitos antes de publicar.',
      requiresAttribution: false,
    }
  }

  if (license.includes('noncommercial') || license.includes('cc-by-nc')) {
    return {
      level: 'risk',
      label: 'Restricao comercial',
      guidance: 'Licenca com restricao comercial. Evite uso em conteudo monetizado.',
      requiresAttribution: true,
    }
  }

  if (license.includes('attribution') || license.includes('cc-by')) {
    return {
      level: 'caution',
      label: 'Atribuicao obrigatoria',
      guidance: 'Pode usar, mas precisa creditar autor e fonte na descricao/publicacao.',
      requiresAttribution: true,
    }
  }

  if (
    license.includes('cc0') ||
    license.includes('creative commons 0') ||
    license.includes('pixabay content license') ||
    license.includes('pexels license')
  ) {
    return {
      level: 'ok',
      label: 'Uso geralmente seguro',
      guidance: 'Licenca permissiva identificada. Mesmo assim, confira os termos da plataforma de origem.',
      requiresAttribution: false,
    }
  }

  if (provider === 'freesound' && hasSource && license) {
    return {
      level: 'caution',
      label: 'Revisar termos da fonte',
      guidance: 'Freesound pode variar por item. Abra a pagina original e confirme os termos desta faixa.',
      requiresAttribution: true,
    }
  }

  if (license || hasSource) {
    return {
      level: 'caution',
      label: 'Licenca informada',
      guidance: 'Existe metadado de licenca/origem. Revise rapidamente os termos antes de exportar.',
      requiresAttribution: false,
    }
  }

  return {
    level: 'unknown',
    label: 'Origem nao confirmada',
    guidance: 'Sem dados suficientes de direitos. Trate como risco ate confirmar a origem.',
    requiresAttribution: false,
  }
}

function rightsClasses(level: RightsLevel): string {
  if (level === 'ok') return 'bg-emerald-500/15 border-emerald-500/40 text-emerald-200'
  if (level === 'caution') return 'bg-amber-500/15 border-amber-500/40 text-amber-200'
  if (level === 'risk') return 'bg-red-500/15 border-red-500/40 text-red-200'
  return 'bg-slate-500/15 border-slate-500/40 text-slate-200'
}

function rightsIcon(level: RightsLevel) {
  if (level === 'ok') return <ShieldCheck size={12} />
  if (level === 'caution' || level === 'risk') return <AlertTriangle size={12} />
  return <CircleHelp size={12} />
}

function probeAudioDuration(path: string): Promise<number> {
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

function categoryListFor(tab: AudioSourceTab) {
  return tab === 'sfx' ? SFX_CATEGORIES : MUSIC_CATEGORIES
}

export function AudioTab() {
  const {
    project,
    previewTime,
    updateClip,
    importAudio,
    deleteClip,
    trackStates,
    setTrackState,
    exportSettings,
    setExportSetting,
  } = useStore()

  const [tab, setTab] = useState<AudioSourceTab>('music')
  const [musicCategory, setMusicCategory] = useState(MUSIC_CATEGORIES[0].id)
  const [sfxCategory, setSfxCategory] = useState(SFX_CATEGORIES[0].id)
  const [search, setSearch] = useState('')
  const [results, setResults] = useState<AudioAsset[]>([])
  const [downloads, setDownloads] = useState<AudioAsset[]>([])
  const [importing, setImporting] = useState(false)
  const [busyAssetKey, setBusyAssetKey] = useState<string | null>(null)
  const [loadingSearch, setLoadingSearch] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [favorites, setFavorites] = useState<string[]>(() => readFavoriteIds())
  const [selectedAssetKey, setSelectedAssetKey] = useState<string | null>(null)
  const audioPreviewRef = useRef<HTMLAudioElement | null>(null)
  const [playingPreviewKey, setPlayingPreviewKey] = useState<string | null>(null)

  const audioMuted = trackStates?.audio?.muted ?? false
  const normalizeOn = exportSettings.normalizeAudio

  const refreshDownloads = useCallback(async () => {
    const res = await api.get('/api/stock/downloads')
    const items = (res.data?.items ?? []) as AudioAsset[]
    setDownloads(items.filter((asset) => asset.type === 'audio'))
  }, [])

  useEffect(() => {
    refreshDownloads().catch(() => undefined)
  }, [refreshDownloads])

  const downloadedByPath = useMemo(() => {
    const map = new Map<string, AudioAsset>()
    for (const asset of downloads) {
      if (asset.local_path) map.set(asset.local_path, asset)
    }
    return map
  }, [downloads])

  const allAudioClips = useMemo(() => {
    if (!project) return []
    return [
      ...project.audio_track.clips,
      ...(project.extra_audio_tracks ?? []).flatMap((track) => track.clips),
    ]
  }, [project])

  const timelineAudioAssets = useMemo(() => {
    return allAudioClips.map((clip) => {
      const path = clip.source_path || ''
      const meta = downloadedByPath.get(path)
      return {
        id: clip.id,
        provider: meta?.provider || (path ? 'importado' : 'timeline'),
        type: 'audio' as const,
        title: clip.label || basename(path || clip.id),
        author: meta?.author || (meta ? '' : 'Local'),
        license: meta?.license || '',
        source_url: meta?.source_url || '',
        thumbnail_url: meta?.thumbnail_url || '',
        duration_s: Math.max(0, clip.end_s - clip.start_s),
        local_path: path,
        downloaded_at: meta?.downloaded_at || '',
        from_clip_id: clip.id,
      }
    })
  }, [allAudioClips, downloadedByPath])

  const recordedAssets = useMemo(() => (
    timelineAudioAssets
      .filter((asset) => asset.local_path && isRecordingPath(asset.local_path))
      .map((asset) => ({
        ...asset,
        provider: 'gravacao',
        author: 'Microfone',
        license: asset.license || 'Arquivo local gravado no CortaCerto',
      }))
  ), [timelineAudioAssets])

  const importedAssets = useMemo(() => (
    timelineAudioAssets.filter((asset) => !asset.local_path || !isRecordingPath(asset.local_path))
  ), [timelineAudioAssets])

  const activeCategory = tab === 'sfx' ? sfxCategory : musicCategory
  const activeCategoryQuery = useMemo(() => {
    const list = categoryListFor(tab)
    return list.find((item) => item.id === activeCategory)?.query || ''
  }, [activeCategory, tab])

  const runSearch = useCallback(async () => {
    if (tab !== 'music' && tab !== 'sfx') return
    const q = search.trim() || activeCategoryQuery
    if (!q) return
    setLoadingSearch(true)
    setError(null)
    try {
      const res = await api.post('/api/stock/search', {
        provider: 'freesound',
        query: q,
        media_type: 'audio',
        per_page: 24,
      })
      const items = (res.data?.items ?? []) as AudioAsset[]
      setResults(items.filter((item) => item.type === 'audio'))
    } catch (e: any) {
      const detail = e?.response?.data?.detail ?? e?.message ?? 'Erro ao buscar audios'
      setError(typeof detail === 'string' ? detail.split('\n')[0] : 'Erro ao buscar audios')
    } finally {
      setLoadingSearch(false)
    }
  }, [activeCategoryQuery, search, tab])

  useEffect(() => {
    if (tab === 'music' || tab === 'sfx') {
      runSearch().catch(() => undefined)
    }
  }, [tab, musicCategory, sfxCategory, runSearch])

  const importLocalAudio = useCallback(async () => {
    if (!project) return
    setImporting(true)
    setError(null)
    try {
      const res = await api.post('/api/open-file-dialog', { type: 'audio' })
      const path: string = res.data.path
      if (!path) return
      const [duration, waveform] = await Promise.all([
        probeAudioDuration(path),
        fetchAudioWaveform(path),
      ])
      importAudio(path, waveform.duration_s || duration, waveform.samples)
    } catch {
      setError('Falha ao importar audio')
    } finally {
      setImporting(false)
    }
  }, [importAudio, project])

  const insertAudioAsset = useCallback(async (asset: AudioAsset) => {
    const path = asset.local_path
    if (!project || !path) return
    setBusyAssetKey(audioAssetKey(asset))
    setError(null)
    try {
      const [duration, waveform] = await Promise.all([
        probeAudioDuration(path),
        fetchAudioWaveform(path),
      ])
      importAudio(path, waveform.duration_s || duration, waveform.samples)
    } catch {
      setError('Falha ao inserir audio na timeline')
    } finally {
      setBusyAssetKey(null)
    }
  }, [importAudio, project])

  const downloadAudioAsset = useCallback(async (asset: AudioAsset) => {
    setBusyAssetKey(audioAssetKey(asset))
    setError(null)
    try {
      const res = await api.post('/api/stock/download', { asset })
      const downloaded = res.data as AudioAsset
      setDownloads((prev) => [downloaded, ...prev.filter((item) => item.local_path !== downloaded.local_path)])
      await insertAudioAsset(downloaded)
    } catch (e: any) {
      const detail = e?.response?.data?.detail ?? e?.message ?? 'Erro ao baixar audio'
      setError(typeof detail === 'string' ? detail.split('\n')[0] : 'Erro ao baixar audio')
    } finally {
      setBusyAssetKey(null)
    }
  }, [insertAudioAsset])

  const toggleFavorite = (asset: AudioAsset) => {
    const key = audioAssetKey(asset)
    setFavorites((prev) => {
      const next = prev.includes(key) ? prev.filter((item) => item !== key) : [key, ...prev]
      writeFavoriteIds(next)
      return next
    })
  }

  const sourceItems = useMemo(() => {
    if (tab === 'music') return results
    if (tab === 'sfx') {
      const localSfx = downloads.filter((asset) => asset.provider === 'cortacerto')
      const seen = new Set<string>()
      return [...localSfx, ...results].filter((asset) => {
        const key = audioAssetKey(asset)
        if (seen.has(key)) return false
        seen.add(key)
        return true
      })
    }
    if (tab === 'downloads') return downloads
    if (tab === 'imported') return importedAssets
    if (tab === 'recorded') return recordedAssets
    const combined = [...downloads, ...results, ...importedAssets, ...recordedAssets]
    const seen = new Set<string>()
    const uniq: AudioAsset[] = []
    for (const item of combined) {
      const key = audioAssetKey(item)
      if (!favorites.includes(key) || seen.has(key)) continue
      seen.add(key)
      uniq.push(item)
    }
    return uniq
  }, [downloads, favorites, importedAssets, recordedAssets, results, tab])

  const filteredItems = useMemo(() => {
    const q = search.trim().toLowerCase()
    let list = sourceItems
    if (q) {
      list = list.filter((item) =>
        `${item.title} ${item.author || ''}`.toLowerCase().includes(q),
      )
    }
    if (tab === 'music') {
      list = [...list].sort((a, b) => (b.duration_s || 0) - (a.duration_s || 0))
    } else if (tab === 'sfx') {
      list = [...list].sort((a, b) => (a.duration_s || 0) - (b.duration_s || 0))
    }
    return list
  }, [search, sourceItems, tab])

  const selectedAsset = useMemo(() => {
    if (!selectedAssetKey) return filteredItems[0] || null
    return filteredItems.find((item) => audioAssetKey(item) === selectedAssetKey) || filteredItems[0] || null
  }, [filteredItems, selectedAssetKey])

  const selectedAudit = selectedAsset ? validateRights(selectedAsset) : null

  useEffect(() => () => {
    audioPreviewRef.current?.pause()
    audioPreviewRef.current = null
  }, [])

  const previewAssetAudio = useCallback((asset: AudioAsset) => {
    const key = audioAssetKey(asset)
    const src = asset.local_path
      ? `${API}/api/serve-file?path=${encodeURIComponent(asset.local_path)}`
      : (asset.download_url || asset.source_url || '')
    if (!src) {
      setError('Audio sem preview disponivel')
      return
    }
    if (playingPreviewKey === key) {
      audioPreviewRef.current?.pause()
      setPlayingPreviewKey(null)
      return
    }
    const audio = audioPreviewRef.current ?? new Audio()
    audioPreviewRef.current = audio
    audio.pause()
    audio.src = src
    audio.currentTime = 0
    audio.volume = 0.85
    audio.onended = () => setPlayingPreviewKey(null)
    audio.onerror = () => {
      setPlayingPreviewKey(null)
      setError('Falha ao reproduzir preview do audio')
    }
    audio.play()
      .then(() => setPlayingPreviewKey(key))
      .catch(() => {
        setPlayingPreviewKey(null)
        setError('Falha ao reproduzir preview do audio')
      })
  }, [playingPreviewKey])

  const startAssetDrag = (e: React.DragEvent<HTMLDivElement>, asset: AudioAsset) => {
    if (!asset.local_path) return
    e.dataTransfer.effectAllowed = 'copy'
    e.dataTransfer.setData('application/x-cortacerto-clip', JSON.stringify({
      type: 'audio',
      path: asset.local_path,
      title: asset.title,
    }))
  }

  const activateAsset = (asset: AudioAsset) => {
    if (asset.from_clip_id) {
      useStore.getState().setSelectedClip(asset.from_clip_id)
      return
    }
    if (asset.local_path) insertAudioAsset(asset)
    else downloadAudioAsset(asset)
  }

  const copyAttribution = async () => {
    if (!selectedAsset) return
    const text = [
      `Faixa: ${selectedAsset.title}`,
      `Autor: ${selectedAsset.author || 'nao informado'}`,
      `Licenca: ${selectedAsset.license || 'nao informada'}`,
      `Fonte: ${selectedAsset.source_url || 'nao informada'}`,
    ].join('\n')
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      // ignore
    }
  }

  const categoryList = categoryListFor(tab)
  const actionLabel = importing
    ? 'Importando audio e calculando onda sonora...'
    : busyAssetKey
      ? 'Preparando audio para a timeline...'
      : loadingSearch
        ? 'Buscando catalogo de audio...'
        : null

  return (
    <div className="p-2 space-y-2 text-xs">
      <div className="grid grid-cols-[94px_1fr] gap-2">
        <div className="space-y-1">
          <button
            onClick={importLocalAudio}
            disabled={!project || importing}
            className="w-full px-2 py-1.5 rounded bg-accent text-white text-[11px] font-medium disabled:opacity-60"
            title={!project ? 'Abra um projeto primeiro' : 'Importar audio local'}
          >
            {importing ? 'Importando...' : 'Importar'}
          </button>

          <button
            onClick={() => setTab('music')}
            className={`w-full text-left px-2 py-1 rounded text-[10px] ${tab === 'music' ? 'bg-bg-surface text-white' : 'text-text-muted hover:text-white'}`}
          >
            Musicas
          </button>
          <button
            onClick={() => setTab('sfx')}
            className={`w-full text-left px-2 py-1 rounded text-[10px] ${tab === 'sfx' ? 'bg-bg-surface text-white' : 'text-text-muted hover:text-white'}`}
          >
            Efeitos
          </button>
          <button
            onClick={() => setTab('imported')}
            className={`w-full text-left px-2 py-1 rounded text-[10px] ${tab === 'imported' ? 'bg-bg-surface text-white' : 'text-text-muted hover:text-white'}`}
          >
            Importados
          </button>
          <button
            onClick={() => setTab('recorded')}
            className={`w-full text-left px-2 py-1 rounded text-[10px] ${tab === 'recorded' ? 'bg-bg-surface text-white' : 'text-text-muted hover:text-white'}`}
          >
            Gravados
          </button>
          <button
            onClick={() => setTab('downloads')}
            className={`w-full text-left px-2 py-1 rounded text-[10px] ${tab === 'downloads' ? 'bg-bg-surface text-white' : 'text-text-muted hover:text-white'}`}
          >
            Baixados
          </button>
          <button
            onClick={() => setTab('favorites')}
            className={`w-full text-left px-2 py-1 rounded text-[10px] ${tab === 'favorites' ? 'bg-bg-surface text-white' : 'text-text-muted hover:text-white'}`}
          >
            Favoritos
          </button>

          {(tab === 'music' || tab === 'sfx') && (
            <div className="border-t border-border pt-1 mt-1 space-y-0.5 max-h-[188px] overflow-y-auto pr-0.5">
              {categoryList.map((cat) => {
                const active = (tab === 'music' ? musicCategory : sfxCategory) === cat.id
                return (
                  <button
                    key={cat.id}
                    onClick={() => tab === 'music' ? setMusicCategory(cat.id) : setSfxCategory(cat.id)}
                    className={`w-full text-left px-2 py-1 rounded text-[10px] ${active ? 'bg-accent/20 text-accent' : 'text-text-muted hover:text-white'}`}
                  >
                    {cat.label}
                  </button>
                )
              })}
            </div>
          )}
        </div>

        <div className="space-y-2">
          <div className="flex items-center gap-1.5">
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') runSearch() }}
              placeholder={tab === 'music' || tab === 'sfx' ? 'Pesquisar musicas ou artistas' : 'Filtrar por nome'}
              className="min-w-0 flex-1 bg-bg-surface border border-border rounded px-2 py-1.5 text-[11px] text-white outline-none"
            />
            {(tab === 'music' || tab === 'sfx') && (
              <button
                onClick={() => runSearch()}
                disabled={loadingSearch}
                className="w-8 h-8 rounded bg-bg-surface border border-border text-text-muted hover:text-white disabled:opacity-60 flex items-center justify-center"
                title="Buscar"
              >
                {loadingSearch ? <Loader2 size={12} className="animate-spin" /> : <Music size={12} />}
              </button>
            )}
          </div>

          {actionLabel && (
            <div className="rounded border border-accent/30 bg-accent/10 px-2 py-1.5 text-[10px] text-accent flex items-center gap-2">
              <Loader2 size={12} className="animate-spin" />
              <span className="truncate">{actionLabel}</span>
            </div>
          )}

          <div className="space-y-1.5 max-h-[330px] overflow-y-auto pr-0.5">
            {filteredItems.length === 0 ? (
              <div className="rounded border border-dashed border-border p-4 text-[11px] text-text-dim text-center">
                {tab === 'imported'
                  ? 'Nenhum audio importado.'
                  : tab === 'recorded'
                    ? 'Nenhuma gravacao de microfone.'
                  : tab === 'downloads'
                    ? 'Nenhum audio baixado.'
                    : tab === 'favorites'
                      ? 'Nenhum favorito ainda.'
                      : 'Busque e baixe audios para usar na timeline.'}
              </div>
            ) : filteredItems.map((asset) => {
              const key = audioAssetKey(asset)
              const fav = favorites.includes(key)
              const selected = selectedAsset && audioAssetKey(selectedAsset) === key
              const busy = busyAssetKey === key
              const audit = validateRights(asset)
              const isImportedClip = Boolean(asset.from_clip_id)
              const clip = isImportedClip
                ? allAudioClips.find((item) => item.id === asset.from_clip_id)
                : null
              return (
                <div
                  key={key}
                  draggable={Boolean(asset.local_path)}
                  onDragStart={(e) => startAssetDrag(e, asset)}
                  onDoubleClick={() => activateAsset(asset)}
                  className={`rounded border ${selected ? 'border-accent' : 'border-border'} bg-bg-surface p-1.5`}
                  onClick={() => setSelectedAssetKey(key)}
                  title={asset.local_path ? 'Arraste para a timeline ou dê duplo clique para usar' : 'Dê duplo clique para baixar e usar'}
                >
                  <div className="flex items-center gap-2">
                    <div className="relative w-10 h-10 rounded bg-bg-panel overflow-hidden flex items-center justify-center text-text-dim">
                      {asset.thumbnail_url ? (
                        <img src={asset.thumbnail_url} alt="" className="w-full h-full object-cover" loading="lazy" />
                      ) : (
                        <Music size={16} />
                      )}
                      <button
                        onClick={(e) => { e.stopPropagation(); previewAssetAudio(asset) }}
                        className="absolute inset-0 m-auto h-6 w-6 rounded-full bg-black/65 text-white/90 hover:bg-black/85 flex items-center justify-center"
                        title={playingPreviewKey === key ? 'Pausar preview' : 'Ouvir preview'}
                      >
                        {playingPreviewKey === key ? <Pause size={12} /> : <Play size={12} className="ml-0.5" />}
                      </button>
                    </div>

                    <div className="min-w-0 flex-1">
                      <p className="text-[11px] text-white truncate">{asset.title}</p>
                      <p className="text-[10px] text-text-dim truncate">
                        {asset.author || 'Autor nao informado'} · {formatDuration(asset.duration_s)}
                      </p>
                      <div
                        className={`mt-1 inline-flex items-center gap-1 rounded text-[8px] opacity-65 ${rightsClasses(audit.level)}`}
                        title={audit.guidance}
                      >
                        {rightsIcon(audit.level)}
                        {audit.label}
                      </div>
                    </div>

                    <div className="flex items-center gap-1">
                      <button
                        onClick={(e) => { e.stopPropagation(); toggleFavorite(asset) }}
                        className={`w-6 h-6 rounded flex items-center justify-center ${fav ? 'text-rose-400' : 'text-text-dim hover:text-rose-300'}`}
                        title={fav ? 'Desfavoritar' : 'Favoritar'}
                      >
                        <Heart size={12} fill={fav ? 'currentColor' : 'none'} />
                      </button>

                      {isImportedClip ? (
                        <button
                          onClick={(e) => { e.stopPropagation(); if (asset.from_clip_id) deleteClip(asset.from_clip_id) }}
                          className="w-6 h-6 rounded flex items-center justify-center text-text-dim hover:text-red-400"
                          title="Remover da timeline"
                        >
                          <Trash2 size={12} />
                        </button>
                      ) : (
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            if (asset.local_path) insertAudioAsset(asset)
                            else downloadAudioAsset(asset)
                          }}
                          disabled={!project || busy}
                          className="w-6 h-6 rounded flex items-center justify-center text-text-dim hover:text-white disabled:opacity-50"
                          title={asset.local_path ? 'Usar na timeline' : 'Baixar e usar'}
                        >
                          {busy
                            ? <Loader2 size={12} className="animate-spin" />
                            : asset.local_path ? <FilePlus size={12} /> : <Download size={12} />}
                        </button>
                      )}
                    </div>
                  </div>

                  {clip && (
                    <div className="mt-1.5 space-y-1">
                      <div className="flex items-center justify-between text-[9px] text-text-dim">
                        <span>Volume</span>
                        <span>{clip.volume_pct ?? 100}%</span>
                      </div>
                      <input
                        type="range"
                        min={0}
                        max={200}
                        step={1}
                        value={clip.volume_pct ?? 100}
                        onChange={(e) => updateClip(clip.id, { volume_pct: Number(e.target.value) })}
                        className="w-full h-1 accent-accent"
                      />
                    </div>
                  )}
                </div>
              )
            })}
          </div>

          {selectedAsset && selectedAudit && (
            <div className="rounded border border-border/70 bg-bg-panel/70 p-2 space-y-1">
              <div className={`inline-flex items-center gap-1 text-[9px] opacity-75 ${rightsClasses(selectedAudit.level)}`}>
                {rightsIcon(selectedAudit.level)}
                Verificacao de direitos
              </div>
              <p className="text-[9px] text-text-dim leading-relaxed">{selectedAudit.guidance}</p>
              <div className="flex items-center gap-1.5">
                <button
                  onClick={copyAttribution}
                  className="px-2 py-1 rounded bg-bg-surface border border-border text-[10px] text-text-muted hover:text-white"
                >
                  Copiar credito
                </button>
                <a
                  href={selectedAsset.source_url || '#'}
                  target="_blank"
                  rel="noreferrer"
                  className={`text-[10px] ${selectedAsset.source_url ? 'text-accent hover:underline' : 'text-text-dim pointer-events-none'}`}
                >
                  Abrir origem
                </a>
                {selectedAudit.requiresAttribution && (
                  <span className="text-[9px] text-amber-300">Atribuicao recomendada</span>
                )}
              </div>
            </div>
          )}

          <div className="rounded border border-border bg-bg-surface p-2 space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-[10px] text-text-dim uppercase tracking-wider">Faixa de audio</span>
              <button
                onClick={() => setTrackState('audio', { muted: !audioMuted })}
                className={`flex items-center gap-1 text-[10px] px-2 py-0.5 rounded ${audioMuted ? 'bg-red-900/40 text-red-300' : 'bg-bg-panel text-text-muted hover:text-white'}`}
              >
                {audioMuted ? <VolumeX size={11} /> : <Volume2 size={11} />}
                {audioMuted ? 'Mudo' : 'Ativo'}
              </button>
            </div>

            <label className="flex items-center justify-between text-[10px] text-text-muted">
              <span>Normalizar audio no export</span>
              <button
                onClick={() => setExportSetting('normalizeAudio', !normalizeOn)}
                className={`relative w-8 h-4 rounded-full transition-colors ${normalizeOn ? 'bg-accent' : 'bg-bg-panel border border-border'}`}
              >
                <span
                  className={`absolute left-0.5 top-0.5 w-3 h-3 rounded-full bg-white transition-transform ${normalizeOn ? 'translate-x-4' : 'translate-x-0'}`}
                />
              </button>
            </label>
            <p className="text-[9px] text-text-dim">Playhead atual: {previewTime.toFixed(1)}s</p>
          </div>
        </div>
      </div>

      {error && (
        <div className="rounded border border-red-800/60 bg-red-900/25 px-2 py-1.5 text-[10px] text-red-300">
          {error}
        </div>
      )}
    </div>
  )
}
