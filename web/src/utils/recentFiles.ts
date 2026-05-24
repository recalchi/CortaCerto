const KEY    = 'cortacerto_recent_files'
const MAX    = 10

export interface RecentFile {
  path:    string
  name:    string
  addedAt: number
  type:    'video' | 'image' | 'audio' | 'project'
}

export function getRecentFiles(): RecentFile[] {
  try {
    const raw: any[] = JSON.parse(localStorage.getItem(KEY) ?? '[]')
    // Back-compat: entries without `type` default to 'video'
    return raw.map((f) => ({ type: 'video', ...f }))
  } catch {
    return []
  }
}

export function addRecentFile(path: string, type: RecentFile['type'] = 'video'): void {
  const name = path.replace(/\\/g, '/').split('/').pop() ?? path
  const list  = getRecentFiles().filter((f) => f.path !== path)
  list.unshift({ path, name, addedAt: Date.now(), type })
  localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX)))
}

export function clearRecentFiles(): void {
  localStorage.removeItem(KEY)
}
