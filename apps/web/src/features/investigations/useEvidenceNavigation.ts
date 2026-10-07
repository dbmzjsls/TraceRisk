import { onScopeDispose, readonly, shallowRef, watch, type Ref } from 'vue'

const HIGHLIGHT_MS = 2400

export function evidenceElementId(chunkId: string): string {
  return `evidence-${chunkId}`
}

export interface NavigationDependencies {
  getElementById: (id: string) => HTMLElement | null
  prefersReducedMotion: () => boolean
  setTimeout: (callback: () => void, delay: number) => ReturnType<typeof setTimeout>
  clearTimeout: (timer: ReturnType<typeof setTimeout>) => void
}

const browserDependencies: NavigationDependencies = {
  getElementById: id => typeof document === 'undefined' ? null : document.getElementById(id),
  prefersReducedMotion: () => typeof window !== 'undefined' && (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false),
  setTimeout: (callback, delay) => globalThis.setTimeout(callback, delay),
  clearTimeout: timer => globalThis.clearTimeout(timer),
}

export function useEvidenceNavigation(
  investigationId?: Readonly<Ref<unknown>>,
  runId?: Readonly<Ref<unknown>>,
  dependencies: NavigationDependencies = browserDependencies,
) {
  const activeChunkId = shallowRef<string | null>(null)
  const selectedChunkId = shallowRef<string | null>(null)
  const unavailableMessage = shallowRef('')
  const canReturn = shallowRef(false)
  let origin: HTMLElement | null = null
  let timer: ReturnType<typeof setTimeout> | undefined

  function clearTimer() {
    if (timer !== undefined) dependencies.clearTimeout(timer)
    timer = undefined
  }

  function reset() {
    clearTimer()
    activeChunkId.value = null
    selectedChunkId.value = null
    unavailableMessage.value = ''
    origin = null
    canReturn.value = false
  }

  function unavailable(message: string) {
    reset()
    unavailableMessage.value = message
    return false
  }

  function navigate(chunkId: string | null, citationOrigin?: unknown) {
    if (!chunkId) {
      return unavailable('此引用没有可用的精确证据分块，未进行推测定位。')
    }
    const target = dependencies.getElementById(evidenceElementId(chunkId))
    if (!target) {
      return unavailable('当前证据清单中没有该精确分块，未进行推测定位。')
    }
    reset()
    origin = citationOrigin && typeof citationOrigin === 'object' && 'focus' in citationOrigin
      ? citationOrigin as HTMLElement
      : null
    canReturn.value = origin !== null
    activeChunkId.value = chunkId
    selectedChunkId.value = chunkId
    const reducedMotion = dependencies.prefersReducedMotion()
    target.scrollIntoView({ block: 'center', behavior: reducedMotion ? 'auto' : 'smooth' })
    target.focus({ preventScroll: true })
    timer = dependencies.setTimeout(() => { activeChunkId.value = null; timer = undefined }, HIGHLIGHT_MS)
    return true
  }

  function returnToOrigin() {
    const target = origin
    if (!target?.isConnected) {
      reset()
      return
    }
    target.focus({ preventScroll: false })
    reset()
  }

  function clearUnavailable() { unavailableMessage.value = '' }
  if (investigationId && runId) watch([investigationId, runId], reset)
  onScopeDispose(reset)
  return { activeChunkId: readonly(activeChunkId), selectedChunkId: readonly(selectedChunkId), unavailableMessage: readonly(unavailableMessage), canReturn: readonly(canReturn), navigate, returnToOrigin, clearUnavailable, reset }
}
