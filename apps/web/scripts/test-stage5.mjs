import { createServer } from 'vite'
import Vue from '@vitejs/plugin-vue'

const server = await createServer({ configFile: false, plugins: [Vue()], optimizeDeps: { noDiscovery: true }, server: { middlewareMode: true, watch: null, ws: false } })
try {
  const { runStage5EventBoundaryTests } = await server.ssrLoadModule('/tests/stage5-event-boundaries.ts')
  await runStage5EventBoundaryTests()
} finally {
  await server.close()
}
