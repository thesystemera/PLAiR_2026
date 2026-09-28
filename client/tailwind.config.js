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
      transitionDuration: {
        tap: '90ms',
        micro: '140ms',
        quick: '200ms',
        base: '300ms',
        fade: '500ms',
        progress: '100ms',
        theme: '700ms',
      },
      transitionTimingFunction: {
        decelerate: 'cubic-bezier(0.2, 0.8, 0.2, 1)',
        accelerate: 'cubic-bezier(0.4, 0, 1, 1)',
        emphasized: 'cubic-bezier(0.4, 0, 0.2, 1)',
        pop: 'cubic-bezier(0.34, 1.56, 0.64, 1)',
      },
      transitionProperty: {
        DEFAULT: 'color, background-color, border-color, text-decoration-color, fill, stroke, opacity, box-shadow, transform, filter, backdrop-filter, scale',
        colors: 'color, background-color, border-color, text-decoration-color, fill, stroke, scale',
      },
    },
  },
  plugins: [],
}
