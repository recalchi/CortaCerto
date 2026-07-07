import { useState, useEffect, useRef } from 'react'
import { Plus, RotateCcw, Trash2 } from 'lucide-react'
import { useStore, Clip } from '../../store/useStore'

type TabId = 'basico' | 'audio' | 'cor' | 'recorte' | 'velocidade' | 'animacao' | 'avancado'

interface TabDef { id: TabId; label: string }

// Tab sets per clip type — match CapCut's per-context inspector tabs.
const TABS_VIDEO: TabDef[] = [
  { id: 'basico',     label: 'Vídeo' },
  { id: 'velocidade', label: 'Veloc.' },
  { id: 'animacao',   label: 'Anim.' },
  { id: 'cor',        label: 'Ajuste' },
  { id: 'recorte',    label: 'Recorte' },
  { id: 'avancado',   label: 'Avanç.' },
]
const TABS_AUDIO: TabDef[] = [
  { id: 'basico',     label: 'Básico' },
  { id: 'velocidade', label: 'Veloc.' },
]
const TABS_TEXT: TabDef[] = [
  { id: 'basico',     label: 'Texto' },
  { id: 'animacao',   label: 'Anim.' },
  { id: 'avancado',   label: 'Avanç.' },
]
const TABS_IMAGE: TabDef[] = [
  { id: 'basico',     label: 'Imagem' },
  { id: 'animacao',   label: 'Anim.' },
  { id: 'cor',        label: 'Ajuste' },
  { id: 'recorte',    label: 'Recorte' },
  { id: 'avancado',   label: 'Avanç.' },
]
// Adjustment layer clips: only the Ajuste tab is meaningful — they don't
// have content of their own, they just shift color of clips below.
const TABS_ADJUSTMENT: TabDef[] = [
  { id: 'cor', label: 'Ajuste' },
]

const ANIMATION_OPTIONS = [
  { id: 'none',         label: 'Nenhuma' },
  { id: 'fade',         label: 'Fade' },
  { id: 'typewriter',   label: 'Digitando' },
  { id: 'magic-sparkle', label: 'Magico brilho' },
  { id: 'draw-reveal',  label: 'Desenhando' },
  { id: 'slide-left',   label: 'Slide ← Esq.' },
  { id: 'slide-right',  label: 'Slide → Dir.' },
  { id: 'slide-up',     label: 'Slide ↑ Cima' },
  { id: 'slide-down',   label: 'Slide ↓ Baixo' },
  { id: 'zoom-in',      label: 'Zoom in' },
  { id: 'zoom-out',     label: 'Zoom out' },
]

const SPEED_PRESETS  = [0.25, 0.5, 1, 1.5, 2, 4]
const BLEND_MODES    = ['Normal','Screen','Multiply','Overlay','Add','Darken','Lighten']
const TRANSITIONS    = ['Corte','Fade','Dissolver','Wipe Esq.','Wipe Dir.','Zoom']
const BLUR_TYPES = [
  { id: 'none',     label: 'Sem blur' },
  { id: 'gaussian', label: 'Gaussian' },
  { id: 'box',      label: 'Forte' },
  { id: 'pixelate', label: 'Pixelado' },
]
const BLUR_DIRECTIONS = [
  { id: 'both',       label: 'X/Y' },
  { id: 'horizontal', label: 'Horizontal' },
  { id: 'vertical',   label: 'Vertical' },
]

const PROFESSIONAL_FONTS = [
  'Sistema', 'Segoe UI', 'Aptos', 'Arial', 'Helvetica', 'Roboto', 'Inter',
  'Montserrat', 'Poppins', 'Manrope', 'DM Sans', 'Nunito', 'Source Sans 3',
  'Fira Sans', 'Raleway', 'Open Sans', 'Lato', 'Verdana', 'Tahoma',
  'Trebuchet MS', 'Century Gothic', 'Bahnschrift', 'Franklin Gothic Medium',
  'Impact', 'Bebas Neue', 'Oswald', 'Arial Black', 'Georgia',
  'Playfair Display', 'Merriweather', 'Lora', 'Garamond', 'Cambria',
  'Palatino Linotype', 'Book Antiqua', 'Times New Roman', 'Consolas',
  'Courier New', 'Lucida Console',
]

// Local UI state mirrors the Clip fields
interface EditState {
  scale_pct:            number
  volume_pct:           number
  opacity_pct:          number
  brightness:           number
  contrast:             number
  saturation:           number
  temperature:          number
  hue:                  number
  exposure:             number
  sharpness:            number
  vignette:             number
  blur_type:            'none' | 'gaussian' | 'box' | 'pixelate'
  blur_intensity:       number
  blur_direction:       'both' | 'horizontal' | 'vertical'
  crop_top_pct:         number
  crop_bottom_pct:      number
  crop_left_pct:        number
  crop_right_pct:       number
  speed_factor:         number
  rotation_deg:         number
  blend_mode:           string
  transition:           string
  transition_duration_s: number
  // Audio
  fade_in_s:            number
  fade_out_s:           number
  normalize_audio:      boolean
  // Transform
  position_x:           number
  position_y:           number
  uniform_scale:        boolean
  // Animations
  animation_in:             string
  animation_out:            string
  animation_in_duration_s:  number
  animation_out_duration_s: number
  // text clip fields
  text_overlay:         string
  text_position_x_pct:  number
  text_position_y_pct:  number
  text_size_pct:        number
  text_side_margin_pct: number
  text_line_spacing:    number
  text_color:           string
  text_bold:            boolean
  text_italic:          boolean
  text_underline:       boolean
  text_align:           string
  text_font:            string
  text_background_enabled: boolean
  text_background_color:   string
  text_background_alpha:   number
  text_stroke_enabled:     boolean
  text_stroke_color:       string
  text_stroke_width:       number
  text_shadow_enabled:     boolean
  text_shadow_color:       string
  // chroma key
  chroma_enabled:   boolean
  chroma_color:     string
  chroma_tolerance: number
}

