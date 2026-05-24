import { FolderOpen, Music, Type, Sparkles, ArrowLeftRight, SlidersHorizontal } from 'lucide-react'
import { useStore } from '../../store/useStore'
import { MediaTab } from './MediaTab'
import { AudioTab } from './AudioTab'
import { TextTab } from './TextTab'
import { EffectsTab } from './EffectsTab'
import { TransitionsTab } from './TransitionsTab'
import { AdjustTab } from './AdjustTab'

const TABS = [
  { id: 'media',       icon: <FolderOpen size={18} />,        label: 'Mídia' },
  { id: 'audio',       icon: <Music size={18} />,             label: 'Áudio' },
  { id: 'text',        icon: <Type size={18} />,              label: 'Texto' },
  { id: 'effects',     icon: <Sparkles size={18} />,          label: 'Efeitos' },
  { id: 'transitions', icon: <ArrowLeftRight size={18} />,    label: 'Tran.' },
  { id: 'adjust',      icon: <SlidersHorizontal size={18} />, label: 'Ajuste' },
]

export function LeftRail({ panel = false, width }: { panel?: boolean; width?: number }) {
  const { activeLeftTab, setActiveLeftTab } = useStore()

  const renderContent = () => {
    switch (activeLeftTab) {
      case 'media':       return <MediaTab />
      case 'audio':       return <AudioTab />
      case 'text':        return <TextTab />
      case 'effects':     return <EffectsTab />
      case 'transitions': return <TransitionsTab />
      case 'adjust':      return <AdjustTab />
      default:            return <MediaTab />
    }
  }

  return (
    <aside
      className={`flex flex-col bg-bg-rail border-r border-border flex-shrink-0 ${panel ? 'w-full h-full rounded-lg border border-border overflow-hidden' : ''}`}
      style={!panel ? { width: width ?? 224 } : undefined}
    >
      {/* Tab bar — 2x3 grid */}
      <div className={`${panel ? 'grid grid-cols-6' : 'grid grid-cols-3'} border-b border-border`}>
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveLeftTab(tab.id)}
            className={`flex flex-col items-center justify-center gap-1 ${panel ? 'py-2 text-[9px]' : 'py-2.5 text-[10px]'} transition-colors ${
              activeLeftTab === tab.id
                ? 'text-accent bg-bg-surface border-b-2 border-accent'
                : 'text-text-muted hover:text-white hover:bg-bg-surface'
            }`}
          >
            {tab.icon}
            <span>{tab.label}</span>
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto">{renderContent()}</div>
    </aside>
  )
}
