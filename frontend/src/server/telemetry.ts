/**
 * OpenTelemetry for the web server (traces only, for the `/api` proxy).
 *
 * - `service.name` is `OTEL_SERVICE_NAME` or `belegbot-web`.
 * - OTLP HTTP/protobuf exporter, configured only by the standard env vars
 *   (`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`).
 * - No endpoint (or `OTEL_SDK_DISABLED=true`) → no exporter and no network calls. A tracer
 *   provider is installed either way, so `traceparent` is still sent upstream.
 */
import type { Tracer } from "@opentelemetry/api";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-proto";
import { resourceFromAttributes } from "@opentelemetry/resources";
import {
  BasicTracerProvider,
  BatchSpanProcessor,
  type SpanProcessor,
} from "@opentelemetry/sdk-trace-base";

export const DEFAULT_WEB_SERVICE_NAME = "belegbot-web";
const TRACER_NAME = "belegbot-web";

type Env = Record<string, string | undefined>;

function nonEmpty(value: string | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed ? trimmed : undefined;
}

export function otlpExportEnabled(env: Env): boolean {
  if (nonEmpty(env["OTEL_SDK_DISABLED"])?.toLowerCase() === "true") return false;
  return nonEmpty(env["OTEL_EXPORTER_OTLP_ENDPOINT"]) !== undefined;
}

export function webResourceAttributes(env: Env): Record<string, string> {
  const attributes: Record<string, string> = {
    "service.name": nonEmpty(env["OTEL_SERVICE_NAME"]) ?? DEFAULT_WEB_SERVICE_NAME,
    "deployment.environment.name":
      nonEmpty(env["RAILWAY_ENVIRONMENT_NAME"]) ?? nonEmpty(env["APP_ENV"]) ?? "development",
  };
  const commit = nonEmpty(env["GIT_SHA"]) ?? nonEmpty(env["RAILWAY_GIT_COMMIT_SHA"]);
  if (commit) attributes["vcs.ref.head.revision"] = commit;
  return attributes;
}

export interface WebTelemetry {
  provider: BasicTracerProvider;
  tracer: Tracer;
  exporting: boolean;
  shutdown: () => Promise<void>;
}

export interface WebTelemetryOptions {
  /** Extra span processors (tests: an in-memory exporter). */
  spanProcessors?: SpanProcessor[];
}

export function setupWebTelemetry(env: Env, options: WebTelemetryOptions = {}): WebTelemetry {
  const exporting = otlpExportEnabled(env);
  const spanProcessors: SpanProcessor[] = [...(options.spanProcessors ?? [])];
  if (exporting) {
    // Reads endpoint, headers and timeout from the standard OTEL_EXPORTER_OTLP_* env vars.
    spanProcessors.push(new BatchSpanProcessor(new OTLPTraceExporter()));
  }
  const provider = new BasicTracerProvider({
    resource: resourceFromAttributes(webResourceAttributes(env)),
    spanProcessors,
  });
  return {
    provider,
    tracer: provider.getTracer(TRACER_NAME),
    exporting,
    shutdown: () => provider.shutdown(),
  };
}
