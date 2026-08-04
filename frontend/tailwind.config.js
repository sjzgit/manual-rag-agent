/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,ts}'],
  theme: {
    extend: {
      colors: {
        primary: {
          DEFAULT: '#3B5BFD',
          light: '#5B7CFF',
          dark: '#2E49D6',
        },
        surface: '#F5F7FB',
        card: '#FFFFFF',
        muted: '#F0F3FA',
        ink: {
          DEFAULT: '#1F2329',
          sub: '#646A73',
        },
        success: '#34A853',
        danger: '#F5222D',
        warning: '#FA8C16',
      },
      fontFamily: {
        sans: ['"PingFang SC"', '"Microsoft YaHei"', '"Noto Sans SC"', 'sans-serif'],
      },
      boxShadow: {
        glass: '0 8px 32px rgba(31, 35, 41, 0.08)',
        lift: '0 12px 40px rgba(59, 91, 253, 0.12)',
      },
    },
  },
  plugins: [require('tailwindcss-animate')],
}
