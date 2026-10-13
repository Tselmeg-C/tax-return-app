import { defineConfig } from "vite";
import { tanstackStart } from "@tanstack/react-start/plugin/vite";
import viteReact from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import tsConfigPaths from "vite-tsconfig-paths";
import { nitro } from "nitro/vite";

export default defineConfig({
  // Dev only. Codespaces forwards `https://<name>-3000.app.github.dev` to the container's
  // network interface, so listen on all interfaces there and allow that host (#51).
  server: {
    port: 3000,
    allowedHosts: [".app.github.dev"],
    ...(process.env["CODESPACES"] ? { host: true } : {}),
  },
  plugins: [
    tsConfigPaths(),
    tailwindcss(),
    tanstackStart(),
    // Node server build target: emits .output/server/index.mjs (`npm run start`).
    nitro({ preset: "node-server", plugins: ["./src/server/nitro-startup.ts"] }),
    viteReact(),
  ],
});
