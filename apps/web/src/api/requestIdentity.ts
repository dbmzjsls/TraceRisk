// Identity belongs to the exact target/body, never to a button click or query refresh.
export function createRequestIdentity(makeKey: () => string = () => crypto.randomUUID()) {
  let previous: { signature: string; key: string } | undefined
  return {
    capture<T>(target: string, body: T): { target: string; body: T; key: string } {
      const serialized = JSON.stringify(body)
      const signature = `${target}\n${serialized}`
      if (previous?.signature !== signature) previous = { signature, key: makeKey() }
      return { target, body: JSON.parse(serialized) as T, key: previous.key }
    },
    clear() { previous = undefined },
  }
}
