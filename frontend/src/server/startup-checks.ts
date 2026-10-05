import { resolveApiUrl } from "./api-proxy";
import { otlpExportEnabled } from "./telemetry";

export interface StartupLogLine {
  level: "info" | "warning";
  event: string;
}

/** Lines the web server logs once at startup (no values, only what is missing). */
export function startupLogLines(env: Record<string, string | undefined>): StartupLogLine[] {
  const lines: StartupLogLine[] = [];
  if (!otlpExportEnabled(env)) lines.push({ level: "info", event: "otel export disabled" });
  const api = resolveApiUrl(env);
  if (api.kind === "not-configured") {
    lines.push({
      level: "warning",
      event: "API_INTERNAL_URL is not set: /api/* answers 503 (api not configured)",
    });
  } else if (api.kind === "invalid") {
    lines.push({
      level: "warning",
      event: "API_INTERNAL_URL is not a valid http(s) URL: /api/* answers 503",
    });
  }
  return lines;
}

export function logStartup(env: Record<string, string | undefined>): void {
  for (const { level, event } of startupLogLines(env)) {
    const line = JSON.stringify({ timestamp: new Date().toISOString(), level, event });
    if (level === "warning") console.warn(line);
    else console.log(line);
  }
}
