import { execFileSync } from 'node:child_process'
import process from 'node:process'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, isAbsolute, join, relative, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import openapiTS, { astToString } from 'openapi-typescript'

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const projectRoot = resolve(webRoot, '../..')
const target = join(webRoot, 'src/api/generated.ts')
const check = process.argv.includes('--check')
const temporary = await mkdtemp(join(tmpdir(), 'tracerisk-openapi-'))
const tempRelative = relative(resolve(tmpdir()), resolve(temporary))
if (!tempRelative || tempRelative.startsWith('..') || isAbsolute(tempRelative)) throw new Error('Temporary directory must stay inside OS temporary root')
try {
  const schema = join(temporary, 'openapi.json')
  execFileSync('uv', ['run', '--project', 'apps/api', 'python', 'scripts/export_openapi.py', '--output', schema], {
    cwd: projectRoot, stdio: 'inherit', env: { ...process.env, UV_CACHE_DIR: join(projectRoot, '.uv-cache') },
  })
  const generated = '/** Generated from local TraceRisk OpenAPI. Run pnpm api:generate; do not edit. */\n' + astToString(await openapiTS(pathToFileURL(schema)))
  if (check) {
    const candidate = join(temporary, 'generated.ts')
    await writeFile(candidate, generated)
    if (!(await readFile(target)).equals(await readFile(candidate))) {
      process.stderr.write('API types have drifted. Run pnpm api:generate.\n')
      process.exitCode = 1
    } else process.stdout.write('API types are consistent with local OpenAPI.\n')
  } else {
    await writeFile(target, generated)
    process.stdout.write('Generated src/api/generated.ts from local OpenAPI.\n')
  }
} finally {
  // This is the directory returned by mkdtemp for this invocation only.
  await rm(temporary, { recursive: true, force: true })
}
