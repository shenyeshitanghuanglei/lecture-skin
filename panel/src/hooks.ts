import { useCallback, useEffect, useRef, useState } from "react";
import { api, Status } from "./api";

/** Polls /status with request deduplication, timeout, and a 2-strike offline grace
 *  so transient spikes never flicker "变声服务未连接". */
export function useStatus(intervalMs = 500) {
  const [status, setStatus] = useState<Status | null>(null);
  const [offline, setOffline] = useState(false);
  const timer = useRef<number>();
  const inFlight = useRef<Promise<Status | null> | null>(null);
  const failCount = useRef(0);

  const refresh = useCallback(async (): Promise<Status | null> => {
    if (inFlight.current) return inFlight.current;
    const task = (async () => {
      try {
        const s = await api.status();
        failCount.current = 0;
        setStatus(s);
        setOffline(false);
        return s;
      } catch {
        failCount.current += 1;
        if (failCount.current >= 2) setOffline(true);
        return null;
      } finally {
        inFlight.current = null;
      }
    })();
    inFlight.current = task;
    return task;
  }, []);

  useEffect(() => {
    let alive = true;
    const schedule = () => {
      if (!alive) return;
      window.clearTimeout(timer.current);
      const delay = document.hidden ? Math.max(2500, intervalMs * 4) : intervalMs;
      timer.current = window.setTimeout(loop, delay);
    };
    const loop = async () => {
      await refresh();
      schedule();
    };
    const onVisible = () => {
      if (!document.hidden && alive) {
        window.clearTimeout(timer.current);
        loop();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    loop();
    return () => {
      alive = false;
      document.removeEventListener("visibilitychange", onVisible);
      window.clearTimeout(timer.current);
    };
  }, [intervalMs, refresh]);

  return { status, setStatus, offline, refresh };
}

/** Small toast-style message queue. */
export function useToast() {
  const [msg, setMsg] = useState<{ text: string; error: boolean } | null>(null);
  const t = useRef<number>();
  const show = useCallback((text: string, error = false) => {
    setMsg({ text, error });
    window.clearTimeout(t.current);
    t.current = window.setTimeout(() => setMsg(null), error ? 6000 : 2500);
  }, []);
  return { msg, show };
}

/** Run an async action, reporting errors through `onError`. */
export async function attempt<T>(fn: () => Promise<T>, onError: (m: string) => void): Promise<T | undefined> {
  try {
    return await fn();
  } catch (e) {
    onError(e instanceof Error ? e.message : String(e));
    return undefined;
  }
}
