/**
 * Process-wide web server state: telemetry and the `/api` proxy, created once on first use.
 * Startup log lines (missing `API_INTERNAL_URL`, export disabled) come from the Nitro plugin
 * in `nitro-startup.ts`, so they are logged at server start and only once.
 */
import { createApiProxy, resolveApiUrl, type ApiProxy } from "./api-proxy";
import { setupWebTelemetry, type WebTelemetry } from "./telemetry";

interface WebServer {
  proxy: ApiProxy;
  telemetry: WebTelemetry;
}

let state: WebServer | undefined;

export function initWebServer(env: Record<string, string | undefined> = process.env): WebServer {
  if (state) return state;

  const telemetry = setupWebTelemetry(env);
  const resolution = resolveApiUrl(env);
  const proxy = createApiProxy({
    apiUrl: resolution.kind === "configured" ? resolution.url : null,
    tracer: telemetry.tracer,
  });

  // Flush pending spans on shutdown (best effort; the server's own handler exits the process).
  const flush = () => void telemetry.provider.forceFlush().catch(() => undefined);
  process.once("SIGTERM", flush);
  process.once("SIGINT", flush);

  state = { proxy, telemetry };
  return state;
}
