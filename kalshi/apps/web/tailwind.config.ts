import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: "#f5f5f7",
          50: "rgba(255, 255, 255, 0.70)",
          100: "rgba(255, 255, 255, 0.86)",
          200: "#ffffff",
          300: "rgba(15, 23, 42, 0.10)",
        },
        accent: {
          green: "rgba(52, 199, 89, 0.95)",
          red: "rgba(255, 59, 48, 0.95)",
          yellow: "rgba(255, 159, 10, 0.95)",
          blue: "rgba(0, 122, 255, 0.95)",
          purple: "#8b5cf6",
        },
        score: {
          low: "#22c55e",
          medium: "#f59e0b",
          high: "#ef4444",
          critical: "#dc2626",
        },
        raven: {
          bg: "#f5f5f7",
          bg2: "#ffffff",
          text: "#0b0b0f",
          muted: "rgba(11, 11, 15, 0.62)",
          muted2: "rgba(11, 11, 15, 0.48)",
          border: "rgba(15, 23, 42, 0.10)",
          borderStrong: "rgba(15, 23, 42, 0.14)",
          line: "rgba(15, 23, 42, 0.08)",
          hover: "rgba(2, 6, 23, 0.03)",
        },
      },
      fontFamily: {
        sans: ["-apple-system", "BlinkMacSystemFont", "SF Pro Text", "Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "SF Mono", "Fira Code", "monospace"],
      },
      borderRadius: {
        card: "18px",
        control: "16px",
      },
      boxShadow: {
        glass: "0 1px 0 rgba(255,255,255,0.85) inset, 0 10px 30px rgba(15, 20, 30, 0.08)",
        card: "0 10px 30px rgba(15, 20, 30, 0.08)",
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
