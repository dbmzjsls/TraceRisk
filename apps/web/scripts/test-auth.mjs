import { createServer } from 'vite'
const server = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true }, server: { middlewareMode: true, watch: null, ws: false } })
try {
  const { runAuthBoundaryTests } = await server.ssrLoadModule('/tests/auth-boundaries.ts')
  await runAuthBoundaryTests()
} finally { await server.close() }
