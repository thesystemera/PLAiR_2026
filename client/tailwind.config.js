export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        dark: {
          bg: '#0a0a0a',
          card: 'rgba(0, 0, 0, 0.4)',
          hover: 'rgba(0, 0, 0, 0.6)',
        },
      },
    },
  },
  plugins: [],
}