---
name: systor
description: Zero-dependency, engineering-grade host and container telemetry design system
colors:
  bg: "#0b0b0b"
  bg-elev: "#151515"
  bg-card: "#1a1a19"
  bg-input: "#111110"
  bg-hover: "#222220"
  border: "#30363d"
  border-2: "#3d444d"
  text: "#d4d3c9"
  text-muted: "#9e9c94"
  text-bright: "#f5f4f0"
  white: "#ffffff"
  teal: "#1d9e75"
  teal-strong: "#0d5c45"
  teal-text: "#2dd4bf"
  teal-light: "#177f5e"
  primary: "#1d9e75"
  primary-light: "#2dd4bf"
  primary-deep: "#0d5c45"
  blue: "#58a6ff"
  blue-2: "#1f6feb"
  success: "#3fb950"
  success-light: "#96f2a6"
  warning: "#e3b341"
  warning-light: "#ffcc8a"
  danger: "#f85149"
  danger-light: "#ff938a"
  purple: "#a78bfa"
  purple-light: "#d8b9ff"
  cyan: "#2dd4bf"
  cyan-light: "#8be9ff"
  paper-bg: "#f9f9f7"
  paper-card: "#ffffff"
  tag-video: "#ffb86c"
  tag-doc: "#93c5fd"
  tag-image: "#a7f3d0"
  tag-archive: "#d8b4fe"
  preview-text: "#d1e7ff"
typography:
  display:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
    fontSize: "22px"
    fontWeight: 650
    lineHeight: 1.1
    letterSpacing: "normal"
  kpi:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
    fontSize: "26px"
    fontWeight: 700
    lineHeight: 1.0
    letterSpacing: "-0.02em"
  heading-2:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
    fontSize: "18px"
    fontWeight: 650
    lineHeight: 1.2
    letterSpacing: "normal"
  subheading:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
    fontSize: "15px"
    fontWeight: 600
    lineHeight: 1.25
    letterSpacing: "normal"
  title:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
    fontSize: "14px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "0.2px"
  body:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  compact:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.4
    letterSpacing: "normal"
  label:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
    fontSize: "11px"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "0.45px"
  mono:
    fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace'
    fontSize: "12px"
    fontWeight: 500
    lineHeight: 1.35
    letterSpacing: "normal"
rounded:
  xs: "4px"
  sm: "6px"
  base: "7px"
  md: "8px"
  lg: "10px"
  card: "12px"
  modal: "16px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "6px"
  md: "8px"
  lg: "12px"
  xl: "16px"
components:
  button-primary:
    backgroundColor: "{colors.success}"
    textColor: "#ffffff"
    rounded: "{rounded.md}"
    padding: "8px 16px"
  button-secondary:
    backgroundColor: "{colors.bg-hover}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: "6px 12px"
  page-tab:
    backgroundColor: "{colors.bg-hover}"
    textColor: "{colors.text}"
    rounded: "{rounded.pill}"
    padding: "5px 12px"
  stat-card:
    backgroundColor: "{colors.bg-card}"
    textColor: "{colors.text-bright}"
    rounded: "{rounded.lg}"
    padding: "10px 14px"
  status-chip:
    backgroundColor: "{colors.bg-input}"
    textColor: "{colors.text-muted}"
    rounded: "{rounded.pill}"
    padding: "4px 8px"
---

## Overview

`systor` adheres to an engineering-first, dark-native design language inspired by low-overhead systems telemetry monitors (Hermes, Netdata, GitHub Dark). The interface is tailored for high information density, instant visual hierarchy, zero layout shift (CLS 0.0), and 100% offline rendering without any external dependencies or font CDNs.

## Colors

All color values are tokenized as CSS Custom Properties in `:root`. Functional semantics are preserved consistently across all surfaces:

- **Neutral Canvas**: `#0f1117` (`--bg`) base background, `#161b22` (`--bg-card` / `--bg-elev`) elevated cards and headers, `#21262d` (`--bg-hover`) hover states, `#0b0f14` (`--bg-input`) form inputs.
- **Borders & Dividers**: `#30363d` (`--border`) primary structural borders, `#3d444d` (`--border-2`) elevated control borders.
- **Typography**: `#e6edf3` (`--text-bright`) titles and values, `#c9d1d9` (`--text`) body copy, `#8b949e` (`--text-muted`) metadata and micro-labels.
- **Accents & Metrics**:
  - **Blue** (`#58a6ff` / `#1f6feb`): Brand accent, primary action focus rings, active navigation tabs.
  - **Cyan** (`#22d3ee`): CPU usage metrics and download rates (`rx`).
  - **Green** (`#3fb950`): System health, normal operational status, save confirmations.
  - **Amber / Yellow** (`#d29922`): Warning thresholds, load averages, write disk activity.
  - **Crimson / Red** (`#f85149`): Critical errors, CPU temperature alerts, upload rates (`tx`).
  - **Amethyst / Purple** (`#bc8cff`): Docker memory usage and specialized operations.

## Typography

The typography scale utilizes system-native UI font stacks for zero font download latency and crystal-clear rendering on Retina, 4K, and low-DPI displays alike:

- **Font Stacks**:
  - UI Sans: `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif`
  - Telemetry Monospace: `ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace`
