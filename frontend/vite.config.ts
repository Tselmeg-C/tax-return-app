import { defineConfig } from "vite";
import { tanstackStart } from "@tanstack/react-start/plugin/vite";
import viteReact from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import tsConfigPaths from "vite-tsconfig-paths";
import { nitro } from "nitro/vite";

export default defineConfig({
  server: { port: 3000 },
  plugins: [
    tsConfigPaths(),
    tailwindcss(),
    tanstackStart(),
    // Node server build target: emits .output/server/index.mjs (`npm run start`).
    nitro({ preset: "node-server", plugins: ["./src/server/nitro-startup.ts"] }),
    viteReact(),
  ],
});
