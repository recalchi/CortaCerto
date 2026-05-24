/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // CortaCerto dark theme (design handoff palette + CapCut timeline)
        bg: {
          DEFAULT: "#0a090c",
          surface: "#17141f",
          panel:   "#100e16",
          rail:    "#0d0b12",
        },
        accent: {
          DEFAULT: "#8B6BFF",
          hover:   "#9C82FF",
          light:   "#b09dff",
          soft:    "rgba(139,107,255,0.18)",
          glow:    "rgba(139,107,255,0.45)",
        },
        border: {
          DEFAULT: "rgba(180,160,255,0.12)",
          light:   "rgba(180,160,255,0.2)",
        },
        text: {
          DEFAULT: "#ffffff",
          muted:   "#888899",
          dim:     "#4a4a62",
        },
      },
      backdropBlur: { glass: '14px' },
    },
  },
  plugins: [],
}
