import { useCallback, useEffect, useRef, useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { Check } from "lucide-react"
import { api } from "../lib/api"
import { readSession } from "../lib/auth"

export type BrowserSetupCapability = {
  gatewayUrl: string
  organisationId: string
  revision: number | null
  setupId: string
  token: string
  providerUrl: string
}

type SetupKey = "ArrowDown" | "ArrowLeft" | "ArrowRight" | "ArrowUp" | "Backspace" | "Enter" | "Tab"

type SetupAction =
  | { kind: "navigate"; url: string }
  | { kind: "click"; x: number; y: number }
  | { kind: "scroll"; delta_y: number }
  | { kind: "key"; key: SetupKey }

type BrowserSetupSessionProps = {
  capability: BrowserSetupCapability
  onComplete: () => void
}

function capabilityFromFragment(): BrowserSetupCapability {
  const fragment = new URLSearchParams(window.location.hash.slice(1))
  const revision = Number(fragment.get("revision"))
  return {
    gatewayUrl: fragment.get("gateway_url") ?? "",
    organisationId: fragment.get("organisation_id") ?? "",
    revision: Number.isSafeInteger(revision) && revision >= 0 ? revision : null,
    setupId: fragment.get("setup_id") ?? "",
    token: fragment.get("token") ?? "",
    providerUrl: fragment.get("provider_url") ?? "",
  }
}

function socketUrl(gatewayUrl: string): string {
  const url = new URL(gatewayUrl)
  if (url.origin !== window.location.origin) throw new Error("Browser gateway must share the Kinu origin")
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:"
  url.pathname = `${url.pathname.replace(/\/$/, "")}/v1/setup/live`
  url.search = ""
  url.hash = ""
  return url.toString()
}

function publicKeyFromPem(value: string): Promise<CryptoKey> {
  const encoded = value.replace(/-----BEGIN PUBLIC KEY-----|-----END PUBLIC KEY-----|\s/g, "")
  const bytes = Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0))
  return crypto.subtle.importKey("spki", bytes.buffer as ArrayBuffer, { name: "RSA-OAEP", hash: "SHA-256" }, false, ["encrypt"])
}

async function encrypt(value: string, key: CryptoKey): Promise<string> {
  const ciphertext = await crypto.subtle.encrypt({ name: "RSA-OAEP" }, key, new TextEncoder().encode(value))
  const bytes = new Uint8Array(ciphertext)
  let encoded = ""
  for (const byte of bytes) encoded += String.fromCharCode(byte)
  return btoa(encoded)
}

function send(socket: WebSocket | null, value: object) {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(value))
}

