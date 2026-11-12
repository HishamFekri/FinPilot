"use client";

import { Children, type CSSProperties, type ReactNode, useEffect, useRef } from "react";
import s from "./landing.module.css";
import { prefersReducedMotion } from "./motion";

/**
 * Editorial panels that stack: each panel scrolls normally until its bottom reaches the bottom of
 * the viewport, then stays put while the next panel rises over it. Tall panels are read in full
 * before they're covered. It's position: sticky, so reverse scrolling, anchors, and keyboard
 * scrolling all behave natively; script only measures heights and dims the covered panel.
 */
export function Stack({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    const items = [...root.children] as HTMLElement[];
    const reduced = prefersReducedMotion();
    let frame = 0;

    const measure = () => {
      for (const item of items) {
        // Stick when the panel's bottom meets the viewport's bottom (or its top, if it's short).
        item.style.top = `${Math.min(0, window.innerHeight - item.offsetHeight)}px`;
      }
      update();
    };
    const update = () => {
      frame = 0;
      if (reduced) return;
      items.forEach((item, i) => {
        const next = items[i + 1];
        if (!next) return;
        const covered = 1 - next.getBoundingClientRect().top / window.innerHeight;
        item.style.setProperty("--cover", Math.min(1, Math.max(0, covered)).toFixed(3));
      });
    };
    const request = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    measure();
    const ro = new ResizeObserver(measure);
    items.forEach((item) => ro.observe(item));
    window.addEventListener("scroll", request, { passive: true });
    window.addEventListener("resize", measure);
    return () => {
      ro.disconnect();
      window.removeEventListener("scroll", request);
      window.removeEventListener("resize", measure);
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);

  return (
    <div ref={ref} className={s.stack}>
      {Children.map(children, (child, i) => (
        <div className={s.stackItem} style={{ zIndex: i + 1 } as CSSProperties}>
          {child}
        </div>
      ))}
    </div>
  );
}
