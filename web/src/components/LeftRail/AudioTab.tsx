import { useState, useCallback } from 'react'
import { Music, Loader2, Volume2, VolumeX, Trash2 } from 'lucide-react'
import { useStore } from '../../store/useStore'
import { api } from '../../api/client'

const API = 'http://127.0.0.1:7472'

function probeAudioDuration(path: string): Promise<number> {
  return new Promise((resolve) => {
    const a = new Audio(`${API}/api/serve-file?path=${encodeURIComponent(path)}`)
    const cleanup = () => { a.src = '' }
    a.addEventListener('loadedmetadata', () => { resolve(a.duration); cleanup() }, { once: true })
    a.addEventListener('error',          () => { resolve(60);         cleanup() }, { once: true })
    a.load()
  })
}

async function fetchAudioWaveform(path: string): Promise<number[]> {
  try {
    const res = await fetch(`${API}/api/audio-waveform?path=${encodeURIComponent(path)}&bins=300`)
    if (!res.ok) return []
    const data = await res.json()
    return data.samples ?? []
  } catch {
    return []
  }
}

export function AudioTab() {
  const {
    project, updateClip, importAudio, deleteClip,
    trackStates, setTrackState,
    exportSettings, setExportSetting,
  } = useStore()

  const [importing, setImporting] = useState(false)
  const [error,     setError]     = useState<string | null>(null)

  const audioClips  = project?.audio_track.clips ?? []
  const audioMuted  = trackStates?.audio?.muted  ?? false
  const normalizeOn = exportSettings.normalizeAudio

  const handleImport = useCallback(async () => {
    if (!project) return
    setError(null)
    setImporting(true)
    try {
      const res  = await api.post('/api/open-file-dialog', { type: 'audio' })
      const path: string = res.data.path
      if (!path) return
      const [dur, waveform] = await Promise.all([
        probeAudioDuration(path),
        fetchAudioWaveform(path),
      ])
      importAudio(path, dur, waveform)
    } catch {
      setError('Falha ao importar áudio')
    } finally {
      setImporting(false)
    }
  }, [project, importAudio])

  return (
    <div className="p-3 space-y-4 text-xs">
      {/* Import button */}
      <button
        onClick={handleImport}
        disabled={!project || importing}
        className="w-full flex items-center gap-2 px-3 py-2 bg-bg-surface hover:bg-border disabled:opacity-50 disabled:cursor-not-allowed text-text-muted hover:text-white rounded-lg transition-colors"
        title={!project ? 'Abra um vídeo primeiro' : 'Importar trilha de áudio'}
      >
        {importing
          ? <Loader2 size={13} className="animate-spin" />
          : <Music size={13} />
        }
        {importing ? 'Importando…' : 'Importar Música / Áudio'}
      </button>

      {error && (
        <p className="text-[10px] text-red-400">{error}</p>
      )}

      {/* Master mute toggle */}
      <div className="flex items-center justify-between">
        <span className="text-[10px] text-text-dim uppercase tracking-wider">Faixa de Áudio</span>
        <button
          onClick={() => setTrackState('audio', { muted: !audioMuted })}
          className={`flex items-center gap-1 text-[10px] px-2 py-0.5 rounded transition-colors ${
            audioMuted ? 'bg-red-900/40 text-red-400' : 'bg-bg-surface text-text-muted hover:text-white'
          }`}
        >
          {audioMuted ? <VolumeX size={11} /> : <Volume2 size={11} />}
          {audioMuted ? 'Mudo' : 'Ativo'}
        </button>
      </div>

      {/* Audio clip list */}
      {audioClips.length === 0 ? (
        <p className="text-[10px] text-text-dim text-center py-3">
          Nenhuma trilha importada
        </p>
      ) : (
        <div className="space-y-2">
          {audioClips.map((clip) => (
            <div
              key={clip.id}
              className="bg-bg-surface rounded-lg p-2.5 border border-border space-y-2"
            >
              <div className="flex items-start justify-between gap-1">
                <div className="min-w-0">
                  <p className="text-[11px] text-white font-medium truncate">{clip.label}</p>
                  <p className="text-[10px] text-text-dim tabular-nums">
                    {clip.start_s.toFixed(1)}s → {clip.end_s.toFixed(1)}s
                  </p>
                </div>
                <button
                  onClick={() => deleteClip(clip.id)}
                  className="text-text-dim hover:text-red-400 flex-shrink-0 transition-colors mt-0.5"
                  title="Remover"
                >
                  <Trash2 size={12} />
                </button>
              </div>

              {/* Volume slider */}
              <div className="space-y-1">
                <div className="flex justify-between text-[10px] text-text-muted">
                  <span>Volume</span>
                  <span className="font-mono text-accent">{clip.volume_pct ?? 100}%</span>
                </div>
                <input
                  type="range" min={0} max={200} step={1}
                  value={clip.volume_pct ?? 100}
                  onChange={(e) => updateClip(clip.id, { volume_pct: +e.target.value })}
                  className="w-full h-1 accent-accent cursor-pointer"
                />
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Normalize audio toggle */}
      <div className="border-t border-border pt-3 space-y-2">
        <p className="text-[10px] text-text-dim uppercase tracking-wider">Exportação</p>
        <label className="flex items-center justify-between cursor-pointer">
          <span className="text-text-muted">Normalizar áudio</span>
          <button
            onClick={() => setExportSetting('normalizeAudio', !normalizeOn)}
            className={`relative w-8 h-4 rounded-full transition-colors ${
              normalizeOn ? 'bg-accent' : 'bg-bg-surface border border-border'
            }`}
          >
            <span
              className={`absolute top-0.5 w-3 h-3 rounded-full bg-white shadow transition-transform ${
                normalizeOn ? 'translate-x-4' : 'translate-x-0.5'
              }`}
            />
          </button>
        </label>
        <p className="text-[10px] text-text-dim leading-relaxed">
          Equaliza volume entre clipes silenciosos e sonoros
        </p>
      </div>
    </div>
  )
}
