import { useStore } from '../../store/useStore'

interface Preset {
  label:      string
  brightness: number
  contrast:   number
  saturation: number
}

const PRESETS: Preset[] = [
  { label: 'Vintage',  brightness: -5,  contrast: -10, saturation: -35 },
  { label: 'Vivid',    brightness:  5,  contrast:  20, saturation:  40 },
  { label: 'Cool',     brightness:  0,  contrast:   5, saturation: -15 },
  { label: 'Warm',     brightness:  5,  contrast:   5, saturation:  15 },
  { label: 'P&B',      brightness:  0,  contrast:  10, saturation: -100 },
  { label: 'Glow',     brightness: 20,  contrast:  -5, saturation:  10 },
  { label: 'Nítido',   brightness:  0,  contrast:  25, saturation:  10 },
  { label: 'Original', brightness:  0,  contrast:   0, saturation:   0 },
]

export function EffectsTab() {
  const { project, selectedClipId, updateClip } = useStore()

  const allClips = project
    ? [...project.video_track.clips, ...project.audio_track.clips,
       ...project.text_track.clips,  ...project.overlay_track.clips]
    : []
  const clip = allClips.find((c) => c.id === selectedClipId) ?? null

  const apply = (p: Preset) => {
    if (!clip) return
    updateClip(clip.id, {
      brightness: p.brightness,
      contrast:   p.contrast,
      saturation: p.saturation,
    })
  }

  const isActive = (p: Preset) =>
    clip &&
    Math.round(clip.brightness) === p.brightness &&
    Math.round(clip.contrast)   === p.contrast   &&
    Math.round(clip.saturation) === p.saturation

  return (
    <div className="p-3 space-y-3">
      {!clip && (
        <p className="text-[10px] text-text-dim text-center py-4">
          Selecione um clipe para aplicar efeitos
        </p>
      )}

      {/* Presets grid */}
      <div>
        <p className="text-[10px] text-text-dim uppercase tracking-wider mb-2">Predefinições</p>
        <div className="grid grid-cols-2 gap-1.5">
          {PRESETS.map((p) => (
            <button
              key={p.label}
              onClick={() => apply(p)}
              disabled={!clip}
              className={`py-2 text-xs rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                isActive(p)
                  ? 'bg-accent text-white'
                  : 'bg-bg-surface text-text-muted hover:text-white hover:bg-border'
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Manual sliders */}
      <div className="space-y-2.5 pt-1">
        <p className="text-[10px] text-text-dim uppercase tracking-wider">Ajuste Manual</p>

        {([
          ['Brilho',    'brightness',  -100, 100],
          ['Contraste', 'contrast',    -100, 100],
          ['Saturação', 'saturation',  -100, 100],
        ] as const).map(([label, key, min, max]) => {
          const val = clip ? (clip[key] ?? 0) : 0
          return (
            <div key={key} className="space-y-1">
              <div className="flex justify-between text-[10px] text-text-muted">
                <span>{label}</span>
                <span className={`font-mono ${val !== 0 ? 'text-accent' : ''}`}>{val > 0 ? `+${val}` : val}</span>
              </div>
              <input
                type="range" min={min} max={max} step={1}
                value={val}
                disabled={!clip}
                onChange={(e) => clip && updateClip(clip.id, { [key]: +e.target.value })}
                className="w-full h-1 accent-accent cursor-pointer disabled:opacity-40"
              />
            </div>
          )
        })}

        {clip && (
          <button
            onClick={() => updateClip(clip.id, { brightness: 0, contrast: 0, saturation: 0 })}
            className="w-full py-1.5 text-[10px] text-text-dim hover:text-white bg-bg-surface hover:bg-border rounded-lg transition-colors mt-1"
          >
            ↺ Resetar ajustes
          </button>
        )}
      </div>
    </div>
  )
}
