import { useEffect, useMemo, useState } from 'react'
import { Loader2, Plus, Search, Sparkles, Star, StarOff, Trash2, Wand2 } from 'lucide-react'
import { api } from '../../api/client'
import { useStore } from '../../store/useStore'
import type { Clip } from '../../store/useStore'

type CaptionProvider = 'auto' | 'openai-api' | 'faster-whisper' | 'whisper'
type CaptionLanguage = 'auto' | 'pt' | 'en' | 'es'

type TextModelCategory = 'popular' | 'classic' | 'novo' | 'meme' | 'gaming' | 'minimal'
type TextEffectCategory = 'anim' | 'glow' | 'stroke' | 'social' | 'cinematic'

type TextModel = {
  id: string
  name: string
  sample: string
  category: TextModelCategory
  style: Partial<Clip>
}

type TextEffect = {
  id: string
  name: string
  sample: string
  category: TextEffectCategory
  patch: Partial<Clip>
}

type CaptionApplyScope = 'selected' | 'all'

const MODEL_FAVORITES_KEY = 'cc_text_model_favorites_v2'
const EFFECT_FAVORITES_KEY = 'cc_text_effect_favorites_v1'

const MODEL_CATEGORIES: Array<{ id: string; label: string }> = [
  { id: 'all', label: 'Todos' },
  { id: 'favorites', label: 'Favoritos' },
  { id: 'popular', label: 'Populares' },
  { id: 'classic', label: 'Classico' },
  { id: 'novo', label: 'Novo' },
  { id: 'meme', label: 'Meme' },
  { id: 'gaming', label: 'Gaming' },
  { id: 'minimal', label: 'Minimal' },
]

const EFFECT_CATEGORIES: Array<{ id: string; label: string }> = [
  { id: 'all', label: 'Todos' },
  { id: 'favorites', label: 'Favoritos' },
  { id: 'anim', label: 'Animacao' },
  { id: 'glow', label: 'Glow' },
  { id: 'stroke', label: 'Stroke' },
  { id: 'social', label: 'Social' },
  { id: 'cinematic', label: 'Cinema' },
]

const TEXT_MODELS: TextModel[] = [
  { id: 'm-pop-01', name: 'Impacto', sample: 'SUPER!', category: 'popular', style: { text_font: 'Impact', text_color: '#f8fafc', text_bold: true, text_size_pct: 145, text_shadow_enabled: true, text_stroke_enabled: true, text_stroke_color: '#0f172a', text_stroke_width: 3, text_position_y_pct: 72 } },
  { id: 'm-pop-02', name: 'Highlight', sample: '+500', category: 'popular', style: { text_font: 'Arial', text_color: '#111827', text_bold: true, text_size_pct: 130, text_background_enabled: true, text_background_color: '#86efac', text_background_alpha: 1, text_position_y_pct: 74 } },
  { id: 'm-pop-03', name: 'Trend', sample: 'ALPHA', category: 'popular', style: { text_font: 'Verdana', text_color: '#fb7185', text_bold: true, text_size_pct: 135, text_shadow_enabled: true, text_background_enabled: false, text_position_y_pct: 70 } },
  { id: 'm-class-01', name: 'Cinema', sample: 'THE MOMENT', category: 'classic', style: { text_font: 'Times New Roman', text_color: '#f8fafc', text_bold: false, text_size_pct: 110, text_background_enabled: true, text_background_color: '#111827', text_background_alpha: 0.85, text_position_y_pct: 82 } },
  { id: 'm-class-02', name: 'Clean', sample: '2026', category: 'classic', style: { text_font: 'Georgia', text_color: '#e2e8f0', text_bold: false, text_size_pct: 124, text_background_enabled: false, text_shadow_enabled: false, text_position_y_pct: 76 } },
  { id: 'm-class-03', name: 'Doc', sample: 'CAP 01', category: 'classic', style: { text_font: 'Helvetica', text_color: '#ffffff', text_bold: true, text_size_pct: 105, text_background_enabled: true, text_background_color: '#000000', text_background_alpha: 0.55, text_position_y_pct: 84 } },
  { id: 'm-novo-01', name: 'Pixel', sample: 'LEVEL UP', category: 'novo', style: { text_font: 'Courier New', text_color: '#22d3ee', text_bold: true, text_size_pct: 120, text_stroke_enabled: true, text_stroke_color: '#0b1020', text_stroke_width: 2, text_position_y_pct: 71 } },
  { id: 'm-novo-02', name: 'Cyber', sample: 'NEON', category: 'novo', style: { text_font: 'Trebuchet MS', text_color: '#c084fc', text_bold: true, text_size_pct: 132, text_shadow_enabled: true, text_background_enabled: false, text_position_y_pct: 70 } },
  { id: 'm-novo-03', name: 'Minimal Beat', sample: 'SLOW VIBE', category: 'novo', style: { text_font: 'Helvetica', text_color: '#f1f5f9', text_bold: false, text_size_pct: 104, text_background_enabled: true, text_background_color: '#1e293b', text_background_alpha: 0.66, text_position_y_pct: 83 } },
  { id: 'm-meme-01', name: 'Meme Box', sample: 'CLIC HERE', category: 'meme', style: { text_font: 'Impact', text_color: '#fde68a', text_bold: true, text_size_pct: 138, text_background_enabled: true, text_background_color: '#0f172a', text_background_alpha: 0.82, text_position_y_pct: 74 } },
  { id: 'm-meme-02', name: 'Rage', sample: 'HEHEHE', category: 'meme', style: { text_font: 'Verdana', text_color: '#fef08a', text_bold: true, text_size_pct: 122, text_stroke_enabled: true, text_stroke_color: '#000000', text_stroke_width: 2, text_position_y_pct: 76 } },
  { id: 'm-game-01', name: 'Gaming HUD', sample: 'HEADSHOT', category: 'gaming', style: { text_font: 'Impact', text_color: '#93c5fd', text_bold: true, text_size_pct: 128, text_background_enabled: true, text_background_color: '#082f49', text_background_alpha: 0.85, text_stroke_enabled: true, text_stroke_color: '#0f172a', text_stroke_width: 2, text_position_y_pct: 70 } },
  { id: 'm-game-02', name: 'Rank', sample: 'DIAMANTE', category: 'gaming', style: { text_font: 'Verdana', text_color: '#e9d5ff', text_bold: true, text_size_pct: 116, text_shadow_enabled: true, text_position_y_pct: 76 } },
  { id: 'm-min-01', name: 'Caption Soft', sample: 'voce faz meu coracao feliz', category: 'minimal', style: { text_font: 'Sistema', text_color: '#ffffff', text_bold: false, text_size_pct: 98, text_background_enabled: true, text_background_color: '#000000', text_background_alpha: 0.55, text_position_y_pct: 83 } },
  { id: 'm-min-02', name: 'Thin Light', sample: 'one scene', category: 'minimal', style: { text_font: 'Helvetica', text_color: '#e2e8f0', text_bold: false, text_size_pct: 92, text_background_enabled: false, text_shadow_enabled: false, text_position_y_pct: 80 } },
]

