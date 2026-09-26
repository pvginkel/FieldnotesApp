import { useEffect, useState } from 'react';

/**
 * React hook that returns a debounced version of the provided value.
 * Updates are delayed until the specified timeout has elapsed.
 */
export function useDebouncedValue<T>(value: T, delayMs = 300): T {
  const [debouncedValue, setDebouncedValue] = useState(value);

  useEffect(() => {
    const handle = setTimeout(() => {
      setDebouncedValue(value);
    }, delayMs);

    return () => {
      clearTimeout(handle);
    };
  }, [value, delayMs]);

  return debouncedValue;
}
