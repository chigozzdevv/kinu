type Point = { x: number; y: number }
type Box = Point & { w: number; h: number; cx: number; cy: number }
type Wire = { name: string; d: string; arrow: boolean }
export type DiagramLayout = {
  wires: Wire[]
  proposal: Point
  context: Point
}

function bounds(element: Element, origin: DOMRect): Box {
  const { left, top, width, height } = element.getBoundingClientRect()
  const x = left - origin.left
  const y = top - origin.top
  return { x, y, w: width, h: height, cx: x + width / 2, cy: y + height / 2 }
}

// Route right-angle connectors with a consistent bend radius, including short segments.
function route(points: Point[], radius = 10): string {
  let path = `M ${points[0].x} ${points[0].y}`
  for (let i = 1; i < points.length - 1; i++) {
    const previous = points[i - 1]
    const corner = points[i]
    const next = points[i + 1]
    const dx = corner.x - previous.x
    const dy = corner.y - previous.y
    const nextX = next.x - corner.x
    const nextY = next.y - corner.y
    // An interior segment is shared by two corners. An end segment belongs to just one.
    const incomingRoom = (Math.abs(dx) + Math.abs(dy)) / (i === 1 ? 1 : 2)
    const outgoingRoom = (Math.abs(nextX) + Math.abs(nextY)) / (i === points.length - 2 ? 1 : 2)
    const bend = Math.min(radius, incomingRoom, outgoingRoom)
    path += ` L ${corner.x - Math.sign(dx) * bend} ${corner.y - Math.sign(dy) * bend}`
    path += ` Q ${corner.x} ${corner.y} ${corner.x + Math.sign(nextX) * bend} ${corner.y + Math.sign(nextY) * bend}`
  }
  const end = points[points.length - 1]
  return `${path} L ${end.x} ${end.y}`
}

const point = (x: number, y: number): Point => ({ x, y })

export function measureDiagram(stage: HTMLElement): DiagramLayout {
  const origin = stage.getBoundingClientRect()
  const box = (selector: string) => bounds(stage.querySelector(selector)!, origin)
  const boxes = (selector: string) => Array.from(stage.querySelectorAll(selector), (el) => bounds(el, origin))
  const agent = box(".ki-builder")
  const resource = box(".ki-incident")
  const executor = box(".ki-browser")
  const sources = boxes(".ki-source")
  const steps = boxes(".ki-step-symbol")
  const first = steps[0]
  const footerY = first.y - 22
  const wires: Wire[] = []
  function add(name: string, points: Point[], radius = 10, arrow = true) {
    wires.push({ name, d: route(points, radius), arrow })
  }

  let proposal: Point
  let context: Point
  if (getComputedStyle(stage).display === "grid") {
    const inY = agent.cy
    const joinX = agent.x - 18
    sources.forEach((source, i) => {
      const points = [point(source.x + source.w, source.cy)]
      if (Math.abs(inY - source.cy) >= 12) points.push(point(joinX, source.cy), point(joinX, inY))
      points.push(point(agent.x - 2, inY))
      add(`source${i}`, points, 7)
    })
    add("resource", [point(agent.x + agent.w, agent.cy), point(resource.x - 2, resource.cy)])
    add("executor", [point(resource.x + resource.w, resource.cy), point(executor.x - 2, executor.cy)])

    // Keep the return port clear of the privacy caption, and enter the executor from its lower left.
    const outX = agent.x + agent.w - 12
    const inX = agent.x + 12
    const turnX = executor.x - 18
    const backX = executor.x + executor.w - 8
    const portY = executor.y + executor.h - 16
    const labelX = (agent.cx + executor.cx) / 2
    // Anchor both return lanes to the lifecycle row as the hero changes height.
    context = point(labelX, footerY - 26)
    proposal = point(labelX, context.y - 32)
    add("proposal", [point(outX, agent.y + agent.h), point(outX, proposal.y), point(turnX, proposal.y), point(turnX, portY), point(executor.x - 2, portY)])
    add("safe-context", [point(backX, executor.y + executor.h), point(backX, context.y), point(inX, context.y), point(inX, agent.y + agent.h + 2)])
    const outerX = stage.clientWidth + 10
    add("progress", [point(executor.x + executor.w, executor.cy), point(outerX, executor.cy), point(outerX, footerY), point(first.cx, footerY), point(first.cx, first.y - 2)])
  } else {
    sources.forEach((source, i) => {
      const joinY = source.y + source.h + 10
      add(`source${i}`, [point(source.cx, source.y + source.h), point(source.cx, joinY), point(agent.cx, joinY), point(agent.cx, agent.y - 2)], 0)
    })
    add("resource", [point(agent.cx, agent.y + agent.h), point(resource.cx, resource.y - 2)])
    add("executor", [point(resource.cx, resource.y + resource.h), point(executor.cx, executor.y - 2)])
    const left = stage.clientWidth < 560 ? 14 : 48
    const right = stage.clientWidth - left
    proposal = point(right, (agent.cy + executor.cy) / 2)
    context = point(left, proposal.y)
    add("proposal", [point(agent.x + agent.w, agent.cy), point(right, agent.cy), point(right, executor.cy), point(executor.x + executor.w + 2, executor.cy)])
    add("safe-context", [point(executor.x, executor.cy), point(left, executor.cy), point(left, agent.cy), point(agent.x - 2, agent.cy)])
    add("progress", [point(executor.cx, executor.y + executor.h + 4), point(executor.cx, footerY), point(first.cx, footerY), point(first.cx, first.y - 2)])
  }
  steps.slice(1).forEach((next, i) => {
    const previous = steps[i]
    add(`step${i}`, [point(previous.x + previous.w + 3, previous.cy), point(next.x - 4, next.cy)], 0, false)
  })
  return { wires, proposal, context }
}
