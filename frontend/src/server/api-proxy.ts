/**
 * Same-origin `/api` proxy: `/api/<path>?<query>` → `${API_INTERNAL_URL}/<path>?<query>`.
 *
 * - The upstream host always comes from `API_INTERNAL_URL`; a crafted path can never change it.
 * - Bodies are streamed both ways; status, headers (incl. multiple `Set-Cookie`) and
 *   redirects (not followed) are passed through unchanged.
 * - `X-Forwarded-For` is **replaced** with one address (the api's rate-limit key): the socket
 *   peer by default, or with `TRUSTED_PROXY_HOPS=n` the n-th entry from the right of the
 *   incoming header (Railway's edge appends the real client, so n=1 there). A client can never
 *   choose its own value.
 * - Unreachable upstream → 502, no response headers within the timeout → 504,
 *   no `API_INTERNAL_URL` in production → 503.
 * - One span per request (`web /api proxy`); its W3C `traceparent` is sent upstream so the
 *   api span becomes its child. Spans and logs never carry query strings, cookies or bodies.
 */
import {
  ROOT_CONTEXT,
  SpanKind,
  SpanStatusCode,
  trace,
  type Context,
  type TextMapGetter,
  type TextMapSetter,
  type Tracer,
} from "@opentelemetry/api";
import { W3CTraceContextPropagator } from "@opentelemetry/core";

export const API_PREFIX = "/api";
export const DEFAULT_DEV_API_URL = "http://localhost:8000";
export const DEFAULT_UPSTREAM_TIMEOUT_MS = 30_000;
export const PROXY_SPAN_NAME = "web /api proxy";

const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
  "te",
  "trailer",
  "trailers",
  "proxy-authenticate",
  "proxy-authorization",
  "proxy-connection",
]);

/** Request headers that are recomputed for the upstream request instead of copied. */
const RECOMPUTED_REQUEST_HEADERS = new Set([
  "host",
  "content-length",
  "accept-encoding",
  "x-forwarded-for",
  "x-forwarded-proto",
  "x-forwarded-host",
  "traceparent",
  "tracestate",
]);

const NULL_BODY_STATUSES = new Set([101, 204, 205, 304]);

export type ApiUrlResolution =
  { kind: "configured"; url: URL } | { kind: "not-configured" } | { kind: "invalid" };

/** `API_INTERNAL_URL`, or the local default outside production. */
export function resolveApiUrl(env: Record<string, string | undefined>): ApiUrlResolution {
  const raw = env["API_INTERNAL_URL"]?.trim();
  if (!raw) {
    if (env["NODE_ENV"] === "production") return { kind: "not-configured" };
    return { kind: "configured", url: new URL(DEFAULT_DEV_API_URL) };
  }
  try {
    const url = new URL(raw);
    if (url.protocol !== "http:" && url.protocol !== "https:") return { kind: "invalid" };
    return { kind: "configured", url };
  } catch {
    return { kind: "invalid" };
  }
}

/** `TRUSTED_PROXY_HOPS`: a non-negative integer, else 0 (trust no forwarded header). */
export function resolveTrustedProxyHops(env: Record<string, string | undefined>): number {
  const raw = env["TRUSTED_PROXY_HOPS"]?.trim();
  const value = raw ? Number(raw) : 0;
  return Number.isInteger(value) && value >= 0 ? value : 0;
}

/**
 * The single client address sent upstream. With `trustedHops` > 0 it is the entry that the
 * trusted proxy closest to us appended (n-th from the right); without enough entries, or with
 * 0 hops, it is the socket peer.
 */
export function forwardedClientIp(
  incomingForwardedFor: string | null,
  clientIp: string | undefined,
  trustedHops: number,
): string | undefined {
  if (trustedHops > 0 && incomingForwardedFor) {
    const entries = incomingForwardedFor
      .split(",")
      .map((e) => e.trim())
      .filter(Boolean);
    const picked = entries[entries.length - trustedHops];
    if (picked) return picked;
  }
  return clientIp;
}

