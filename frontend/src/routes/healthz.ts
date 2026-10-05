import { createFileRoute } from "@tanstack/react-router";

// Health check for the Railway `web` service. No upstream call, so a down api or DB
// never blocks a web deploy; `/api/health` is the end-to-end check.
export const Route = createFileRoute("/healthz")({
  server: {
    handlers: {
      GET: () => Response.json({ status: "ok" }),
    },
  },
});
