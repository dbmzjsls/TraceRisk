import { createServer } from 'vite'
// Query timers use browser semantics, while Vue runs only in an injection context.
globalThis.window = {}
const server = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true }, server: { middlewareMode: true, watch: null, ws: false } })
try {
  const { runBusinessBoundaryTests } = await server.ssrLoadModule('/tests/business-boundaries.ts')
  await runBusinessBoundaryTests()
} finally { await server.close(); delete globalThis.window }
