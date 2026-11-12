"use client";

import { type RefObject, useEffect, useSyncExternalStore } from "react";

const REDUCED = "(prefers-reduced-motion: reduce)";

function subscribeReduced(onChange: () => void) {
  const query = window.matchMedia(REDUCED);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

/** False on the server and during hydration, so markup always matches. */
export function useReducedMotion(): boolean {
  return useSyncExternalStore(
    subscribeReduced,
    () => window.matchMedia(REDUCED).matches,
    () => false,
  );
}

export function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia(REDUCED).matches;
}

/**
 * Marks an element with data-in="true" the first time it's on screen. CSS owns what that means,
 * so each section can enter its own way. Attributes are set directly: no re-render, no mismatch.
 */
export function useReveal(ref: RefObject<HTMLElement | null>, threshold = 0.2) {
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (prefersReducedMotion() || !("IntersectionObserver" in window)) {
      el.dataset.in = "true";
      return;
    }
    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          el.dataset.in = "true";
          io.disconnect();
        }
      },
      { threshold },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [ref, threshold]);
}

/**
 * Scroll progress through a tall section, from 0 (its top reaches the viewport top) to 1 (its
 * bottom reaches the viewport bottom). Written to --p on the element, only while it's near the
 * viewport, at most once per frame. With reduced motion, progress is pinned to 1.
 */
export function useScrollProgress(
  ref: RefObject<HTMLElement | null>,
  onProgress?: (p: number) => void,
) {
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (prefersReducedMotion()) {
      el.style.setProperty("--p", "1");
      onProgress?.(1);
      return;
    }
    let frame = 0;
    let active = false;
    let last = -1;
    const update = () => {
      frame = 0;
      const r = el.getBoundingClientRect();
      const range = Math.max(1, r.height - window.innerHeight);
      const p = Math.min(1, Math.max(0, -r.top / range));
      if (Math.abs(p - last) < 0.001) return;
      last = p;
      el.style.setProperty("--p", p.toFixed(4));
      onProgress?.(p);
    };
    const request = () => {
      if (active && !frame) frame = requestAnimationFrame(update);
    };
    const io = new IntersectionObserver(([entry]) => {
      active = Boolean(entry?.isIntersecting);
      request();
    });
    io.observe(el);
    window.addEventListener("scroll", request, { passive: true });
    window.addEventListener("resize", request);
    update();
    return () => {
      io.disconnect();
      window.removeEventListener("scroll", request);
      window.removeEventListener("resize", request);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [ref, onProgress]);
}

/** Tweens text from one number to another. Instant with reduced motion. Returns a canceller. */
export function tweenNumber(
  el: HTMLElement,
  from: number,
  to: number,
  format: (n: number) => string,
  duration = 700,
): () => void {
  if (prefersReducedMotion() || from === to) {
    el.textContent = format(to);
    return () => {};
  }
  let frame = 0;
  const start = performance.now();
  const ease = (x: number) => 1 - Math.pow(1 - x, 4);
  const step = (now: number) => {
    const t = Math.min(1, (now - start) / duration);
    el.textContent = format(from + (to - from) * ease(t));
    if (t < 1) frame = requestAnimationFrame(step);
  };
  frame = requestAnimationFrame(step);
  return () => cancelAnimationFrame(frame);
}
