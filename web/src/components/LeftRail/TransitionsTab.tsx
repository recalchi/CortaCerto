import { useEffect, useMemo, useState } from 'react'
import { ArrowLeftRight, Clock3, Search, Star, StarOff } from 'lucide-react'
import { Clip, useStore } from '../../store/useStore'

const TRANSITION_FAVORITES_KEY = 'cortacerto_transition_favorites_v1'

type TransitionCategory =
  | 'popular'
  | 'classic'
  | 'novo'
  | 'camera'
  | 'light'
  | 'blur'
  | 'glitch'
  | '3d'

type TransitionPreset = {
  id: string
  label: string
  category: TransitionCategory
  transition: string
  duration: number
  colorA: string
  colorB: string
}

const PRESETS: TransitionPreset[] = [
  { id: 'fade_soft',      label: 'Fade suave',        category: 'popular', transition: 'Fade',      duration: 0.35, colorA: '#111827', colorB: '#6366f1' },
  { id: 'dissolve_soft',  label: 'Dissolver clean',   category: 'popular', transition: 'Dissolver', duration: 0.40, colorA: '#0f172a', colorB: '#06b6d4' },
  { id: 'wipe_right',     label: 'Varrer direita',    category: 'classic', transition: 'Wipe Dir.', duration: 0.35, colorA: '#1e293b', colorB: '#0ea5e9' },
  { id: 'wipe_left',      label: 'Varrer esquerda',   category: 'classic', transition: 'Wipe Esq.', duration: 0.35, colorA: '#111827', colorB: '#14b8a6' },
  { id: 'zoom_quick',     label: 'Zoom rapido',       category: 'camera',  transition: 'Zoom',      duration: 0.30, colorA: '#1f2937', colorB: '#f97316' },
  { id: 'zoom_cine',      label: 'Zoom cinematico',   category: 'camera',  transition: 'Zoom',      duration: 0.55, colorA: '#0f172a', colorB: '#f43f5e' },
  { id: 'flash_glitch',   label: 'Flash glitch',      category: 'glitch',  transition: 'Dissolver', duration: 0.25, colorA: '#312e81', colorB: '#ec4899' },
  { id: 'blur_mix',       label: 'Blur mix',          category: 'blur',    transition: 'Dissolver', duration: 0.45, colorA: '#334155', colorB: '#93c5fd' },
  { id: 'light_sweep',    label: 'Sweep de luz',      category: 'light',   transition: 'Wipe Dir.', duration: 0.38, colorA: '#111827', colorB: '#f59e0b' },
  { id: 'light_bloom',    label: 'Bloom',             category: 'light',   transition: 'Fade',      duration: 0.50, colorA: '#1f2937', colorB: '#fde68a' },
  { id: 'new_hard_cut',   label: 'Corte dinamico',    category: 'novo',    transition: 'Corte',     duration: 0.10, colorA: '#0f172a', colorB: '#38bdf8' },
  { id: 'new_soft_xfade', label: 'Cross suave',       category: 'novo',    transition: 'Fade',      duration: 0.42, colorA: '#1e1b4b', colorB: '#22d3ee' },
  { id: 'threeD_flip',    label: '3D Flip',           category: '3d',      transition: 'Zoom',      duration: 0.40, colorA: '#111827', colorB: '#a855f7' },
  { id: 'threeD_depth',   label: '3D Depth',          category: '3d',      transition: 'Dissolver', duration: 0.46, colorA: '#172554', colorB: '#0891b2' },
]

const CATEGORY_LABELS: Record<string, string> = {
  all: 'Todas',
  favorites: 'Favoritos',
  popular: 'Populares',
  classic: 'Classico',
  novo: 'Novo',
  camera: 'Camera',
  light: 'Luz',
  blur: 'Desfocar',
  glitch: 'Glitch',
  '3d': '3D',
}

