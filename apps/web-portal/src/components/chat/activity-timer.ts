import { useEffect, useState } from "react";

const startedAtByKey = new Map<string, number>();

function startedAt(key?: string): number {
  if (!key) {
    return Date.now();
  }

  const existing = startedAtByKey.get(key);
  if (existing !== undefined) return existing;

  const now = Date.now();
  startedAtByKey.set(key, now);
  return now;
}

export function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function elapsedFrom(start: number): number {
  return Math.max(0, Math.floor((Date.now() - start) / 1000));
}

export function useElapsedSeconds(active = true, timerKey?: string): number {
  const [elapsed, setElapsed] = useState(() => elapsedFrom(startedAt(timerKey)));

  useEffect(() => {
    const start = startedAt(timerKey);
    const tick = () => setElapsed(elapsedFrom(start));
    tick();
    if (!active) return;
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [active, timerKey]);

  return elapsed;
}

export function __resetElapsedTimerRegistryForTests() {
  startedAtByKey.clear();
}
