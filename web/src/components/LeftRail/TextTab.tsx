import { useState } from 'react'
import { Plus, Type, Wand2, Loader2 } from 'lucide-react'
import { useStore } from '../../store/useStore'
import type { Clip } from '../../store/useStore'
import { api } from '../../api/client'

// Named presets — quick-access buttons for common roles (title/caption/highlight)
const PRESETS: {
  label: string; desc: string
  text: string
  style: Partial<Clip>
}[] = [
  {
    label: 'Título',
    desc: 'Grande / negrito',
    text: 'Título',
    style: { text_size_pct: 180, text_color: '#ffffff', text_bold: true, text_position_y_pct: 20 },
  },
  {
    label: 'Legenda',
    desc: 'Médio / branco',
    text: 'Legenda',
    style: { text_size_pct: 100, text_color: '#ffffff', text_bold: false, text_position_y_pct: 82 },
  },
  {
    label: 'Destaque',
    desc: 'Colorido / sombra',
    text: 'Destaque!',
    style: { text_size_pct: 130, text_color: '#facc15', text_bold: true, text_position_y_pct: 50,
             text_shadow_enabled: true } as Partial<Clip>,
  },
]

// CapCut-style visual style swatches — each renders an "Aa" preview matching how the
// text will look when applied. Uses background pill, stroke, shadow, and color combos
// to mirror CapCut's "Estilo predefinido" grid.
const STYLE_SWATCHES: {
  id: string
  preview: { color: string; bg?: string; bgAlpha?: number; stroke?: string; shadow?: boolean; bold?: boolean }
  style: Partial<Clip>
}[] = [
  // Row 1: clean text variants
  { id: 'plain-white',
    preview: { color: '#ffffff' },
    style: { text_color: '#ffffff', text_bold: false, text_background_enabled: false } as any },
  { id: 'bold-white',
    preview: { color: '#ffffff', bold: true },
    style: { text_color: '#ffffff', text_bold: true, text_background_enabled: false } as any },
  { id: 'bold-shadow',
    preview: { color: '#ffffff', bold: true, shadow: true },
    style: { text_color: '#ffffff', text_bold: true, text_shadow_enabled: true,
             text_background_enabled: false } as any },
  { id: 'stroke-white',
    preview: { color: '#ffffff', bold: true, stroke: '#000000' },
    style: { text_color: '#ffffff', text_bold: true, text_stroke_enabled: true,
             text_stroke_color: '#000000', text_stroke_width: 3,
             text_background_enabled: false } as any },

  // Row 2: pill backgrounds (CapCut signature look)
  { id: 'white-on-black',
    preview: { color: '#ffffff', bg: '#000000', bgAlpha: 0.7, bold: true },
    style: { text_color: '#ffffff', text_bold: true, text_background_enabled: true,
             text_background_color: '#000000', text_background_alpha: 0.7 } as any },
  { id: 'black-on-yellow',
    preview: { color: '#000000', bg: '#facc15', bgAlpha: 1, bold: true },
    style: { text_color: '#000000', text_bold: true, text_background_enabled: true,
             text_background_color: '#facc15', text_background_alpha: 1 } as any },
  { id: 'white-on-red',
    preview: { color: '#ffffff', bg: '#dc2626', bgAlpha: 1, bold: true },
    style: { text_color: '#ffffff', text_bold: true, text_background_enabled: true,
             text_background_color: '#dc2626', text_background_alpha: 1 } as any },
  { id: 'white-on-blue',
    preview: { color: '#ffffff', bg: '#2563eb', bgAlpha: 1, bold: true },
    style: { text_color: '#ffffff', text_bold: true, text_background_enabled: true,
             text_background_color: '#2563eb', text_background_alpha: 1 } as any },

  // Row 3: vibrant colors (no background)
  { id: 'yellow',
    preview: { color: '#facc15', bold: true, shadow: true },
    style: { text_color: '#facc15', text_bold: true, text_shadow_enabled: true,
             text_background_enabled: false } as any },
  { id: 'red',
    preview: { color: '#ef4444', bold: true, shadow: true },
    style: { text_color: '#ef4444', text_bold: true, text_shadow_enabled: true,
             text_background_enabled: false } as any },
  { id: 'green',
    preview: { color: '#22c55e', bold: true, shadow: true },
    style: { text_color: '#22c55e', text_bold: true, text_shadow_enabled: true,
             text_background_enabled: false } as any },
  { id: 'magenta',
    preview: { color: '#ec4899', bold: true, shadow: true },
    style: { text_color: '#ec4899', text_bold: true, text_shadow_enabled: true,
             text_background_enabled: false } as any },

  // Row 4: more pills + accents
  { id: 'white-on-purple',
    preview: { color: '#ffffff', bg: '#7c3aed', bgAlpha: 1, bold: true },
    style: { text_color: '#ffffff', text_bold: true, text_background_enabled: true,
             text_background_color: '#7c3aed', text_background_alpha: 1 } as any },
  { id: 'white-on-green',
    preview: { color: '#ffffff', bg: '#16a34a', bgAlpha: 1, bold: true },
    style: { text_color: '#ffffff', text_bold: true, text_background_enabled: true,
             text_background_color: '#16a34a', text_background_alpha: 1 } as any },
  { id: 'cyan',
    preview: { color: '#06b6d4', bold: true, stroke: '#ffffff' },
    style: { text_color: '#06b6d4', text_bold: true, text_stroke_enabled: true,
             text_stroke_color: '#ffffff', text_stroke_width: 2,
             text_background_enabled: false } as any },
  { id: 'gold-shadow',
    preview: { color: '#fbbf24', bold: true, shadow: true, stroke: '#78350f' },
    style: { text_color: '#fbbf24', text_bold: true, text_stroke_enabled: true,
             text_stroke_color: '#78350f', text_stroke_width: 2,
             text_shadow_enabled: true,
             text_background_enabled: false } as any },
]

