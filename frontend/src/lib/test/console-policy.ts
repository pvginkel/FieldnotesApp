/**
 * Console error tracking setup for test infrastructure
 * Tracks console errors while still logging them to original console
 */

interface ConsoleError {
  message: string;
  timestamp: Date;
  stack?: string;
}

let originalConsoleError: typeof console.error;
let trackedErrors: ConsoleError[] = [];
let isSetup = false;

/**
 * Setup console error tracking
 * Wraps console.error to track errors while still logging them
 */
export function setupConsolePolicy(): void {
  if (isSetup) {
    return;
  }

  // Store reference to original console.error
  originalConsoleError = console.error;

  // Wrap console.error to track errors
  console.error = (...args: unknown[]) => {
    // Call original console.error first
    originalConsoleError.apply(console, args);

    // Track the error
    const message = args.map(arg =>
      typeof arg === 'string' ? arg : JSON.stringify(arg)
    ).join(' ');

    const error: ConsoleError = {
      message,
      timestamp: new Date(),
      stack: args.find(arg => arg instanceof Error)?.stack,
    };

    trackedErrors.push(error);
  };

  isSetup = true;
}

/**
 * Get all tracked console errors
 */
export function getConsoleErrors(): ConsoleError[] {
  return [...trackedErrors];
}

/**
 * Clear tracked console errors
 * Useful for test cleanup between tests
 */
export function clearConsoleErrors(): void {
  trackedErrors = [];
}

/**
 * Restore original console.error
 * Should be called when test mode is disabled
 */
export function restoreConsolePolicy(): void {
  if (isSetup && originalConsoleError) {
    console.error = originalConsoleError;
    isSetup = false;
  }
}