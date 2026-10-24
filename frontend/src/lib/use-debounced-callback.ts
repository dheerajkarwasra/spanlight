import { useEffect, useRef } from "react";

/**
 * Returns `schedule(value)` that calls `callback(value)` after `delayMs` of
 * quiet, plus `flush()` to run the pending call now. Pending calls are dropped
 * on unmount.
 */
export function useDebouncedCallback<T>(callback: (value: T) => void, delayMs: number) {
  const timeoutRef = useRef<number | undefined>(undefined);
  const pendingRef = useRef<{ value: T } | null>(null);
  const callbackRef = useRef(callback);

  useEffect(() => {
    callbackRef.current = callback;
  }, [callback]);

  useEffect(() => {
    return () => {
      window.clearTimeout(timeoutRef.current);
    };
  }, []);

  function schedule(value: T): void {
    pendingRef.current = { value };
    window.clearTimeout(timeoutRef.current);
    timeoutRef.current = window.setTimeout(() => {
      pendingRef.current = null;
      callbackRef.current(value);
    }, delayMs);
  }

  function flush(): void {
    window.clearTimeout(timeoutRef.current);
    const pending = pendingRef.current;
    pendingRef.current = null;
    if (pending) {
      callbackRef.current(pending.value);
    }
  }

  return { schedule, flush };
}
