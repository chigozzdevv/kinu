import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "")
  const apiTarget = env.KINU_DEV_API_URL || "http://127.0.0.1:8000"

  return {
    plugins: [react(), tailwindcss()],
    server: {
      host: "localhost",
      port: 5173,
      proxy: {
        "/v1": apiTarget,
        "/health": apiTarget,
        "/auth/": apiTarget,
        "/_agent-native": env.KINU_AGENT_URL || "http://127.0.0.1:8001",
      },
    },
  }
})
