import typography from '@tailwindcss/typography';

const themeColor = variable => ({ opacityValue } = {}) => {
  if (opacityValue === undefined) return `var(${variable})`;
  return `color-mix(in srgb, var(${variable}) ${Number(opacityValue) * 100}%, transparent)`;
};

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
        muted: 'var(--color-bg-subtle)',
      },
      textColor: {
        default: 'var(--color-text)',
        muted: 'var(--color-text-muted)',
        soft: 'var(--color-text-soft)',
        foreground: 'var(--color-text)',
        'muted-foreground': 'var(--color-text-muted)',
        midground: themeColor('--dt-midground'),
      },
      borderColor: {
        default: 'var(--color-border)',
        subtle: 'var(--color-border-subtle)',
        border: 'var(--color-border)',
      },
      colors: {
        border: 'var(--color-border)',
        foreground: 'var(--color-text)',
        'muted-foreground': 'var(--color-text-muted)',
        midground: themeColor('--dt-midground'),
        muted: 'var(--color-bg-subtle)',
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
  plugins: [typography],
}
