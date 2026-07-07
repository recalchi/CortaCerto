import { useEffect, useMemo, useState } from 'react'
import { Box, Filter, Layers, Search, Sparkles, Star, StarOff } from 'lucide-react'
import { Clip, useStore } from '../../store/useStore'

const API = 'http://127.0.0.1:7472'
const FILTER_FAVORITES_KEY = 'cortacerto_filter_favorites_v2'

type ApplyMode = 'clip' | 'layer'
type FilterCategory =
  | 'cinematic'
  | 'retro'
  | 'vibrant'
  | 'mono'
  | 'correction'
  | 'creative'
  | 'glow'
  | 'blur'
  | 'film'

type FilterPreset = {
  id: string
  label: string
  category: FilterCategory
  brightness: number
  contrast: number
  saturation: number
  temperature: number
  hue: number
  exposure: number
  sharpness: number
  vignette: number
  blur_type?: Clip['blur_type']
  blur_intensity?: number
  blur_direction?: Clip['blur_direction']
  swatchA: string
  swatchB: string
}

const PRESETS: FilterPreset[] = [
  { id: 'cinema_soft', label: 'Cinema Soft', category: 'cinematic', brightness: -2, contrast: 12, saturation: 8, temperature: -4, hue: 1, exposure: 4, sharpness: 8, vignette: 8, swatchA: '#1f2937', swatchB: '#6b7280' },
  { id: 'cinema_teal', label: 'Teal Orange', category: 'cinematic', brightness: 2, contrast: 18, saturation: 14, temperature: 8, hue: -4, exposure: 6, sharpness: 10, vignette: 10, swatchA: '#155e75', swatchB: '#fb923c' },
  { id: 'cinema_drama', label: 'Drama', category: 'cinematic', brightness: -8, contrast: 26, saturation: 4, temperature: -6, hue: 0, exposure: 2, sharpness: 14, vignette: 16, swatchA: '#111827', swatchB: '#1f2937' },
  { id: 'cinema_block', label: 'Blockbuster', category: 'cinematic', brightness: -3, contrast: 20, saturation: 12, temperature: 6, hue: -3, exposure: 4, sharpness: 16, vignette: 14, swatchA: '#172554', swatchB: '#f97316' },
  { id: 'retro_vhs', label: 'VHS', category: 'retro', brightness: 4, contrast: -10, saturation: -22, temperature: 10, hue: 2, exposure: 0, sharpness: 4, vignette: 6, swatchA: '#7c2d12', swatchB: '#ca8a04' },
  { id: 'retro_film', label: 'Filme 35', category: 'retro', brightness: 0, contrast: -4, saturation: -12, temperature: 12, hue: -2, exposure: 2, sharpness: 6, vignette: 12, swatchA: '#854d0e', swatchB: '#f59e0b' },
  { id: 'retro_fade', label: 'Fade Wash', category: 'retro', brightness: 6, contrast: -18, saturation: -8, temperature: 6, hue: 1, exposure: 6, sharpness: 2, vignette: 4, swatchA: '#a16207', swatchB: '#facc15' },
  { id: 'retro_tape', label: 'Tape Dust', category: 'retro', brightness: 2, contrast: -14, saturation: -18, temperature: 14, hue: 3, exposure: 4, sharpness: 2, vignette: 9, swatchA: '#78350f', swatchB: '#d6d3d1' },
  { id: 'vivid_pop', label: 'Pop', category: 'vibrant', brightness: 4, contrast: 12, saturation: 28, temperature: 2, hue: 0, exposure: 6, sharpness: 12, vignette: 4, swatchA: '#2563eb', swatchB: '#ec4899' },
  { id: 'vivid_sport', label: 'Sport', category: 'vibrant', brightness: 2, contrast: 20, saturation: 24, temperature: -2, hue: -1, exposure: 4, sharpness: 18, vignette: 6, swatchA: '#0f766e', swatchB: '#22c55e' },
  { id: 'vivid_neon', label: 'Neon', category: 'vibrant', brightness: 0, contrast: 22, saturation: 30, temperature: -8, hue: 4, exposure: 3, sharpness: 16, vignette: 10, swatchA: '#4c1d95', swatchB: '#06b6d4' },
  { id: 'vivid_boost', label: 'Boost', category: 'vibrant', brightness: 6, contrast: 10, saturation: 22, temperature: 2, hue: 1, exposure: 8, sharpness: 12, vignette: 3, swatchA: '#0ea5e9', swatchB: '#22d3ee' },
  { id: 'mono_clean', label: 'PB Clean', category: 'mono', brightness: 2, contrast: 18, saturation: -100, temperature: 0, hue: 0, exposure: 5, sharpness: 12, vignette: 8, swatchA: '#e5e7eb', swatchB: '#374151' },
  { id: 'mono_fade', label: 'PB Fade', category: 'mono', brightness: 8, contrast: -6, saturation: -100, temperature: 0, hue: 0, exposure: 8, sharpness: 4, vignette: 6, swatchA: '#9ca3af', swatchB: '#111827' },
  { id: 'mono_hard', label: 'PB Hard', category: 'mono', brightness: -4, contrast: 30, saturation: -100, temperature: 0, hue: 0, exposure: 0, sharpness: 20, vignette: 14, swatchA: '#000000', swatchB: '#9ca3af' },
  { id: 'corr_skin', label: 'Pele Natural', category: 'correction', brightness: 2, contrast: 6, saturation: 6, temperature: 4, hue: -1, exposure: 4, sharpness: 6, vignette: 2, swatchA: '#78350f', swatchB: '#fdba74' },
  { id: 'corr_coldfix', label: 'Corrigir Frio', category: 'correction', brightness: 3, contrast: 6, saturation: 8, temperature: 10, hue: 0, exposure: 4, sharpness: 8, vignette: 2, swatchA: '#0f172a', swatchB: '#fb7185' },
  { id: 'corr_hotfix', label: 'Corrigir Quente', category: 'correction', brightness: 1, contrast: 4, saturation: 4, temperature: -10, hue: 0, exposure: 3, sharpness: 8, vignette: 2, swatchA: '#0c4a6e', swatchB: '#bae6fd' },
  { id: 'corr_clear', label: 'Clear Face', category: 'correction', brightness: 2, contrast: 7, saturation: 5, temperature: 1, hue: 0, exposure: 5, sharpness: 9, vignette: 0, swatchA: '#475569', swatchB: '#e2e8f0' },
  { id: 'creative_dream', label: 'Dream', category: 'creative', brightness: 12, contrast: -8, saturation: 14, temperature: -2, hue: 3, exposure: 10, sharpness: 0, vignette: 0, swatchA: '#f5d0fe', swatchB: '#a5f3fc' },
  { id: 'creative_night', label: 'Night Pop', category: 'creative', brightness: -10, contrast: 14, saturation: 10, temperature: -16, hue: 4, exposure: -4, sharpness: 14, vignette: 18, swatchA: '#0f172a', swatchB: '#2563eb' },
  { id: 'creative_amber', label: 'Amber', category: 'creative', brightness: 4, contrast: 8, saturation: 10, temperature: 16, hue: -2, exposure: 6, sharpness: 10, vignette: 8, swatchA: '#7c2d12', swatchB: '#f59e0b' },
  { id: 'creative_fantasy', label: 'Fantasy', category: 'creative', brightness: 8, contrast: 6, saturation: 16, temperature: -6, hue: 8, exposure: 10, sharpness: 4, vignette: 4, swatchA: '#1d4ed8', swatchB: '#c026d3' },
  { id: 'glow_pink', label: 'Pink Glow', category: 'glow', brightness: 10, contrast: 2, saturation: 20, temperature: 4, hue: 6, exposure: 12, sharpness: 0, vignette: 0, swatchA: '#db2777', swatchB: '#f9a8d4' },
  { id: 'glow_blue', label: 'Blue Glow', category: 'glow', brightness: 8, contrast: 4, saturation: 18, temperature: -8, hue: -4, exposure: 10, sharpness: 0, vignette: 1, swatchA: '#0ea5e9', swatchB: '#93c5fd' },
  { id: 'blur_soft_focus', label: 'Soft Focus', category: 'blur', brightness: 4, contrast: -4, saturation: 2, temperature: 2, hue: 0, exposure: 4, sharpness: 0, vignette: 0, blur_type: 'gaussian', blur_intensity: 18, blur_direction: 'both', swatchA: '#64748b', swatchB: '#c4b5fd' },
  { id: 'blur_dream', label: 'Dream Blur', category: 'blur', brightness: 10, contrast: -10, saturation: 8, temperature: -2, hue: 2, exposure: 8, sharpness: 0, vignette: 0, blur_type: 'gaussian', blur_intensity: 32, blur_direction: 'both', swatchA: '#f0abfc', swatchB: '#93c5fd' },
  { id: 'blur_privacy', label: 'Privacidade', category: 'blur', brightness: 0, contrast: -2, saturation: -4, temperature: 0, hue: 0, exposure: 0, sharpness: 0, vignette: 0, blur_type: 'box', blur_intensity: 62, blur_direction: 'both', swatchA: '#1f2937', swatchB: '#94a3b8' },
  { id: 'blur_speed_h', label: 'Motion H', category: 'blur', brightness: 0, contrast: 2, saturation: 0, temperature: 0, hue: 0, exposure: 0, sharpness: 0, vignette: 0, blur_type: 'gaussian', blur_intensity: 42, blur_direction: 'horizontal', swatchA: '#0f172a', swatchB: '#38bdf8' },
  { id: 'blur_tilt_v', label: 'Motion V', category: 'blur', brightness: 0, contrast: 2, saturation: 0, temperature: 0, hue: 0, exposure: 0, sharpness: 0, vignette: 0, blur_type: 'gaussian', blur_intensity: 42, blur_direction: 'vertical', swatchA: '#312e81', swatchB: '#a78bfa' },
  { id: 'blur_pixel', label: 'Mosaico', category: 'blur', brightness: 0, contrast: 4, saturation: 0, temperature: 0, hue: 0, exposure: 0, sharpness: 0, vignette: 0, blur_type: 'pixelate', blur_intensity: 55, blur_direction: 'both', swatchA: '#111827', swatchB: '#f97316' },
  { id: 'film_warm', label: 'Film Warm', category: 'film', brightness: 3, contrast: 9, saturation: -5, temperature: 12, hue: -1, exposure: 4, sharpness: 5, vignette: 9, swatchA: '#7c2d12', swatchB: '#fbbf24' },
  { id: 'film_cool', label: 'Film Cool', category: 'film', brightness: 1, contrast: 11, saturation: -3, temperature: -12, hue: 1, exposure: 3, sharpness: 6, vignette: 9, swatchA: '#1e3a8a', swatchB: '#67e8f9' },
]

