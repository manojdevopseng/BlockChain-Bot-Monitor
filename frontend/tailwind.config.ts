import type { Config } from "tailwindcss";

// Colours resolve to CSS variables (space-separated RGB triplets) so the whole
// UI re-themes by swapping variables on <html> — see app/globals.css. The
// `<alpha-value>` placeholder keeps opacity modifiers (bg-brand/15) working.
const v = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

const config: Config = {
  darkMode: "class",
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: {
          DEFAULT: v("bg"),
          soft: v("bg-soft"),
          card: v("bg-card"),
          hover: v("bg-hover"),
        },
        border: { DEFAULT: v("border"), soft: v("border-soft") },
        brand: { DEFAULT: v("brand"), soft: v("brand-soft") },
        accent: {
          green: v("accent-green"),
          red: v("accent-red"),
          amber: v("accent-amber"),
          blue: v("accent-blue"),
          purple: v("accent-purple"),
          cyan: v("accent-cyan"),
        },
        text: { DEFAULT: v("text"), muted: v("text-muted"), dim: v("text-dim") },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
      keyframes: {
        "fade-in": { from: { opacity: "0", transform: "translateY(4px)" }, to: { opacity: "1", transform: "translateY(0)" } },
        "pulse-soft": { "0%,100%": { opacity: "1" }, "50%": { opacity: "0.5" } },
        "slide-in": { from: { transform: "translateX(-100%)" }, to: { transform: "translateX(0)" } },
      },
      animation: {
        "fade-in": "fade-in 0.3s ease-out",
        "pulse-soft": "pulse-soft 2s ease-in-out infinite",
        "slide-in": "slide-in 0.2s ease-out",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};
export default config;
