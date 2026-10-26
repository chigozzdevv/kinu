import { spawn } from "node:child_process"
import { fileURLToPath } from "node:url"
import { PGlite } from "@electric-sql/pglite"
import { PGLiteSocketServer } from "@electric-sql/pglite-socket"

const root = fileURLToPath(new URL("../../", import.meta.url))
const db = await PGlite.create()
const server = new PGLiteSocketServer({ db, port: 0, host: "127.0.0.1", maxConnections: 5 })
await server.start()
try {
  const child = spawn("uv", ["run", "pytest", "server/tests/storage"], {
    cwd: root,
    env: {
      ...process.env,
      UV_CACHE_DIR: ".cache/uv",
      UV_PYTHON_INSTALL_DIR: ".cache/python",
      KINU_TEST_DATABASE_URL: `postgresql://postgres:postgres@${server.getServerConn()}/postgres?sslmode=disable`,
    },
    stdio: "inherit",
  })
  process.exitCode = await new Promise<number>((resolve, reject) => {
    child.once("error", reject)
    child.once("exit", (code) => resolve(code ?? 1))
  })
} finally {
  await server.stop()
  await db.close()
}
