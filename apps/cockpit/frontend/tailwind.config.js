/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: { DEFAULT: "#0a0d14", soft: "#0f1320", panel: "#141926", elev: "#1a2030" },
        line: "#222a3a",
        ink: { DEFAULT: "#e6ebf4", dim: "#9aa6bd", faint: "#6b7689" },
        accent: { DEFAULT: "#5b8cff", soft: "#2a3b6b" },
        ok: "#3fb950",
        warn: "#d6a121",
        bad: "#f0556a",
        run: "#4aa3ff",
      },
      fontFamily: {
        mono: ["JetBrains Mono", "SF Mono", "Cascadia Code", "Consolas", "monospace"],
        sans: ["Inter", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
      },
      boxShadow: {
        panel: "0 1px 0 rgba(255,255,255,0.03) inset, 0 8px 24px -12px rgba(0,0,0,0.6)",
        glow: "0 0 12px -2px var(--tw-shadow-color)",
      },
      borderRadius: { xl2: "14px" },
    },
  },
  plugins: [],
};
