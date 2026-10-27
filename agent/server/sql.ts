export function bindSql(sql: string, count: number): string {
  let result = "", index = 0, position = 0
  let style: "question" | "ordinal" | undefined
  const ordinals = new Set<number>()
  while (position < sql.length) {
    const rest = sql.slice(position)
    const token = /^(?:'(?:''|[^'])*'|"(?:""|[^"])*"|--[^\n]*(?:\n|$)|\/\*[\s\S]*?\*\/|\$([a-zA-Z_][a-zA-Z_0-9]*|)\$[\s\S]*?\$\1\$)/.exec(rest)?.[0]
    if (token) { result += token; position += token.length; continue }
    if (rest[0] === "?") {
      if (style === "ordinal") throw new Error("Mixed native SQL parameter styles")
      style = "question"
      result += `:p${++index}`; position++; continue
    }
    const ordinal = /^\$(\d+)/.exec(rest)
    if (ordinal) {
      if (style === "question") throw new Error("Mixed native SQL parameter styles")
      const parameter = Number(ordinal[1])
      if (!Number.isSafeInteger(parameter) || parameter < 1 || parameter > count || ordinal[1]!.startsWith("0")) throw new Error("Native SQL parameter count differs")
      style = "ordinal"
      ordinals.add(parameter)
      result += `:p${parameter}`; position += ordinal[0].length; continue
    }
    result += rest[0]
    position++
  }
  if ((style === "ordinal" ? ordinals.size : index) !== count) throw new Error("Native SQL parameter count differs")
  return result
}