/** Upstream response-head timeout: `API_PROXY_TIMEOUT_MS` if a positive integer, else 30 s. */
export function resolveTimeoutMs(env: Record<string, string | undefined>): number {
  const raw = env["API_PROXY_TIMEOUT_MS"]?.trim();
  const value = raw ? Number(raw) : NaN;
  return Number.isInteger(value) && value > 0 ? value : DEFAULT_UPSTREAM_TIMEOUT_MS;
}

/**
 * Map an incoming `/api…` URL onto the upstream base. Returns null for paths outside `/api`.
 * Only the path and query are taken from the incoming URL; scheme, host and port always come
 * from `base` (the pathname setter cannot change the host, e.g. for `//evil.example/x`).
 */
export function buildUpstreamUrl(base: URL, incoming: URL): URL | null {
  const path = incoming.pathname;
  if (path !== API_PREFIX && !path.startsWith(`${API_PREFIX}/`)) return null;
  const rest = path.slice(API_PREFIX.length) || "/";
  const target = new URL(base.href);
  const basePath = target.pathname.replace(/\/+$/, "");
  target.pathname = `${basePath}${rest}`;
  target.search = incoming.search;
  target.hash = "";
  if (target.origin !== base.origin) return null; // defence in depth; cannot happen
  return target;
}

function connectionTokens(headers: Headers): Set<string> {
  const value = headers.get("connection");
  if (!value) return new Set();
  return new Set(
    value
      .split(",")
      .map((t) => t.trim().toLowerCase())
      .filter(Boolean),
  );
}

function isHopByHop(name: string, listed: Set<string>): boolean {
  return HOP_BY_HOP.has(name) || name.startsWith("proxy-") || listed.has(name);
}

export function buildUpstreamHeaders(
  incoming: Request,
  incomingUrl: URL,
  clientIp: string | undefined,
  trustedHops = 0,
): Headers {
  const listed = connectionTokens(incoming.headers);
  const out = new Headers();
  incoming.headers.forEach((value, key) => {
    const name = key.toLowerCase();
    if (isHopByHop(name, listed) || RECOMPUTED_REQUEST_HEADERS.has(name)) return;
    out.append(name, value);
  });
  out.set("accept-encoding", "identity");

  const forwardedFor = forwardedClientIp(
    incoming.headers.get("x-forwarded-for"),
    clientIp,
    trustedHops,
  );
  if (forwardedFor) out.set("x-forwarded-for", forwardedFor);
  out.set(
    "x-forwarded-proto",
    incoming.headers.get("x-forwarded-proto") ?? incomingUrl.protocol.replace(/:$/, ""),
  );
  out.set(
    "x-forwarded-host",
    incoming.headers.get("x-forwarded-host") ?? incoming.headers.get("host") ?? incomingUrl.host,
  );
  return out;
}

export function buildClientHeaders(upstream: Headers): Headers {
  const listed = connectionTokens(upstream);
  const out = new Headers();
  const decoded = upstream.has("content-encoding"); // fetch already decompressed the body
  upstream.forEach((value, key) => {
    const name = key.toLowerCase();
    if (name === "set-cookie") return; // appended one by one below
    if (isHopByHop(name, listed)) return;
    if (decoded && (name === "content-encoding" || name === "content-length")) return;
    out.append(name, value);
  });
  for (const cookie of upstream.getSetCookie()) out.append("set-cookie", cookie);
  return out;
}

function jsonError(status: number, detail: string): Response {
  return Response.json({ status: "error", detail }, { status });
}

const headerGetter: TextMapGetter<Headers> = {
  keys: (carrier) => [...carrier.keys()],
  get: (carrier, key) => carrier.get(key) ?? undefined,
};

const headerSetter: TextMapSetter<Headers> = {
  set: (carrier, key, value) => carrier.set(key, value),
};

