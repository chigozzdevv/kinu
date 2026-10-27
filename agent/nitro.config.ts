import { defineConfig } from "nitro"

export default defineConfig({
  serverDir: "./server",
  preset: process.env.NITRO_PRESET ?? "node_server",
})