// Preset applied to every segment when generating auto-captions. Matches CapCut's
// "Legendas automáticas" default: white text with subtle dark pill, anchored low.
const CAPTION_STYLE: Partial<Clip> = {
  text_color:               '#ffffff',
  text_bold:                true,
  text_size_pct:            110,
  text_position_y_pct:      82,
  text_background_enabled:  true,
  text_background_color:    '#000000',
  text_background_alpha:    0.55,
  text_shadow_enabled:      false,
}

export function TextTab() {
  const { project, previewTime, addTextClip, setActiveLeftTab } = useStore()
  const [captionLoading, setCaptionLoading] = useState(false)
  const [captionError,   setCaptionError]   = useState<string | null>(null)

  const handleCreate = (text: string, style = {}) => {
    if (!project) return
    const startS = previewTime
    const endS   = Math.min(startS + 3, project.duration_s)
    addTextClip(startS, endS, text, style)
    // Switch to Inspector so the user can edit the new clip immediately
    setActiveLeftTab('media')
  }

  const handleAutoCaption = async () => {
    if (!project?.videoPath || captionLoading) return
    setCaptionLoading(true)
    setCaptionError(null)
    try {
      const res = await api.post('/api/transcribe', { path: project.videoPath })
      const segments = (res.data?.segments ?? []) as { start_s: number; end_s: number; text: string }[]
      if (segments.length === 0) {
        setCaptionError('Nenhuma fala detectada.')
        return
      }
      // Create one styled text clip per segment.
      for (const seg of segments) {
        if (!seg.text?.trim()) continue
        addTextClip(seg.start_s, seg.end_s, seg.text.trim(), CAPTION_STYLE)
      }
    } catch (e: any) {
      const detail = e?.response?.data?.detail ?? e?.message ?? 'Falha ao transcrever'
      setCaptionError(typeof detail === 'string' ? detail.split('\n')[0] : 'Falha ao transcrever')
    } finally {
      setCaptionLoading(false)
    }
  }

  return (
    <div className="p-3 space-y-3">
      {!project && (
        <p className="text-[10px] text-text-dim text-center py-4">
          Abra um vídeo primeiro para adicionar texto
        </p>
      )}

      {project && (
        <>
          {/* Quick create */}
          <button
            onClick={() => handleCreate('Novo Texto')}
            className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-accent hover:bg-accent-hover text-white text-xs font-medium rounded-lg transition-colors"
          >
            <Plus size={13} /> Criar Texto
          </button>

          {/* Current time hint */}
          <p className="text-[10px] text-text-dim text-center -mt-1">
            inserido em {previewTime.toFixed(2)}s · duração 3s
          </p>

          {/* Auto-captions (Whisper transcription → one styled text clip per segment) */}
          <button
            onClick={handleAutoCaption}
            disabled={captionLoading || !project?.videoPath}
            className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-bg-surface hover:bg-border disabled:opacity-60 disabled:cursor-wait text-white text-xs font-medium rounded-lg border border-border transition-colors"
          >
            {captionLoading
              ? <><Loader2 size={13} className="animate-spin" /> Transcrevendo…</>
              : <><Wand2 size={13} /> Legendas automáticas</>
            }
          </button>
          {captionError && (
            <p className="text-[10px] text-red-400 text-center -mt-1">{captionError}</p>
          )}

          {/* Named presets (title / caption / highlight) */}
          <div className="space-y-1.5">
            <p className="text-[10px] text-text-dim uppercase tracking-wider">Atalhos</p>
            {PRESETS.map((p) => (
              <button
                key={p.label}
                onClick={() => handleCreate(p.text, p.style)}
                className="w-full flex items-center justify-between px-3 py-2 bg-bg-surface hover:bg-border rounded-lg transition-colors group"
              >
                <div className="flex items-center gap-2">
                  <Type size={11} className="text-text-dim group-hover:text-accent transition-colors" />
                  <span
                    className="text-white"
                    style={{
                      fontSize:   (p.style.text_size_pct ?? 100) > 130 ? 13 : 11,
                      fontWeight: p.style.text_bold ? 700 : 400,
                      color:      p.style.text_color ?? '#ffffff',
                    }}
                  >
                    {p.label}
                  </span>
                </div>
                <span className="text-[10px] text-text-muted">{p.desc}</span>
              </button>
            ))}
          </div>

          {/* Visual style swatches (CapCut "Estilo predefinido" grid) */}
          <div className="space-y-1.5">
            <p className="text-[10px] text-text-dim uppercase tracking-wider">Estilos</p>
            <div className="grid grid-cols-4 gap-1.5">
              {STYLE_SWATCHES.map((s) => {
                const pv = s.preview
                const pill = !!pv.bg
                return (
                  <button
                    key={s.id}
                    onClick={() => handleCreate('Texto', s.style)}
                    title="Aplicar estilo"
                    className="aspect-square flex items-center justify-center bg-bg-surface hover:ring-2 hover:ring-accent rounded-md transition-all"
                  >
                    <span
                      style={{
                        fontSize:    16,
                        fontWeight:  pv.bold ? 800 : 500,
                        color:       pv.color,
                        background:  pill ? pv.bg : 'transparent',
                        padding:     pill ? '2px 6px' : 0,
                        borderRadius: pill ? 4 : 0,
                        opacity:     pv.bgAlpha ?? 1,
                        textShadow:  pv.shadow ? '1px 1px 2px rgba(0,0,0,0.8)' : undefined,
                        WebkitTextStroke: pv.stroke ? `1px ${pv.stroke}` : undefined,
                        lineHeight:  1,
                      }}
                    >
                      Aa
                    </span>
                  </button>
                )
              })}
            </div>
          </div>

          {/* Text clips in project */}
          {project.text_track.clips.length > 0 && (
            <div className="space-y-0.5">
              <p className="text-[10px] text-text-dim uppercase tracking-wider mt-2">Na timeline</p>
              {project.text_track.clips.map((c) => (
                <div
                  key={c.id}
                  className="flex items-center justify-between px-2 py-1.5 rounded text-left"
                >
                  <span className="text-[10px] text-text-muted truncate max-w-[110px]">
                    {c.text_overlay || c.label}
                  </span>
                  <span className="text-[10px] text-text-dim tabular-nums">
                    {c.start_s.toFixed(1)}–{c.end_s.toFixed(1)}s
                  </span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