function clipToEdit(clip: Clip): EditState {
  return {
    scale_pct:            clip.scale_pct            ?? 100,
    volume_pct:           clip.volume_pct           ?? 100,
    opacity_pct:          clip.opacity_pct          ?? 100,
    brightness:           clip.brightness           ?? 0,
    contrast:             clip.contrast             ?? 0,
    saturation:           clip.saturation           ?? 0,
    temperature:          clip.temperature          ?? 0,
    hue:                  clip.hue                  ?? 0,
    exposure:             clip.exposure             ?? 0,
    sharpness:            clip.sharpness            ?? 0,
    vignette:             clip.vignette             ?? 0,
    blur_type:            clip.blur_type            ?? 'none',
    blur_intensity:       clip.blur_intensity       ?? 0,
    blur_direction:       clip.blur_direction       ?? 'both',
    crop_top_pct:         clip.crop_top_pct         ?? 0,
    crop_bottom_pct:      clip.crop_bottom_pct      ?? 0,
    crop_left_pct:        clip.crop_left_pct        ?? 0,
    crop_right_pct:       clip.crop_right_pct       ?? 0,
    speed_factor:         clip.speed_factor         ?? 1,
    rotation_deg:         clip.rotation_deg         ?? 0,
    blend_mode:           clip.blend_mode           ?? 'Normal',
    transition:           clip.transition           ?? 'Corte',
    transition_duration_s: clip.transition_duration_s ?? 0.4,
    fade_in_s:            clip.fade_in_s            ?? 0,
    fade_out_s:           clip.fade_out_s           ?? 0,
    normalize_audio:      clip.normalize_audio      ?? false,
    position_x:           clip.position_x           ?? 0,
    position_y:           clip.position_y           ?? 0,
    uniform_scale:        clip.uniform_scale        ?? true,
    animation_in:             clip.animation_in             ?? 'none',
    animation_out:            clip.animation_out            ?? 'none',
    animation_in_duration_s:  clip.animation_in_duration_s  ?? 0.5,
    animation_out_duration_s: clip.animation_out_duration_s ?? 0.5,
    text_overlay:         clip.text_overlay         ?? '',
    text_position_x_pct:  clip.text_position_x_pct  ?? 0,
    text_position_y_pct:  clip.text_position_y_pct  ?? 72,
    text_size_pct:        clip.text_size_pct         ?? 100,
    text_side_margin_pct: clip.text_side_margin_pct  ?? 5,
    text_line_spacing:    clip.text_line_spacing     ?? 1.25,
    text_color:           clip.text_color            ?? '#ffffff',
    text_bold:            clip.text_bold             ?? false,
    text_italic:          clip.text_italic           ?? false,
    text_underline:       clip.text_underline        ?? false,
    text_align:           clip.text_align            ?? 'center',
    text_font:            clip.text_font             ?? 'Sistema',
    text_background_enabled: clip.text_background_enabled ?? false,
    text_background_color:   clip.text_background_color   ?? '#000000',
    text_background_alpha:   clip.text_background_alpha   ?? 0.65,
    text_stroke_enabled:     clip.text_stroke_enabled     ?? false,
    text_stroke_color:       clip.text_stroke_color       ?? '#000000',
    text_stroke_width:       clip.text_stroke_width       ?? 2,
    text_shadow_enabled:     clip.text_shadow_enabled     ?? true,
    text_shadow_color:       clip.text_shadow_color       ?? '#000000',
    chroma_enabled:       clip.chroma_enabled        ?? false,
    chroma_color:         clip.chroma_color          ?? '#00ff00',
    chroma_tolerance:     clip.chroma_tolerance      ?? 45,
  }
}

