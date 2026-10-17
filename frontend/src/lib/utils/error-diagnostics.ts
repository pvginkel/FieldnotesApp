/**
 * Serialize an error into the diagnostics string an error toast's copy button
 * puts on the clipboard: name, message, stack, and the HTTP status when the
 * error carries one.
 */
export function serializeErrorDiagnostics(error: unknown): string | undefined {
  try {
    if (error instanceof Error) {
      let diagnostics = `${error.name}: ${error.message}`
      if (error.stack) {
        diagnostics += `\n${error.stack}`
      }
      // Include HTTP status if present
      const status = (error as Error & { status?: number }).status
      if (status !== undefined) {
        diagnostics = `HTTP ${status}\n${diagnostics}`
      }
      return diagnostics
    } else if (typeof error === 'string') {
      return error
    } else if (error !== null && error !== undefined) {
      return JSON.stringify(error, null, 2)
    }
    return undefined
  } catch {
    return String(error)
  }
}