function readFavorites(): string[] {
  try {
    const raw = localStorage.getItem(TRANSITION_FAVORITES_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter((v) => typeof v === 'string') : []
  } catch {
    return []
  }
}

function writeFavorites(ids: string[]) {
  try {
    localStorage.setItem(TRANSITION_FAVORITES_KEY, JSON.stringify(ids.slice(0, 120)))
  } catch {
    // ignore
  }
}

function normalizeTransition(value?: string): string {
  const v = String(value || 'Corte').trim()
  return v || 'Corte'
}

function TransitionCubePreview({
  preset,
  phase,
  active,
}: {
  preset: TransitionPreset
  phase: boolean
  active: boolean
}) {
  const transition = normalizeTransition(preset.transition).toLowerCase()
  const showMotion = active && transition !== 'corte'

  const incomingOpacity = showMotion ? (phase ? 1 : 0.12) : 0.06
  const incomingScale = showMotion && transition.includes('zoom')
    ? (phase ? 1 : 1.12)
    : 1
  const incomingTranslateX = showMotion && transition.includes('wipe')
    ? (transition.includes('esq') ? (phase ? '0%' : '-100%') : (phase ? '0%' : '100%'))
    : '0%'
  const incomingBlur = showMotion && transition.includes('dissolver')
    ? (phase ? 0 : 8)
    : 0
  const flashOpacity = showMotion && transition.includes('fade') ? (phase ? 0.18 : 0) : 0

  return (
    <div className="relative w-full h-full overflow-hidden rounded-md bg-[#090b16]">
      <div
        className="absolute inset-0"
        style={{
          background: `linear-gradient(135deg, ${preset.colorA} 0%, ${preset.colorB} 100%)`,
          transform: `scale(${showMotion && transition.includes('zoom') && !phase ? 1.04 : 1})`,
          transition: 'transform 360ms ease, opacity 360ms ease',
          opacity: 0.95,
        }}
      />

      <div
        className="absolute inset-0"
        style={{
          background: `linear-gradient(135deg, ${preset.colorB} 0%, ${preset.colorA} 100%)`,
          opacity: incomingOpacity,
          transform: `translateX(${incomingTranslateX}) scale(${incomingScale})`,
          filter: `blur(${incomingBlur}px)`,
          transition: 'opacity 360ms ease, transform 360ms ease, filter 360ms ease',
        }}
      />

      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          opacity: flashOpacity,
          background: 'linear-gradient(90deg, transparent 10%, rgba(255,255,255,0.75) 50%, transparent 90%)',
          transition: 'opacity 260ms ease',
        }}
      />

      <div className="absolute left-1.5 bottom-1.5 text-[9px] leading-none text-white/90">
        {preset.label}
      </div>
    </div>
  )
}

