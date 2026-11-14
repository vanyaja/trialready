import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: { DEFAULT: "#0f172a", raised: "#16213a", sunk: "#0b1120", line: "#26334f" },
        ink: { DEFAULT: "#f0efec", soft: "#c3c7d1", mute: "#8591a8" },
        brand: { DEFAULT: "#3987e5", soft: "#1d3a66" },
        census: "#d95926",
        good: "#0ca30c", warn: "#fab219", serious: "#ec835a", critical: "#d03b3b",
      },
      fontFamily: { sans: ["Inter", "ui-sans-serif", "system-ui", "Segoe UI", "Roboto", "sans-serif"] },
    },
  },
  plugins: [],
};
export default config;
