import { useMemo, useState } from 'react'
import { Box, Search, Star, StarOff, Sticker } from 'lucide-react'
import { useStore } from '../../store/useStore'

const STICKER_FAVORITES_STORAGE_ID = 'cortacerto_sticker_favorites_v1'

type StickerItem = {
  id: string
  label: string
  category: 'popular' | 'classic' | 'novo' | 'meme' | 'emoji' | 'shape' | 'fire'
  emoji: string
  bg: string
  fg: string
}

const STICKERS: StickerItem[] = [
  { id: 'cat-wow',      label: 'Gato Wow',      category: 'meme',    emoji: '🙀', bg: '#111827', fg: '#ffffff' },
  { id: 'tv-noise',     label: 'Glitch TV',     category: 'popular', emoji: '📺', bg: '#1e293b', fg: '#22d3ee' },
  { id: 'scribble',     label: 'Rabisco',       category: 'shape',   emoji: '〰️', bg: '#111111', fg: '#e5e7eb' },
  { id: 'fire-border',  label: 'Borda Fogo',    category: 'fire',    emoji: '🔥', bg: '#7c2d12', fg: '#fde68a' },
  { id: 'flower-cat',   label: 'Cat Flor',      category: 'meme',    emoji: '😺', bg: '#334155', fg: '#f8fafc' },
  { id: 'smirk',        label: 'Sorriso',       category: 'emoji',   emoji: '😏', bg: '#1f2937', fg: '#facc15' },
  { id: 'mosaic',       label: 'Mosaico',       category: 'shape',   emoji: '◼️', bg: '#111827', fg: '#cbd5e1' },
  { id: 'face-lol',     label: 'Face LOL',      category: 'meme',    emoji: '🤣', bg: '#312e81', fg: '#f8fafc' },
  { id: 'circle-red',   label: 'Circulo',       category: 'shape',   emoji: '⭕', bg: '#111827', fg: '#ef4444' },
  { id: 'heart-pack',   label: 'Coraçoes',      category: 'emoji',   emoji: '💖', bg: '#1f2937', fg: '#f472b6' },
  { id: 'boom',         label: 'BOOM',          category: 'popular', emoji: '💥', bg: '#7c2d12', fg: '#fef3c7' },
  { id: 'thumb-up',     label: 'Like',          category: 'classic', emoji: '👍', bg: '#0f172a', fg: '#93c5fd' },
  { id: 'ok-sign',      label: 'OK',            category: 'classic', emoji: '👌', bg: '#111827', fg: '#86efac' },
  { id: 'eyes',         label: 'Olhos',         category: 'emoji',   emoji: '👀', bg: '#111827', fg: '#ffffff' },
  { id: 'x-mark',       label: 'Erro',          category: 'novo',    emoji: '❌', bg: '#1f2937', fg: '#f87171' },
]

const CATEGORY_LABELS: Record<string, string> = {
  all: 'Todos',
  favorites: 'Favoritos',
  popular: 'Populares',
  classic: 'Classico',
  novo: 'Novo',
  meme: 'Meme',
  emoji: 'Emoji',
  shape: 'Formas',
  fire: 'Fire',
}

function readFavorites(): string[] {
  try {
    const raw = localStorage.getItem(STICKER_FAVORITES_STORAGE_ID)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter((v) => typeof v === 'string') : []
  } catch {
    return []
  }
}

function writeFavorites(ids: string[]) {
  try {
    localStorage.setItem(STICKER_FAVORITES_STORAGE_ID, JSON.stringify(ids.slice(0, 160)))
  } catch {
    // ignore
  }
}

export function StickersTab() {
  const { project, previewTime, addStickerClip } = useStore()
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState<string>('all')
  const [favorites, setFavorites] = useState<string[]>(() => readFavorites())

  const categories = useMemo(
    () => ['all', 'favorites', ...Array.from(new Set(STICKERS.map((s) => s.category)))],
    [],
  )

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return STICKERS.filter((sticker) => {
      if (category === 'favorites' && !favorites.includes(sticker.id)) return false
      if (category !== 'all' && category !== 'favorites' && sticker.category !== category) return false
      if (!q) return true
      return sticker.label.toLowerCase().includes(q)
    })
  }, [category, favorites, search])

  const toggleFavorite = (id: string) => {
    const next = favorites.includes(id) ? favorites.filter((f) => f !== id) : [...favorites, id]
    setFavorites(next)
    writeFavorites(next)
  }

  const insertSticker = (sticker: StickerItem) => {
    if (!project) return
    const start = Math.max(0, previewTime)
    const end = Math.min(start + 2.8, Math.max(start + 0.1, project.duration_s || start + 2.8))
    addStickerClip(start, end, sticker)
  }

  return (
    <div className="p-3 space-y-3 text-xs">
      {!project && (
        <p className="text-[10px] text-text-dim text-center py-2">
          Abra um projeto para inserir figurinhas.
        </p>
      )}

      <div className="relative">
        <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-text-dim" />
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Pesquisar adesivos"
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
        <p className="text-[10px] uppercase tracking-wider text-text-dim mb-2 flex items-center gap-1.5">
          <Sticker size={10} /> Figurinhas
        </p>
        <div className="grid grid-cols-3 gap-2 max-h-[300px] overflow-y-auto pr-1">
          {filtered.map((sticker) => {
            const fav = favorites.includes(sticker.id)
            return (
              <div key={sticker.id} className="relative group">
                <button
                  onClick={() => insertSticker(sticker)}
                  disabled={!project}
                  className="w-full aspect-square rounded border border-border overflow-hidden hover:border-accent/60 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  title={`Inserir ${sticker.label}`}
                >
                  <div
                    className="w-full h-full flex items-center justify-center"
                    style={{
                      background: `linear-gradient(145deg, ${sticker.bg} 0%, #0b1020 100%)`,
                    }}
                  >
                    <div className="text-[34px] leading-none">{sticker.emoji}</div>
                  </div>
                  <div className="absolute inset-x-0 bottom-0 bg-black/55 px-1 py-0.5">
                    <div className="flex items-center justify-center gap-1">
                      <Box size={10} className="text-white/90" />
                      <span className="text-[9px] text-white truncate">{sticker.label}</span>
                    </div>
                  </div>
                </button>
                <button
                  onClick={() => toggleFavorite(sticker.id)}
                  className="absolute top-1 right-1 h-5 w-5 rounded-full bg-black/65 text-white/85 hover:text-yellow-300 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                  title={fav ? 'Remover dos favoritos' : 'Favoritar figurinha'}
                >
                  {fav ? <Star size={11} fill="currentColor" /> : <StarOff size={11} />}
                </button>
              </div>
            )
          })}
        </div>
      </div>

      {category === 'favorites' && filtered.length === 0 && (
        <p className="text-[10px] text-text-dim text-center py-1">
          Nenhuma figurinha favoritada.
        </p>
      )}
    </div>
  )
}
