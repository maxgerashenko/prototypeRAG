// Keyed async cache with de-duplication of concurrent loads ("waiters"), ported from
// web/mic-test.html's loadConvos/loadTranscript pattern. Lives at module scope so the
// cache survives screen navigation (mirrors the original's single global `state`
// object) without needing a shared React context.
export interface CacheEntry<T> {
  status: "loading" | "ready" | "error";
  data: T | null;
  waiters: Array<() => void>;
}

export class AsyncCache<T> {
  private map = new Map<string, CacheEntry<T>>();

  get(key: string): CacheEntry<T> | undefined {
    return this.map.get(key);
  }

  delete(key: string): void {
    this.map.delete(key);
  }

  /** Start (or join) a load for `key`. `onDone` fires once, when the entry becomes
   * ready or error -- immediately if it already is. */
  load(key: string, fetcher: () => Promise<T>, onDone?: () => void): void {
    let entry = this.map.get(key);
    if (!entry || entry.status === "error") {
      entry = { status: "loading", data: null, waiters: [] };
      this.map.set(key, entry);
      const current = entry;
      fetcher()
        .then((data) => {
          current.status = "ready";
          current.data = data;
          current.waiters.forEach((fn) => fn());
          current.waiters = [];
        })
        .catch(() => {
          current.status = "error";
          current.waiters.forEach((fn) => fn());
          current.waiters = [];
        });
    }
    if (entry.status === "loading") {
      if (onDone) entry.waiters.push(onDone);
    } else if (onDone) {
      onDone();
    }
  }
}