export function TransitionsTab() {
  const { project, selectedClipId, selectedClipIds, updateClip } = useStore()
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState<string>('all')
  const [favorites, setFavorites] = useState<string[]>(() => readFavorites())
  const [hoveredPreset, setHoveredPreset] = useState<string | null>(null)
  const [hoverPhase, setHoverPhase] = useState(false)

  useEffect(() => {
    if (!hoveredPreset) return
    const t = window.setInterval(() => setHoverPhase((prev) => !prev), 430)
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

  const selectedClip = selectedClipId
    ? allClips.find((c) => c.id === selectedClipId) ?? null
    : null
  const canApplySelected = !!selectedClip && selectedClip.clip_type !== 'audio' && selectedClip.clip_type !== 'music'

  const categories = useMemo(
    () => ['all', 'favorites', ...Array.from(new Set(PRESETS.map((p) => p.category)))],
    [],
  )

  const filteredPresets = useMemo(() => {
    const q = search.trim().toLowerCase()
    return PRESETS.filter((preset) => {
      if (category === 'favorites' && !favorites.includes(preset.id)) return false
      if (category !== 'all' && category !== 'favorites' && preset.category !== category) return false
      if (!q) return true
      return preset.label.toLowerCase().includes(q) || preset.transition.toLowerCase().includes(q)
    })
  }, [category, favorites, search])

  const transitionAppliedClips = useMemo(() => {
    if (!project) return [] as Clip[]
    return allClips.filter((clip) => normalizeTransition(clip.transition) !== 'Corte')
  }, [allClips, project])

  const toggleFavorite = (id: string) => {
    const next = favorites.includes(id) ? favorites.filter((f) => f !== id) : [...favorites, id]
    setFavorites(next)
    writeFavorites(next)
  }

  const applyPreset = (preset: TransitionPreset) => {
    if (!canApplySelected || !selectedClip) return
    updateClip(selectedClip.id, {
      transition: preset.transition,
      transition_duration_s: preset.duration,
    })
  }

  const applyToSelection = () => {
    if (!project) return
    const ids = selectedClipIds.length > 0
      ? selectedClipIds
      : selectedClipId ? [selectedClipId] : []
    if (ids.length === 0) return
    const preset = PRESETS.find((p) => p.id === hoveredPreset) ?? PRESETS[0]
    for (const id of ids) {
      const clip = allClips.find((c) => c.id === id)
      if (!clip) continue
      if (clip.clip_type === 'audio' || clip.clip_type === 'music') continue
      updateClip(id, {
        transition: preset.transition,
        transition_duration_s: preset.duration,
      })
    }
  }

  const applyToAllVideo = () => {
    if (!project) return
    const preset = PRESETS.find((p) => p.id === hoveredPreset) ?? PRESETS[0]
    const targets = allClips.filter((clip) => (
      clip.clip_type === 'video'
      || clip.clip_type === 'video_overlay'
      || clip.clip_type === 'image'
    ))
    for (const clip of targets) {
      updateClip(clip.id, {
        transition: preset.transition,
        transition_duration_s: preset.duration,
      })
    }
  }

  const activeTransition = normalizeTransition(selectedClip?.transition)
  const activeDuration = Number(selectedClip?.transition_duration_s ?? 0.4)

  return (
    <div className="p-3 space-y-3 text-xs">
      {!selectedClip && (
        <p className="text-[10px] text-text-dim text-center py-2">
          Selecione um clipe para aplicar transicao.
        </p>
      )}

      <div className="relative">
        <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-text-dim" />
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Pesquisar transicoes"
          className="w-full pl-7 pr-2 py-1.5 rounded-md bg-bg-surface border border-border text-[11px] text-white placeholder:text-text-dim focus:outline-none focus:border-accent"
        />
      </div>

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

      <div>
        <p className="text-[10px] uppercase tracking-wider text-text-dim mb-2">
          Presets de transicao
        </p>
        <div className="grid grid-cols-3 gap-2 max-h-[250px] overflow-y-auto pr-1">
          {filteredPresets.map((preset) => {
            const isFav = favorites.includes(preset.id)
            const isApplied = activeTransition === normalizeTransition(preset.transition)
              && Math.abs(activeDuration - preset.duration) < 0.11
            const isHovered = hoveredPreset === preset.id
            return (
              <div
                key={preset.id}
                className={`relative rounded-md border ${isApplied ? 'border-accent' : 'border-border'} bg-bg-surface p-1`}
                onMouseEnter={() => setHoveredPreset(preset.id)}
                onMouseLeave={() => setHoveredPreset(null)}
              >
                <button
                  onClick={() => applyPreset(preset)}
                  disabled={!canApplySelected}
                  className="w-full aspect-square rounded-md overflow-hidden disabled:opacity-45 disabled:cursor-not-allowed"
                  title={`${preset.label} (${CATEGORY_LABELS[preset.category] ?? preset.category})`}
                >
                  <TransitionCubePreview preset={preset} phase={hoverPhase} active={isHovered} />
                </button>

                <button
                  onClick={() => toggleFavorite(preset.id)}
                  className="absolute top-1.5 right-1.5 h-5 w-5 rounded-full bg-black/60 text-white/90 hover:text-yellow-300 flex items-center justify-center"
                  title={isFav ? 'Remover dos favoritos' : 'Favoritar transicao'}
                >
                  {isFav ? <Star size={11} fill="currentColor" /> : <StarOff size={11} />}
                </button>

                <div className="mt-1 px-0.5">
                  <p className="text-[9px] text-white truncate leading-tight">{preset.label}</p>
                  <p className="text-[9px] text-text-dim truncate">{preset.duration.toFixed(2)}s · {preset.transition}</p>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {category === 'favorites' && filteredPresets.length === 0 && (
        <p className="text-[10px] text-text-dim text-center py-1">
          Nenhuma transicao favoritada.
        </p>
      )}

      <div className="grid grid-cols-2 gap-2">
        <button
          onClick={applyToSelection}
          disabled={!project || (!selectedClipId && selectedClipIds.length === 0)}
          className="px-2 py-2 rounded-lg border border-border bg-bg-surface hover:bg-bg-secondary disabled:opacity-45 disabled:cursor-not-allowed text-[11px] text-white transition-colors"
        >
          Aplicar na selecao
        </button>
        <button
          onClick={applyToAllVideo}
          disabled={!project}
          className="px-2 py-2 rounded-lg border border-border bg-bg-surface hover:bg-bg-secondary disabled:opacity-45 disabled:cursor-not-allowed text-[11px] text-white transition-colors"
        >
          Aplicar em todos
        </button>
      </div>

      {selectedClip && (
        <div className="rounded-lg border border-border bg-bg-surface px-2.5 py-2">
          <p className="text-[10px] text-text-dim mb-0.5">Clipe selecionado</p>
          <p className="text-white text-[11px] truncate">{selectedClip.label}</p>
          <p className="text-[10px] text-text-muted mt-0.5 flex items-center gap-1">
            <ArrowLeftRight size={10} />
            <span>{activeTransition}</span>
            <Clock3 size={10} className="ml-1" />
            <span>{activeDuration.toFixed(2)}s</span>
          </p>
          {!canApplySelected && (
            <p className="text-[10px] text-amber-300 mt-1">
              Transicoes sao aplicaveis em video, imagem e overlay.
            </p>
          )}
        </div>
      )}

      <div className="rounded-lg border border-border bg-bg-surface px-2.5 py-2">
        <p className="text-[10px] text-text-dim mb-1">Aplicadas na timeline</p>
        <p className="text-[11px] text-white">{transitionAppliedClips.length} clipe(s) com transicao</p>
      </div>
    </div>
  )
}
