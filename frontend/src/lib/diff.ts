/**
 * Minimal unified-diff parser for rendering a generated patch.
 *
 * The backend stores the patch exactly as it was applied and committed, so
 * the dashboard parses that text rather than keeping a second, prettier
 * representation that could drift from what was actually committed.
 */

export interface DiffLine {
  type: 'context' | 'added' | 'removed'
  content: string
  oldLine: number | null
  newLine: number | null
}

export interface DiffHunk {
  header: string
  lines: DiffLine[]
}

export interface DiffFile {
  path: string
  hunks: DiffHunk[]
  added: number
  removed: number
}

const HUNK_HEADER = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/

function stripPrefix(path: string): string {
  return path.startsWith('a/') || path.startsWith('b/') ? path.slice(2) : path
}

export function parseDiff(diff: string | null | undefined): DiffFile[] {
  if (!diff) return []

  const files: DiffFile[] = []
  let file: DiffFile | null = null
  let hunk: DiffHunk | null = null
  let oldLine = 0
  let newLine = 0

  for (const line of diff.split('\n')) {
    if (line.startsWith('diff --git') || line.startsWith('--- ')) {
      // `+++` carries the post-image path, which is the one worth showing;
      // `---` only opens the header.
      continue
    }

    if (line.startsWith('+++ ')) {
      const path = stripPrefix(line.slice(4).trim())
      file = { path, hunks: [], added: 0, removed: 0 }
      files.push(file)
      hunk = null
      continue
    }

    const hunkMatch = HUNK_HEADER.exec(line)
    if (hunkMatch && file) {
      oldLine = Number(hunkMatch[1])
      newLine = Number(hunkMatch[2])
      hunk = { header: line, lines: [] }
      file.hunks.push(hunk)
      continue
    }

    if (!file || !hunk) continue

    if (line.startsWith('+')) {
      hunk.lines.push({ type: 'added', content: line.slice(1), oldLine: null, newLine })
      newLine += 1
      file.added += 1
    } else if (line.startsWith('-')) {
      hunk.lines.push({ type: 'removed', content: line.slice(1), oldLine, newLine: null })
      oldLine += 1
      file.removed += 1
    } else if (line.startsWith(' ') || line === '') {
      hunk.lines.push({ type: 'context', content: line.slice(1), oldLine, newLine })
      oldLine += 1
      newLine += 1
    }
    // "\ No newline at end of file" and anything else is not a content line.
  }

  return files
}

export function diffStats(files: DiffFile[]): { files: number; added: number; removed: number } {
  return {
    files: files.length,
    added: files.reduce((sum, f) => sum + f.added, 0),
    removed: files.reduce((sum, f) => sum + f.removed, 0),
  }
}
