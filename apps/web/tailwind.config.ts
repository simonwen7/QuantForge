import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#0c0e0d",
        panel: "#141716",
        raised: "#1c201e",
        line: "rgba(243, 239, 230, 0.12)",
        paper: "#f3efe6",
        mute: "#a39b8d",
        tide: "#3dbeb6",
        loss: "#c4655a",
      },
      fontFamily: {
        sans: ['"IBM Plex Sans"', "sans-serif"],
        mono: ['"IBM Plex Mono"', "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
