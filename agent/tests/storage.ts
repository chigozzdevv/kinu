import { spawn } from "node:child_process"
import { fileURLToPath } from "node:url"
import { MongoMemoryReplSet } from "mongodb-memory-server"

const root = fileURLToPath(new URL("../../", import.meta.url))
const mongo = await MongoMemoryReplSet.create({ replSet: { count: 1 } })
try {
  const child = spawn("uv", ["run", "pytest", "server/tests/storage", ...process.argv.slice(2)], {
    cwd: root,
    env: {
      ...process.env,
      UV_CACHE_DIR: ".cache/uv", UV_PYTHON_INSTALL_DIR: ".cache/python",
      KINU_TEST_MONGO_URL: mongo.getUri(),
    },
    stdio: "inherit",
  })
  process.exitCode = await new Promise<number>((resolve, reject) => {
    child.once("error", reject)
    child.once("exit", (code) => resolve(code ?? 1))
  })
} finally {
  await mongo.stop()
}
