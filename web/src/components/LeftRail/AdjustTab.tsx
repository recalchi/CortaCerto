import { useMemo, useState } from 'react'
import { Sparkles, Save, Box, Wand2, RotateCcw, Loader2, Plus, Star } from 'lucide-react'
import { api } from '../../api/client'
import { Clip, useStore } from '../../store/useStore'

type AdjustTabId = 'basic' | 'hsl' | 'curves' | 'wheels' | 'mask' | 'premium' | 'lut'
type HslChannel = 'master' | 'red' | 'yellow' | 'green' | 'cyan' | 'blue' | 'magenta'

type LutPreset = {
  id: string
  name: string
  patch: Partial<Clip>
  favorite?: boolean
}

type AiMode = 'auto' | 'palette' | 'correct'

const AI_FLAG_BY_MODE: Record<AiMode, keyof Clip> = {
  auto: 'ai_premium_auto',
  palette: 'ai_premium_palette',
  correct: 'ai_premium_correct',
}

const ADJUST_TABS: Array<{ id: AdjustTabId; label: string }> = [
  { id: 'basic', label: 'Básico' },
  { id: 'hsl', label: 'HSL' },
  { id: 'curves', label: 'Curvas' },
  { id: 'wheels', label: 'Roda de cores' },
  { id: 'mask', label: 'Mascarar' },
  { id: 'premium', label: 'IA Premium' },
  { id: 'lut', label: 'LUT' },
]

const HSL_CHANNEL_META: Array<{ id: HslChannel; label: string; color: string }> = [
  { id: 'master', label: 'Master', color: '#a78bfa' },
  { id: 'red', label: 'Vermelho', color: '#ef4444' },
  { id: 'yellow', label: 'Amarelo', color: '#eab308' },
  { id: 'green', label: 'Verde', color: '#22c55e' },
  { id: 'cyan', label: 'Ciano', color: '#06b6d4' },
  { id: 'blue', label: 'Azul', color: '#3b82f6' },
  { id: 'magenta', label: 'Magenta', color: '#ec4899' },
]

const LUT_PRESET_KEY = 'cortacerto_lut_cubes_v1'

function clamp(v: number, min: number, max: number) {
  return Math.max(min, Math.min(max, v))
}

