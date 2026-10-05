import { createFileRoute } from "@tanstack/react-router";
import { getRequestIP } from "@tanstack/react-start/server";

import { initWebServer } from "@/server/web-server";

// Same-origin `/api/*` proxy to the FastAPI `api` service (see src/server/api-proxy.ts).
function handle({ request }: { request: Request }): Promise<Response> {
  let clientIp: string | undefined;
  try {
    clientIp = getRequestIP(); // the directly connected peer (Railway edge in production)
  } catch {
    clientIp = undefined;
  }
  return initWebServer().proxy(request, { clientIp });
}

export const Route = createFileRoute("/api/$")({
  server: {
    handlers: {
      GET: handle,
      HEAD: handle,
      POST: handle,
      PUT: handle,
      PATCH: handle,
      DELETE: handle,
      OPTIONS: handle,
    },
  },
});
