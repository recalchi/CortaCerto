import { useState, useEffect } from 'react'
import { Loader2, RefreshCw, Cpu } from 'lucide-react'
import { useStore, ExportSettings } from '../../store/useStore'
import { api } from '../../api/client'

export function AdjustTab() {
  const { project, exportSettings, setExportSetting, setProject, setPreviewTime } = useStore()
  const { crf, preset, silenceEnabled, silenceStyle, platform } = exportSettings
  const [reanalyzing, setReanalyzing] = useState(false)
  const [reanalyzeError, setReanalyzeError] = useState<string | null>(null)
  const [encoderLabel, setEncoderLabel] = useState<string | null>(null)

  useEffect(() => {
    api.get('/api/encoder-info')
      .then((r) => setEncoderLabel(r.data.label))
      .catch(() => setEncoderLabel('CPU (x264)'))
  }, [])

  const set = <K extends keyof ExportSettings>(key: K) =>
    (val: ExportSettings[K]) => setExportSetting(key, val)

  const handleReanalyze = async () => {
    if (!project?.videoPath || reanalyzing) return
    setReanalyzing(true)
    setReanalyzeError(null)
    try {
      const res = await api.post('/api/open-project', {
        path: project.videoPath,
        silence_style: silenceEnabled ? silenceStyle : 'light',
      })
      setProject(res.data)
      setPreviewTime(0)
    } catch (e: any) {
      const detail = e?.response?.data?.detail ?? e?.message ?? 'Erro ao reanalisar'
      setReanalyzeError(typeof detail === 'string' ? detail.split('\n')[0] : 'Erro ao reanalisar')
    } finally {
      setReanalyzing(false)
    }
  }

  return (
    <div className="p-3 space-y-4 text-xs">
      <Section label="CORTE DE SILÊNCIO">
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={silenceEnabled}
            onChange={(e) => set('silenceEnabled')(e.target.checked)}
            className="accent-accent"
          />
          <span className="text-text-muted">Ativar corte automático</span>
        </label>
        <div className="flex gap-3 mt-2">
          {(['aggressive', 'natural', 'light'] as const).map((s) => (
            <label key={s} className={`flex items-center gap-1 cursor-pointer ${!silenceEnabled ? 'opacity-40 pointer-events-none' : ''}`}>
              <input
                type="radio"
                name="silenceStyle"
                value={s}
                checked={silenceStyle === s}
                onChange={() => set('silenceStyle')(s)}
                className="accent-accent"
              />
              <span className="text-[10px] text-text-muted">
                {s === 'aggressive' ? 'Agressivo' : s === 'natural' ? 'Natural' : 'Leve'}
              </span>
            </label>
          ))}
        </div>
        <p className="text-[10px] text-text-dim mt-1 leading-relaxed">
          {silenceStyle === 'aggressive'
            ? 'Corta pausas ≥ 0.15s — ideal para conteúdo rápido'
            : silenceStyle === 'natural'
            ? 'Corta pausas ≥ 0.4s mantendo ritmo natural'
            : 'Só remove silêncios longos ≥ 0.8s'}
        </p>

        {/* Reanalisar button */}
        {project && (
          <button
            onClick={handleReanalyze}
            disabled={reanalyzing}
            className="mt-2 w-full flex items-center justify-center gap-1.5 py-1.5 text-[11px] bg-bg-surface hover:bg-border disabled:opacity-50 disabled:cursor-wait text-text-muted hover:text-white rounded-lg transition-colors"
          >
            {reanalyzing
              ? <Loader2 size={11} className="animate-spin" />
              : <RefreshCw size={11} />
            }
            {reanalyzing ? 'Reanalisando…' : 'Reanalisar vídeo atual'}
          </button>
        )}
        {reanalyzeError && (
          <p className="text-[10px] text-red-400 mt-1">{reanalyzeError}</p>
        )}
      </Section>

      <Section label="PLATAFORMA ALVO">
        <div className="grid grid-cols-2 gap-1">
          {([
            ['youtube', 'YouTube (16:9)'],
            ['reels',   'Reels (9:16)'],
            ['tiktok',  'TikTok (9:16)'],
            ['shorts',  'Shorts (9:16)'],
          ] as const).map(([v, l]) => (
            <label key={v} className="flex items-center gap-1.5 cursor-pointer">
              <input
                type="radio"
                name="platform"
                value={v}
                checked={platform === v}
                onChange={() => set('platform')(v)}
                className="accent-accent"
              />
              <span className="text-[10px] text-text-muted">{l}</span>
            </label>
          ))}
        </div>
      </Section>

      <Section label="QUALIDADE DE EXPORTAÇÃO">
        {encoderLabel && (
          <div className="flex items-center gap-1.5 px-2 py-1 bg-bg-surface rounded-md mb-1">
            <Cpu size={10} className={encoderLabel.includes('CPU') ? 'text-text-dim' : 'text-green-400'} />
            <span className={`text-[9px] ${encoderLabel.includes('CPU') ? 'text-text-dim' : 'text-green-400'}`}>
              {encoderLabel}
            </span>
          </div>
        )}
        {/* CRF slider */}
        <div className="flex justify-between text-[10px] text-text-muted mb-1">
          <span>CRF (qualidade H.264)</span>
          <span className="font-mono text-accent">{crf}</span>
        </div>
        <input
          type="range" min={15} max={28} step={1} value={crf}
          onChange={(e) => set('crf')(+e.target.value)}
          className="w-full h-1 accent-accent cursor-pointer"
        />
        <div className="flex justify-between text-[10px] text-text-dim mt-1">
          <span>← Melhor qualidade</span>
          <span>Arquivo menor →</span>
        </div>

        {/* Preset selector */}
        <div className="mt-3 space-y-1">
          <p className="text-[10px] text-text-muted">Preset de codificação</p>
          <div className="grid grid-cols-4 gap-1">
            {(['ultrafast', 'fast', 'medium', 'slow'] as const).map((p) => (
              <button
                key={p}
                onClick={() => set('preset')(p)}
                className={`py-1 text-[10px] rounded transition-colors capitalize ${
                  preset === p
                    ? 'bg-accent text-white'
                    : 'bg-bg-surface text-text-muted hover:text-white hover:bg-border'
                }`}
                title={
                  p === 'ultrafast' ? 'Exportação muito rápida, arquivo maior' :
                  p === 'fast'      ? 'Equilíbrio recomendado (padrão)' :
                  p === 'medium'    ? 'Compressão melhor, um pouco mais lento' :
                                      'Máxima compressão, exportação lenta'
                }
              >
                {p === 'ultrafast' ? 'ultra' : p}
              </button>
            ))}
          </div>
          <p className="text-[10px] text-text-dim mt-1">
            {preset === 'ultrafast' ? 'Muito rápido — arquivo maior' :
             preset === 'fast'      ? 'Equilíbrio recomendado' :
             preset === 'medium'    ? 'Boa compressão, mais lento' :
                                      'Máxima compressão — muito lento'}
          </p>
        </div>
      </Section>
    </div>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <p className="text-[10px] text-text-dim uppercase tracking-wider">{label}</p>
      {children}
    </div>
  )
}
