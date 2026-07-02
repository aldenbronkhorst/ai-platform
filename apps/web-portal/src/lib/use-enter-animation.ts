import { useCallback } from "react";

const playedAnimationKeys = new Set<string>();
const playedAnimationOrder: string[] = [];
const MAX_TRACKED_KEYS = 2048;

function hasPlayedAnimation(key: string): boolean {
  return playedAnimationKeys.has(key);
}

function rememberPlayedAnimation(key: string): void {
  if (playedAnimationKeys.has(key)) return;

  playedAnimationKeys.add(key);
  playedAnimationOrder.push(key);

  if (playedAnimationOrder.length > MAX_TRACKED_KEYS) {
    const evicted = playedAnimationOrder.shift();
    if (evicted) playedAnimationKeys.delete(evicted);
  }
}

function scheduleMicrotask(cb: () => void): void {
  if (typeof queueMicrotask === "function") {
    queueMicrotask(cb);
    return;
  }

  void Promise.resolve().then(cb);
}

export function useEnterAnimation(enabled: boolean, animationKey?: string): (el: HTMLElement | null) => void {
  return useCallback((el: HTMLElement | null) => {
    if (!el || !enabled || typeof window === "undefined") return;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;

    const key = animationKey;
    if (key && hasPlayedAnimation(key)) return;

    el.animate(
      [
        { opacity: 0, transform: "translateY(0.375rem)" },
        { opacity: 1, transform: "translateY(0)" },
      ],
      { duration: 180, easing: "cubic-bezier(0.16, 1, 0.3, 1)", fill: "both" },
    );

    if (key) {
      scheduleMicrotask(() => {
        if (el.isConnected) rememberPlayedAnimation(key);
      });
    }
  }, [animationKey, enabled]);
}
