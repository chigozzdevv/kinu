export function normaliseApiOrigin(value: string, development = false): string {
  if (!value.trim()) return ""
  const url = new URL(value)
  if (url.username || url.password || url.pathname !== "/" || url.search || url.hash ||
      !(url.protocol === "https:" || development && url.protocol === "http:" && url.hostname === "localhost")) {
    throw new Error("API URL must be an HTTPS origin")
  }
  return url.origin
}

export const apiOrigin = normaliseApiOrigin(import.meta.env?.VITE_API_BASE_URL ?? "", import.meta.env?.DEV ?? false)
export const apiCredentials: RequestCredentials = apiOrigin ? "include" : "same-origin"

export function apiUrl(path: string): string {
  if (!path.startsWith("/") || path.startsWith("//") || /[\\\r\n]/.test(path)) throw new Error("Use a local API path")
  return apiOrigin + path
}

export function browserGatewayUrl(gateway: string, frontendOrigin: string, configuredOrigin = apiOrigin): string {
  const url = new URL(gateway)
  if (url.username || url.password || !/^\/browser-gateway\/?$/.test(url.pathname) || url.origin !== (configuredOrigin || frontendOrigin)) throw new Error("Browser gateway must use the configured Kinu API")
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:"
  url.pathname = `${url.pathname.replace(/\/$/, "")}/v1/setup/live`
  url.search = ""
  url.hash = ""
  return url.toString()
}
