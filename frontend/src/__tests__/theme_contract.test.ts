/*
 Structural contract tests for the Lockverity colour system.

 These guard the invariants that, when broken, produced the
 rejected Dark build: white surfaces under light-on-dark text,
 a second parallel palette that drifted from the first, and
 foregrounds below the WCAG AA floor.

 They are deliberately *structural*, not pixel-level. Nothing
 here asserts that a given element carries a given Tailwind
 utility, because that kind of test breaks on every harmless
 markup edit and teaches people to update the assertion instead
 of thinking. What they assert is:

   1. every palette step the Tailwind config exposes is actually
      declared, in BOTH themes;
   2. no shared component hard-codes a light-only surface;
   3. there is exactly one palette (no parallel dark ramp);
   4. the shared primitives resolve through tokens;
   5. the documented Dark foreground/surface pairs clear AA.

 Rule 5 is a real contrast computation over the values in
 ``index.css``, so a future palette tweak that quietly drops a
 pair below 4.5:1 fails here rather than in user QA.
*/

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

// Resolved the same way the other filesystem-facing suites in
// this directory do it (see ``favicon_assets.test.ts``).
const SRC = resolve(__dirname, "..");
const ROOT = resolve(__dirname, "..", "..");

const css = readFileSync(join(SRC, "index.css"), "utf8");
const tailwindConfig = readFileSync(join(ROOT, "tailwind.config.js"), "utf8");

/** Every ``.ts``/``.tsx`` file under ``src`` except the tests. */
function sourceFiles(dir: string = SRC): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      if (entry === "__tests__" || entry === "test") continue;
      out.push(...sourceFiles(full));
      continue;
    }
    if (!/\.tsx?$/.test(entry)) continue;
    if (/\.test\.tsx?$/.test(entry)) continue;
    out.push(full);
  }
  return out;
}

/** The ``--lv-*`` declarations inside one selector block. */
function tokenBlock(selector: string): Map<string, string> {
  const start = css.indexOf(selector + " {");
  expect(start, `${selector} block is missing from index.css`).toBeGreaterThan(
    -1,
  );
  const end = css.indexOf("\n}", start);
  const body = css.slice(start, end);
  const tokens = new Map<string, string>();
  for (const [, name, value] of body.matchAll(
    /--lv-([a-z0-9-]+):\s*([^;]+);/g,
  )) {
    tokens.set(name, value.trim());
  }
  return tokens;
}

const lightTokens = tokenBlock(":root");
const darkTokens = tokenBlock('[data-theme="dark"]');

// ---------------------------------------------------------------
// Contrast helpers (WCAG 2.1 relative luminance).
// ---------------------------------------------------------------

function rgb(triplet: string): [number, number, number] {
  const parts = triplet.split(/\s+/).map(Number);
  expect(parts).toHaveLength(3);
  return [parts[0], parts[1], parts[2]];
}