- **Scale & Line-Heights**:
  - H1 / Page Header: 17px–18px, weight 650, line-height 1.1
  - Card Headings (H3): 12px–13px, weight 600, uppercase, letter-spacing 0.7px
  - Body Text: 13px–14px, weight 400, line-height 1.5
  - Micro-Labels / Tags: 11px, weight 700, uppercase, letter-spacing 0.45px
- **Numerals**: All tabular data, timestamps, KPI metrics, chart legends, and process tables strictly enforce `font-variant-numeric: tabular-nums;` to guarantee numbers never wiggle or cause horizontal jitter during streaming updates.
- **Craft Floor**: No interactive or functional text is permitted below 11px.

## Layout

- **Maximum Width**: 1800px fluid desktop width with 8px–16px horizontal container padding.
- **Grid Rhythms**:
  - KPI Stat Grid: Fluid auto-fit grid (`repeat(auto-fit, minmax(156px, 1fr))`) with 8px gaps.
  - Two-Column Metric Grid: 2-column on desktop (`1fr 1fr`), collapsing to single column on viewports `<= 1100px`.
  - Settings Split Grid: Asymmetric `1.05fr 0.95fr` grid collapsing cleanly on mobile viewports `<= 900px`.
- **Top Sticky Header**: Compact 38px–42px sticky bar providing instant global health telemetry (`global-header-meta`) alongside pill navigation tabs.

## Elevation & Depth

`systor` avoids heavy, muddy drop shadows in favor of crisp tonal layering and subtle ambient borders:
- **Card Surfaces**: 1px solid border `#30363d` on `#161b22` background.
- **Ambient Accent Tinting**: KPI cards apply ultra-subtle 4% linear gradients matching metric states (e.g., green health, amber warning) rather than garish artificial side-stripes.
- **Modals & Overlays**: `#121822` surface with 1px border `rgba(88,166,255,0.28)` and smooth deep shadow `0 18px 60px rgba(0,0,0,0.45)`.

## Shapes

- **Corner Radii**:
  - Small elements (buttons, inputs, status badges): 6px
  - Cards, panels, and chart containers: 8px
  - Modal dialogs: 16px–18px
  - Navigation tabs and status chips: 999px (pill geometry)
- **Form Geometry**:
  - Input heights: 30px compact (forms) / 34px (mobile touch-friendly)
  - Toolbar selectors: 26px compact height with native OS select drop-downs.

## Components

### Buttons
- **Primary Button**: Emerald green gradient `#2ea043` to `#238636` with 1px subtle highlight.
- **Secondary / Compact Button**: Neutral `#21262d` surface with `#3d444d` border; hover transitions to `#30363d` with blue border accent.
- **Danger Button**: Transparent red wash `rgba(248,81,73,0.14)` with `#ffb4ad` text.

### Tabs (`.page-tab` & `.storage-tab`)
- Pill geometry with 5px 12px padding. Inactive tabs rest on `#21262d` with neutral text; active tab adopts vibrant blue gradient `#1f6feb` to `#1a4f9c` with `aria-current="page"` and `aria-selected="true"`.

### Data Tables (`.apps-table`, `.network-table`)
- Dense, zero-clutter rows with 6px–8px padding, aligned numeric headers, hover state `rgba(88,166,255,0.065)`, and explicit empty-state cells.

## Mobile & Touch Ergonomics

- **Touch Target Dimensions**: Minimum 40px–44px hit bounds on primary navigation tabs, action buttons, and segmented toggles complying with Fitts' Law and mobile touch psychology.
- **Touch Spacing**: Minimum 4px–8px physical separation between adjacent buttons and interactive chips to eliminate accidental miss-taps.
- **Safe Area Insets (Edge-to-Edge Displays)**: Full support for `viewport-fit=cover` and dynamic CSS environment variables (`--sat`, `--sar`, `--sab`, `--sal`) to avoid occlusion by iPhone dynamic island / notches and Android system gesture bars.
- **iOS Viewport Zoom Mitigation**: Mobile form controls (`input`, `select`, `textarea`) enforce a minimum `16px` font-size on viewports `<= 700px`, completely preventing iOS Safari layout shift and involuntary auto-zoom.
- **Touch Feedback**: Micro-scale physical deflection (`:active { transform: scale(0.96); }`) with `-webkit-tap-highlight-color: rgba(88, 166, 255, 0.15)` for tactile responsiveness.
- **Momentum & Scroll Safety**: Horizontal scroll containers (e.g. `.page-tabs`, tables) enforce `-webkit-overflow-scrolling: touch;` and `touch-action: pan-y;` to guarantee fluid 60fps scrolling without touch-freeze.

## Do's and Don'ts

### Do
- Always use CSS tokens (`var(--blue)`, `var(--text)`, `var(--border)`).
- Ensure all interactive elements have explicit `:focus-visible` styling with a 2px blue ring.
- Use `touch-action: manipulation;` on interactive controls to eliminate 300ms mobile tap delays.
- Keep the craft floor for functional text at 11px or higher.
- Maintain `color-scheme: dark;` on `:root` and `html`.
- Use the unicode ellipsis character `…` instead of three literal periods `...`.

### Don't
- Never introduce external CSS or JS frameworks (Tailwind, React, Vite, Webpack).
- Never use AI-tells such as thick colored 3px side-tab pseudo-stripes on cards.
- Never use unstyled `outline: none;` without providing an accessible `:focus-visible` alternative.
- Never hardcode fixed pixel widths on mobile viewports.
- Never animate non-composited properties (e.g., width, height, margin) when transforms or opacity can achieve the effect.
