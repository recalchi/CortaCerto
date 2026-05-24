import { useState } from 'react'
import { useStore } from '../../store/useStore'

const TRANS = [
  'Corte', 'Fade', 'Dissolver', 'Slide →', 'Slide ←',
  'Zoom In', 'Zoom Out', 'Flash', 'Wipe H', 'Wipe V',
]

export function TransitionsTab() {
  const { project, selectedClipId, updateClip } = useStore()
  const [duration, setDuration] = useState(0.4)

  const allClips = project
    ? [...project.video_track.clips, ...project.audio_track.clips,
       ...project.text_track.clips,  ...project.overlay_track.clips]
    : []
  const clip = allClips.find((c) => c.id === selectedClipId) ?? null
  const activeTransition = clip?.transition ?? 'Corte'

  const applyToAll = () => {
    if (!project) return
    project.video_track.clips.forEach((c) => {
      updateClip(c.id, { transition: activeTransition })
    })
  }

  return (
    <div className="p-3 space-y-3">
      {!clip && (
        <p className="text-[10px] text-text-dim text-center py-4">
          Selecione um clipe para escolher a transição
        </p>
      )}

      {/* Transition grid */}
      <div className="grid grid-cols-2 gap-1.5">
        {TRANS.map((t) => (
          <button
            key={t}
            onClick={() => clip && updateClip(clip.id, { transition: t })}
            disabled={!clip}
            className={`py-1.5 text-xs rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
              activeTransition === t
                ? 'bg-accent text-white'
                : 'bg-bg-surface text-text-muted hover:text-white hover:bg-border'
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Duration slider */}
      <div className="space-y-1">
        <div className="flex justify-between text-[10px] text-text-muted">
          <span>Duração</span>
          <span className="font-mono text-accent">{duration.toFixed(1)}s</span>
        </div>
        <input
          type="range" min={0.1} max={2} step={0.1} value={duration}
          onChange={(e) => setDuration(+e.target.value)}
          className="w-full h-1 accent-accent cursor-pointer"
        />
      </div>

      {/* Apply to all */}
      <button
        onClick={applyToAll}
        disabled={!project || !clip}
        className="w-full py-2 text-xs text-white bg-bg-surface hover:bg-border disabled:opacity-40 disabled:cursor-not-allowed rounded-lg transition-colors"
        title="Aplica a transição selecionada a todos os clipes de vídeo"
      >
        Aplicar "{activeTransition}" a todos os clipes
      </button>

      {/* Info about current clip */}
      {clip && (
        <div className="bg-bg-surface rounded-lg px-2.5 py-2 border border-border">
          <p className="text-[10px] text-text-dim mb-0.5">Clipe selecionado</p>
          <p className="text-xs text-white truncate">{clip.label}</p>
          <p className="text-[10px] text-text-muted mt-0.5">
            Transição: <span className="text-accent">{activeTransition}</span>
          </p>
        </div>
      )}
    </div>
  )
}
