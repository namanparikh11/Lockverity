/** @type {import('tailwindcss').Config} */

/*
 * Lockverity colour system.
 *
 * Every palette step below resolves to a CSS custom
 * property rather than a literal hex value. The
 * properties are declared twice in ``src/index.css``:
 * once on ``:root`` (the Light theme, whose values are
 * byte-for-byte the pre-theme Lockverity palette) and
 * once on ``[data-theme="dark"]`` (the Dark theme).
 *
 * This is the single structural fix for the Dark-mode
 * QA failure. The previous implementation kept the
 * ramps Light-only and tried to repair Dark with
 * ~800 per-element ``dark:`` utilities. Because those
 * utilities almost exclusively overrode *text* colour
 * and almost never the *surface* underneath, Dark mode
 * ended up rendering near-white text on the untouched
 * white ``bg-white`` / ``.card`` / ``.input`` /
 * ``<tbody>`` surfaces. Making the ramp itself
 * theme-aware means a single declaration block fixes
 * every one of the 130 source files at once, and no
 * page can drift out of the system again.
 *
 * Rules for anyone editing this file:
 *
 *  - A step used as a *solid action surface* (a filled
 *    button) must NOT be remapped, because a step that
 *    is dark-on-light must stay dark to keep white
 *    label text legible. Those surfaces use the
 *    dedicated ``brand``/``danger`` solid tokens below
 *    and are pinned per theme in ``index.css``.
 *  - Everything else (tints, borders, foregrounds)
 *    inverts: a step that reads "quiet" on white must
 *    read "quiet" on charcoal, and a step that reads
 *    "loud" must stay loud.
 */

/** Expand a CSS custom property into a Tailwind colour value. */
const v = (name) => `rgb(var(--lv-${name}) / <alpha-value>)`;

/** Build a 50..900 ramp bound to ``--lv-<prefix>-<step>``. */
const ramp = (prefix) =>
  Object.fromEntries(
    [50, 100, 200, 300, 400, 500, 600, 700, 800, 900].map((step) => [
      step,
      v(`${prefix}-${step}`),
    ]),
  );

export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  // Custom dark-mode selector. The resolved theme is
  // applied to ``<html data-theme="dark">`` (or
  // ``data-theme="light"``) by the early-paint bootstrap
  // script in ``index.html`` and by the
  // ``AppearanceProvider`` in ``src/appearance/``. The
  // default Tailwind v3 dark-mode strategy is the OS
  // ``prefers-color-scheme`` media query, which does not
  // allow the explicit Light/Dark overrides; the
  // ``selector`` strategy is the only one that satisfies
  // the "System / Light / Dark" appearance model.
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        // Neutral ramp. Light values are the original
        // Lockverity ``ink`` scale; Dark values invert
        // the *role* of each step (50/100 become quiet
        // surfaces, 200/300 become borders, 400-900
        // become progressively stronger foregrounds).
        ink: ramp("ink"),
        // Brand ramp. ``accent-500`` is the Lockverity
        // blue in both themes so the brand mark reads
        // identically.
        accent: ramp("accent"),
        // Status ramps. Light values are the Tailwind
        // defaults the pre-theme UI already used, so
        // Light output is unchanged; Dark values are
        // muted tints (50/100), quiet borders (200/300)
        // and legible foregrounds (600-900).
        emerald: ramp("emerald"),
        amber: ramp("amber"),
        rose: ramp("rose"),

        // Semantic surfaces. ``bg-surface`` replaces the
        // hard-coded ``bg-white`` that made every card,
        // drawer, filter bar and table body render as a
        // white slab in Dark mode.
        surface: {
          DEFAULT: v("surface"),
          raised: v("raised"),
          sunken: v("sunken"),
          control: v("control"),
          sidebar: v("sidebar"),
        },
        canvas: v("canvas"),
        // Modal / drawer scrim. Kept off the ``ink``
        // ramp: ``bg-ink-900/40`` used to be the scrim,
        // and an inverted ``ink-900`` would have turned
        // it into a white wash in Dark mode.
        scrim: v("scrim"),
        // Solid action surfaces. Deliberately NOT part
        // of a ramp: these always carry white label
        // text, so they must stay dark in both themes.
        brand: {
          solid: v("brand-solid"),
          "solid-hover": v("brand-solid-hover"),
        },
        danger: {
          solid: v("danger-solid"),
          "solid-hover": v("danger-solid-hover"),
        },
      },
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
        mono: [
          "JetBrains Mono",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "monospace",
        ],
      },
    },
  },
  plugins: [],
};