function luminance(channels: [number, number, number]): number {
  const [r, g, b] = channels.map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const la = luminance(rgb(a));
  const lb = luminance(rgb(b));
  const [hi, lo] = la > lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

// ---------------------------------------------------------------
// 1. Every exposed palette step is declared in both themes.
// ---------------------------------------------------------------

describe("theme tokens", () => {
  const RAMPS = ["ink", "accent", "emerald", "amber", "rose"];
  const STEPS = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900];
  const SINGLETONS = [
    "canvas",
    "surface",
    "raised",
    "sunken",
    "control",
    "sidebar",
    "control-border",
    "control-placeholder",
    "brand-solid",
    "brand-solid-hover",
    "danger-solid",
    "danger-solid-hover",
    "disabled-surface",
    "disabled-text",
    "disabled-border",
    "scrim",
    "focus",
  ];
  const expected = [
    ...RAMPS.flatMap((ramp) => STEPS.map((step) => `${ramp}-${step}`)),
    ...SINGLETONS,
  ];

  it.each(expected)("declares --lv-%s in Light", (name) => {
    expect(lightTokens.has(name)).toBe(true);
  });

  it.each(expected)("declares --lv-%s in Dark", (name) => {
    expect(darkTokens.has(name)).toBe(true);
  });

  it("declares exactly the same token set in both themes", () => {
    expect([...darkTokens.keys()].sort()).toEqual(
      [...lightTokens.keys()].sort(),
    );
  });

  it("uses space-separated sRGB channels so /<alpha-value> works", () => {
    for (const [name, value] of lightTokens) {
      expect(value, `--lv-${name}`).toMatch(/^\d{1,3} \d{1,3} \d{1,3}$/);
    }
    for (const [name, value] of darkTokens) {
      expect(value, `--lv-${name}`).toMatch(/^\d{1,3} \d{1,3} \d{1,3}$/);
    }
  });

  it("binds every Tailwind colour to a declared token", () => {
    const referenced = [
      ...tailwindConfig.matchAll(/--lv-([a-z0-9-]+)\b/g),
    ].map((m) => m[1]);
    for (const name of referenced) {
      expect(lightTokens.has(name), `--lv-${name} is referenced but undeclared`)
        .toBe(true);
    }
  });
});

// ---------------------------------------------------------------
// 2. No shared component may hard-code a light-only surface.
// ---------------------------------------------------------------

describe("surface discipline", () => {
  const files = sourceFiles();

  it("finds the application source tree", () => {
    expect(files.length).toBeGreaterThan(50);
  });

  it("never hard-codes bg-white", () => {
    // ``bg-white`` was on 32 elements in the rejected build - every
    // card, drawer, filter bar and table container - and is the
    // single reason Dark mode showed white slabs. Surfaces go
    // through ``bg-surface`` / ``.card`` so they follow the theme.
    const offenders = files.filter((f) =>
      /\bbg-white\b/.test(readFileSync(f, "utf8")),
    );
    expect(offenders.map((f) => f.replace(SRC, ""))).toEqual([]);
  });

  it("keeps the scrim off the neutral ramp", () => {
    // ``bg-ink-900/40`` was the modal scrim. ``ink-900`` is a
    // foreground step and inverts to near-white in Dark, which
    // would have turned the scrim into a white wash.
    const offenders = files.filter((f) =>
      /\bbg-ink-(800|900)\b/.test(readFileSync(f, "utf8")),
    );
    expect(offenders.map((f) => f.replace(SRC, ""))).toEqual([]);
  });
});

// ---------------------------------------------------------------
// 3. Exactly one palette.
// ---------------------------------------------------------------

