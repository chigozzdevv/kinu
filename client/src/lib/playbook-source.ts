function looksLikeJson(value: string) {
  return value.startsWith("{") || value.startsWith("[") && !/^\[[^\]\r\n]+\]\(/.test(value)
}

export function apiSourceError(source: string): string | undefined {
  const text = source.trim()
  const examples: string[] = []
  for (const match of text.matchAll(/```([^\r\n`]*)\r?\n([\s\S]*?)(?:```|$)/g)) {
    const language = match[1]!.trim().toLowerCase()
    const content = match[2]!.trim()
    if (language === "json" || !language && looksLikeJson(content)) examples.push(content)
  }
  if (!examples.length && looksLikeJson(text)) examples.push(text)
  for (const [index, example] of examples.entries()) {
    try {
      JSON.parse(example)
    } catch {
      const label = examples.length > 1 ? `JSON example ${index + 1}` : "JSON"
      return `${label} is invalid. Check quotes, commas, and brackets.`
    }
  }
}

const linkBoundary = /[\r\n]+|,\s*|\s+(?=https?:\/\/)/i

export function parseLinks(value: string): string[] {
  return [...new Set(value.split(linkBoundary).map((url) => url.trim()).filter(Boolean))]
}

export function linkError(value: string): string | undefined {
  if (!value.trim()) return undefined
  const links = parseLinks(value)
  if (links.length > 5) return "Add up to five documentation links."
  if (links.some((link) => !/^https:\/\/\S+$/i.test(link))) {
    return "Each link must be a public https:// address."
  }
}
