import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: "#0a0a0f",
          50: "#111118",
          100: "#18181f",
          200: "#1e1e28",
          300: "#2a2a36",
        },
        accent: {
          green: "#10b981",
          red: "#ef4444",
          yellow: "#f59e0b",
          blue: "#3b82f6",
          purple: "#8b5cf6",
        },
        score: {
          low: "#22c55e",
          medium: "#f59e0b",
          high: "#ef4444",
          critical: "#dc2626",
        },
      },
      fontFamily: {
        mono: ["JetBrains Mono", "Fira Code", "monospace"],
      },
      animation: {
        "pulse-alert": "pulse-alert 2s ease-in-out infinite",
        "slide-in": "slide-in 0.3s ease-out",
      },
      keyframes: {
        "pulse-alert": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.7" },
        },
        "slide-in": {
          "0%": { transform: "translateY(-10px)", opacity: "0" },
          "100%": { transform: "translateY(0)", opacity: "1" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
