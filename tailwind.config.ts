import type { Config } from 'tailwindcss';

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#162126',
        fog: '#f3f0e8',
        sand: '#d2c4ad',
        clay: '#c7744f',
        moss: '#4e6a57',
        tide: '#2d5f73',
        ember: '#8e392d',
        paper: '#faf8f3',
      },
      boxShadow: {
        soft: '0 24px 80px rgba(22, 33, 38, 0.12)',
        card: '0 1px 0 rgba(22,33,38,0.04), 0 8px 24px -16px rgba(22,33,38,0.18)',
      },
      fontFamily: {
        sans: ['"Aptos"', '"Segoe UI"', 'sans-serif'],
      },
    },
  },
  plugins: [],
} satisfies Config;