export function BrowserSetupSession({ capability, onComplete }: BrowserSetupSessionProps) {
  const { gatewayUrl, organisationId, revision, setupId, token, providerUrl } = capability
  const [frame, setFrame] = useState("")
  const [status, setStatus] = useState<"connecting" | "ready" | "closed">("connecting")
  const [error, setError] = useState("")
  const socket = useRef<WebSocket | null>(null)
  const key = useRef<CryptoKey | null>(null)
  const queuedInput = useRef("")
  const keyRequestPending = useRef(false)
  const secureSendChain = useRef(Promise.resolve())
  const frameUrl = useRef("")
  const frameTimer = useRef<number | undefined>(undefined)
  const keyboard = useRef<HTMLTextAreaElement | null>(null)
  const focusTimer = useRef<number | undefined>(undefined)
  const capturing = useRef(false)
  const completed = useRef(false)
  const aborting = useRef(false)
  const [focusPoint, setFocusPoint] = useState<{ x: number; y: number } | null>(null)
  const onCompleteRef = useRef(onComplete)
  const completeMutation = useRef<() => void>(() => undefined)
  onCompleteRef.current = onComplete

  const complete = useMutation({
    mutationFn: () => api.completeBrowserSetup(setupId, revision ?? -1, token),
    onSuccess: () => {
      completed.current = true
      onCompleteRef.current()
    },
  })
  completeMutation.current = () => complete.mutate()

  const requestFrame = useCallback(() => send(socket.current, { type: "frame" }), [])
  const requestStatus = useCallback(() => send(socket.current, { type: "status" }), [])

  const scheduleFrame = useCallback((delay = 120) => {
    if (frameTimer.current !== undefined) window.clearTimeout(frameTimer.current)
    frameTimer.current = window.setTimeout(() => {
      frameTimer.current = undefined
      requestFrame()
    }, delay)
  }, [requestFrame])

  const abort = useCallback(() => {
    if (completed.current || aborting.current || revision === null) return
    aborting.current = true
    void api.abortBrowserSetup(setupId, revision).catch(() => undefined)
  }, [revision, setupId])

  const sendAction = useCallback((action: SetupAction) => {
    send(socket.current, { type: "action", action })
  }, [])

  const sendSecureInput = useCallback((value: string) => {
    if (!value) return
    if (key.current === null) {
      queuedInput.current += value
      if (!keyRequestPending.current) {
        keyRequestPending.current = true
        send(socket.current, { type: "secure-key" })
      }
      return
    }
    const activeKey = key.current
    secureSendChain.current = secureSendChain.current.then(async () => {
      if (socket.current?.readyState !== WebSocket.OPEN) return
      send(socket.current, { type: "secure-input", ciphertext: await encrypt(value, activeKey) })
    }).catch(() => setError("Secure input could not be prepared. Close and try again."))
  }, [])

  useEffect(() => {
    if (!gatewayUrl || !organisationId || !setupId || !token || revision === null) {
      setStatus("closed")
      setError("This secure session is no longer available. Close and try again.")
      return
    }
    let active = true
    let interval: number | undefined
    let reconnectTimer: number | undefined
    let attempts = 0

    const clearTimers = () => {
      if (interval !== undefined) window.clearInterval(interval)
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer)
      if (frameTimer.current !== undefined) window.clearTimeout(frameTimer.current)
      interval = undefined
      reconnectTimer = undefined
      frameTimer.current = undefined
    }

    const connect = () => {
      if (!active) return
      let opened: WebSocket
      try {
        opened = new WebSocket(socketUrl(gatewayUrl))
      } catch {
        if (attempts < 15) {
          attempts += 1
          reconnectTimer = window.setTimeout(connect, 1500)
          return
        }
        setStatus("closed")
        setError("The secure browser could not connect. Close and try again.")
        return
      }
      socket.current = opened
      opened.binaryType = "arraybuffer"
      opened.onopen = async () => {
        if (!active || opened !== socket.current) return
        try {
          if (!await readSession()) throw new Error("Sign-in is required")
        } catch {
          if (!active) return
          setStatus("closed")
          setError("Your Kinu sign-in is no longer available. Close and sign in again.")
          opened.close(4401, "application identity unavailable")
          return
        }
        if (!active || opened.readyState !== WebSocket.OPEN) return
        opened.send(JSON.stringify({
          organisation_id: organisationId,
          setup_id: setupId,
          token,
        }))

        if (providerUrl) opened.send(JSON.stringify({ type: "action", action: { kind: "navigate", url: providerUrl } }))
        attempts = 0
        requestFrame()
        interval = window.setInterval(() => {
          requestFrame()
          requestStatus()
        }, 1500)
      }
      opened.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        const next = URL.createObjectURL(new Blob([event.data], { type: "image/png" }))
        if (frameUrl.current) URL.revokeObjectURL(frameUrl.current)
        frameUrl.current = next
        setFrame(next)
        setStatus("ready")
        return
      }
      try {
        const message = JSON.parse(String(event.data)) as { type?: string; public_key?: string; succeeded?: boolean; authenticated?: boolean }
        if (message.type === "secure-key" && message.public_key) {
          void publicKeyFromPem(message.public_key).then(async (imported) => {
            key.current = imported
            keyRequestPending.current = false
            const pending = queuedInput.current
            queuedInput.current = ""
            if (pending) sendSecureInput(pending)
          }).catch(() => {
            keyRequestPending.current = false
            setError("Secure input could not be prepared. Close and try again.")
          })
        }
        if ((message.type === "action" || message.type === "secure-input") && message.succeeded === false) setError("That interaction could not be completed. Try again.")
        if (message.type === "status" && message.authenticated && !capturing.current) {
          capturing.current = true
          completeMutation.current()
        }
        if (message.type === "action" || message.type === "secure-input") scheduleFrame()
      } catch {
        setError("Couldn’t read the secure session response. Close and try again.")
      }
      }
      opened.onclose = (event) => {
        if (!active || completed.current || opened !== socket.current) return
        clearTimers()
        if (event.code === 4401 || event.code === 4403) {
          setStatus("closed")
          setError("This secure session is not authorized. Close and sign in again.")
          return
        }
        if (attempts < 15) {
          attempts += 1
          setStatus("connecting")
          reconnectTimer = window.setTimeout(connect, 1500)
          return
        }
        setStatus("closed")
        setError("The secure browser could not connect. Close and try again.")
      }
      opened.onerror = () => {
        if (active && opened.readyState !== WebSocket.CLOSED) opened.close()
      }
    }
    connect()
    return () => {
      active = false
      clearTimers()
      socket.current?.close()
      socket.current = null

      if (frameUrl.current) URL.revokeObjectURL(frameUrl.current)
    }
  }, [gatewayUrl, organisationId, providerUrl, requestFrame, requestStatus, scheduleFrame, revision, sendSecureInput, setupId, token])

  useEffect(() => () => abort(), [abort])

  useEffect(() => () => {
    if (focusTimer.current !== undefined) window.clearTimeout(focusTimer.current)
  }, [])

  function clickFrame(event: React.PointerEvent<HTMLImageElement>) {
    if (event.pointerType === "mouse" && event.button !== 0) return
    event.preventDefault()
    const bounds = event.currentTarget.getBoundingClientRect()
    if (!bounds.width || !bounds.height) return
    const naturalWidth = event.currentTarget.naturalWidth || bounds.width
    const naturalHeight = event.currentTarget.naturalHeight || bounds.height
    const scale = Math.min(bounds.width / naturalWidth, bounds.height / naturalHeight)
    const renderedWidth = naturalWidth * scale
    const renderedHeight = naturalHeight * scale
    const offsetX = (bounds.width - renderedWidth) / 2
    const offsetY = 0
    const localX = event.clientX - bounds.left - offsetX
    const localY = event.clientY - bounds.top - offsetY
    if (localX < 0 || localY < 0 || localX > renderedWidth || localY > renderedHeight) return
    const x = Math.round((localX / renderedWidth) * 1000)
    const y = Math.round((localY / renderedHeight) * 1000)
    setFocusPoint({
      x: ((offsetX + localX) / bounds.width) * 100,
      y: ((offsetY + localY) / bounds.height) * 100,
    })
    if (focusTimer.current !== undefined) window.clearTimeout(focusTimer.current)
    focusTimer.current = window.setTimeout(() => {
      focusTimer.current = undefined
      setFocusPoint(null)
    }, 1200)
    setError("")
    sendAction({ kind: "click", x, y })
    keyboard.current?.focus({ preventScroll: true })
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.metaKey || event.ctrlKey || event.altKey) return
    if (event.key.length === 1) {
      event.preventDefault()
      event.stopPropagation()
      void sendSecureInput(event.key)
      return
    }
    const keys = new Set<SetupKey>(["ArrowDown", "ArrowLeft", "ArrowRight", "ArrowUp", "Backspace", "Enter", "Tab"])
    if (keys.has(event.key as SetupKey)) {
      event.preventDefault()
      event.stopPropagation()
      sendAction({ kind: "key", key: event.key as SetupKey })
    }
  }

  function handlePaste(event: React.ClipboardEvent<HTMLTextAreaElement>) {
    event.preventDefault()
    void sendSecureInput(event.clipboardData.getData("text"))
  }

  if (complete.isPending || complete.isSuccess) return <div className="grid min-h-[360px] place-items-center"><span className="grid size-10 place-items-center rounded-full bg-[var(--green-soft)] text-[var(--green)]"><Check className="size-4" /></span></div>

  return <div role="application" className="relative min-h-[360px] overflow-hidden rounded-xl border border-[var(--border)] bg-white outline-none" onWheel={(event) => { event.preventDefault(); sendAction({ kind: "scroll", delta_y: Math.max(-1200, Math.min(1200, Math.round(event.deltaY))) }) }} aria-label="Secure provider browser">
    {frame ? <div className="relative"><img src={frame} alt="Provider" className="block max-h-[calc(100vh-240px)] min-h-[360px] w-full select-none object-contain object-top" draggable={false} onPointerDown={clickFrame} />{focusPoint && <span aria-hidden="true" className="pointer-events-none absolute size-5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-[var(--accent)] bg-white/20 shadow-[0_0_0_3px_rgba(36,39,43,0.18)]" style={{ left: `${focusPoint.x}%`, top: `${focusPoint.y}%` }} />}</div> : <div className="grid min-h-[360px] place-items-center text-[12px] text-[var(--ink-muted)]">{status === "connecting" ? "Loading…" : "Unavailable"}</div>}
    <textarea ref={keyboard} aria-label="Secure provider keyboard input" readOnly tabIndex={0} className="pointer-events-none absolute left-0 top-0 size-px resize-none border-0 p-0 opacity-0" onKeyDown={handleKeyDown} onPaste={handlePaste} />
    {(error || complete.error) && <p role="alert" className="absolute inset-x-0 bottom-0 bg-white/90 px-5 py-3 text-center text-[11px] text-[var(--red)]">{error || (complete.error instanceof Error ? complete.error.message : "Couldn’t save this connection. Close and try again.")}</p>}
  </div>
}

export function BrowserSetupPage() {
  const [capability] = useState(capabilityFromFragment)
  useEffect(() => {
    window.history.replaceState({}, "", window.location.pathname)
  }, [])
  return <main className="grid min-h-screen place-items-center bg-[var(--workspace)] p-5 text-[var(--ink)]"><div className="w-full max-w-[680px]"><BrowserSetupSession capability={capability} onComplete={() => window.close()} /></div></main>
}