export function Inspector() {
  const { project, selectedClipId, previewTime, setPreviewTime, updateClip } = useStore()
  const [activeTab, setActiveTab] = useState<TabId>('basico')
  const [edit, setEdit]           = useState<EditState | null>(null)
  const [labelDraft, setLabelDraft] = useState('')
  const syncTimer                 = useRef<ReturnType<typeof setTimeout> | null>(null)

  const allClips = project
    ? [...project.video_track.clips, ...project.audio_track.clips,
       ...project.text_track.clips, ...project.overlay_track.clips,
       ...(project.extra_video_tracks ?? []).flatMap((t) => t.clips),
       ...(project.extra_audio_tracks ?? []).flatMap((t) => t.clips),
       ...(project.extra_overlay_tracks ?? []).flatMap((t) => t.clips)]
    : []
  const clip = allClips.find((c) => c.id === selectedClipId) ?? null

  // Re-init local state when the selected clip changes
  useEffect(() => {
    setEdit(clip ? clipToEdit(clip) : null)
    setLabelDraft(clip?.label ?? '')
  }, [selectedClipId])

  // When clip type changes, ensure the active tab is valid for the new tab set.
  useEffect(() => {
    if (!clip) return
    const isAdj = clip.clip_type === 'adjustment'
    const isAud = clip.clip_type === 'music' || clip.clip_type === 'audio'
    const isTxt = clip.clip_type === 'text'
    const isImg = clip.clip_type === 'image' || clip.clip_type === 'sticker'
    const valid: TabDef[] =
        isAdj ? TABS_ADJUSTMENT
      : isTxt ? TABS_TEXT
      : isAud ? TABS_AUDIO
      : isImg ? TABS_IMAGE
      : TABS_VIDEO
    if (!valid.find((t) => t.id === activeTab)) setActiveTab(valid[0].id)
  }, [clip?.clip_type])  // eslint-disable-line react-hooks/exhaustive-deps

  // Debounce sync back to store whenever edit changes
  useEffect(() => {
    if (!edit || !clip) return
    if (syncTimer.current) clearTimeout(syncTimer.current)
    syncTimer.current = setTimeout(() => {
      updateClip(clip.id, {
        scale_pct:            edit.scale_pct,
        volume_pct:           edit.volume_pct,
        opacity_pct:          edit.opacity_pct,
        brightness:           edit.brightness,
        contrast:             edit.contrast,
        saturation:           edit.saturation,
        temperature:          edit.temperature,
        hue:                  edit.hue,
        exposure:             edit.exposure,
        sharpness:            edit.sharpness,
        vignette:             edit.vignette,
        blur_type:            edit.blur_type,
        blur_intensity:       edit.blur_intensity,
        blur_direction:       edit.blur_direction,
        crop_top_pct:         edit.crop_top_pct,
        crop_bottom_pct:      edit.crop_bottom_pct,
        crop_left_pct:        edit.crop_left_pct,
        crop_right_pct:       edit.crop_right_pct,
        speed_factor:         edit.speed_factor,
        rotation_deg:         edit.rotation_deg,
        blend_mode:           edit.blend_mode,
        transition:           edit.transition,
        transition_duration_s: edit.transition_duration_s,
        text_overlay:         edit.text_overlay,
        text_position_x_pct:  edit.text_position_x_pct,
        text_position_y_pct:  edit.text_position_y_pct,
        text_size_pct:        edit.text_size_pct,
        text_side_margin_pct: edit.text_side_margin_pct,
        text_line_spacing:    edit.text_line_spacing,
        text_color:           edit.text_color,
        text_bold:            edit.text_bold,
        text_italic:          edit.text_italic,
        text_underline:       edit.text_underline,
        text_align:           edit.text_align,
        text_font:            edit.text_font,
        text_background_enabled: edit.text_background_enabled,
        text_background_color:   edit.text_background_color,
        text_background_alpha:   edit.text_background_alpha,
        text_stroke_enabled:     edit.text_stroke_enabled,
        text_stroke_color:       edit.text_stroke_color,
        text_stroke_width:       edit.text_stroke_width,
        text_shadow_enabled:     edit.text_shadow_enabled,
        text_shadow_color:       edit.text_shadow_color,
        chroma_enabled:       edit.chroma_enabled,
        chroma_color:         edit.chroma_color,
        chroma_tolerance:     edit.chroma_tolerance,
        fade_in_s:            edit.fade_in_s,
        fade_out_s:           edit.fade_out_s,
        normalize_audio:      edit.normalize_audio,
        position_x:           edit.position_x,
        position_y:           edit.position_y,
        uniform_scale:        edit.uniform_scale,
        animation_in:             edit.animation_in,
        animation_out:            edit.animation_out,
        animation_in_duration_s:  edit.animation_in_duration_s,
        animation_out_duration_s: edit.animation_out_duration_s,
      })
    }, 80)
    return () => { if (syncTimer.current) clearTimeout(syncTimer.current) }
  }, [edit])   // eslint-disable-line react-hooks/exhaustive-deps

  const set = <K extends keyof EditState>(key: K) =>
    (val: EditState[K]) => setEdit((e) => (e ? { ...e, [key]: val } : e))

  const clipRelTime = Math.max(0, Math.min(clip ? clip.end_s - clip.start_s : 0, previewTime - (clip?.start_s ?? 0)))
  const keyframes = (clip?.motion_keyframes ?? []).slice().sort((a, b) => a.t - b.t)
  const kfIndexAtPlayhead = keyframes.findIndex((k) => Math.abs(k.t - clipRelTime) <= 0.04)

  const addOrUpdateMotionKeyframe = () => {
    if (!clip || !edit) return
    const t = Math.round(clipRelTime * 100) / 100
    const next = [...(clip.motion_keyframes ?? [])]
    const ix = next.findIndex((k) => Math.abs(k.t - t) <= 0.04)
    const frame = {
      t,
      position_x: edit.position_x,
      position_y: edit.position_y,
      scale_pct: edit.scale_pct,
      opacity_pct: edit.opacity_pct,
      volume_pct: edit.volume_pct,
    }
    if (ix >= 0) next[ix] = frame
    else next.push(frame)
    next.sort((a, b) => a.t - b.t)
    updateClip(clip.id, { motion_keyframes: next })
  }

  const removeMotionKeyframe = () => {
    if (!clip || kfIndexAtPlayhead < 0) return
    const next = (clip.motion_keyframes ?? []).filter((_, ix) => ix !== kfIndexAtPlayhead)
    updateClip(clip.id, { motion_keyframes: next })
  }

  const setMotionKeyframeEasing = (easing: 'linear' | 'ease-in' | 'ease-out' | 'ease-in-out') => {
    if (!clip || kfIndexAtPlayhead < 0) return
    const next = (clip.motion_keyframes ?? []).map((kf, ix) => (
      ix === kfIndexAtPlayhead ? { ...kf, easing } : kf
    ))
    updateClip(clip.id, { motion_keyframes: next })
  }

  if (!clip || !edit) {
    return (
      <div className="flex flex-col h-full">
        <div className="flex-1 flex flex-col items-center justify-center gap-2 p-4 text-center">
          <div className="w-10 h-10 rounded-xl bg-bg-surface flex items-center justify-center text-text-dim text-lg">◻</div>
          <p className="text-text-dim text-xs leading-relaxed">
            Selecione um clipe<br />na timeline para editar
          </p>
        </div>
      </div>
    )
  }

  const isAudio      = clip.clip_type === 'music' || clip.clip_type === 'audio'
  const isText       = clip.clip_type === 'text'
  const isImage      = clip.clip_type === 'image' || clip.clip_type === 'sticker'
  const isAdjustment = clip.clip_type === 'adjustment'
  // Pick the tab set matching the clip type — mirrors CapCut's per-context tabs
  const TABS: TabDef[] =
      isAdjustment ? TABS_ADJUSTMENT
    : isText       ? TABS_TEXT
    : isAudio      ? TABS_AUDIO
    : isImage      ? TABS_IMAGE
    : TABS_VIDEO
  const dur = (clip.end_s - clip.start_s).toFixed(2)

  return (
    <div className="flex flex-col h-full text-xs select-none">
      {/* Clip info strip */}
      <div className="px-3 pt-2.5 pb-2 border-b border-border flex-shrink-0">
        <div className="flex items-start justify-between gap-1">
          <input
            value={labelDraft}
            onChange={(e) => setLabelDraft(e.target.value)}
            onBlur={() => {
              const clean = labelDraft.trim()
              if (clean && clean !== clip.label) updateClip(clip.id, { label: clean })
              else setLabelDraft(clip.label)
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') e.currentTarget.blur()
              if (e.key === 'Escape') {
                setLabelDraft(clip.label)
                e.currentTarget.blur()
              }
            }}
            className="min-w-0 flex-1 bg-transparent text-white font-semibold truncate leading-tight outline-none border-b border-transparent focus:border-accent"
            title="Renomear clipe"
          />
          <span className="text-[9px] text-text-dim bg-bg-surface px-1.5 py-0.5 rounded flex-shrink-0 mt-0.5 uppercase">
            {clip.clip_type}
          </span>
        </div>
        <p className="text-text-muted text-[10px] mt-1 tabular-nums">
          {clip.start_s.toFixed(2)}s → {clip.end_s.toFixed(2)}s
          <span className="text-text-dim"> · {dur}s</span>
        </p>
      </div>

      {/* Tab bar */}
      <div className="flex border-b border-border flex-shrink-0">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setActiveTab(t.id)}
            className={`flex-1 py-2 text-[10px] font-medium transition-colors border-b-2 ${
              activeTab === t.id
                ? 'text-accent border-accent'
                : 'text-text-muted border-transparent hover:text-white'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3">

        {/* BÁSICO — content depends on clip type */}
        {activeTab === 'basico' && (
          <>
            {/* Video / Image: Transform controls (CapCut "Transformar 2" section) */}
            {!isAudio && !isText && (
              <>
                <p className="text-[10px] text-text-dim uppercase tracking-wider">Transformar</p>
                {/* Escala 10..500% — CapCut goes up to 500%, user pushed to 255% in test */}
                <Slider label="Escala"    value={edit.scale_pct}   min={10}  max={500} unit="%" onChange={set('scale_pct')} />
                <Slider label="Opacidade" value={edit.opacity_pct} min={0}   max={100} unit="%" onChange={set('opacity_pct')} />
                {!isImage && !isAdjustment && (
                  <Slider
                    label="Volume do video"
                    value={edit.volume_pct}
                    min={0}
                    max={200}
                    unit={` (${volumeDb(edit.volume_pct).toFixed(1)} dB)`}
                    onChange={set('volume_pct')}
                  />
                )}
                <Slider label="Rotação"   value={edit.rotation_deg} min={-180} max={180} unit="°" onChange={set('rotation_deg')} zeroed />
                {/* Position offsets in pixels (CapCut shows X=-414 etc.) */}
                <Row label="Posição">
                  <div className="flex items-center gap-1">
                    <span className="text-[9px] text-text-dim">X</span>
                    <SignedNumberInput value={edit.position_x} onChange={set('position_x')} />
                    <span className="text-[9px] text-text-dim ml-1">Y</span>
                    <SignedNumberInput value={edit.position_y} onChange={set('position_y')} />
                  </div>
                </Row>
                <Toggle label="Escala uniforme" value={edit.uniform_scale} onChange={set('uniform_scale')} />
                <Divider />
                <div className="space-y-1">
                  <p className="text-text-muted text-[11px]">Transição</p>
                  <select
                    value={edit.transition}
                    onChange={(e) => set('transition')(e.target.value)}
                    className="w-full bg-bg-surface text-white text-[11px] px-2 py-1.5 rounded-lg border border-border focus:outline-none focus:border-accent cursor-pointer"
                  >
                    {TRANSITIONS.map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                </div>
                {edit.transition !== 'Corte' && (
                  <Slider
                    label="Duracao transicao"
                    value={edit.transition_duration_s}
                    min={0.1}
                    max={1.5}
                    step={0.05}
                    unit="s"
                    onChange={set('transition_duration_s')}
                  />
                )}
              </>
            )}

            {/* Audio: Volume in dB + Fade in/out + Normalize (CapCut Áudio Básico) */}
            {isAudio && (
              <>
                <p className="text-[10px] text-text-dim uppercase tracking-wider">Básico</p>
                <Slider
                  label="Volume"
                  value={edit.volume_pct} min={0} max={200}
                  unit={` (${volumeDb(edit.volume_pct).toFixed(1)} dB)`}
                  onChange={set('volume_pct')}
                />
                <Slider label="Fade-in"  value={edit.fade_in_s}  min={0} max={5} step={0.1} unit="s" onChange={set('fade_in_s')} />
                <Slider label="Fade-out" value={edit.fade_out_s} min={0} max={5} step={0.1} unit="s" onChange={set('fade_out_s')} />
                <Toggle label="Normalizar nível de volume" value={edit.normalize_audio} onChange={set('normalize_audio')} />
              </>
            )}

            <Row label="Duração"><Chip>{dur}s</Chip></Row>

            {!isAdjustment && (
              <>
                <Divider />
                <p className="text-[10px] text-text-dim uppercase tracking-wider">Keyframes</p>
                <Row label="Playhead">
                  <Chip>{clipRelTime.toFixed(2)}s</Chip>
                </Row>
                <div className="grid grid-cols-2 gap-1">
                  <button
                    onClick={addOrUpdateMotionKeyframe}
                    className="flex items-center justify-center gap-1 py-1.5 rounded-lg text-[10px] bg-bg-surface text-text-muted hover:text-white hover:bg-border transition-colors"
                    title="Adicionar ou atualizar keyframe no playhead"
                  >
                    <Plus size={10} /> Salvar
                  </button>
                  <button
                    onClick={removeMotionKeyframe}
                    disabled={kfIndexAtPlayhead < 0}
                    className="flex items-center justify-center gap-1 py-1.5 rounded-lg text-[10px] bg-bg-surface text-text-muted hover:text-white hover:bg-border transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                    title="Remover keyframe nesta posição"
                  >
                    <Trash2 size={10} /> Remover
                  </button>
                </div>
                {kfIndexAtPlayhead >= 0 && (
                  <Row label="Easing">
                    <select
                      value={keyframes[kfIndexAtPlayhead]?.easing ?? 'linear'}
                      onChange={(e) => setMotionKeyframeEasing(e.target.value as 'linear' | 'ease-in' | 'ease-out' | 'ease-in-out')}
                      className="bg-bg-surface text-white text-[10px] px-1.5 py-0.5 rounded border border-border focus:outline-none focus:border-accent w-28"
                    >
                      <option value="linear">Linear</option>
                      <option value="ease-in">Ease in</option>
                      <option value="ease-out">Ease out</option>
                      <option value="ease-in-out">Ease in-out</option>
                    </select>
                  </Row>
                )}
                <div className="flex flex-wrap gap-1">
                  {keyframes.length === 0 ? (
                    <span className="text-[10px] text-text-dim">Sem keyframes neste clipe</span>
                  ) : keyframes.map((kf, ix) => (
                    <button
                      key={`${kf.t}-${ix}`}
                      onClick={() => setPreviewTime(clip.start_s + kf.t)}
                      className={`px-1.5 py-0.5 rounded text-[9px] tabular-nums transition-colors ${
                        Math.abs(kf.t - clipRelTime) <= 0.04
                          ? 'bg-accent text-white'
                          : 'bg-bg-surface text-text-muted hover:text-white'
                      }`}
                      title="Ir para keyframe"
                    >
                      {kf.t.toFixed(2)}s
                    </button>
                  ))}
                </div>
              </>
            )}

            {/* TEXT EDITING — only shown for text clips */}
            {isText && (
              <>
                <Divider />
                <div className="space-y-1.5">
                  <p className="text-[10px] text-text-dim uppercase tracking-wider">Conteúdo do texto</p>
                  <textarea
                    value={edit.text_overlay}
                    onChange={(e) => setEdit((prev) => prev ? { ...prev, text_overlay: e.target.value } : prev)}
                    className="w-full bg-bg-surface text-white text-[11px] px-2 py-1.5 rounded-lg border border-border focus:outline-none focus:border-accent resize-none leading-relaxed"
                    rows={3}
                    placeholder="Digite o texto aqui…"
                  />
                </div>
                <Row label="Fonte">
                  <select
                    value={edit.text_font}
                    onChange={(e) => set('text_font')(e.target.value)}
                    className="bg-bg-surface text-white text-[10px] px-1.5 py-0.5 rounded border border-border focus:outline-none focus:border-accent w-40"
                  >
                    {PROFESSIONAL_FONTS.map((font) => (
                      <option key={font} value={font}>{font}</option>
                    ))}
                  </select>
                </Row>
                <Slider label="Tamanho"  value={edit.text_size_pct}        min={20}  max={300} unit="%" onChange={set('text_size_pct')} />
                <Slider label="Pos. X"   value={edit.text_position_x_pct}  min={-50} max={50}  unit="%" onChange={set('text_position_x_pct')} zeroed />
                <Slider label="Pos. Y"   value={edit.text_position_y_pct}  min={0}   max={100} unit="%" onChange={set('text_position_y_pct')} />
                <Slider label="Margem lateral" value={edit.text_side_margin_pct} min={0} max={35} unit="%" onChange={set('text_side_margin_pct')} />
                <Slider label="Espacamento linhas" value={edit.text_line_spacing} min={0.85} max={2} step={0.05} unit="x" onChange={set('text_line_spacing')} />
                <Row label="Cor">
                  <input
                    type="color"
                    value={edit.text_color}
                    onChange={(e) => setEdit((prev) => prev ? { ...prev, text_color: e.target.value } : prev)}
                    className="w-7 h-7 rounded cursor-pointer border border-border"
                    title="Cor do texto"
                  />
                </Row>
                {/* B / I / U style buttons + alignment (CapCut "Padrão" row) */}
                <Divider />
                <p className="text-[10px] text-text-dim uppercase tracking-wider">Caixa e acabamento</p>
                <Toggle label="Fundo do texto" value={edit.text_background_enabled} onChange={set('text_background_enabled')} />
                {edit.text_background_enabled && (
                  <>
                    <Row label="Cor fundo">
                      <input
                        type="color"
                        value={edit.text_background_color}
                        onChange={(e) => setEdit((prev) => prev ? { ...prev, text_background_color: e.target.value } : prev)}
                        className="w-7 h-7 rounded cursor-pointer border border-border"
                      />
                    </Row>
                    <Slider label="Opacidade fundo" value={edit.text_background_alpha * 100} min={0} max={100} unit="%" onChange={(v) => setEdit((prev) => prev ? { ...prev, text_background_alpha: v / 100 } : prev)} />
                  </>
                )}
                <Toggle label="Sombra" value={edit.text_shadow_enabled} onChange={set('text_shadow_enabled')} />
                {edit.text_shadow_enabled && (
                  <Row label="Cor sombra">
                    <input
                      type="color"
                      value={edit.text_shadow_color}
                      onChange={(e) => setEdit((prev) => prev ? { ...prev, text_shadow_color: e.target.value } : prev)}
                      className="w-7 h-7 rounded cursor-pointer border border-border"
                    />
                  </Row>
                )}
                <Toggle label="Contorno" value={edit.text_stroke_enabled} onChange={set('text_stroke_enabled')} />
                {edit.text_stroke_enabled && (
                  <>
                    <Row label="Cor contorno">
                      <input
                        type="color"
                        value={edit.text_stroke_color}
                        onChange={(e) => setEdit((prev) => prev ? { ...prev, text_stroke_color: e.target.value } : prev)}
                        className="w-7 h-7 rounded cursor-pointer border border-border"
                      />
                    </Row>
                    <Slider label="Espessura" value={edit.text_stroke_width} min={1} max={12} unit="px" onChange={set('text_stroke_width')} />
                  </>
                )}
                <Divider />
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => setEdit((prev) => prev ? { ...prev, text_bold: !prev.text_bold } : prev)}
                    className={`flex-1 py-1 rounded text-[11px] font-bold transition-colors ${edit.text_bold ? 'bg-accent text-white' : 'bg-bg-surface text-text-muted hover:text-white'}`}
                    title="Negrito"
                  >B</button>
                  <button
                    onClick={() => setEdit((prev) => prev ? { ...prev, text_italic: !prev.text_italic } : prev)}
                    className={`flex-1 py-1 rounded text-[11px] italic transition-colors ${edit.text_italic ? 'bg-accent text-white' : 'bg-bg-surface text-text-muted hover:text-white'}`}
                    title="Itálico"
                  >I</button>
                  <button
                    onClick={() => setEdit((prev) => prev ? { ...prev, text_underline: !prev.text_underline } : prev)}
                    className={`flex-1 py-1 rounded text-[11px] transition-colors ${edit.text_underline ? 'bg-accent text-white' : 'bg-bg-surface text-text-muted hover:text-white'}`}
                    style={{ textDecoration: 'underline' }}
                    title="Sublinhado"
                  >U</button>
                </div>
                <div className="flex items-center gap-1">
                  {(['left', 'center', 'right'] as const).map((a) => (
                    <button
                      key={a}
                      onClick={() => setEdit((prev) => prev ? { ...prev, text_align: a } : prev)}
                      className={`flex-1 py-1 rounded text-[10px] transition-colors ${edit.text_align === a ? 'bg-accent text-white' : 'bg-bg-surface text-text-muted hover:text-white'}`}
                      title={`Alinhar ${a === 'left' ? 'esquerda' : a === 'center' ? 'centro' : 'direita'}`}
                    >{a === 'left' ? '⇐' : a === 'center' ? '☰' : '⇒'}</button>
                  ))}
                </div>
                <button
                  onClick={() => setEdit((prev) => prev ? {
                    ...prev, text_overlay: '', text_size_pct: 100,
                    text_position_x_pct: 0, text_position_y_pct: 72,
                    text_side_margin_pct: 5, text_line_spacing: 1.25,
                    text_color: '#ffffff', text_bold: false, text_italic: false, text_align: 'center',
                    text_background_enabled: false, text_background_color: '#000000', text_background_alpha: 0.65,
                    text_shadow_enabled: true, text_shadow_color: '#000000',
                    text_stroke_enabled: false, text_stroke_color: '#000000', text_stroke_width: 2,
                  } : prev)}
                  className="flex items-center gap-1.5 text-text-muted hover:text-white transition-colors text-[10px] w-full justify-center py-1"
                >
                  <RotateCcw size={10} /> Resetar texto
                </button>
              </>
            )}
          </>
        )}

        {/* AJUSTE (color grading) — matches CapCut "Ajuste" panel */}
        {activeTab === 'cor' && (
          isAudio ? <Unavailable /> : (
            <>
              <p className="text-[10px] text-text-dim uppercase tracking-wider">Básico</p>
              <Slider label="Temperatura" value={edit.temperature} min={-100} max={100} onChange={set('temperature')} zeroed />
              <Slider label="Matiz"       value={edit.hue}         min={-180} max={180} unit="°" onChange={set('hue')}       zeroed />
              <Slider label="Saturação"   value={edit.saturation}  min={-100} max={100} onChange={set('saturation')}  zeroed />
              <Slider label="Claridade"   value={edit.contrast}    min={-100} max={100} onChange={set('contrast')}    zeroed />
              <Slider label="Exposição"   value={edit.exposure}    min={-100} max={100} onChange={set('exposure')}    zeroed />
              <Slider label="Brilho"      value={edit.brightness}  min={-100} max={100} onChange={set('brightness')}  zeroed />
              <Divider />
              <p className="text-[10px] text-text-dim uppercase tracking-wider">Efeitos</p>
              <Slider label="Aumentar nitidez" value={edit.sharpness} min={0} max={100} onChange={set('sharpness')} />
              <Slider label="Vinheta"          value={edit.vignette}  min={0} max={100} onChange={set('vignette')} />
              <Divider />
              <p className="text-[10px] text-text-dim uppercase tracking-wider">Desfoque</p>
              <div className="grid grid-cols-2 gap-1">
                {BLUR_TYPES.map((item) => (
                  <button
                    key={item.id}
                    onClick={() => setEdit((e) => e ? { ...e, blur_type: item.id as EditState['blur_type'], blur_intensity: item.id === 'none' ? 0 : Math.max(e.blur_intensity, 35) } : e)}
                    className={`px-2 py-1 rounded text-[10px] transition-colors ${edit.blur_type === item.id ? 'bg-accent text-white' : 'bg-bg-surface text-text-muted hover:text-white'}`}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
              <Slider label="Intensidade do desfoque" value={edit.blur_intensity} min={0} max={100} onChange={set('blur_intensity')} />
              <div className="grid grid-cols-3 gap-1">
                {BLUR_DIRECTIONS.map((item) => (
                  <button
                    key={item.id}
                    onClick={() => setEdit((e) => e ? { ...e, blur_direction: item.id as EditState['blur_direction'] } : e)}
                    disabled={edit.blur_type === 'pixelate'}
                    className={`px-2 py-1 rounded text-[10px] transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${edit.blur_direction === item.id ? 'bg-accent text-white' : 'bg-bg-surface text-text-muted hover:text-white'}`}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
              <Divider />
              <button
                onClick={() => setEdit((e) => e ? {
                  ...e, brightness: 0, contrast: 0, saturation: 0,
                  temperature: 0, hue: 0, exposure: 0, sharpness: 0, vignette: 0,
                  blur_type: 'none', blur_intensity: 0, blur_direction: 'both',
                } : e)}
                className="flex items-center gap-1.5 text-text-muted hover:text-white transition-colors text-[10px] w-full justify-center py-1"
              >
                <RotateCcw size={10} /> Resetar ajustes
              </button>
            </>
          )
        )}

        {/* RECORTE */}
        {activeTab === 'recorte' && (
          isAudio ? <Unavailable /> : (
            <>
              {/* Visual crop preview */}
              <div className="relative w-full aspect-video bg-bg-surface rounded border border-border mb-1 overflow-hidden">
                <div className="absolute inset-0" style={{
                  background: `linear-gradient(to bottom,
                    rgba(139,107,255,0.25) ${edit.crop_top_pct}%,
                    transparent ${edit.crop_top_pct}%,
                    transparent ${100 - edit.crop_bottom_pct}%,
                    rgba(139,107,255,0.25) ${100 - edit.crop_bottom_pct}%)`
                }} />
                <div className="absolute inset-0" style={{
                  background: `linear-gradient(to right,
                    rgba(139,107,255,0.25) ${edit.crop_left_pct}%,
                    transparent ${edit.crop_left_pct}%,
                    transparent ${100 - edit.crop_right_pct}%,
                    rgba(139,107,255,0.25) ${100 - edit.crop_right_pct}%)`
                }} />
                <span className="absolute inset-0 flex items-center justify-center text-[9px] text-text-dim">
                  Preview recorte
                </span>
              </div>
              <Slider label="Topo"  value={edit.crop_top_pct}    min={0} max={50} unit="%" onChange={set('crop_top_pct')} />
              <Slider label="Base"  value={edit.crop_bottom_pct} min={0} max={50} unit="%" onChange={set('crop_bottom_pct')} />
              <Slider label="Esq."  value={edit.crop_left_pct}   min={0} max={50} unit="%" onChange={set('crop_left_pct')} />
              <Slider label="Dir."  value={edit.crop_right_pct}  min={0} max={50} unit="%" onChange={set('crop_right_pct')} />
              <Divider />
              <button
                onClick={() => setEdit((e) => e ? { ...e, crop_top_pct: 0, crop_bottom_pct: 0, crop_left_pct: 0, crop_right_pct: 0 } : e)}
                className="flex items-center gap-1.5 text-text-muted hover:text-white transition-colors text-[10px] w-full justify-center py-1"
              >
                <RotateCcw size={10} /> Resetar recorte
              </button>
            </>
          )
        )}

        {/* VELOCIDADE */}
        {activeTab === 'velocidade' && (
          <>
            <Slider
              label="Velocidade"
              value={edit.speed_factor * 100}
              min={10} max={400} unit="%"
              onChange={(v) => setEdit((e) => e ? { ...e, speed_factor: v / 100 } : e)}
            />
            <p className="text-text-dim text-[10px] text-center tabular-nums">
              {edit.speed_factor.toFixed(2)}× velocidade
            </p>
            <div className="grid grid-cols-3 gap-1 pt-1">
              {SPEED_PRESETS.map((spd) => {
                const active = Math.abs(edit.speed_factor - spd) < 0.01
                return (
                  <button
                    key={spd}
                    onClick={() => setEdit((e) => e ? { ...e, speed_factor: spd } : e)}
                    className={`py-1.5 rounded-lg text-[10px] font-medium transition-colors ${
                      active ? 'bg-accent text-white' : 'bg-bg-surface text-text-muted hover:text-white hover:bg-border'
                    }`}
                  >
                    {spd}×
                  </button>
                )
              })}
            </div>
            <Divider />
            <Row label="Duração real">
              <Chip>{(parseFloat(dur) / edit.speed_factor).toFixed(2)}s</Chip>
            </Row>
          </>
        )}

        {/* ANIMACAO - clip enter/exit animations (CapCut "Animacao" tab) */}
        {activeTab === 'animacao' && (
          isAudio ? <Unavailable /> : (
            <>
              <p className="text-[10px] text-text-dim uppercase tracking-wider">Entrada</p>
              <Row label="Tipo">
                <select
                  value={edit.animation_in}
                  onChange={(e) => set('animation_in')(e.target.value)}
                  className="bg-bg-surface text-white text-[10px] px-1.5 py-0.5 rounded border border-border focus:outline-none focus:border-accent w-32"
                >
                  {ANIMATION_OPTIONS.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                </select>
              </Row>
              {edit.animation_in !== 'none' && (
                <Slider label="Duração entrada" value={edit.animation_in_duration_s}
                        min={0.1} max={2} step={0.1} unit="s"
                        onChange={set('animation_in_duration_s')} />
              )}
              <Divider />
              <p className="text-[10px] text-text-dim uppercase tracking-wider">Saída</p>
              <Row label="Tipo">
                <select
                  value={edit.animation_out}
                  onChange={(e) => set('animation_out')(e.target.value)}
                  className="bg-bg-surface text-white text-[10px] px-1.5 py-0.5 rounded border border-border focus:outline-none focus:border-accent w-32"
                >
                  {ANIMATION_OPTIONS.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                </select>
              </Row>
              {edit.animation_out !== 'none' && (
                <Slider label="Duração saída" value={edit.animation_out_duration_s}
                        min={0.1} max={2} step={0.1} unit="s"
                        onChange={set('animation_out_duration_s')} />
              )}
              <Divider />
              <button
                onClick={() => setEdit((e) => e ? {
                  ...e, animation_in: 'none', animation_out: 'none',
                  animation_in_duration_s: 0.5, animation_out_duration_s: 0.5,
                } : e)}
                className="flex items-center gap-1.5 text-text-muted hover:text-white transition-colors text-[10px] w-full justify-center py-1"
              >
                <RotateCcw size={10} /> Resetar animações
              </button>
            </>
          )
        )}

        {/* AVANÇADO */}
        {activeTab === 'avancado' && (
          isAudio ? <Unavailable /> : (
            <>
              <Slider label="Rotação" value={edit.rotation_deg} min={-180} max={180} unit="°" onChange={set('rotation_deg')} zeroed />
              <Divider />
              <div className="space-y-1.5">
                <p className="text-[10px] text-text-dim uppercase tracking-wider">Modo de mistura</p>
                <select
                  value={edit.blend_mode}
                  onChange={(e) => set('blend_mode')(e.target.value)}
                  className="w-full bg-bg-surface text-white text-[11px] px-2 py-1.5 rounded-lg border border-border focus:outline-none focus:border-accent cursor-pointer"
                >
                  {BLEND_MODES.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </div>
              <Divider />
              <button
                onClick={() => setEdit((e) => e ? { ...e, rotation_deg: 0, blend_mode: 'Normal' } : e)}
                className="flex items-center gap-1.5 text-text-muted hover:text-white transition-colors text-[10px] w-full justify-center py-1"
              >
                <RotateCcw size={10} /> Resetar transformação
              </button>
              <Divider />
              {/* Chroma Key */}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <p className="text-[10px] text-text-dim uppercase tracking-wider">Chroma Key</p>
                  <button
                    onClick={() => setEdit((e) => e ? { ...e, chroma_enabled: !e.chroma_enabled } : e)}
                    className={`relative inline-flex h-4 w-7 items-center rounded-full transition-colors ${
                      edit.chroma_enabled ? 'bg-accent' : 'bg-bg-surface border border-border'
                    }`}
                    title={edit.chroma_enabled ? 'Desativar chroma key' : 'Ativar chroma key'}
                  >
                    <span className={`inline-block h-3 w-3 rounded-full bg-white shadow transition-transform ${
                      edit.chroma_enabled ? 'translate-x-3.5' : 'translate-x-0.5'
                    }`} />
                  </button>
                </div>
                {edit.chroma_enabled && (
                  <>
                    <Row label="Cor-chave">
                      <input
                        type="color"
                        value={edit.chroma_color}
                        onChange={(e) => setEdit((prev) => prev ? { ...prev, chroma_color: e.target.value } : prev)}
                        className="w-7 h-7 rounded cursor-pointer border border-border"
                        title="Cor a remover (normalmente verde)"
                      />
                    </Row>
                    <Slider
                      label="Tolerância"
                      value={edit.chroma_tolerance}
                      min={1} max={100} unit="%"
                      onChange={set('chroma_tolerance')}
                    />
                  </>
                )}
              </div>
            </>
          )
        )}
      </div>
    </div>
  )
}

// ── Sub-components ──────────────────────────────────────────────────────────

function Slider({ label, value, min, max, unit = '', onChange, zeroed = false, step = 1 }: {
  label: string; value: number; min: number; max: number
  unit?: string; onChange: (v: number) => void; zeroed?: boolean
  /** Resolution of the slider — pass 0.1 for fade times, etc. */
  step?: number
}) {
  const isZero = zeroed && Math.abs(value) < 0.5
  const displayValue = step < 1 ? value.toFixed(1) : value.toFixed(0)
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <span className="text-text-muted">{label}</span>
        <span className={`tabular-nums text-[10px] ${isZero ? 'text-text-dim' : 'text-white'}`}>
          {displayValue}{unit}
        </span>
      </div>
      <input
        type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full cursor-pointer"
      />
    </div>
  )
}

function Toggle({ label, value, onChange }: {
  label: string; value: boolean; onChange: (v: boolean) => void
}) {
  return (
    <div
      className="flex items-center justify-between cursor-pointer select-none"
      onClick={() => onChange(!value)}
    >
      <span className="text-text-muted">{label}</span>
      <div
        className={`w-7 h-4 rounded-full transition-colors relative ${value ? 'bg-accent' : 'bg-bg-surface'}`}
      >
        <div
          className="absolute top-0.5 w-3 h-3 rounded-full bg-white transition-all"
          style={{ left: value ? 14 : 2 }}
        />
      </div>
    </div>
  )
}

/** Convert volume percentage (0..200) to a perceptual dB value.
 *  100% → 0 dB, 200% → +6 dB, 50% → -6 dB, 0% → -∞ (we cap at -60).
 *  CapCut displays volume in dB; this matches its scale. */
function volumeDb(pct: number): number {
  if (pct <= 0) return -60
  // 20 * log10(pct/100) — standard amplitude→dB
  return 20 * Math.log10(pct / 100)
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-text-muted">{label}</span>
      {children}
    </div>
  )
}

function Chip({ children }: { children: React.ReactNode }) {
  return <span className="text-white bg-bg-surface px-2 py-0.5 rounded text-[10px]">{children}</span>
}

function Divider() {
  return <div className="border-t border-border" />
}

function Unavailable() {
  return <p className="text-text-dim text-[10px] text-center pt-6 pb-4">Não disponível para este tipo de clipe</p>
}

// Number input that accepts negative values via intermediate "-" string state.
// Native type="number" with parseFloat falls back to 0 on NaN, which erases the
// leading "-" before the user can type a digit. This keeps a local string while
// editing so the dash is preserved.
function SignedNumberInput({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  const [text, setText] = useState(String(Math.round(value)))
  const [editing, setEditing] = useState(false)
  useEffect(() => { if (!editing) setText(String(Math.round(value))) }, [value, editing])
  return (
    <input
      type="text"
      inputMode="numeric"
      value={text}
      onFocus={() => setEditing(true)}
      onChange={(e) => {
        const v = e.target.value
        setText(v)
        if (v === '' || v === '-') return
        const n = parseFloat(v)
        if (!isNaN(n)) onChange(n)
      }}
      onBlur={() => {
        setEditing(false)
        const n = parseFloat(text)
        if (isNaN(n)) setText(String(Math.round(value)))
        else { onChange(n); setText(String(Math.round(n))) }
      }}
      className="w-14 bg-bg-surface text-white text-[10px] px-1.5 py-0.5 rounded border border-border focus:outline-none focus:border-accent"
    />
  )
}
