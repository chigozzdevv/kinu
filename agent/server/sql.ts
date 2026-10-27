export function bindSql(sql: string, count: number): string {
  let result = "", index = 0, position = 0
  while (position < sql.length) {
    const rest = sql.slice(position)
    const token = /^(?:'(?:''|[^'])*'|"(?:""|[^"])*"|--[^\n]*(?:\n|$)|\/\*[\s\S]*?\*\/|\$([a-zA-Z_][a-zA-Z_0-9]*|)\$[\s\S]*?\$\1\$)/.exec(rest)?.[0]
    if (token) { result += token; position += token.length; continue }
    if (rest[0] === "?") { result += `:p${++index}`; position++; continue }
    if (/^\$\d+/.test(rest)) throw new Error("Use positional question marks for native storage")
    result += rest[0]
    position++
  }
  if (index !== count) throw new Error("Native SQL parameter count differs")
  return result
}