const TEXT_EFFECTS: TextEffect[] = [
  { id: 'e-anim-fade', name: 'Fade In/Out', sample: 'FADE', category: 'anim', patch: { animation_in: 'fade', animation_out: 'fade', animation_in_duration_s: 0.35, animation_out_duration_s: 0.35 } },
  { id: 'e-anim-slide', name: 'Slide Up', sample: 'SLIDE', category: 'anim', patch: { animation_in: 'slide-up', animation_out: 'fade', animation_in_duration_s: 0.35, animation_out_duration_s: 0.25 } },
  { id: 'e-anim-zoom', name: 'Zoom Pop', sample: 'ZOOM', category: 'anim', patch: { animation_in: 'zoom-in', animation_out: 'zoom-out', animation_in_duration_s: 0.28, animation_out_duration_s: 0.28 } },
  { id: 'e-glow-neon', name: 'Neon Glow', sample: 'NEON', category: 'glow', patch: { text_color: '#22d3ee', text_shadow_enabled: true, text_background_enabled: false, text_stroke_enabled: false } },
  { id: 'e-glow-pink', name: 'Pink Glow', sample: 'PINK', category: 'glow', patch: { text_color: '#f472b6', text_shadow_enabled: true, text_background_enabled: false } },
  { id: 'e-stroke-bold', name: 'Bold Stroke', sample: 'STROKE', category: 'stroke', patch: { text_bold: true, text_stroke_enabled: true, text_stroke_color: '#000000', text_stroke_width: 3, text_shadow_enabled: false } },
  { id: 'e-stroke-white', name: 'White Outline', sample: 'OUTLINE', category: 'stroke', patch: { text_color: '#0f172a', text_stroke_enabled: true, text_stroke_color: '#ffffff', text_stroke_width: 3, text_background_enabled: false } },
  { id: 'e-social-tag', name: 'Tag Social', sample: '#viral', category: 'social', patch: { text_background_enabled: true, text_background_color: '#7c3aed', text_background_alpha: 0.9, text_color: '#ffffff', text_bold: true } },
  { id: 'e-social-money', name: 'Money Pop', sample: '+500', category: 'social', patch: { text_color: '#22c55e', text_bold: true, text_stroke_enabled: true, text_stroke_color: '#0a0a0a', text_stroke_width: 2 } },
  { id: 'e-cine-lower', name: 'Lower Third', sample: 'ONE SCENE', category: 'cinematic', patch: { text_font: 'Times New Roman', text_color: '#f8fafc', text_background_enabled: true, text_background_color: '#111827', text_background_alpha: 0.85, text_position_y_pct: 86 } },
]

const DEFAULT_CAPTION_STYLE: Partial<Clip> = {
  text_font: 'Arial',
  text_color: '#ffffff',
  text_bold: false,
  text_italic: false,
  text_shadow_enabled: false,
  text_stroke_enabled: false,
  text_background_enabled: false,
  text_size_pct: 88,
  text_position_y_pct: 84,
  text_side_margin_pct: 5,
  text_align: 'center',
}

function wrapWordsToLines(text: string, maxChars: number): string[] {
  const limit = Math.max(16, Math.min(60, Math.floor(maxChars || 42)))
  const words = text.split(' ').filter(Boolean)
  const lines: string[] = []
  let line = ''
  for (const word of words) {
    if (word.length > limit) {
      if (line) {
        lines.push(line)
        line = ''
      }
      for (let i = 0; i < word.length; i += limit) {
        lines.push(word.slice(i, i + limit))
      }
      continue
    }
    const next = line ? `${line} ${word}` : word
    if (next.length <= limit) {
      line = next
      continue
    }
    if (line) lines.push(line)
    line = word
  }
  if (line) lines.push(line)
  return lines
}

function buildCaptionBlocks(text: string, maxCharsPerLine: number, maxLinesPerBlock = 2): string[] {
  const clean = text.trim().replace(/\s+/g, ' ')
  if (!clean) return []

  const phrases = clean.split(/(?<=[.!?;:])\s+/).filter(Boolean)
  const allLines: string[] = []
  for (const phrase of phrases) {
    const lines = wrapWordsToLines(phrase, maxCharsPerLine)
    allLines.push(...lines)
  }
  if (allLines.length === 0) return []

  const maxLines = Math.max(1, Math.min(3, Math.floor(maxLinesPerBlock || 2)))
  const blocks: string[] = []
  for (let i = 0; i < allLines.length; i += maxLines) {
    blocks.push(allLines.slice(i, i + maxLines).join('\n'))
  }
  return blocks
}