function readLutPresets(): LutPreset[] {
  try {
    const raw = localStorage.getItem(LUT_PRESET_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter((p) => p && typeof p.id === 'string' && typeof p.name === 'string' && p.patch)
  } catch {
    return []
  }
}

function writeLutPresets(items: LutPreset[]) {
  try {
    localStorage.setItem(LUT_PRESET_KEY, JSON.stringify(items.slice(0, 30)))
  } catch {
    // ignore
  }
}

function channelField(channel: HslChannel, suffix: 'hue' | 'sat' | 'luma') {
  return `${channel}_${suffix}` as const
}

function currentNumber(clip: Clip | null, key: keyof Clip, fallback = 0) {
  if (!clip) return fallback
  const val = clip[key] as number | undefined
  return Number.isFinite(val) ? Number(val) : fallback
}

export function AdjustTab() {
  const { project, selectedClipId, updateClip, addAdjustmentLayer, setSelectedClip } = useStore()
  const [activeTab, setActiveTab] = useState<AdjustTabId>('basic')
  const [activeChannel, setActiveChannel] = useState<HslChannel>('master')
  const [lutPresets, setLutPresets] = useState<LutPreset[]>(() => readLutPresets())
  const [aiStrength, setAiStrength] = useState(0.6)
  const [aiLoading, setAiLoading] = useState(false)
  const [aiMessage, setAiMessage] = useState<string | null>(null)

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
  const canAdjust = !!clip && clip.clip_type !== 'audio' && clip.clip_type !== 'music'

  const patchClip = (patch: Partial<Clip>) => {
    if (!clip || !canAdjust) return
    updateClip(clip.id, patch)
  }

  const addCustomAdjustment = () => {
    addAdjustmentLayer(3)
    const state = useStore.getState()
    const overlay = state.project?.overlay_track.clips ?? []
    const last = overlay[overlay.length - 1]
    if (last) setSelectedClip(last.id)
  }

  const resetColor = () => {
    patchClip({
      brightness: 0,
      contrast: 0,
      saturation: 0,
      temperature: 0,
      hue: 0,
      exposure: 0,
      sharpness: 0,
      vignette: 0,
      lut_preset_name: '',
      lut_intensity: 100,
      color_hsl_grid: {},
      color_curves_grid: {},
      color_wheels_grid: {},
      color_mask_grid: {},
    })
  }

  const saveLutPreset = () => {
    if (!clip || !canAdjust) return
    const name = (window.prompt('Nome do preset LUT (cubo):', clip.lut_preset_name || 'LUT Personalizado') || '').trim()
    if (!name) return
    const patch: Partial<Clip> = {
      brightness: clip.brightness,
      contrast: clip.contrast,
      saturation: clip.saturation,
      temperature: clip.temperature ?? 0,
      hue: clip.hue ?? 0,
      exposure: clip.exposure ?? 0,
      sharpness: clip.sharpness ?? 0,
      vignette: clip.vignette ?? 0,
      color_hsl_grid: clip.color_hsl_grid ?? {},
      color_curves_grid: clip.color_curves_grid ?? {},
      color_wheels_grid: clip.color_wheels_grid ?? {},
      color_mask_grid: clip.color_mask_grid ?? {},
    }
    const next: LutPreset[] = [
      { id: `lut_${Date.now().toString(36)}`, name, patch },
      ...lutPresets,
    ].slice(0, 30)
    setLutPresets(next)
    writeLutPresets(next)
    patchClip({ lut_preset_name: name })
  }

  const applyLutPreset = (preset: LutPreset) => {
    if (!clip || !canAdjust) return
    const intensity = clamp(Number(clip.lut_intensity ?? 100), 0, 200) / 100
    const blend = (current: number, target: number) => Math.round((current + (target - current) * intensity) * 100) / 100
    const patch = { ...preset.patch }
    patch.brightness = blend(clip.brightness, Number(preset.patch.brightness ?? 0))
    patch.contrast = blend(clip.contrast, Number(preset.patch.contrast ?? 0))
    patch.saturation = blend(clip.saturation, Number(preset.patch.saturation ?? 0))
    patch.temperature = blend(Number(clip.temperature ?? 0), Number(preset.patch.temperature ?? 0))
    patch.hue = blend(Number(clip.hue ?? 0), Number(preset.patch.hue ?? 0))
    patch.exposure = blend(Number(clip.exposure ?? 0), Number(preset.patch.exposure ?? 0))
    patch.sharpness = blend(Number(clip.sharpness ?? 0), Number(preset.patch.sharpness ?? 0))
    patch.vignette = blend(Number(clip.vignette ?? 0), Number(preset.patch.vignette ?? 0))
    patch.lut_preset_name = preset.name
    patchClip(patch)
  }

  const removeLutPreset = (id: string) => {
    const next = lutPresets.filter((p) => p.id !== id)
    setLutPresets(next)
    writeLutPresets(next)
  }

  const toggleLutFavorite = (id: string) => {
    const next = lutPresets
      .map((p) => p.id === id ? { ...p, favorite: !p.favorite } : p)
      .sort((a, b) => Number(Boolean(b.favorite)) - Number(Boolean(a.favorite)))
    setLutPresets(next)
    writeLutPresets(next)
  }

  const applyAi = async (mode: AiMode) => {
    if (!clip || !canAdjust || aiLoading) return
    setAiLoading(true)
    setAiMessage(null)
    try {
      const res = await api.post('/api/ai/premium-color-assist', {
        mode,
        strength: aiStrength,
        brightness: clip.brightness,
        contrast: clip.contrast,
        saturation: clip.saturation,
        temperature: clip.temperature ?? 0,
        hue: clip.hue ?? 0,
        exposure: clip.exposure ?? 0,
        sharpness: clip.sharpness ?? 0,
        vignette: clip.vignette ?? 0,
      })
      patchClip({
        ...(res.data?.patch ?? {}),
        ai_premium_auto: mode === 'auto',
        ai_premium_palette: mode === 'palette',
        ai_premium_correct: mode === 'correct',
      })
      setAiMessage(res.data?.explanation || 'Ajuste IA aplicado.')
    } catch {
      setAiMessage('Falha ao aplicar IA Premium.')
    } finally {
      setAiLoading(false)
    }
  }

  const toggleAi = (mode: AiMode) => {
    if (!clip || !canAdjust || aiLoading) return
    const flag = AI_FLAG_BY_MODE[mode]
    if (clip[flag]) {
      patchClip({ [flag]: false } as Partial<Clip>)
      setAiMessage('Ajuste IA desativado.')
      return
    }
    applyAi(mode)
  }

  const hslGrid = clip?.color_hsl_grid ?? {}
  const curvesGrid = clip?.color_curves_grid ?? {}
  const wheelsGrid = clip?.color_wheels_grid ?? {}
  const maskGrid = clip?.color_mask_grid ?? {}

  const updateHsl = (field: string, value: number) => {
    patchClip({
      color_hsl_grid: { ...hslGrid, [field]: value },
      // Master channel also affects base grading immediately.
      ...(field === 'master_hue' ? { hue: value } : {}),
      ...(field === 'master_sat' ? { saturation: value } : {}),
      ...(field === 'master_luma' ? { brightness: value } : {}),
    } as Partial<Clip>)
  }

  return (
    <div className="h-full min-h-0 flex">
      <aside className="w-28 border-r border-border bg-bg-rail p-2 space-y-2">
        <button
          onClick={addCustomAdjustment}
          className="w-full flex items-center justify-center gap-1 rounded bg-accent/20 text-accent hover:bg-accent/30 px-2 py-1.5 text-[10px]"
        >
          <Plus size={11} /> Adicionar ajuste
        </button>
        <select className="w-full rounded bg-bg-surface border border-border px-1.5 py-1 text-[10px] text-white">
          <option>Seus</option>
        </select>
        <div className="space-y-1">
          <button
            onClick={() => setActiveTab('lut')}
            className={`w-full text-left rounded px-2 py-1 text-[10px] ${activeTab === 'lut' ? 'bg-bg-surface text-white' : 'text-text-muted hover:text-white'}`}
          >
            LUT
          </button>
          <button
            onClick={() => setActiveTab('premium')}
            className={`w-full text-left rounded px-2 py-1 text-[10px] ${activeTab === 'premium' ? 'bg-bg-surface text-white' : 'text-text-muted hover:text-white'}`}
          >
            IA Premium
          </button>
        </div>
      </aside>

      <div className="flex-1 min-w-0 p-3 space-y-3 overflow-y-auto">
        <div className="flex flex-wrap gap-1 border-b border-border pb-2">
          {ADJUST_TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-2 py-1 text-[10px] rounded ${activeTab === tab.id ? 'bg-bg-surface text-white' : 'text-text-muted hover:text-white'}`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {!clip && (
          <p className="text-[11px] text-text-dim">Selecione um clipe para ajustar cor.</p>
        )}
        {clip && !canAdjust && (
          <p className="text-[11px] text-text-dim">Ajuste de cor indisponível para clipes de áudio.</p>
        )}

        {clip && canAdjust && (
          <>
            {activeTab === 'basic' && (
              <div className="space-y-2">
                <ToggleRow
                  label="Ajuste automático"
                  title="Analisa o clipe e sugere brilho, contraste, exposicao e saturacao para uma base rapida."
                  enabled={!!clip.ai_premium_auto}
                  onToggle={() => toggleAi('auto')}
                  icon={<Sparkles size={11} />}
                />
                <ToggleRow
                  label="Combinação de cores"
                  title="Aproxima a paleta do clipe de um visual mais uniforme entre cenas."
                  enabled={!!clip.ai_premium_palette}
                  onToggle={() => toggleAi('palette')}
                  icon={<Wand2 size={11} />}
                />
                <ToggleRow
                  label="Correção de cor"
                  title="Corrige dominante de cor, temperatura e exposicao sem mudar tanto o estilo criativo."
                  enabled={!!clip.ai_premium_correct}
                  onToggle={() => toggleAi('correct')}
                  icon={<Sparkles size={11} />}
                />
                <MiniSlider label="Brilho" value={currentNumber(clip, 'brightness', 0)} min={-100} max={100} onChange={(v) => patchClip({ brightness: v })} />
                <MiniSlider label="Contraste" value={currentNumber(clip, 'contrast', 0)} min={-100} max={100} onChange={(v) => patchClip({ contrast: v })} />
                <MiniSlider label="Saturação" value={currentNumber(clip, 'saturation', 0)} min={-100} max={100} onChange={(v) => patchClip({ saturation: v })} />
                <MiniSlider label="Exposição" value={currentNumber(clip, 'exposure', 0)} min={-100} max={100} onChange={(v) => patchClip({ exposure: v })} />
                <MiniSlider label="Temperatura" value={Number(clip.temperature ?? 0)} min={-100} max={100} onChange={(v) => patchClip({ temperature: v })} />
              </div>
            )}

            {activeTab === 'hsl' && (
              <div className="space-y-2">
                <div className="grid grid-cols-4 gap-1">
                  {HSL_CHANNEL_META.map((ch) => (
                    <button
                      key={ch.id}
                      onClick={() => setActiveChannel(ch.id)}
                      className={`rounded border px-2 py-1 text-[9px] ${activeChannel === ch.id ? 'border-accent text-white' : 'border-border text-text-muted hover:text-white'}`}
                      style={{ background: `${ch.color}22` }}
                    >
                      {ch.label}
                    </button>
                  ))}
                </div>
                <MiniSlider
                  label={`${HSL_CHANNEL_META.find((ch) => ch.id === activeChannel)?.label} - Matiz`}
                  value={Number((hslGrid as any)[channelField(activeChannel, 'hue')] ?? 0)}
                  min={-100}
                  max={100}
                  onChange={(v) => updateHsl(channelField(activeChannel, 'hue'), v)}
                />
                <MiniSlider
                  label={`${HSL_CHANNEL_META.find((ch) => ch.id === activeChannel)?.label} - Saturação`}
                  value={Number((hslGrid as any)[channelField(activeChannel, 'sat')] ?? 0)}
                  min={-100}
                  max={100}
                  onChange={(v) => updateHsl(channelField(activeChannel, 'sat'), v)}
                />
                <MiniSlider
                  label={`${HSL_CHANNEL_META.find((ch) => ch.id === activeChannel)?.label} - Luminosidade`}
                  value={Number((hslGrid as any)[channelField(activeChannel, 'luma')] ?? 0)}
                  min={-100}
                  max={100}
                  onChange={(v) => updateHsl(channelField(activeChannel, 'luma'), v)}
                />
              </div>
            )}

            {activeTab === 'curves' && (
              <div className="space-y-2">
                <MiniSlider label="Sombras" value={Number(curvesGrid.shadows ?? 0)} min={-100} max={100} onChange={(v) => patchClip({ color_curves_grid: { ...curvesGrid, shadows: v }, brightness: clamp((clip.brightness ?? 0) + v * 0.05, -100, 100) })} />
                <MiniSlider label="Meios-tons" value={Number(curvesGrid.midtones ?? 0)} min={-100} max={100} onChange={(v) => patchClip({ color_curves_grid: { ...curvesGrid, midtones: v }, contrast: clamp((clip.contrast ?? 0) + v * 0.05, -100, 100) })} />
                <MiniSlider label="Altas luzes" value={Number(curvesGrid.highlights ?? 0)} min={-100} max={100} onChange={(v) => patchClip({ color_curves_grid: { ...curvesGrid, highlights: v }, exposure: clamp((clip.exposure ?? 0) + v * 0.05, -100, 100) })} />
                <MiniSlider label="Fade" value={Number(curvesGrid.fade ?? 0)} min={0} max={100} onChange={(v) => patchClip({ color_curves_grid: { ...curvesGrid, fade: v }, vignette: clamp((clip.vignette ?? 0) + v * 0.04, 0, 100) })} />
              </div>
            )}

            {activeTab === 'wheels' && (
              <div className="space-y-2">
                <MiniSlider label="Roda sombras (hue)" value={Number(wheelsGrid.shadow_hue ?? 0)} min={-180} max={180} onChange={(v) => patchClip({ color_wheels_grid: { ...wheelsGrid, shadow_hue: v }, hue: clamp((clip.hue ?? 0) + v * 0.05, -180, 180) })} />
                <MiniSlider label="Sombras intensidade" value={Number(wheelsGrid.shadow_intensity ?? 0)} min={0} max={100} onChange={(v) => patchClip({ color_wheels_grid: { ...wheelsGrid, shadow_intensity: v }, brightness: clamp((clip.brightness ?? 0) + v * 0.05, -100, 100) })} />
                <MiniSlider label="Roda médios (hue)" value={Number(wheelsGrid.mid_hue ?? 0)} min={-180} max={180} onChange={(v) => patchClip({ color_wheels_grid: { ...wheelsGrid, mid_hue: v }, temperature: clamp((clip.temperature ?? 0) + v * 0.04, -100, 100) })} />
                <MiniSlider label="Médios intensidade" value={Number(wheelsGrid.mid_intensity ?? 0)} min={0} max={100} onChange={(v) => patchClip({ color_wheels_grid: { ...wheelsGrid, mid_intensity: v }, contrast: clamp((clip.contrast ?? 0) + v * 0.06, -100, 100) })} />
                <MiniSlider label="Roda altas (hue)" value={Number(wheelsGrid.high_hue ?? 0)} min={-180} max={180} onChange={(v) => patchClip({ color_wheels_grid: { ...wheelsGrid, high_hue: v }, hue: clamp((clip.hue ?? 0) + v * 0.05, -180, 180) })} />
                <MiniSlider label="Altas intensidade" value={Number(wheelsGrid.high_intensity ?? 0)} min={0} max={100} onChange={(v) => patchClip({ color_wheels_grid: { ...wheelsGrid, high_intensity: v }, sharpness: clamp((clip.sharpness ?? 0) + v * 0.08, 0, 100) })} />
              </div>
            )}

            {activeTab === 'mask' && (
              <div className="space-y-2">
                <ToggleRow
                  label="Proteger tom da pele"
                  enabled={!!maskGrid.skin_tone_protect}
                  onToggle={() => patchClip({ color_mask_grid: { ...maskGrid, skin_tone_protect: !maskGrid.skin_tone_protect } })}
                />
                <MiniSlider
                  label="Força tom de pele"
                  value={Number(maskGrid.skin_tone_strength ?? 0)}
                  min={0}
                  max={100}
                  onChange={(v) => patchClip({ color_mask_grid: { ...maskGrid, skin_tone_strength: v } })}
                />
                <MiniSlider
                  label="Máscara vinheta"
                  value={Number(maskGrid.vignette_mask ?? 0)}
                  min={0}
                  max={100}
                  onChange={(v) => patchClip({ color_mask_grid: { ...maskGrid, vignette_mask: v }, vignette: v })}
                />
                <ToggleRow
                  label="Remover pessoa"
                  enabled={!!clip.person_remove_enabled}
                  onToggle={() => patchClip({ person_remove_enabled: !clip.person_remove_enabled })}
                  icon={<Sparkles size={11} />}
                />
                {clip.person_remove_enabled && (
                  <>
                    <MiniSlider
                      label="Força remoção"
                      value={Number(clip.person_remove_strength ?? 72)}
                      min={10}
                      max={100}
                      onChange={(v) => patchClip({ person_remove_strength: v })}
                      unit="%"
                    />
                    <MiniSlider
                      label="Suavização"
                      value={Number(clip.person_remove_feather ?? 10)}
                      min={0}
                      max={30}
                      onChange={(v) => patchClip({ person_remove_feather: v })}
                    />
                  </>
                )}
              </div>
            )}

            {activeTab === 'premium' && (
              <div className="space-y-3">
                <div className="rounded border border-border bg-bg-surface p-2">
                  <p className="text-[10px] text-white flex items-center gap-1"><Sparkles size={11} /> Funções Premium IA</p>
                  <p className="text-[9px] text-text-dim mt-1">Aplicação inteligente de gradação, paleta e correção com motor IA local.</p>
                </div>
                <MiniSlider label="Força da IA" value={Math.round(aiStrength * 100)} min={10} max={100} onChange={(v) => setAiStrength(v / 100)} unit="%" />
                <div className="grid grid-cols-3 gap-1.5">
                  <button onClick={() => toggleAi('auto')} className="rounded bg-bg-surface hover:bg-border text-text-muted hover:text-white text-[10px] py-1.5">Auto IA</button>
                  <button onClick={() => toggleAi('palette')} className="rounded bg-bg-surface hover:bg-border text-text-muted hover:text-white text-[10px] py-1.5">Paleta IA</button>
                  <button onClick={() => toggleAi('correct')} className="rounded bg-bg-surface hover:bg-border text-text-muted hover:text-white text-[10px] py-1.5">Correção IA</button>
                </div>
                {aiLoading && (
                  <p className="text-[10px] text-accent flex items-center gap-1"><Loader2 size={11} className="animate-spin" />Aplicando IA Premium...</p>
                )}
                {aiMessage && <p className="text-[10px] text-text-dim">{aiMessage}</p>}
              </div>
            )}

            {activeTab === 'lut' && (
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <select
                    value={clip.lut_preset_name || ''}
                    onChange={(e) => patchClip({ lut_preset_name: e.target.value })}
                    className="flex-1 rounded border border-border bg-bg-surface px-2 py-1 text-[10px] text-white"
                  >
                    <option value="">Nenhum</option>
                    {lutPresets.map((p) => <option key={p.id} value={p.name}>{p.name}</option>)}
                  </select>
                  <button onClick={saveLutPreset} title="Salvar preset como cubo" className="rounded bg-bg-surface hover:bg-border px-2 py-1 text-text-muted hover:text-white">
                    <Save size={12} />
                  </button>
                </div>
                <MiniSlider
                  label="Intensidade"
                  value={Number(clip.lut_intensity ?? 100)}
                  min={0}
                  max={200}
                  onChange={(v) => patchClip({ lut_intensity: v })}
                  unit="%"
                />
                <div className="grid grid-cols-3 gap-2 pt-1">
                  {[...lutPresets].sort((a, b) => Number(Boolean(b.favorite)) - Number(Boolean(a.favorite))).map((preset) => (
                    <div key={preset.id} className="relative">
                      <button
                        onClick={() => applyLutPreset(preset)}
                        className={`aspect-square w-full rounded border ${clip.lut_preset_name === preset.name ? 'border-accent' : 'border-border'} bg-bg-surface hover:bg-border p-2 text-[9px] text-white flex flex-col items-center justify-center gap-1`}
                        title={preset.name}
                      >
                        <Box size={14} />
                        <span className="truncate w-full text-center">{preset.name}</span>
                      </button>
                      <button
                        onClick={() => toggleLutFavorite(preset.id)}
                        className={`absolute -top-1 left-1 h-4 w-4 rounded-full bg-black/70 flex items-center justify-center ${preset.favorite ? 'text-yellow-300' : 'text-text-dim hover:text-yellow-300'}`}
                        title={preset.favorite ? 'Remover dos favoritos' : 'Favoritar preset'}
                      >
                        <Star size={10} fill={preset.favorite ? 'currentColor' : 'none'} />
                      </button>
                      <button
                        onClick={() => removeLutPreset(preset.id)}
                        className="absolute -top-1 -right-1 h-4 w-4 rounded-full bg-black/70 text-[9px] text-text-dim hover:text-red-300"
                        title="Remover preset"
                      >
                        ×
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="pt-1 border-t border-border">
              <button
                onClick={resetColor}
                className="w-full flex items-center justify-center gap-1.5 rounded bg-bg-surface hover:bg-border text-text-muted hover:text-white py-1.5 text-[10px]"
              >
                <RotateCcw size={11} /> Resetar ajustes
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function ToggleRow({
  label,
  title,
  enabled,
  onToggle,
  icon,
}: {
  label: string
  title?: string
  enabled: boolean
  onToggle: () => void
  icon?: React.ReactNode
}) {
  return (
    <button
      onClick={onToggle}
      title={title}
      className="w-full flex items-center justify-between rounded border border-border bg-bg-surface px-2 py-1.5 text-[10px] text-text-muted hover:text-white"
    >
      <span className="flex items-center gap-1.5">{icon}{label}</span>
      <span className={`h-3.5 w-6 rounded-full ${enabled ? 'bg-accent' : 'bg-bg-panel'} relative`}>
        <span className={`absolute top-0.5 h-2.5 w-2.5 rounded-full bg-white transition-all ${enabled ? 'left-3' : 'left-0.5'}`} />
      </span>
    </button>
  )
}

function MiniSlider({
  label,
  value,
  min,
  max,
  onChange,
  unit = '',
}: {
  label: string
  value: number
  min: number
  max: number
  onChange: (v: number) => void
  unit?: string
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-[10px]">
        <span className="text-text-muted">{label}</span>
        <span className="text-white tabular-nums">{value}{unit}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full h-1 accent-accent cursor-pointer"
      />
    </div>
  )
}
