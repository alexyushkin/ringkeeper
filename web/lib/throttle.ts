// At most one call per `ms`, and always one after the last: a burst of events ends with the
// latest state on screen.
export function throttle(run: () => void, ms: number, later: (fn: () => void, ms: number) => unknown = setTimeout): () => void {
  let last = -Infinity;
  let pending = false;
  return () => {
    const now = Date.now();
    if (now - last >= ms) {
      last = now;
      run();
      return;
    }
    if (pending) return;
    pending = true;
    later(() => {
      pending = false;
      last = Date.now();
      run();
    }, ms - (now - last));
  };
}