function readFavoriteIds(key: string): string[] {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter((v) => typeof v === 'string')
  } catch {
    return []
  }
}

function writeFavoriteIds(key: string, ids: string[]) {
  try {
    localStorage.setItem(key, JSON.stringify(ids.slice(0, 200)))
  } catch {
    // ignore
  }
}

export function TextTab() {
  const {
    project,
    previewTime,
    selectedClipId,
    addTextClip,
    applyTextStyle,
    clearTextClips,
    deleteClip,
    updateClip,
    setCaptionPreview,
  } = useStore()

  const [captionLoading, setCaptionLoading] = useState(false)
  const [captionError, setCaptionError] = useState<string | null>(null)
  const [captionProgressText, setCaptionProgressText] = useState('')
  const [captionStartedAt, setCaptionStartedAt] = useState<number | null>(null)
  const [captionElapsedS, setCaptionElapsedS] = useState(0)
  const [captionStatusLoading, setCaptionStatusLoading] = useState(true)
  const [captionStatusError, setCaptionStatusError] = useState<string | null>(null)
  const [providersAvailable, setProvidersAvailable] = useState<string[]>([])
  const [openaiConfigured, setOpenaiConfigured] = useState(false)
  const [provider, setProvider] = useState<CaptionProvider>('auto')
  const [language, setLanguage] = useState<CaptionLanguage>('auto')
  const [replaceAll, setReplaceAll] = useState(false)
  const [captionStyle, setCaptionStyle] = useState<Partial<Clip>>(DEFAULT_CAPTION_STYLE)
  const [captionMaxChars, setCaptionMaxChars] = useState(42)
  const [captionApplyScope, setCaptionApplyScope] = useState<CaptionApplyScope>('selected')
  const [captionPreviewText, setCaptionPreviewText] = useState('Exemplo de legenda automatica com mais palavras para testar a quebra de linha e a margem lateral.')
  const [captionConfigActive, setCaptionConfigActive] = useState(false)

  const [modelSearch, setModelSearch] = useState('')
  const [modelCategory, setModelCategory] = useState<string>('all')
  const [modelFavorites, setModelFavorites] = useState<string[]>(() => readFavoriteIds(MODEL_FAVORITES_KEY))
  const [activeModelId, setActiveModelId] = useState<string>(TEXT_MODELS[0].id)

  const [effectSearch, setEffectSearch] = useState('')
  const [effectCategory, setEffectCategory] = useState<string>('all')
  const [effectFavorites, setEffectFavorites] = useState<string[]>(() => readFavoriteIds(EFFECT_FAVORITES_KEY))

  useEffect(() => {
    if (!captionLoading || !captionStartedAt) {
      setCaptionElapsedS(0)
      return
    }
    const tick = () => setCaptionElapsedS(Math.max(0, Math.floor((Date.now() - captionStartedAt) / 1000)))
    tick()
    const timer = window.setInterval(tick, 1000)
    return () => window.clearInterval(timer)
  }, [captionLoading, captionStartedAt])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setCaptionStatusLoading(true)
      try {
        const res = await api.get('/api/transcribe-status')
        const providers = Array.isArray(res.data?.providers)
          ? (res.data.providers as string[])
          : []
        if (!cancelled) {
          setProvidersAvailable(providers)
          setOpenaiConfigured(Boolean(res.data?.openai_configured))
          setCaptionStatusError(null)
        }
      } catch {
        if (!cancelled) {
          setProvidersAvailable([])
          setOpenaiConfigured(false)
          setCaptionStatusError('Nao foi possivel verificar o provider de transcricao.')
        }
      } finally {
        if (!cancelled) setCaptionStatusLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [])

  const activeModel = useMemo(
    () => TEXT_MODELS.find((m) => m.id === activeModelId) ?? TEXT_MODELS[0],
    [activeModelId],
  )

  const selectedTextClip = useMemo(() => {
    if (!project || !selectedClipId) return null
    const clip = project.text_track.clips.find((c) => c.id === selectedClipId) ?? null
    if (!clip) return null
    return clip.clip_type === 'text' ? clip : null
  }, [project, selectedClipId])

  const selectedTimelineClip = useMemo(() => {
    if (!project || !selectedClipId) return null
    const all = [
      ...project.video_track.clips,
      ...project.audio_track.clips,
      ...project.text_track.clips,
      ...project.overlay_track.clips,
      ...(project.extra_video_tracks ?? []).flatMap((t) => t.clips),
      ...(project.extra_audio_tracks ?? []).flatMap((t) => t.clips),
      ...(project.extra_overlay_tracks ?? []).flatMap((t) => t.clips),
    ]
    return all.find((clip) => clip.id === selectedClipId) ?? null
  }, [project, selectedClipId])

  const visibleModels = useMemo(() => {
    const q = modelSearch.trim().toLowerCase()
    return TEXT_MODELS.filter((model) => {
      if (modelCategory === 'favorites' && !modelFavorites.includes(model.id)) return false
      if (modelCategory !== 'all' && modelCategory !== 'favorites' && model.category !== modelCategory) return false
      if (!q) return true
      return model.name.toLowerCase().includes(q) || model.sample.toLowerCase().includes(q)
    })
  }, [modelCategory, modelFavorites, modelSearch])

  const visibleEffects = useMemo(() => {
    const q = effectSearch.trim().toLowerCase()
    return TEXT_EFFECTS.filter((effect) => {
      if (effectCategory === 'favorites' && !effectFavorites.includes(effect.id)) return false
      if (effectCategory !== 'all' && effectCategory !== 'favorites' && effect.category !== effectCategory) return false
      if (!q) return true
      return effect.name.toLowerCase().includes(q) || effect.sample.toLowerCase().includes(q)
    })
  }, [effectCategory, effectFavorites, effectSearch])

  const toggleModelFavorite = (id: string) => {
    setModelFavorites((prev) => {
      const next = prev.includes(id) ? prev.filter((v) => v !== id) : [...prev, id]
      writeFavoriteIds(MODEL_FAVORITES_KEY, next)
      return next
    })
  }

  const toggleEffectFavorite = (id: string) => {
    setEffectFavorites((prev) => {
      const next = prev.includes(id) ? prev.filter((v) => v !== id) : [...prev, id]
      writeFavoriteIds(EFFECT_FAVORITES_KEY, next)
      return next
    })
  }

  const createTextFromModel = (model: TextModel, textOverride?: string, stylePatch?: Partial<Clip>) => {
    if (!project) return
    const startS = previewTime
    const endS = Math.min(startS + 3, project.duration_s)
    addTextClip(
      startS,
      endS,
      textOverride || model.sample || 'Novo texto',
      { ...model.style, ...(stylePatch ?? {}) },
    )
  }

  const createTextNow = () => createTextFromModel(activeModel, 'Novo texto')

  const applyModelToSelected = () => {
    if (!selectedTextClip) return
    applyTextStyle(activeModel.style, [selectedTextClip.id])
  }

  const applyModelToAll = () => {
    if (!project || project.text_track.clips.length === 0) return
    applyTextStyle(activeModel.style)
  }

  const applyCaptionStyle = () => {
    if (!project || project.text_track.clips.length === 0) return
    const styleOnly: Partial<Clip> = {
      text_font: captionStyle.text_font,
      text_color: captionStyle.text_color,
      text_bold: captionStyle.text_bold,
      text_italic: captionStyle.text_italic,
      text_underline: captionStyle.text_underline,
      text_align: captionStyle.text_align,
      text_size_pct: captionStyle.text_size_pct,
      text_position_y_pct: captionStyle.text_position_y_pct,
      text_background_enabled: captionStyle.text_background_enabled,
      text_background_color: captionStyle.text_background_color,
      text_background_alpha: captionStyle.text_background_alpha,
      text_side_margin_pct: captionStyle.text_side_margin_pct,
      text_stroke_enabled: captionStyle.text_stroke_enabled,
      text_stroke_color: captionStyle.text_stroke_color,
      text_stroke_width: captionStyle.text_stroke_width,
      text_shadow_enabled: captionStyle.text_shadow_enabled,
    }
    if (captionApplyScope === 'all') {
      applyTextStyle(styleOnly)
      return
    }
    if (!selectedTextClip) return
    applyTextStyle(styleOnly, [selectedTextClip.id])
  }

  const applyEffectToSelected = (effect: TextEffect) => {
    if (!selectedTextClip) return
    updateClip(selectedTextClip.id, effect.patch)
  }

  const handleAutoCaption = async () => {
    if (!project?.videoPath || captionLoading) return
    setCaptionLoading(true)
    setCaptionStartedAt(Date.now())
    setCaptionError(null)
    setCaptionProgressText('Verificando provider de transcricao...')
    setCaptionStatusLoading(true)
    try {
      try {
        const statusRes = await api.get('/api/transcribe-status')
        const canTranscribe = Boolean(statusRes.data?.can_transcribe)
        const providers = Array.isArray(statusRes.data?.providers)
          ? (statusRes.data.providers as string[])
          : []
        setProvidersAvailable(providers)
        setOpenaiConfigured(Boolean(statusRes.data?.openai_configured))
        setCaptionStatusError(null)
        if (!canTranscribe) {
          const message = 'Transcricao indisponivel. Configure OPENAI_API_KEY ou instale faster-whisper.'
          setCaptionStatusError(message)
          setCaptionError(message)
          setCaptionProgressText('')
          return
        }
      } catch (e: any) {
        const detail = e?.response?.data?.detail ?? e?.message ?? 'Nao foi possivel verificar a transcricao.'
        setCaptionStatusError(String(detail))
        // keep going; backend will return a clearer error if unavailable
      } finally {
        setCaptionStatusLoading(false)
      }

      if (replaceAll && project.text_track.clips.length > 0) {
        setCaptionProgressText('Limpando legendas antigas...')
        clearTextClips()
      }
      const targetPath = selectedTimelineClip?.source_path || project.videoPath
      const payload: { path: string; provider: CaptionProvider; language?: string } = {
        path: targetPath,
        provider,
      }
      if (language !== 'auto') payload.language = language
      setCaptionProgressText('Transcrevendo audio do clipe...')
      const res = await api.post('/api/transcribe', payload)
      const segments = (res.data?.segments ?? []) as Array<{ start_s: number; end_s: number; text: string }>
      if (segments.length === 0) {
        setCaptionError('Nenhuma fala detectada.')
        setCaptionProgressText('')
        return
      }
      setCaptionProgressText('Organizando quebras de linha...')
      const timelineOffset =
        selectedTimelineClip && selectedTimelineClip.source_path === targetPath
          ? Number(selectedTimelineClip.source_offset_s ?? (targetPath !== project.videoPath ? selectedTimelineClip.start_s : 0))
          : 0
      const hasSelectionWindow =
        Boolean(selectedTimelineClip) &&
        selectedTimelineClip?.source_path === targetPath &&
        Number.isFinite(selectedTimelineClip?.start_s) &&
        Number.isFinite(selectedTimelineClip?.end_s)
      const winStart = hasSelectionWindow ? Number(selectedTimelineClip!.start_s) : -Infinity
      const winEnd = hasSelectionWindow ? Number(selectedTimelineClip!.end_s) : Infinity
      const sideMarginPct = Math.max(0, Math.min(25, Number(captionStyle.text_side_margin_pct ?? 5)))
      const usableWidthFactor = Math.max(0.35, (100 - sideMarginPct * 2) / 100)
      const effectiveMaxChars = Math.max(16, Math.min(90, Math.round(captionMaxChars * usableWidthFactor)))

      let inserted = 0
      setCaptionProgressText(`Aplicando ${segments.length} segmentos na timeline...`)
      for (const seg of segments) {
        const text = seg.text?.trim()
        if (!text) continue
        const rawStart = Math.max(0, Number(seg.start_s ?? 0))
        const rawEnd = Math.max(rawStart + 0.1, Number(seg.end_s ?? rawStart + 0.1))
        const timelineStart = rawStart + timelineOffset
        const timelineEnd = rawEnd + timelineOffset
        if (timelineEnd <= winStart || timelineStart >= winEnd) continue
        const startS = Math.max(0, Math.max(timelineStart, winStart))
        const endS = Math.max(startS + 0.1, Math.min(timelineEnd, winEnd))
        const parts = buildCaptionBlocks(text, effectiveMaxChars, 2)
        const localParts = parts.length > 0 ? parts : [text]
        const totalDur = Math.max(0.1, endS - startS)
        const weights = localParts.map((part) => Math.max(8, part.replace(/\s+/g, '').length))
        const weightSum = Math.max(1, weights.reduce((acc, v) => acc + v, 0))
        const minDur = totalDur >= localParts.length * 0.35
          ? 0.35
          : Math.max(0.1, (totalDur / localParts.length) * 0.6)
        let cursor = startS
        localParts.forEach((part, index) => {
          const remaining = localParts.length - index
          const remainMin = Math.max(0, (remaining - 1) * minDur)
          let dur = index === localParts.length - 1
            ? endS - cursor
            : (totalDur * (weights[index] / weightSum))
          dur = Math.max(minDur, dur)
          dur = Math.min(dur, Math.max(0.1, endS - cursor - remainMin))
          const s = cursor
          const e = index === localParts.length - 1 ? endS : Math.max(s + 0.1, s + dur)
          addTextClip(s, e, part, captionStyle)
          cursor = e
          inserted += 1
        })
      }
      if (inserted === 0) {
        setCaptionError('Nenhuma fala detectada na faixa selecionada.')
      } else {
        setCaptionProgressText(`Legendagem concluida: ${inserted} blocos.`)
      }
    } catch (e: any) {
      const detail = e?.response?.data?.detail ?? e?.message ?? 'Falha ao gerar legendas'
      if (typeof detail === 'string') {
        const compact = detail.split('\n').map((line: string) => line.trim()).filter(Boolean).slice(0, 3).join(' | ')
        setCaptionError(compact || 'Falha ao gerar legendas')
      } else {
        setCaptionError('Falha ao gerar legendas')
      }
    } finally {
      setCaptionLoading(false)
      setCaptionStartedAt(null)
      setTimeout(() => setCaptionProgressText(''), 3500)
    }
  }

  const deleteCurrentCaption = () => {
    if (!selectedTextClip) return
    deleteClip(selectedTextClip.id)
  }

  const deleteAllCaptions = () => {
    if (!project || project.text_track.clips.length === 0) return
    const ok = window.confirm('Excluir todas as legendas desta timeline?')
    if (!ok) return
    clearTextClips()
  }

  const canUseProvider = (id: CaptionProvider) => {
    if (id === 'auto') return true
    if (id === 'openai-api') return openaiConfigured || providersAvailable.includes('openai-api')
    return providersAvailable.includes(id)
  }
  const canTranscribeNow = providersAvailable.length > 0 || openaiConfigured
  const captionButtonDisabled = captionLoading || !project?.videoPath
  const captionStatusText = captionStatusLoading
    ? 'Verificando transcricao...'
    : canTranscribeNow
      ? `Transcricao pronta: ${providersAvailable.length > 0 ? providersAvailable.join(', ') : 'OpenAI configurado'}`
      : 'Clique para revalidar. Se continuar indisponivel, configure OPENAI_API_KEY ou instale faster-whisper.'

  useEffect(() => {
    if (!project || !captionConfigActive) {
      setCaptionPreview(false, undefined, null)
      return
    }
    setCaptionPreview(true, captionPreviewText, captionStyle)
    return () => {
      setCaptionPreview(false, undefined, null)
    }
  }, [captionConfigActive, captionPreviewText, captionStyle, project, setCaptionPreview])

  return (
    <div className="p-3 space-y-3 text-xs">
      {!project && (
        <p className="text-[10px] text-text-dim text-center py-4">
          Abra um projeto para usar textos.
        </p>
      )}

      {project && (
        <>
          <button
            onClick={createTextNow}
            className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-accent hover:bg-accent-hover text-white text-xs font-medium rounded-lg transition-colors"
          >
            <Plus size={13} /> Adicionar texto
          </button>

          <div className="space-y-2">
            <p className="text-[10px] text-text-dim uppercase tracking-wider">Modelos de texto</p>

            <div className="relative">
              <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-text-dim" />
              <input
                value={modelSearch}
                onChange={(e) => setModelSearch(e.target.value)}
                placeholder="Pesquisar modelos de texto"
                className="w-full pl-7 pr-2 py-1.5 rounded-md bg-bg-surface border border-border text-[11px] text-white placeholder:text-text-dim focus:outline-none focus:border-accent"
              />
            </div>

            <div className="flex flex-wrap gap-1">
              {MODEL_CATEGORIES.map((cat) => (
                <button
                  key={cat.id}
                  onClick={() => setModelCategory(cat.id)}
                  className={`px-2 py-1 rounded text-[10px] transition-colors ${
                    modelCategory === cat.id
                      ? 'bg-accent text-white'
                      : 'bg-bg-surface text-text-muted hover:text-white hover:bg-border'
                  }`}
                >
                  {cat.label}
                </button>
              ))}
            </div>

            <div className="grid grid-cols-3 gap-2 max-h-[240px] overflow-y-auto pr-1">
              {visibleModels.map((model) => {
                const isFav = modelFavorites.includes(model.id)
                const isActive = activeModel.id === model.id
                const bgEnabled = model.style.text_background_enabled
                return (
                  <div
                    key={model.id}
                    className={`relative group rounded border ${isActive ? 'border-accent' : 'border-border'} bg-bg-surface`}
                  >
                    <button
                      onClick={() => {
                        setActiveModelId(model.id)
                        createTextFromModel(model)
                      }}
                      className="w-full aspect-square p-2"
                      title={`Inserir modelo ${model.name}`}
                    >
                      <div
                        className="w-full h-full rounded flex items-center justify-center text-center leading-tight px-1"
                        style={{
                          background: 'linear-gradient(145deg, #1f2937 0%, #0f172a 100%)',
                        }}
                      >
                        <span
                          style={{
                            fontSize: 14,
                            fontWeight: model.style.text_bold ? 700 : 500,
                            color: model.style.text_color ?? '#ffffff',
                            background: bgEnabled ? model.style.text_background_color ?? '#000000' : 'transparent',
                            opacity: bgEnabled ? Number(model.style.text_background_alpha ?? 1) : 1,
                            padding: bgEnabled ? '2px 6px' : '0px',
                            borderRadius: bgEnabled ? 4 : 0,
                            textShadow: model.style.text_shadow_enabled ? '1px 1px 3px rgba(0,0,0,0.85)' : undefined,
                            WebkitTextStroke: model.style.text_stroke_enabled
                              ? `${Number(model.style.text_stroke_width ?? 2)}px ${model.style.text_stroke_color ?? '#000000'}`
                              : undefined,
                          }}
                        >
                          {model.sample}
                        </span>
                      </div>
                    </button>

                    <button
                      onClick={() => toggleModelFavorite(model.id)}
                      className="absolute top-1 right-1 h-5 w-5 rounded-full bg-black/65 text-white/85 hover:text-yellow-300 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                      title={isFav ? 'Remover dos favoritos' : 'Favoritar modelo'}
                    >
                      {isFav ? <Star size={11} fill="currentColor" /> : <StarOff size={11} />}
                    </button>
                  </div>
                )
              })}
            </div>
          </div>

          <div className="space-y-2">
            <p className="text-[10px] text-text-dim uppercase tracking-wider flex items-center gap-1">
              <Sparkles size={11} /> Efeitos de texto
            </p>

            <div className="relative">
              <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-text-dim" />
              <input
                value={effectSearch}
                onChange={(e) => setEffectSearch(e.target.value)}
                placeholder="Pesquisar efeitos de texto"
                className="w-full pl-7 pr-2 py-1.5 rounded-md bg-bg-surface border border-border text-[11px] text-white placeholder:text-text-dim focus:outline-none focus:border-accent"
              />
            </div>

            <div className="flex flex-wrap gap-1">
              {EFFECT_CATEGORIES.map((cat) => (
                <button
                  key={cat.id}
                  onClick={() => setEffectCategory(cat.id)}
                  className={`px-2 py-1 rounded text-[10px] transition-colors ${
                    effectCategory === cat.id
                      ? 'bg-accent text-white'
                      : 'bg-bg-surface text-text-muted hover:text-white hover:bg-border'
                  }`}
                >
                  {cat.label}
                </button>
              ))}
            </div>

            <div className="grid grid-cols-3 gap-2 max-h-[200px] overflow-y-auto pr-1">
              {visibleEffects.map((effect) => {
                const isFav = effectFavorites.includes(effect.id)
                return (
                  <div key={effect.id} className="relative group rounded border border-border bg-bg-surface">
                    <button
                      onClick={() => {
                        if (selectedTextClip) applyEffectToSelected(effect)
                        else createTextFromModel(activeModel, effect.sample, effect.patch)
                      }}
                      className="w-full aspect-square p-2"
                      title={`Aplicar efeito ${effect.name}`}
                    >
                      <div className="w-full h-full rounded bg-gradient-to-br from-slate-800 to-slate-950 flex items-center justify-center px-1">
                        <span className="text-[14px] font-semibold text-white text-center leading-tight">
                          {effect.sample}
                        </span>
                      </div>
                    </button>
                    <button
                      onClick={() => toggleEffectFavorite(effect.id)}
                      className="absolute top-1 right-1 h-5 w-5 rounded-full bg-black/65 text-white/85 hover:text-yellow-300 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                      title={isFav ? 'Remover dos favoritos' : 'Favoritar efeito'}
                    >
                      {isFav ? <Star size={11} fill="currentColor" /> : <StarOff size={11} />}
                    </button>
                  </div>
                )
              })}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <button
              onClick={applyModelToSelected}
              disabled={!selectedTextClip}
              className="px-2 py-2 rounded-lg border border-border bg-bg-surface hover:bg-bg-secondary disabled:opacity-50 text-[11px] text-white transition-colors"
            >
              Aplicar modelo no atual
            </button>
            <button
              onClick={applyModelToAll}
              disabled={project.text_track.clips.length === 0}
              className="px-2 py-2 rounded-lg border border-border bg-bg-surface hover:bg-bg-secondary disabled:opacity-50 text-[11px] text-white transition-colors"
            >
              Aplicar modelo em todas
            </button>
          </div>

          <div className="rounded-lg border border-border bg-bg-surface p-2 space-y-2">
            <p className="text-[10px] text-text-dim uppercase tracking-wider">Legendas automaticas</p>
            <div className="grid grid-cols-2 gap-2">
              <label className="space-y-1">
                <span className="text-[10px] text-text-dim">Provider</span>
                <select
                  value={provider}
                  onChange={(e) => setProvider(e.target.value as CaptionProvider)}
                  className="w-full bg-bg-secondary text-white text-[11px] px-2 py-1.5 rounded border border-border focus:outline-none focus:border-accent"
                >
                  <option value="auto">Auto</option>
                  <option value="openai-api" disabled={!canUseProvider('openai-api')}>OpenAI API</option>
                  <option value="faster-whisper" disabled={!canUseProvider('faster-whisper')}>Faster Whisper</option>
                  <option value="whisper" disabled={!canUseProvider('whisper')}>Whisper Local</option>
                </select>
              </label>
              <label className="space-y-1">
                <span className="text-[10px] text-text-dim">Idioma</span>
                <select
                  value={language}
                  onChange={(e) => setLanguage(e.target.value as CaptionLanguage)}
                  className="w-full bg-bg-secondary text-white text-[11px] px-2 py-1.5 rounded border border-border focus:outline-none focus:border-accent"
                >
                  <option value="auto">Auto</option>
                  <option value="pt">Portugues</option>
                  <option value="en">English</option>
                  <option value="es">Espanol</option>
                </select>
              </label>
            </div>
            <label className="flex items-center justify-between text-[10px] text-text-muted">
              <span>Substituir legendas atuais</span>
              <input
                type="checkbox"
                checked={replaceAll}
                onChange={(e) => setReplaceAll(e.target.checked)}
                className="accent-accent"
              />
            </label>
            <div
              className="rounded border border-border bg-bg-secondary p-2 space-y-2"
              onFocusCapture={() => setCaptionConfigActive(true)}
              onBlurCapture={(e) => {
                const next = e.relatedTarget as Node | null
                if (!next || !e.currentTarget.contains(next)) {
                  setCaptionConfigActive(false)
                }
              }}
              onMouseEnter={() => setCaptionConfigActive(true)}
              onMouseLeave={(e) => {
                const current = e.currentTarget as HTMLElement
                const active = document.activeElement
                if (!active || !current.contains(active)) {
                  setCaptionConfigActive(false)
                }
              }}
            >
              <p className="text-[10px] text-text-dim">Configuracao antes de gerar</p>
              <label className="space-y-1 block">
                <span className="text-[10px] text-text-dim">Texto de teste no preview</span>
                <input
                  type="text"
                  value={captionPreviewText}
                  onChange={(e) => setCaptionPreviewText(e.target.value)}
                  className="w-full bg-bg-surface text-white text-[11px] px-2 py-1 rounded border border-border focus:outline-none focus:border-accent"
                />
              </label>
              <div className="grid grid-cols-2 gap-2">
                <label className="space-y-1">
                  <span className="text-[10px] text-text-dim">Tamanho (%)</span>
                  <input
                    type="number"
                    min={30}
                    max={180}
                    step={1}
                    value={Number(captionStyle.text_size_pct ?? 92)}
                    onChange={(e) => {
                      const raw = Number(e.target.value)
                      const next = Number.isFinite(raw) ? Math.max(30, Math.min(180, raw)) : 92
                      setCaptionStyle((prev) => ({ ...prev, text_size_pct: next }))
                    }}
                    className="w-full bg-bg-surface text-white text-[11px] px-2 py-1 rounded border border-border focus:outline-none focus:border-accent"
                  />
                </label>
                <label className="space-y-1">
                  <span className="text-[10px] text-text-dim">Posicao Y (%)</span>
                  <input
                    type="number"
                    min={60}
                    max={95}
                    step={1}
                    value={Number(captionStyle.text_position_y_pct ?? 82)}
                    onChange={(e) => setCaptionStyle((prev) => ({ ...prev, text_position_y_pct: Number(e.target.value) || 82 }))}
                    className="w-full bg-bg-surface text-white text-[11px] px-2 py-1 rounded border border-border focus:outline-none focus:border-accent"
                  />
                </label>
                <label className="space-y-1">
                  <span className="text-[10px] text-text-dim">Fonte</span>
                  <select
                    value={String(captionStyle.text_font ?? 'Arial')}
                    onChange={(e) => setCaptionStyle((prev) => ({ ...prev, text_font: e.target.value }))}
                    className="w-full bg-bg-surface text-white text-[11px] px-2 py-1 rounded border border-border focus:outline-none focus:border-accent"
                  >
                    <option value="Arial">Arial</option>
                    <option value="Helvetica">Helvetica</option>
                    <option value="Verdana">Verdana</option>
                    <option value="Trebuchet MS">Trebuchet</option>
                  </select>
                </label>
                <label className="space-y-1">
                  <span className="text-[10px] text-text-dim">Max chars/legenda</span>
                  <input
                    type="number"
                    min={20}
                    max={90}
                    step={1}
                    value={captionMaxChars}
                    onChange={(e) => setCaptionMaxChars(Math.max(20, Math.min(90, Number(e.target.value) || 42)))}
                    className="w-full bg-bg-surface text-white text-[11px] px-2 py-1 rounded border border-border focus:outline-none focus:border-accent"
                  />
                </label>
                <label className="space-y-1">
                  <span className="text-[10px] text-text-dim">Margem lateral (%)</span>
                  <input
                    type="number"
                    min={0}
                    max={25}
                    step={1}
                    value={Number(captionStyle.text_side_margin_pct ?? 5)}
                    onChange={(e) => {
                      const raw = Number(e.target.value)
                      const next = Number.isFinite(raw) ? Math.max(0, Math.min(25, raw)) : 5
                      setCaptionStyle((prev) => ({ ...prev, text_side_margin_pct: next }))
                    }}
                    className="w-full bg-bg-surface text-white text-[11px] px-2 py-1 rounded border border-border focus:outline-none focus:border-accent"
                  />
                  <span className="text-[10px] text-text-dim">
                    Area util de texto: {Math.max(50, 100 - (Math.max(0, Math.min(25, Number(captionStyle.text_side_margin_pct ?? 5))) * 2))}%
                  </span>
                </label>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <button
                  onClick={() => setCaptionStyle(DEFAULT_CAPTION_STYLE)}
                  className="px-2 py-1.5 rounded border border-border bg-bg-surface hover:bg-border text-[10px] text-white"
                >
                  Usar padrao limpo
                </button>
                <label className="flex items-center justify-between text-[10px] text-text-muted px-2 py-1 rounded border border-border bg-bg-surface">
                  <span>Negrito</span>
                  <input
                    type="checkbox"
                    checked={Boolean(captionStyle.text_bold)}
                    onChange={(e) => setCaptionStyle((prev) => ({ ...prev, text_bold: e.target.checked }))}
                    className="accent-accent"
                  />
                </label>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <select
                  value={captionApplyScope}
                  onChange={(e) => setCaptionApplyScope(e.target.value as CaptionApplyScope)}
                  className="w-full bg-bg-surface text-white text-[11px] px-2 py-1 rounded border border-border focus:outline-none focus:border-accent"
                >
                  <option value="selected">Alterar somente selecionada</option>
                  <option value="all">Alterar todas as legendas</option>
                </select>
                <button
                  onClick={applyCaptionStyle}
                  disabled={project.text_track.clips.length === 0 || (captionApplyScope === 'selected' && !selectedTextClip)}
                  className="px-2 py-1.5 rounded border border-border bg-bg-surface hover:bg-border disabled:opacity-50 text-[10px] text-white"
                >
                  Aplicar ajuste
                </button>
              </div>
            </div>
            <button
              onClick={handleAutoCaption}
              disabled={captionButtonDisabled}
              title={!project.videoPath ? 'Importe ou abra um video para gerar legendas.' : captionStatusText}
              className={`w-full flex items-center justify-center gap-2 px-3 py-2 bg-bg-secondary hover:bg-border disabled:opacity-60 text-white text-xs font-medium rounded-lg border border-border transition-colors ${
                captionLoading ? 'cursor-wait' : 'disabled:cursor-not-allowed'
              }`}
            >
              {captionLoading
                ? <><Loader2 size={13} className="animate-spin" /> Gerando legendas...</>
                : <><Wand2 size={13} /> Legendar automatico</>
              }
            </button>
            {(captionLoading || captionProgressText) && (
              <div className={`rounded border px-2 py-2 ${captionError ? 'border-red-500/40 bg-red-500/10' : 'border-accent/30 bg-accent/10'}`}>
                <div className="flex items-center gap-2">
                  {captionLoading && <Loader2 size={13} className="animate-spin text-accent" />}
                  <div className="min-w-0 flex-1">
                    <p className={`text-[10px] font-medium truncate ${captionError ? 'text-red-300' : 'text-accent'}`}>
                      {captionLoading ? 'Criando legendas automaticas' : 'Legenda automatica'}
                    </p>
                    <p className={`text-[10px] truncate ${captionError ? 'text-red-200' : 'text-text-muted'}`} title={captionProgressText}>
                      {captionProgressText || 'Processando...'}
                    </p>
                  </div>
                  {captionLoading && (
                    <span className="rounded bg-bg-surface px-1.5 py-0.5 text-[9px] text-text-dim tabular-nums">
                      {captionElapsedS}s
                    </span>
                  )}
                </div>
                {captionLoading && (
                  <div className="mt-2 h-1 rounded-full bg-bg-surface overflow-hidden">
                    <div className="h-full w-1/2 rounded-full bg-accent animate-pulse" />
                  </div>
                )}
              </div>
            )}
            {!canTranscribeNow && (
              <p className="text-[10px] text-amber-300" title={captionStatusError ?? captionStatusText}>
                {captionStatusText}
              </p>
            )}
            {captionStatusLoading && canTranscribeNow && !captionLoading && (
              <p className="text-[10px] text-text-dim">Verificando provider de transcricao em segundo plano...</p>
            )}
            {captionStatusError && canTranscribeNow && (
              <p className="text-[10px] text-amber-300">{captionStatusError}</p>
            )}
            {captionError && (
              <p className="text-[10px] text-red-400">{captionError}</p>
            )}
          </div>

          <div className="grid grid-cols-2 gap-2">
            <button
              onClick={deleteCurrentCaption}
              disabled={!selectedTextClip}
              className="px-2 py-2 rounded-lg border border-red-500/30 bg-red-500/10 hover:bg-red-500/20 disabled:opacity-50 text-[11px] text-red-200 transition-colors flex items-center justify-center gap-1"
            >
              <Trash2 size={12} /> Excluir atual
            </button>
            <button
              onClick={deleteAllCaptions}
              disabled={project.text_track.clips.length === 0}
              className="px-2 py-2 rounded-lg border border-red-500/30 bg-red-500/10 hover:bg-red-500/20 disabled:opacity-50 text-[11px] text-red-200 transition-colors"
            >
              Excluir todas
            </button>
          </div>

          <p className="text-[10px] text-text-dim text-center">
            {project.text_track.clips.length} texto(s) na timeline
          </p>
        </>
      )}
    </div>
  )
}
