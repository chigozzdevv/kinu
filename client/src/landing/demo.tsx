import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from "react"
import overview from "./media/resend-run.png"
import "./demo.css"

// Coordinates are in pixels of the original run capture, including the phone views.
const sourceWidth = 3024, sourceHeight = 1718
type Frame = readonly [centerX: number, centerY: number, width: number]
const views = [
  { id: "overview", name: "Overview", desktop: [1512, 859, 3024], mobile: [1790, 914, 2010], alt: "Kinu rotation transcript showing Resend’s key form and Builder AI’s proposed action.", caption: "Kinu prepares a new sending key in the Create step." },
  { id: "browser", name: "Browser", desktop: [2035, 876, 1130], mobile: [2035, 876, 570], alt: "Resend’s Add API Key form with Sending access selected for muwa.io.", caption: "Isolated browser capture with secrets redacted." },
  { id: "action", name: "Agent action", desktop: [1390, 1380, 1310], mobile: [1203, 1524, 600], alt: "Builder AI’s proposed click targets the Add button in Resend’s key form.", caption: "Builder AI proposes clicking Add in Resend." },
] as const

function camera([centerX, centerY, width]: Frame, aspect: number) {
  const height = width / aspect
  const x = Math.max(width / 2, Math.min(centerX, sourceWidth - width / 2))
  const y = Math.max(height / 2, Math.min(centerY, sourceHeight - height / 2))
  return `scale(${sourceWidth / width}) translate(${-100 * x / sourceWidth}%, ${-100 * y / sourceHeight}%)`
}

function PixelEdge({ corner }: { corner: "top" | "bottom" }) {
  return <svg className={`example-pixel-edge example-pixel-edge--${corner}`} viewBox="0 0 8 3" aria-hidden="true" focusable="false" shapeRendering="crispEdges">
    <path fill="currentColor" d="M0 0h1v1h1v1H0z M2 0h1v1H2z M3 1h3v1H3z" />
    <path fill="white" d="M7 2h1v1H7z" />
  </svg>
}

export default function Demo() {
  const [selected, setSelected] = useState(0)
  const [scrollDriven, setScrollDriven] = useState(false)
  const section = useRef<HTMLElement>(null)
  const track = useRef<HTMLDivElement>(null)
  const sticky = useRef<HTMLDivElement>(null)
  const viewer = useRef<HTMLDivElement>(null)
  const triggers = useRef<(HTMLButtonElement | null)[]>([])
  const view = views[selected]
  const style = {
    "--view-desktop": camera(view.desktop, sourceWidth / sourceHeight),
    "--view-mobile": camera(view.mobile, 5 / 4),
  } as CSSProperties

  useLayoutEffect(() => {
    if (!viewer.current || !sticky.current || !section.current) return
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)")
    function updateMode() {
      const styles = getComputedStyle(sticky.current!)
      const padding = Number.parseFloat(styles.paddingTop) + Number.parseFloat(styles.paddingBottom)
      const header = Number.parseFloat(getComputedStyle(section.current!).getPropertyValue("--new-header-height"))
      setScrollDriven(!motion.matches && viewer.current!.offsetHeight + padding < window.innerHeight - header)
    }

    const observer = new ResizeObserver(updateMode)
    observer.observe(viewer.current)
    window.addEventListener("resize", updateMode)
    motion.addEventListener("change", updateMode)
    updateMode()
    return () => {
      observer.disconnect()
      window.removeEventListener("resize", updateMode)
      motion.removeEventListener("change", updateMode)
    }
  }, [])

  useEffect(() => {
    if (!scrollDriven) return
    let frame: number | null = null
    function updateSelection() {
      frame = null
      const header = Number.parseFloat(getComputedStyle(section.current!).getPropertyValue("--new-header-height"))
      const step = (track.current!.offsetHeight - sticky.current!.offsetHeight) / views.length
      if (step <= 0) return
      const progress = (header - track.current!.getBoundingClientRect().top) / step
      setSelected(Math.max(0, Math.min(views.length - 1, Math.floor(progress))))
    }
    function scheduleUpdate() {
      if (frame === null) frame = window.requestAnimationFrame(updateSelection)
    }
    window.addEventListener("scroll", scheduleUpdate, { passive: true })
    window.addEventListener("resize", scheduleUpdate)
    updateSelection()
    return () => {
      window.removeEventListener("scroll", scheduleUpdate)
      window.removeEventListener("resize", scheduleUpdate)
      if (frame !== null) window.cancelAnimationFrame(frame)
    }
  }, [scrollDriven])

  function selectView(index: number) {
    setSelected(index)
    if (!scrollDriven) return
    const header = Number.parseFloat(getComputedStyle(section.current!).getPropertyValue("--new-header-height"))
    const step = (track.current!.offsetHeight - sticky.current!.offsetHeight) / views.length
    if (step <= 0) return
    const top = window.scrollY + track.current!.getBoundingClientRect().top - header
    window.scrollTo({ top: top + (index + 0.25) * step, behavior: "instant" })
  }

  function moveFocus(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const next = event.key === "ArrowDown" ? (index + 1) % views.length
      : event.key === "ArrowUp" ? (index + views.length - 1) % views.length
        : event.key === "Home" ? 0 : event.key === "End" ? views.length - 1 : null
    if (next === null) return
    event.preventDefault()
    triggers.current[next]?.focus({ preventScroll: true })
  }

  return <section ref={section} id="rotation-example" className="new-example" data-scroll-driven={scrollDriven} aria-labelledby="rotation-example-title">
    <PixelEdge corner="top" />
    <div className="demo-scroll-track" ref={track}>
      <div className="demo-sticky" ref={sticky}>
        <div className="demo-viewer" ref={viewer}>
          <div className="demo-controls">
            <h2 id="rotation-example-title">Inside a Resend key rotation.</h2>
            {views.map((item, index) => <div key={item.id} className="demo-row">
              <h3>
                <button ref={node => { triggers.current[index] = node }} type="button"
                  aria-expanded={index === selected} aria-disabled={index === selected}
                  aria-controls={`description-${item.id}`}
                  onClick={() => selectView(index)} onKeyDown={event => moveFocus(event, index)}>
                  {item.name}
                  <svg aria-hidden="true" width="14" height="14" viewBox="0 0 16 16" fill="none">
                    <path d="m4 6 4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>
              </h3>
              <p className="demo-caption" id={`description-${item.id}`} hidden={index !== selected}>{item.caption}</p>
            </div>)}
          </div>
          <div className="demo-stage" style={style}>
            <div className="demo-camera">
              <img src={overview} alt={view.alt} width={sourceWidth} height={sourceHeight} loading="lazy" decoding="async" />
            </div>
          </div>
        </div>
      </div>
    </div>
    <PixelEdge corner="bottom" />
  </section>
}
