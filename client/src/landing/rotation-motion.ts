import { useEffect, useLayoutEffect, useRef, useState } from "react"
import type { RefObject } from "react"
import { measureDiagram } from "./rotation-paths"
import type { DiagramLayout } from "./rotation-paths"

const cycleLength = 20500
const createAt = 6500
const completedAt = 16500
const stepStarts = [createAt, 7500, 10000, 14000, completedAt]
const milestones = [0, 2450, ...stepStarts]
const transfers: [wire: string, start: number, duration: number][] = [
  ["source", 0, 950],
  ["resource", 1400, 700],
  ["executor", 2300, 650],
  ["safe-context", 3200, 700],
  ["proposal", 4200, 700],
  ["progress", createAt - 900, 900],
  ["step0", 7000, 500],
  ["step1", 9500, 500],
  ["safe-context", 12200, 700],
  ["proposal", 13200, 700],
  ["step2", 13500, 500],
  ["step3", 16000, 500],
]

type Packet = { element: SVGPathElement; length: number; visible: boolean }

export function useRotationMotion(stage: RefObject<HTMLDivElement | null>) {
  const [layout, setLayout] = useState<DiagramLayout | null>(null)
  const [playing, setPlaying] = useState(() => !matchMedia("(prefers-reduced-motion: reduce)").matches)
  const [milestone, setMilestone] = useState(playing ? 0 : completedAt)
  const elapsed = useRef(playing ? 0 : completedAt)

  useLayoutEffect(() => {
    const diagram = stage.current
    if (!diagram) return
    const measure = () => setLayout(measureDiagram(diagram))
    const observer = new ResizeObserver(measure)
    measure()
    observer.observe(diagram)
    document.fonts.addEventListener("loadingdone", measure)
    return () => {
      observer.disconnect()
      document.fonts.removeEventListener("loadingdone", measure)
    }
  }, [stage])

  useEffect(() => {
    const preference = matchMedia("(prefers-reduced-motion: reduce)")
    const syncPreference = () => {
      elapsed.current = preference.matches ? completedAt : 0
      setMilestone(elapsed.current)
      setPlaying(!preference.matches)
    }
    preference.addEventListener("change", syncPreference)
    return () => preference.removeEventListener("change", syncPreference)
  }, [])

  useEffect(() => {
    const diagram = stage.current
    if (!diagram || !layout) return
    const packets = new Map<string, Packet>()
    for (const element of diagram.querySelectorAll<SVGPathElement>(".ki-packet")) {
      const length = element.getTotalLength()
      element.setAttribute("stroke-dasharray", `18 ${length + 18}`)
      packets.set(element.dataset.wire!, { element, length, visible: false })
    }
    let lastMilestone = -1
    function draw() {
      const time = elapsed.current % cycleLength
      const cue = milestones.findLast((start) => time >= start)!
      if (cue !== lastMilestone) {
        lastMilestone = cue
        setMilestone(cue)
      }
      const active = new Set<string>()
      for (const [wire, start, duration] of transfers) {
        if (time < start || time > start + duration) continue
        const name = wire === "source" ? `source${Math.floor(elapsed.current / cycleLength) % 3}` : wire
        const packet = packets.get(name)
        if (!packet) continue
        active.add(name)
        packet.element.setAttribute("stroke-dashoffset", String(18 - (packet.length + 18) * (time - start) / duration))
        if (!packet.visible) packet.element.setAttribute("opacity", "1")
        packet.visible = true
      }
      for (const [name, packet] of packets) {
        if (packet.visible && !active.has(name)) {
          packet.element.setAttribute("opacity", "0")
          packet.visible = false
        }
      }
    }

    packets.forEach(({ element }) => element.setAttribute("opacity", "0"))
    draw()
    if (!playing) return

    let previous: number | undefined
    let frame = 0
    let onScreen = false
    function tick(now: number) {
      if (previous !== undefined) elapsed.current += Math.min(now - previous, 100)
      previous = now
      draw()
      frame = requestAnimationFrame(tick)
    }
    function syncPlayback() {
      cancelAnimationFrame(frame)
      previous = undefined
      if (onScreen && !document.hidden) frame = requestAnimationFrame(tick)
    }
    const observer = new IntersectionObserver(([entry]) => {
      onScreen = entry.isIntersecting
      syncPlayback()
    })
    observer.observe(diagram)
    document.addEventListener("visibilitychange", syncPlayback)
    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
      document.removeEventListener("visibilitychange", syncPlayback)
    }
  }, [layout, playing, stage])

  return {
    layout,
    currentStep: stepStarts.findLastIndex((start) => milestone >= start),
    hasReplacement: milestone >= createAt,
    revoked: milestone >= completedAt,
    policyReady: milestone >= 2450,
  }
}