describe("single palette", () => {
  it("has no parallel dark ramp in the Tailwind config", () => {
    // The rejected build carried a second ``surface.dark`` /
    // ``accent-dark`` palette alongside the real one. The two
    // drifted: ``accent-dark`` ran dark-to-light while every call
    // site used its low steps as *bright* link colours, so links
    // rendered navy-on-charcoal at 2.4:1.
    expect(tailwindConfig).not.toMatch(/accent-dark/);
    expect(tailwindConfig).not.toMatch(/surface-dark|dark:\s*\{/);
  });

  it("has no per-element dark: overrides left in the components", () => {
    const offenders = sourceFiles().filter((f) =>
      /\bdark:[a-z]/.test(readFileSync(f, "utf8")),
    );
    expect(offenders.map((f) => f.replace(SRC, ""))).toEqual([]);
  });
});

// ---------------------------------------------------------------
// 4. Shared primitives resolve through tokens.
// ---------------------------------------------------------------

describe("shared primitives", () => {
  function rule(selector: string): string {
    const start = css.indexOf(`\n  ${selector} {`);
    expect(start, `${selector} is not defined in index.css`).toBeGreaterThan(-1);
    return css.slice(start, css.indexOf("\n  }", start));
  }

  it(".card is a themed surface, not white", () => {
    expect(rule(".card")).toMatch(/bg-surface\b/);
    expect(rule(".card")).not.toMatch(/bg-white/);
  });

  it(".input is a themed control with its own border and placeholder", () => {
    const body = rule(".input");
    expect(body).toMatch(/--lv-control\)/);
    expect(body).toMatch(/--lv-control-border\)/);
    expect(css).toMatch(/\.input::placeholder/);
    expect(css).toMatch(/\.input:focus/);
  });

  it("buttons use pinned solid tokens rather than ramp steps", () => {
    // A filled button always carries white label text, so its
    // surface must stay dark in both themes; taking it from a ramp
    // that inverts would make it pale with white text on it.
    expect(rule(".btn-primary")).toMatch(/bg-brand-solid\b/);
    expect(rule(".btn-danger")).toMatch(/bg-danger-solid\b/);
    expect(rule(".btn-secondary")).toMatch(/bg-surface\b/);
  });

  it("gives disabled buttons a readable Dark treatment", () => {
    expect(css).toMatch(
      /\[data-theme="dark"\]\s+\.btn:disabled\s*\{[^}]*--lv-disabled-surface/,
    );
  });

  it("defines the .link class every privacy link references", () => {
    expect(css).toMatch(/\n {2}\.link \{/);
  });

  it("table helpers take their colours from the neutral ramp", () => {
    expect(rule(".table-cell")).toMatch(/text-ink-700\b/);
    expect(rule(".table-head")).toMatch(/text-ink-500\b/);
    expect(rule(".table-row")).toMatch(/border-ink-100\b/);
  });
});

// ---------------------------------------------------------------
// 5. The documented Dark pairs clear WCAG AA.
// ---------------------------------------------------------------