const CATEGORY_LABELS: Record<string, string> = {
  all: 'Todos',
  favorites: 'Favoritos',
  cinematic: 'Cinematico',
  retro: 'Retro',
  vibrant: 'Vibrante',
  mono: 'PB',
  correction: 'Correcao',
  creative: 'Criativo',
  glow: 'Glow',
  blur: 'Desfoque',
  film: 'Film Look',
}

function readFavorites(): string[] {
  try {
    const raw = localStorage.getItem(FILTER_FAVORITES_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter((v) => typeof v === 'string') : []
  } catch {
    return []
  }
}

function writeFavorites(ids: string[]) {
  try {
    localStorage.setItem(FILTER_FAVORITES_KEY, JSON.stringify(ids.slice(0, 180)))
  } catch {
    // ignore
  }
}

function filterCssFromPreset(p: FilterPreset): string {
  const brightness = (1 + (p.brightness + p.exposure * 0.5) / 100).toFixed(3)
  const contrast = (1 + p.contrast / 100).toFixed(3)
  const saturate = (1 + p.saturation / 100).toFixed(3)
  const hue = p.hue
  const blur = p.blur_type && p.blur_type !== 'none'
    ? ` blur(${Math.max(0, Math.min(18, (p.blur_intensity ?? 0) * 0.16)).toFixed(1)}px)`
    : ''
  return `brightness(${brightness}) contrast(${contrast}) saturate(${saturate}) hue-rotate(${hue}deg)${blur}`
}

function effectPatchFromPreset(preset: FilterPreset, intensity: number): Partial<Clip> {
  const k = Math.max(0, Math.min(200, intensity)) / 100
  const mul = (v: number) => Math.round(v * k * 100) / 100
  const patch: Partial<Clip> = {
    brightness: mul(preset.brightness),
    contrast: mul(preset.contrast),
    saturation: mul(preset.saturation),
    temperature: mul(preset.temperature),
    hue: mul(preset.hue),
    exposure: mul(preset.exposure),
    sharpness: mul(preset.sharpness),
    vignette: mul(preset.vignette),
  }
  if (preset.blur_type && preset.blur_type !== 'none') {
    patch.blur_type = preset.blur_type
    patch.blur_intensity = Math.round((preset.blur_intensity ?? 0) * k)
    patch.blur_direction = preset.blur_direction ?? 'both'
  }
  return patch
}

function FilterPreviewCube({
  preset,
  previewSrc,
  hovered,
}: {
  preset: FilterPreset
  previewSrc: string
  hovered: boolean
}) {
  return (
    <div className="relative w-full h-full rounded overflow-hidden bg-[#0b0f18]">
      {previewSrc ? (
        <img
          src={previewSrc}
          alt=""
          draggable={false}
          className="absolute inset-0 w-full h-full object-cover"
          style={{
            filter: filterCssFromPreset(preset),
            transform: hovered ? 'scale(1.05)' : 'scale(1)',
            transition: 'transform 280ms ease',
          }}
        />
      ) : (
        <div
          className="absolute inset-0"
          style={{
            background: `linear-gradient(145deg, ${preset.swatchA} 0%, ${preset.swatchB} 100%)`,
            transform: hovered ? 'scale(1.04)' : 'scale(1)',
            transition: 'transform 280ms ease',
          }}
        />
      )}
      <div className="absolute inset-0 bg-gradient-to-t from-black/50 via-transparent to-transparent" />
      <div className="absolute left-1.5 bottom-1 text-[9px] text-white/95 truncate">{preset.label}</div>
    </div>
  )
}

export function EffectsTab() {
  const {
    project,
    selectedClipId,
    previewTime,
    updateClip,
    addAdjustmentLayer,
  } = useStore()

  const [category, setCategory] = useState<string>('all')
  const [search, setSearch] = useState('')
  const [favorites, setFavorites] = useState<string[]>(() => readFavorites())
  const [filterIntensity, setFilterIntensity] = useState(100)
  const [applyMode, setApplyMode] = useState<ApplyMode>('clip')
  const [hoveredPreset, setHoveredPreset] = useState<string | null>(null)
  const [hoverTick, setHoverTick] = useState(0)

  useEffect(() => {
    if (!hoveredPreset) return
    const t = window.setInterval(() => setHoverTick((prev) => prev + 1), 320)
    return () => window.clearInterval(t)
  }, [hoveredPreset])

  const allClips = useMemo(() => {
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

  const clip = selectedClipId ? allClips.find((c) => c.id === selectedClipId) ?? null : null
  const canApplyDirect = !!clip && clip.clip_type !== 'audio' && clip.clip_type !== 'music'

  const categories = useMemo(
    () => ['all', 'favorites', ...Array.from(new Set(PRESETS.map((p) => p.category)))],
    [],
  )

  const filteredPresets = useMemo(() => {
    const q = search.trim().toLowerCase()
    return PRESETS.filter((p) => {
      if (category === 'favorites' && !favorites.includes(p.id)) return false
      if (category !== 'all' && category !== 'favorites' && p.category !== category) return false
      if (!q) return true
      return p.label.toLowerCase().includes(q)
    })
  }, [category, favorites, search])

  const buildPreviewSrc = (presetId: string): string => {
    if (!clip?.source_path) return ''
    if (clip.clip_type === 'image') {
      return `${API}/api/serve-file?path=${encodeURIComponent(clip.source_path)}`
    }
    if (clip.clip_type === 'video' || clip.clip_type === 'video_overlay') {
      const isHovered = hoveredPreset === presetId
      const localTick = isHovered ? (hoverTick % 9) : 0
      const clipDur = Math.max(0.2, (clip.end_s ?? 0) - (clip.start_s ?? 0))
      const samplePos = Math.min(0.92, Math.max(0.04, 0.06 + localTick * 0.1))
      const t = Math.max(0, clip.start_s + clipDur * samplePos)
      return `${API}/api/thumb?path=${encodeURIComponent(clip.source_path)}&t=${t.toFixed(2)}&w=280`
    }
    return ''
  }

  const toggleFavorite = (id: string) => {
    const next = favorites.includes(id) ? favorites.filter((f) => f !== id) : [...favorites, id]
    setFavorites(next)
    writeFavorites(next)
  }

  const applyDirectToClip = (preset: FilterPreset, targetClip: Clip) => {
    const patch = effectPatchFromPreset(preset, filterIntensity)
    const blend = (curr: number, target: number) => Math.round((curr + target) * 100) / 100
    updateClip(targetClip.id, {
      brightness: blend(targetClip.brightness ?? 0, patch.brightness ?? 0),
      contrast: blend(targetClip.contrast ?? 0, patch.contrast ?? 0),
      saturation: blend(targetClip.saturation ?? 0, patch.saturation ?? 0),
      temperature: blend(targetClip.temperature ?? 0, patch.temperature ?? 0),
      hue: blend(targetClip.hue ?? 0, patch.hue ?? 0),
      exposure: blend(targetClip.exposure ?? 0, patch.exposure ?? 0),
      sharpness: blend(targetClip.sharpness ?? 0, patch.sharpness ?? 0),
      vignette: blend(targetClip.vignette ?? 0, patch.vignette ?? 0),
      ...(patch.blur_type ? {
        blur_type: patch.blur_type,
        blur_intensity: Math.max(0, Math.min(100, patch.blur_intensity ?? targetClip.blur_intensity ?? 0)),
        blur_direction: patch.blur_direction ?? targetClip.blur_direction ?? 'both',
      } : {}),
    })
  }

  const applyAsLayer = (preset: FilterPreset) => {
    if (!project) return
    const start = canApplyDirect && clip ? clip.start_s : previewTime
    const end = canApplyDirect && clip ? clip.end_s : (previewTime + 4)
    const duration = Math.max(0.1, end - start)
    addAdjustmentLayer(duration)
    const st = useStore.getState()
    const adjId = st.selectedClipId
    if (!adjId) return
    updateClip(adjId, {
      label: `FX ${preset.label}`,
      ...effectPatchFromPreset(preset, filterIntensity),
    })
    if (start > 0 && Math.abs(start - previewTime) > 0.01) {
      updateClip(adjId, { start_s: start, end_s: start + duration })
    }
  }

  const apply = (preset: FilterPreset) => {
    if (!project) return
    if (applyMode === 'layer') {
      applyAsLayer(preset)
      return
    }
    if (!clip || !canApplyDirect) return
    applyDirectToClip(preset, clip)
  }

  const isApplied = (preset: FilterPreset) =>
    !!clip &&
    Math.round(clip.brightness ?? 0) === preset.brightness &&
    Math.round(clip.contrast ?? 0) === preset.contrast &&
    Math.round(clip.saturation ?? 0) === preset.saturation

  return (
    <div className="p-3 space-y-3 text-xs">
      {!clip && (
        <p className="text-[10px] text-text-dim text-center py-2">
          Selecione um clipe para aplicar no proprio video, ou use modo camada.
        </p>
      )}

      <div className="space-y-1">
        <p className="text-[10px] uppercase tracking-wider text-text-dim flex items-center gap-1.5">
          <Layers size={10} /> Aplicacao
        </p>
        <div className="grid grid-cols-2 gap-1.5">
          <button
            onClick={() => setApplyMode('clip')}
            className={`px-2 py-1.5 rounded text-[10px] transition-colors ${
              applyMode === 'clip'
                ? 'bg-accent text-white'
                : 'bg-bg-surface text-text-muted hover:text-white hover:bg-border'
            }`}
          >
            No clipe
          </button>
          <button
            onClick={() => setApplyMode('layer')}
            className={`px-2 py-1.5 rounded text-[10px] transition-colors ${
              applyMode === 'layer'
                ? 'bg-accent text-white'
                : 'bg-bg-surface text-text-muted hover:text-white hover:bg-border'
            }`}
          >
            Nova camada
          </button>
        </div>
      </div>

      <div className="relative">
        <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-text-dim" />
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Buscar efeitos"
          className="w-full pl-7 pr-2 py-1.5 rounded-md bg-bg-surface border border-border text-[11px] text-white placeholder:text-text-dim focus:outline-none focus:border-accent"
        />
      </div>

      <div className="space-y-2">
        <p className="text-[10px] uppercase tracking-wider text-text-dim flex items-center gap-1.5">
          <Filter size={10} /> Categorias
        </p>
        <div className="flex flex-wrap gap-1">
          {categories.map((c) => (
            <button
              key={c}
              onClick={() => setCategory(c)}
              className={`px-2 py-1 rounded text-[10px] transition-colors ${
                category === c
                  ? 'bg-accent text-white'
                  : 'bg-bg-surface text-text-muted hover:text-white hover:bg-border'
              }`}
            >
              {CATEGORY_LABELS[c] ?? c}
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-1">
        <div className="flex items-center justify-between text-[10px] text-text-muted">
          <span>Intensidade</span>
          <span className="tabular-nums text-accent">{filterIntensity}%</span>
        </div>
        <input
          type="range"
          min={0}
          max={200}
          value={filterIntensity}
          onChange={(e) => setFilterIntensity(Number(e.target.value))}
          className="w-full h-1 accent-accent cursor-pointer"
        />
      </div>

      {applyMode === 'clip' && !canApplyDirect && (
        <p className="text-[10px] text-amber-300">
          No modo "No clipe", selecione um video/imagem/overlay.
        </p>
      )}

      <div>
        <p className="text-[10px] uppercase tracking-wider text-text-dim mb-2 flex items-center gap-1.5">
          <Sparkles size={10} /> Efeitos
        </p>
        <div className="grid grid-cols-3 gap-2 max-h-[280px] overflow-y-auto pr-1">
          {filteredPresets.map((preset) => {
            const fav = favorites.includes(preset.id)
            const previewSrc = buildPreviewSrc(preset.id)
            const hovered = hoveredPreset === preset.id
            return (
              <div
                key={preset.id}
                className="relative group"
                onMouseEnter={() => setHoveredPreset(preset.id)}
                onMouseLeave={() => setHoveredPreset(null)}
              >
                <button
                  onClick={() => apply(preset)}
                  disabled={!project || (applyMode === 'clip' && !canApplyDirect)}
                  className={`w-full aspect-square rounded border overflow-hidden transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                    isApplied(preset)
                      ? 'border-accent'
                      : 'border-border hover:border-accent/60'
                  }`}
                  title={`${preset.label} (${CATEGORY_LABELS[preset.category]})`}
                >
                  <FilterPreviewCube preset={preset} previewSrc={previewSrc} hovered={hovered} />
                  <div className="absolute inset-x-0 bottom-0 bg-black/60 px-1 py-0.5">
                    <div className="flex items-center justify-center gap-1">
                      <Box size={10} className="text-white/90" />
                      <span className="text-[9px] text-white truncate">{preset.label}</span>
                    </div>
                  </div>
                </button>
                <button
                  onClick={() => toggleFavorite(preset.id)}
                  className="absolute top-1 right-1 h-5 w-5 rounded-full bg-black/65 text-white/85 hover:text-yellow-300 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                  title={fav ? 'Remover dos favoritos' : 'Favoritar efeito'}
                >
                  {fav ? <Star size={11} fill="currentColor" /> : <StarOff size={11} />}
                </button>
              </div>
            )
          })}
        </div>
      </div>

      {category === 'favorites' && filteredPresets.length === 0 && (
        <p className="text-[10px] text-text-dim text-center py-2">
          Nenhum efeito favoritado ainda.
        </p>
      )}
    </div>
  )
}
