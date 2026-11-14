"use client";

import { useTranslations } from "next-intl";
import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";
import s from "./landing.module.css";
import { SettingsMenu } from "./settings-menu";
import { GoogleButton } from "./shared";

type RGB = { r: number; g: number; b: number; a: number };

const LIGHT_TEXT = { r: 243, g: 238, b: 227, a: 1 }; // #f3eee3
const DARK_TEXT = { r: 13, g: 27, b: 34, a: 1 }; // #0d1b22

function luminance({ r, g, b }: RGB): number {
  const lin = (v: number) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}
function contrast(a: RGB, b: RGB): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

/**
 * No bar and no box. The wordmark floats, and its colour is chosen from the actual background
 * under it: whichever of light or dark text has more contrast. Colours are read through a 1×1
 * canvas so any CSS colour format (lab, oklab, color-mix results) compares correctly. The sign-in
 * button and the ⋮ menu belong to the top of the page only, and leave once the page scrolls. When
 * text scrolls under the wordmark, the wordmark fades out until the text has passed.
 */
export function LandingHeader() {
  const shell = useTranslations("shell");
  const ref = useRef<HTMLElement>(null);
  const brandRef = useRef<HTMLAnchorElement>(null);

  useEffect(() => {
    const el = ref.current;
    const brand = brandRef.current;
    const ctx = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
    if (!el || !brand || !ctx) return;

    const toRGB = (css: string): RGB => {
      ctx.clearRect(0, 0, 1, 1);
      ctx.fillStyle = "rgba(0, 0, 0, 0)";
      ctx.fillStyle = css;
      ctx.fillRect(0, 0, 1, 1);
      const [r = 0, g = 0, b = 0, a = 0] = ctx.getImageData(0, 0, 1, 1).data;
      return { r, g, b, a: a / 255 };
    };
    // The first mostly opaque background under a point, ignoring the header itself. A band drawn
    // with a gradient names its colour in --tone-bg, since a gradient has no background colour.
    const backgroundAt = (x: number, y: number): RGB => {
      for (const node of document.elementsFromPoint(x, y)) {
        if (el.contains(node)) continue;
        const style = getComputedStyle(node);
        const bg = toRGB(style.backgroundColor);
        if (bg.a > 0.5) return bg;
        const hint = style.getPropertyValue("--tone-bg").trim();
        if (hint) {
          // A band whose edges fade to the page names how far the pale part reaches: there, the
          // page behind is the colour that counts.
          const box = node.getBoundingClientRect();
          const length = (name: string) => {
            const v = style.getPropertyValue(name).trim();
            const n = Number.parseFloat(v);
            if (!Number.isFinite(n)) return 0;
            return v.endsWith("vh") ? (n * window.innerHeight) / 100 : n;
          };
          const pale =
            y - box.top < length("--tone-pale-top") ||
            box.bottom - y < length("--tone-pale-bottom");
          if (!pale) return toRGB(hint);
        }
      }
      return toRGB(getComputedStyle(document.body).backgroundColor);
    };

    // Is any page text or picture actually drawn under the wordmark? Text is tested by its glyph
    // boxes rather than its element, so a wide heading whose words sit elsewhere doesn't count.
    const MEDIA = "svg, img, canvas, video, input, button, select, textarea";
    const crowded = (box: DOMRect): boolean => {
      const pad = 6;
      const hits = (r: DOMRect) =>
        r.width > 0 &&
        r.right > box.left - pad &&
        r.left < box.right + pad &&
        r.bottom > box.top - pad &&
        r.top < box.bottom + pad;
      const range = document.createRange();
      const seen = new Set<Element>();
      for (const fx of [0.1, 0.5, 0.9]) {
        for (const fy of [0.2, 0.8]) {
          const x = box.left + box.width * fx;
          const y = box.top + box.height * fy;
          for (const node of document.elementsFromPoint(x, y)) {
            if (el.contains(node)) continue;
            // Stop at the first solid surface: nothing beneath it is visible.
            const solid = toRGB(getComputedStyle(node).backgroundColor).a > 0.5;
            if (seen.has(node)) {
              if (solid) break;
              continue;
            }
            seen.add(node);
            const media = node.closest(MEDIA);
            if (media && !el.contains(media) && hits(media.getBoundingClientRect())) return true;
            for (const child of node.childNodes) {
              if (child.nodeType !== Node.TEXT_NODE || !child.textContent?.trim()) continue;
              range.selectNodeContents(child);
              for (const r of range.getClientRects()) if (hits(r)) return true;
            }
            if (solid) break;
          }
        }
      }
      return false;
    };

    let frame = 0;
    let reveal = 0;
    const update = () => {
      frame = 0;
      el.dataset.scrolled = String(window.scrollY > 24);
      const box = brand.getBoundingClientRect();
      const bg = backgroundAt(box.left + box.width / 2, box.top + box.height / 2);
      const tone = contrast(LIGHT_TEXT, bg) >= contrast(DARK_TEXT, bg) ? "light" : "dark";
      if (el.dataset.tone !== tone) el.dataset.tone = tone;
      // Hide at once; come back only after the area has stayed clear for a moment, so the
      // wordmark doesn't flicker between lines of text.
      if (crowded(box)) {
        window.clearTimeout(reveal);
        reveal = 0;
        el.dataset.clear = "false";
      } else if (el.dataset.clear === "false" && !reveal) {
        reveal = window.setTimeout(() => {
          reveal = 0;
          el.dataset.clear = "true";
        }, 220);
      }
    };
    const request = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", request, { passive: true });
    window.addEventListener("resize", request);
    // The theme can change without a scroll (system setting or the ⋮ menu).
    const observer = new MutationObserver(request);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => {
      window.removeEventListener("scroll", request);
      window.removeEventListener("resize", request);
      observer.disconnect();
      if (frame) cancelAnimationFrame(frame);
      window.clearTimeout(reveal);
    };
  }, []);

  return (
    <header
      ref={ref}
      className={s.header}
      data-scrolled="false"
      data-tone="light"
      data-clear="true"
    >
      <div className={cn(s.wrap, s.headerInner)}>
        <a ref={brandRef} className={s.brand} href="#top">
          {shell("brand")}
        </a>
        <div className={s.headerEnd}>
          <GoogleButton size="compact" />
          <SettingsMenu container={ref} />
        </div>
      </div>
    </header>
  );
}
