/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: ['selector', '[data-theme="dark"]'],
  theme: {
    extend: {
      backgroundColor: {
        canvas: 'var(--color-bg)',
        subtle: 'var(--color-bg-subtle)',
        surface: 'var(--color-surface)',
        raised: 'var(--color-surface-raised)',
        sidebar: 'var(--color-sidebar)',
        glass: 'var(--glass-bg)',
        'glass-hover': 'var(--glass-bg-strong)',
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
      keyframes: {
        'fade-in': {
          '0%': { opacity: '0', transform: 'translateY(4px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        'fade-in': 'fade-in 0.25s ease-out',
      },
    },
  },
  plugins: [],
}
