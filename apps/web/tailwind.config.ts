import type { Config } from "tailwindcss";

export default {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Teks & aksi utama: navy yang tenang, bukan hitam pekat
        ink: { DEFAULT: "#1B2A41", soft: "#3A4A60", muted: "#5E6B7A", line: "#33445A" },
        // Kanvas & garis: abu terang yang lembut di mata untuk dipakai seharian
        concrete: { DEFAULT: "#F4F6F5", dark: "#E2E7E5", line: "#CDD5D2" },
        // Kuning hanya untuk hal yang butuh perhatian / posisi aktif
        signal: { DEFAULT: "#F5C400", dark: "#B38F00", soft: "#FFF6CC" },
        ok: "#1E7A4C",
        danger: "#B3261E",
      },
      fontFamily: {
        display: ["'Instrument Sans'", "system-ui", "sans-serif"],
        sans: ["'Instrument Sans'", "system-ui", "sans-serif"],
        brand: ["Archivo", "system-ui", "sans-serif"],
        mono: ["'JetBrains Mono'", "ui-monospace", "monospace"],
      },
      boxShadow: {
        card: "0 1px 2px rgba(27,42,65,0.06), 0 1px 1px rgba(27,42,65,0.04)",
        pop: "0 12px 32px -8px rgba(27,42,65,0.25)",
      },
    },
  },
  plugins: [],
} satisfies Config;
