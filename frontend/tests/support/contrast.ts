/**
 * The mockup's contrast check (FieldnotesAppSpecs/mockups/triage-ui/README.md): text meets WCAG AA
 * in both themes.
 */

import type { Page } from '@playwright/test';

export interface Failure {
  text: string;
  ratio: number;
  required: number;
  fg: string;
  bg: string;
}

/**
 * Every visible text on the page, and every icon, against the background it sits on: 4.5:1, or
 * 3:1 for large text (24px, or 18.66px bold) and icons. The background is the element's own and
 * its ancestors', composited until one is opaque; the element's and its ancestors' opacity fade
 * the text. A placeholder counts as text. Exempt: a disabled control, as WCAG exempts inactive
 * components; the meta line's `·` separators, which are decoration; and the header's help icon,
 * dimmed until hovered by the operator's call (triage.css, `.help`). Measured at rest: the mouse
 * is moved off whatever it was over.
 */
export async function contrastFailures(page: Page): Promise<Failure[]> {
  await page.mouse.move(0, 0);
  return page.evaluate(() => {
    type Rgba = [number, number, number, number];
    const probe = document.createElement('canvas').getContext('2d', { willReadFrequently: true })!;
    // The browser's own parser: any colour syntax, painted and read back as sRGB bytes.
    const rgba = (color: string): Rgba => {
      probe.clearRect(0, 0, 1, 1);
      probe.fillStyle = '#000';
      probe.fillStyle = color;
      probe.fillRect(0, 0, 1, 1);
      const [r, g, b, a] = probe.getImageData(0, 0, 1, 1).data;
      return [r, g, b, a / 255];
    };
    const over = (top: Rgba, below: Rgba): Rgba => {
      const a = top[3] + below[3] * (1 - top[3]);
      if (a === 0) return [0, 0, 0, 0];
      const mix = (i: number) => (top[i] * top[3] + below[i] * below[3] * (1 - top[3])) / a;
      return [mix(0), mix(1), mix(2), a];
    };
    const luminance = ([r, g, b]: Rgba) => {
      const lin = (c: number) => {
        const s = c / 255;
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
    };
    const background = (el: Element): Rgba => {
      const layers: Rgba[] = [];
      for (let node: Element | null = el; node; node = node.parentElement) {
        const layer = rgba(getComputedStyle(node).backgroundColor);
        if (layer[3] > 0) layers.push(layer);
        if (layer[3] >= 1) break;
      }
      return layers.reduceRight<Rgba>((below, top) => over(top, below), [255, 255, 255, 1]);
    };
    const opacity = (el: Element) => {
      let value = 1;
      for (let node: Element | null = el; node; node = node.parentElement) {
        value *= Number(getComputedStyle(node).opacity);
      }
      return value;
    };
    const show = ([r, g, b, a]: Rgba) => `rgba(${r.toFixed(0)}, ${g.toFixed(0)}, ${b.toFixed(0)}, ${a.toFixed(2)})`;

    const failures: Failure[] = [];
    const check = (el: Element, text: string, required: number, css = getComputedStyle(el).color) => {
      const bg = background(el);
      const color = rgba(css);
      const fg = over([color[0], color[1], color[2], color[3] * opacity(el)], bg);
      const [hi, lo] = [luminance(fg), luminance(bg)].sort((a, b) => b - a);
      const ratio = (hi + 0.05) / (lo + 0.05);
      if (ratio < required) {
        failures.push({ text: text.slice(0, 60), ratio: Math.round(ratio * 100) / 100, required, fg: show(fg), bg: show(bg) });
      }
    };
    const shown = (el: Element) => {
      const box = el.getBoundingClientRect();
      return el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }) && box.width > 1 && box.height > 1;
    };

    for (const el of Array.from(document.body.querySelectorAll('*'))) {
      if (!shown(el) || el.closest(':disabled, [aria-hidden="true"]:not(svg)')) continue;
      if (el instanceof SVGSVGElement) {
        if (el.closest('.help')) continue;
        check(el, `icon in ${el.parentElement?.className || el.parentElement?.tagName}`, 3);
        continue;
      }
      if (el.closest('svg')) continue;
      const own = Array.from(el.childNodes)
        .filter((node) => node.nodeType === Node.TEXT_NODE)
        .map((node) => node.textContent ?? '')
        .join('')
        .trim();
      if (el instanceof HTMLTextAreaElement && el.placeholder && !el.value) {
        check(el, `placeholder: ${el.placeholder}`, 4.5, getComputedStyle(el, '::placeholder').color);
      }
      if (!own || /^[·\s]+$/.test(own)) continue;
      const style = getComputedStyle(el);
      const size = parseFloat(style.fontSize);
      const large = size >= 24 || (size >= 18.66 && Number(style.fontWeight) >= 700);
      check(el, own, large ? 3 : 4.5);
    }
    return failures;
  });
}
