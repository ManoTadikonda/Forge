/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // A restrained palette: one accent, neutral surfaces, and semantic
        // colours reserved for state so they keep their meaning.
        surface: {
          DEFAULT: '#0b0e14',
          raised: '#121722',
          overlay: '#1a2130',
          border: '#232b3b',
        },
        ink: {
          DEFAULT: '#e6eaf2',
          muted: '#95a0b5',
          faint: '#64708a',
        },
        accent: {
          DEFAULT: '#4c8dff',
          soft: '#1e3153',
        },
        ok: { DEFAULT: '#3ecf8e', soft: '#10291f' },
        warn: { DEFAULT: '#f5a623', soft: '#2e2412' },
        danger: { DEFAULT: '#ff5c5c', soft: '#331818' },
        info: { DEFAULT: '#7aa2f7', soft: '#1a2235' },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
      animation: {
        'pulse-soft': 'pulse 2.4s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      },
    },
  },
  plugins: [],
}
