/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: ['./matador/templates/**/*.html'],
  // The state->colour macros build class names dynamically (e.g. `bg-{{token}}`),
  // which the static scanner can't see — safelist the status utilities they emit.
  safelist: [
    ...['info', 'success', 'warning', 'danger', 'muted'].flatMap((c) => [
      `text-${c}`, `bg-${c}`, `bg-${c}/10`, `ring-${c}/30`,
    ]),
  ],
  theme: {
    extend: {
      colors: {
        // Surfaces + content
        ink: 'rgb(var(--ink) / <alpha-value>)',
        panel: 'rgb(var(--panel) / <alpha-value>)',
        panel2: 'rgb(var(--panel2) / <alpha-value>)',
        line: 'rgb(var(--line) / <alpha-value>)',
        fg: 'rgb(var(--fg) / <alpha-value>)',
        muted: 'rgb(var(--muted) / <alpha-value>)',
        // Brand accent (one colour, used sparingly) + semantic status colours
        accent: 'rgb(var(--accent) / <alpha-value>)',
        info: 'rgb(var(--info) / <alpha-value>)',
        success: 'rgb(var(--success) / <alpha-value>)',
        warning: 'rgb(var(--warning) / <alpha-value>)',
        danger: 'rgb(var(--danger) / <alpha-value>)',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      // Entry animations live here (the Tailwind home for keyframes), used as
      // animate-acc-in / animate-dlg-in component utilities.
      keyframes: {
        accIn: { from: { opacity: '0', transform: 'translateY(-3px)' } },
        dlgIn: { from: { opacity: '0', transform: 'translateY(-6px) scale(0.985)' } },
      },
      animation: {
        'acc-in': 'accIn 0.18s ease',
        'dlg-in': 'dlgIn 0.15s ease',
      },
    },
  },
};
