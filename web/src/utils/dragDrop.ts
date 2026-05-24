/**
 * dragDrop.ts — cross-runtime file path extraction from drag events.
 *
 * Different runtimes expose the local file path differently:
 *   • Electron / Tauri  → `(file as any).path`   (non-standard property injected by the runtime)
 *   • pywebview/WebView2 → `dataTransfer.getData('text/uri-list')` returns `file:///C:/...` URIs
 *   • Firefox on Linux   → same text/uri-list approach
 *
 * The function tries each method in order and returns the first non-empty result.
 * Returns '' if no path can be determined (browser sandbox, no drag from Explorer).
 */
export function extractDragFilePath(e: React.DragEvent): string {
  // ── Method 1: Electron / Tauri runtime ──────────────────────────────────────
  const file = e.dataTransfer.files[0]
  if (file) {
    const p = (file as any).path as string | undefined
    if (p && p.trim()) return p.trim()
  }

  // ── Method 2: text/uri-list (WebView2, Firefox, some Chromium builds) ───────
  // Windows Explorer drags produce one `file:///` URI per file.
  const uriList = e.dataTransfer.getData('text/uri-list')
  if (uriList) {
    const firstUri = uriList
      .split(/\r?\n/)
      .map((l) => l.trim())
      .find((l) => l && !l.startsWith('#'))
    if (firstUri) {
      const p = fileUriToPath(firstUri)
      if (p) return p
    }
  }

  // ── Method 3: text/plain ─────────────────────────────────────────────────────
  // Some DnD sources serialise the path as plain text.
  const text = e.dataTransfer.getData('text/plain')
  if (
    text &&
    text.trim() &&
    !text.includes('\n') &&
    !text.startsWith('http')
  ) {
    return text.trim()
  }

  return ''
}

/** Extract all file paths from a multi-file drag (returns '' for files without paths). */
export function extractDragAllFilePaths(e: React.DragEvent): string[] {
  const paths: string[] = []

  // Method 1: runtime-injected .path
  for (let i = 0; i < e.dataTransfer.files.length; i++) {
    const f = e.dataTransfer.files[i]
    const p = (f as any).path as string | undefined
    if (p && p.trim()) paths.push(p.trim())
  }
  if (paths.length > 0) return paths

  // Method 2: text/uri-list (one URI per line)
  const uriList = e.dataTransfer.getData('text/uri-list')
  if (uriList) {
    for (const line of uriList.split(/\r?\n/)) {
      const l = line.trim()
      if (!l || l.startsWith('#')) continue
      const p = fileUriToPath(l)
      if (p) paths.push(p)
    }
  }
  return paths
}

/** Convert a `file://` URI to a local OS path. */
function fileUriToPath(uri: string): string {
  if (!uri.startsWith('file://')) return ''
  try {
    // Remove the file:// prefix — leaves /C:/path (Windows) or /home/user/... (Unix)
    const decoded = decodeURIComponent(uri.replace(/^file:\/\//, ''))
    // Windows absolute paths start with /X:/ — strip the leading slash
    if (/^\/[A-Za-z]:[\\/]/.test(decoded)) {
      return decoded.slice(1).replace(/\//g, '\\')
    }
    return decoded
  } catch {
    return ''
  }
}
