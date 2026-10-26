import { spawn, type ChildProcess } from "node:child_process"
import { once } from "node:events"
import { createServer } from "node:net"
import { fileURLToPath } from "node:url"

export const root = fileURLToPath(new URL("../../", import.meta.url))

export async function freePort(): Promise<number> {
  const server = createServer()
  server.listen(0, "127.0.0.1")
  await once(server, "listening")
  const address = server.address()
  if (!address || typeof address === "string") throw new Error("Port allocation failed")
  await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()))
  return address.port
}

export function service(command: string, args: string[], cwd: string, env: NodeJS.ProcessEnv) {
  const child = spawn(command, args, { cwd, env, stdio: ["ignore", "pipe", "pipe"] })
  let output = ""
  let failure: Error | undefined
  child.on("error", (error) => { failure = error })
  for (const stream of [child.stdout, child.stderr]) {
    stream?.on("data", (data: Buffer) => { output = `${output}${data.toString()}`.slice(-8000) })
  }
  return {
    child,
    async ready(url: string) {
      for (let attempt = 0; attempt < 150; attempt++) {
        if (failure || child.exitCode !== null || child.signalCode !== null) {
          throw new Error(`Service stopped: ${failure?.message ?? output}`)
        }
        try {
          if ((await fetch(url, { signal: AbortSignal.timeout(500) })).ok) return
        } catch { /* The child may still be opening its listener. */ }
        await new Promise((resolve) => setTimeout(resolve, 100))
      }
      throw new Error(`Service did not become ready: ${output}`)
    },
  }
}

export async function stop(child: ChildProcess): Promise<void> {
  if (child.exitCode !== null || child.signalCode !== null) return
  const exited = once(child, "exit")
  child.kill("SIGTERM")
  const timer = setTimeout(() => child.kill("SIGKILL"), 3000)
  try { await exited } finally { clearTimeout(timer) }
}
