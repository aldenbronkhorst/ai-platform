/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        neutral: {
          750: '#2a2a2a',
          850: '#1e1e1e',
        },
      },
      backgroundColor: {
        canvas: 'var(--color-canvas)',
        surface: 'var(--color-surface)',
        raised: 'var(--color-raised)',
        sidebar: 'var(--color-sidebar)',
        glass: 'var(--color-glass)',
        'glass-hover': 'var(--color-glass-hover)',
      },
      textColor: {
        default: 'var(--color-text)',
        muted: 'var(--color-text-muted)',
        soft: 'var(--color-text-soft)',
      },
      borderColor: {
        default: 'var(--color-border)',
        subtle: 'var(--color-border-subtle)',
      },
    },
  },
  plugins: [],
}
