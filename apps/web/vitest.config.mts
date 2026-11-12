import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    // next-intl imports "next/server" and "next/navigation" without an extension, which Node's ESM
    // resolver refuses. Letting Vite process it resolves them the way Next.js does.
    server: { deps: { inline: ["next-intl"] } },
  },
});