export interface ApiProxyOptions {
  /** Upstream base URL, or null when `API_INTERNAL_URL` is required but missing. */
  apiUrl: URL | null;
  tracer: Tracer;
  timeoutMs?: number;
  /** `TRUSTED_PROXY_HOPS` (see `forwardedClientIp`); default 0. */
  trustedProxyHops?: number;
  fetchImpl?: typeof fetch;
  /** Warnings for upstream failures; never receives bodies, cookies or query strings. */
  warn?: (message: string, fields: Record<string, string | number>) => void;
}

export interface ProxyRequestInfo {
  /** Address of the directly connected peer (the `X-Forwarded-For` value by default). */
  clientIp?: string | undefined;
}

export type ApiProxy = (request: Request, info?: ProxyRequestInfo) => Promise<Response>;

const defaultWarn = (message: string, fields: Record<string, string | number>) => {
  console.warn(JSON.stringify({ level: "warning", event: message, ...fields }));
};

export function createApiProxy(options: ApiProxyOptions): ApiProxy {
  const { apiUrl, tracer } = options;
  const timeoutMs = options.timeoutMs ?? DEFAULT_UPSTREAM_TIMEOUT_MS;
  const trustedHops = options.trustedProxyHops ?? 0;
  const fetchImpl = options.fetchImpl ?? fetch;
  const warn = options.warn ?? defaultWarn;
  const propagator = new W3CTraceContextPropagator();

  return async function proxy(request, info = {}) {
    const method = request.method.toUpperCase();
    const parent: Context = propagator.extract(ROOT_CONTEXT, request.headers, headerGetter);
    const span = tracer.startSpan(
      PROXY_SPAN_NAME,
      {
        kind: SpanKind.CLIENT,
        attributes: { "http.request.method": method, "http.route": `${API_PREFIX}/*` },
      },
      parent,
    );

    const finish = (response: Response, errorType?: string): Response => {
      span.setAttribute("http.response.status_code", response.status);
      if (errorType) {
        span.setAttribute("error.type", errorType);
        span.setStatus({ code: SpanStatusCode.ERROR });
      }
      span.end();
      return response;
    };

    if (apiUrl === null) return finish(jsonError(503, "api not configured"), "not_configured");

    const incomingUrl = new URL(request.url);
    const target = buildUpstreamUrl(apiUrl, incomingUrl);
    if (target === null) return finish(jsonError(404, "not found"), "not_found");

    const headers = buildUpstreamHeaders(request, incomingUrl, info.clientIp, trustedHops);
    propagator.inject(trace.setSpan(parent, span), headers, headerSetter);

    const hasBody = method !== "GET" && method !== "HEAD" && request.body !== null;
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);

    let upstream: Response;
    try {
      const init: RequestInit & { duplex?: "half" } = {
        method,
        headers,
        redirect: "manual",
        signal: controller.signal,
      };
      if (hasBody) {
        init.body = request.body;
        init.duplex = "half"; // stream the request body instead of buffering it
      }
      upstream = await fetchImpl(target, init);
    } catch (error) {
      const kind = timedOut ? "timeout" : "unavailable";
      warn(timedOut ? "api proxy: upstream timeout" : "api proxy: upstream unavailable", {
        method,
        route: `${API_PREFIX}/*`,
        error: error instanceof Error ? error.name : "unknown",
      });
      return finish(
        timedOut ? jsonError(504, "upstream timeout") : jsonError(502, "upstream unavailable"),
        kind,
      );
    } finally {
      // Only waiting for the response head is bounded; a streaming body may take longer.
      clearTimeout(timer);
    }

    const nullBody = method === "HEAD" || NULL_BODY_STATUSES.has(upstream.status);
    const response = new Response(nullBody ? null : upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: buildClientHeaders(upstream.headers),
    });
    return finish(response, upstream.status >= 500 ? String(upstream.status) : undefined);
  };
}