describe("dark contrast floor", () => {
  const SURFACES = ["canvas", "surface", "raised", "control", "sidebar"];
  const BODY_FOREGROUNDS = [
    "ink-400",
    "ink-500",
    "ink-600",
    "ink-700",
    "ink-800",
    "ink-900",
    "accent-600",
    "accent-700",
    "accent-800",
    "accent-900",
  ];

  it.each(
    BODY_FOREGROUNDS.flatMap((fg) => SURFACES.map((bg) => [fg, bg] as const)),
  )("dark %s on %s clears 4.5:1", (fg, bg) => {
    const ratio = contrast(darkTokens.get(fg)!, darkTokens.get(bg)!);
    expect(
      ratio,
      `--lv-${fg} on --lv-${bg} is ${ratio.toFixed(2)}:1`,
    ).toBeGreaterThanOrEqual(4.5);
  });

  it.each([
    ["emerald", "ok"],
    ["amber", "warn"],
    ["rose", "danger"],
    ["accent", "info"],
  ])("dark %s chip (%s) is readable on its own tint", (ramp) => {
    // Status chips paint ``bg-<ramp>-50`` with ``text-<ramp>-700``.
    const ratio = contrast(
      darkTokens.get(`${ramp}-700`)!,
      darkTokens.get(`${ramp}-50`)!,
    );
    expect(ratio, `${ramp}-700 on ${ramp}-50 is ${ratio.toFixed(2)}:1`)
      .toBeGreaterThanOrEqual(4.5);
  });

  it.each(["emerald", "amber", "rose", "accent"])(
    "dark %s-700 stays readable directly on the card surface",
    (ramp) => {
      const ratio = contrast(
        darkTokens.get(`${ramp}-700`)!,
        darkTokens.get("surface")!,
      );
      expect(ratio, `${ramp}-700 on surface is ${ratio.toFixed(2)}:1`)
        .toBeGreaterThanOrEqual(4.5);
    },
  );

  it("keeps white legible on the solid action surfaces", () => {
    for (const token of ["brand-solid", "brand-solid-hover", "danger-solid"]) {
      const ratio = contrast("255 255 255", darkTokens.get(token)!);
      expect(ratio, `white on --lv-${token} is ${ratio.toFixed(2)}:1`)
        .toBeGreaterThanOrEqual(4.5);
      const lightRatio = contrast("255 255 255", lightTokens.get(token)!);
      expect(lightRatio, `white on Light --lv-${token}`).toBeGreaterThanOrEqual(
        4.5,
      );
    }
  });

  it("keeps disabled controls readable rather than merely faded", () => {
    const ratio = contrast(
      darkTokens.get("disabled-text")!,
      darkTokens.get("disabled-surface")!,
    );
    expect(ratio, `disabled text is ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(
      4.5,
    );
  });

  it("gives form controls a 3:1 boundary against the surface behind them", () => {
    // WCAG 1.4.11: the edge of a control is "visual information
    // required to identify a user interface component".
    const ratio = contrast(
      darkTokens.get("control-border")!,
      darkTokens.get("surface")!,
    );
    expect(ratio, `control border is ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(
      3,
    );
  });

  it("keeps the focus ring visible on every dark surface", () => {
    for (const bg of SURFACES) {
      const ratio = contrast(darkTokens.get("focus")!, darkTokens.get(bg)!);
      expect(ratio, `focus ring on --lv-${bg} is ${ratio.toFixed(2)}:1`)
        .toBeGreaterThanOrEqual(3);
    }
  });
});

// ---------------------------------------------------------------
// 6. Light is byte-identical to the pre-theme palette.
// ---------------------------------------------------------------

describe("light palette is unchanged", () => {
  // The pre-theme Lockverity scales, transcribed from the palette
  // the approved Light UI shipped with. If a future change edits a
  // Light token, this fails and the change has to be deliberate.
  const LIGHT_INK: Record<string, string> = {
    "ink-50": "245 247 250",
    "ink-100": "228 233 240",
    "ink-200": "200 209 222",
    "ink-300": "155 168 187",
    "ink-400": "108 122 145",
    "ink-500": "74 86 112",
    "ink-600": "56 65 88",
    "ink-700": "43 50 69",
    "ink-800": "28 34 51",
    "ink-900": "15 19 34",
  };
  const LIGHT_ACCENT: Record<string, string> = {
    "accent-50": "238 244 255",
    "accent-100": "219 230 255",
    "accent-200": "182 204 255",
    "accent-300": "131 168 255",
    "accent-400": "86 132 245",
    "accent-500": "52 96 219",
    "accent-600": "39 72 177",
    "accent-700": "31 56 138",
    "accent-800": "23 42 104",
    "accent-900": "15 28 69",
  };

  it.each(Object.entries({ ...LIGHT_INK, ...LIGHT_ACCENT }))(
    "%s is still %s in Light",
    (name, value) => {
      expect(lightTokens.get(name)).toBe(value);
    },
  );

  it("keeps the Light surfaces white / ink-50", () => {
    expect(lightTokens.get("surface")).toBe("255 255 255");
    expect(lightTokens.get("control")).toBe("255 255 255");
    expect(lightTokens.get("sidebar")).toBe("255 255 255");
    expect(lightTokens.get("canvas")).toBe(LIGHT_INK["ink-50"]);
    expect(lightTokens.get("raised")).toBe(LIGHT_INK["ink-50"]);
    expect(lightTokens.get("control-border")).toBe(LIGHT_INK["ink-200"]);
  });

  it("keeps the Light solid buttons on the original accent / rose steps", () => {
    expect(lightTokens.get("brand-solid")).toBe(LIGHT_ACCENT["accent-600"]);
    expect(lightTokens.get("brand-solid-hover")).toBe(LIGHT_ACCENT["accent-700"]);
    expect(lightTokens.get("danger-solid")).toBe("225 29 72"); // rose-600
    expect(lightTokens.get("danger-solid-hover")).toBe("190 18 60"); // rose-700
  });
});
