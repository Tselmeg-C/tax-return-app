// @vitest-environment node
import http from "node:http";
import type { AddressInfo } from "node:net";
import { randomBytes } from "node:crypto";

import { trace } from "@opentelemetry/api";
import { InMemorySpanExporter, SimpleSpanProcessor } from "@opentelemetry/sdk-trace-base";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import {
  buildUpstreamUrl,
  createApiProxy,
  resolveApiUrl,
  PROXY_SPAN_NAME,
  type ApiProxy,
} from "./api-proxy";
import { setupWebTelemetry, webResourceAttributes } from "./telemetry";
import { startupLogLines } from "./startup-checks";

interface Received {
  method: string;
  url: string;
  headers: http.IncomingHttpHeaders;
  body: Buffer;
}

type StubHandler = (req: http.IncomingMessage, res: http.ServerResponse, body: Buffer) => void;

let server: http.Server;
let baseUrl: URL;
let received: Received[] = [];
let handler: StubHandler;

const defaultHandler: StubHandler = (req, res) => {
  res.setHeader("content-type", "application/json");
  res.end(JSON.stringify({ path: req.url }));
};

beforeAll(async () => {
  server = http.createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => {
      const body = Buffer.concat(chunks);
      received.push({ method: req.method ?? "", url: req.url ?? "", headers: req.headers, body });
      handler(req, res, body);
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as AddressInfo;
  baseUrl = new URL(`http://127.0.0.1:${port}`);
});

afterAll(async () => {
  server.closeAllConnections();
  await new Promise<void>((resolve) => server.close(() => resolve()));
});

afterEach(() => {
  received = [];
  handler = defaultHandler;
});
handler = defaultHandler;

function makeProxy(overrides: Partial<Parameters<typeof createApiProxy>[0]> = {}): ApiProxy {
  return createApiProxy({
    apiUrl: baseUrl,
    tracer: trace.getTracer("test"),
    warn: () => {},
    ...overrides,
  });
}

function req(path: string, init: RequestInit & { duplex?: "half" } = {}): Request {
  return new Request(`http://web.local${path}`, init);
}

describe("resolveApiUrl", () => {
  it("defaults to localhost:8000 outside production", () => {
    const r = resolveApiUrl({});
    expect(r.kind === "configured" && r.url.href).toBe("http://localhost:8000/");
  });

  it("is not configured in production without API_INTERNAL_URL", () => {
    expect(resolveApiUrl({ NODE_ENV: "production" }).kind).toBe("not-configured");
    expect(resolveApiUrl({ NODE_ENV: "production", API_INTERNAL_URL: "  " }).kind).toBe(
      "not-configured",
    );
  });

  it("rejects non-http URLs", () => {
    expect(resolveApiUrl({ API_INTERNAL_URL: "file:///etc/passwd" }).kind).toBe("invalid");
    expect(resolveApiUrl({ API_INTERNAL_URL: "not a url" }).kind).toBe("invalid");
  });
});

describe("buildUpstreamUrl", () => {
  const base = new URL("http://api.internal:8000");
  const map = (path: string) => buildUpstreamUrl(base, new URL(`http://web.local${path}`))?.href;

  it("strips the /api prefix and keeps the query", () => {
    expect(map("/api/health")).toBe("http://api.internal:8000/health");
    expect(map("/api/version?x=1")).toBe("http://api.internal:8000/version?x=1");
    expect(map("/api")).toBe("http://api.internal:8000/");
    expect(map("/api/")).toBe("http://api.internal:8000/");
  });

  it("never changes the upstream host", () => {
    for (const path of [
      "/api//evil.example/x",
      "/api/http://evil.example/",
      "/api/../x",
      "/api/%2e%2e/x",
      "/api/@evil.example",
      "/api/\\\\evil.example/x",
    ]) {
      // Dot segments are resolved by URL parsing: `/api/../x` becomes `/x`, which is not
      // proxied at all (null). Everything else must keep the upstream host.
      const url = buildUpstreamUrl(base, new URL(`http://web.local${path}`));
      if (url !== null) expect(url.host, path).toBe("api.internal:8000");
    }
  });

  it("does not proxy dot-segment escapes", () => {
    expect(map("/api/../x")).toBeUndefined();
  });

  it("ignores paths outside /api", () => {
    expect(map("/apix")).toBeUndefined();
    expect(map("/")).toBeUndefined();
  });
});

describe("api proxy against a stub upstream", () => {
  it("strips the prefix and passes the query through", async () => {
    const res = await makeProxy()(req("/api/version?x=1&y=two"));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ path: "/version?x=1&y=two" });
    expect(received[0]?.url).toBe("/version?x=1&y=two");
  });

  it("maps /api and /api/ to /", async () => {
    await makeProxy()(req("/api"));
    await makeProxy()(req("/api/"));
    expect(received.map((r) => r.url)).toEqual(["/", "/"]);
  });

  it("streams a 1 MB POST body byte-identical", async () => {
    const payload = randomBytes(1024 * 1024);
    const stream = new Blob([payload]).stream();
    const res = await makeProxy()(
      req("/api/upload", {
        method: "POST",
        body: stream,
        duplex: "half",
        headers: { "content-type": "application/octet-stream" },
      }),
    );
    expect(res.status).toBe(200);
    expect(received[0]?.method).toBe("POST");
    expect(received[0]?.headers["content-type"]).toBe("application/octet-stream");
    expect(Buffer.compare(received[0]!.body, payload)).toBe(0);
  });

  it("returns both upstream Set-Cookie headers", async () => {
    handler = (_req, res) => {
      res.setHeader("set-cookie", ["a=1; Path=/; HttpOnly", "b=2; Path=/"]);
      res.end("ok");
    };
    const res = await makeProxy()(req("/api/login"));
    expect(res.headers.getSetCookie()).toEqual(["a=1; Path=/; HttpOnly", "b=2; Path=/"]);
  });

  it("forwards cookies, content type and accept, and passes X-Trace-Id back", async () => {
    handler = (_req, res) => {
      res.setHeader("x-trace-id", "0123456789abcdef0123456789abcdef");
      res.end("ok");
    };
    const res = await makeProxy()(
      req("/api/me", { headers: { cookie: "s=abc", accept: "application/json" } }),
    );
    expect(received[0]?.headers["cookie"]).toBe("s=abc");
    expect(received[0]?.headers["accept"]).toBe("application/json");
    expect(received[0]?.headers["accept-encoding"]).toBe("identity");
    expect(res.headers.get("x-trace-id")).toBe("0123456789abcdef0123456789abcdef");
  });

  it("returns an upstream 302 with Location instead of following it", async () => {
    handler = (_req, res) => {
      res.statusCode = 302;
      res.setHeader("location", "/elsewhere");
      res.end();
    };
    const res = await makeProxy()(req("/api/redirect"));
    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe("/elsewhere");
    expect(received).toHaveLength(1);
  });

  it("keeps an upstream 503", async () => {
    handler = (_req, res) => {
      res.statusCode = 503;
      res.setHeader("content-type", "application/json");
      res.end(JSON.stringify({ status: "error", db: "error" }));
    };
    const res = await makeProxy()(req("/api/health"));
    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ status: "error", db: "error" });
  });

  it("does not forward hop-by-hop headers", async () => {
    handler = (_req, res) => {
      res.setHeader("keep-alive", "timeout=5");
      res.setHeader("x-upstream-only", "1");
      res.setHeader("connection", "x-upstream-only");
      res.end("ok");
    };
    const res = await makeProxy()(
      req("/api/h", {
        headers: {
          connection: "x-custom-hop",
          "keep-alive": "timeout=5",
          "proxy-authorization": "Basic abc",
          te: "trailers",
          trailer: "x-foo",
          upgrade: "websocket",
          "x-custom-hop": "1",
          "x-end-to-end": "kept",
        },
      }),
    );
    const headers = received[0]!.headers;
    for (const name of ["keep-alive", "proxy-authorization", "te", "trailer", "upgrade"]) {
      expect(headers[name], name).toBeUndefined();
    }
    expect(headers["x-custom-hop"]).toBeUndefined();
    expect(headers["x-end-to-end"]).toBe("kept");
    expect(res.headers.get("keep-alive")).toBeNull();
    expect(res.headers.get("x-upstream-only")).toBeNull();
  });

  it("sets X-Forwarded-For/-Proto/-Host", async () => {
    await makeProxy()(
      req("/api/h", { headers: { host: "web.example", "x-forwarded-for": "203.0.113.7" } }),
      { clientIp: "10.0.0.1" },
    );
    const headers = received[0]!.headers;
    expect(headers["x-forwarded-for"]).toBe("203.0.113.7, 10.0.0.1");
    expect(headers["x-forwarded-proto"]).toBe("http");
    expect(headers["x-forwarded-host"]).toBeDefined();
  });

  it("crafted paths still hit the stub host", async () => {
    await makeProxy()(req("/api//evil.example/x"));
    await makeProxy()(req("/api/http://evil.example/"));
    expect(received.map((r) => r.url)).toEqual(["//evil.example/x", "/http://evil.example/"]);
    for (const r of received) expect(r.headers["host"]).toBe(baseUrl.host);
  });

  it("answers 504 when the upstream does not respond in time", async () => {
    handler = () => {
      /* never respond */
    };
    const started = Date.now();
    const res = await makeProxy({ timeoutMs: 200 })(req("/api/slow"));
    expect(res.status).toBe(504);
    expect(await res.json()).toEqual({ status: "error", detail: "upstream timeout" });
    expect(Date.now() - started).toBeLessThan(2000);
  });

  it("answers 502 when the upstream is unreachable", async () => {
    const res = await makeProxy({ apiUrl: new URL("http://127.0.0.1:1") })(req("/api/health"));
    expect(res.status).toBe(502);
    expect(await res.json()).toEqual({ status: "error", detail: "upstream unavailable" });
  });

  it("answers 503 when the api is not configured", async () => {
    const res = await makeProxy({ apiUrl: null })(req("/api/health"));
    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ status: "error", detail: "api not configured" });
    expect(received).toHaveLength(0);
  });

  it("logs upstream failures without query, cookies or body", async () => {
    const warnings: string[] = [];
    await makeProxy({
      apiUrl: new URL("http://127.0.0.1:1"),
      warn: (message, fields) => warnings.push(JSON.stringify({ message, ...fields })),
    })(
      req("/api/x?token=sentinel-q", {
        method: "POST",
        body: "sentinel-body",
        headers: { cookie: "s=sentinel-cookie" },
      }),
    );
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).not.toMatch(/sentinel/);
  });
});

describe("tracing", () => {
  it("creates one span and propagates its trace id upstream", async () => {
    const exporter = new InMemorySpanExporter();
    const telemetry = setupWebTelemetry(
      {},
      { spanProcessors: [new SimpleSpanProcessor(exporter)] },
    );
    expect(telemetry.exporting).toBe(false);
    const proxy = makeProxy({ tracer: telemetry.tracer });

    const res = await proxy(
      req("/api/health?token=sentinel-q-123", { headers: { cookie: "s=sentinel-cookie-123" } }),
    );
    expect(res.status).toBe(200);

    const spans = exporter.getFinishedSpans();
    expect(spans).toHaveLength(1);
    const span = spans[0]!;
    expect(span.name).toBe(PROXY_SPAN_NAME);
    expect(span.attributes["http.route"]).toBe("/api/*");
    expect(span.attributes["http.request.method"]).toBe("GET");
    expect(span.attributes["http.response.status_code"]).toBe(200);

    const traceparent = received[0]?.headers["traceparent"];
    expect(typeof traceparent).toBe("string");
    const [, traceId, spanId] = String(traceparent).split("-");
    expect(traceId).toBe(span.spanContext().traceId);
    expect(spanId).toBe(span.spanContext().spanId);

    const serialised = JSON.stringify({
      name: span.name,
      attributes: span.attributes,
      events: span.events,
      resource: span.resource.attributes,
    });
    expect(serialised).not.toContain("sentinel");
    expect(serialised).not.toContain("token=");
    await telemetry.shutdown();
  });

  it("continues a browser-sent trace", async () => {
    const telemetry = setupWebTelemetry({});
    const incoming = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01";
    await makeProxy({ tracer: telemetry.tracer })(
      req("/api/health", { headers: { traceparent: incoming } }),
    );
    const sent = String(received[0]?.headers["traceparent"]);
    expect(sent.split("-")[1]).toBe("0af7651916cd43dd8448eb211c80319c");
    expect(sent).not.toBe(incoming); // our proxy span is the new parent
    await telemetry.shutdown();
  });

  it("forwards traceparent with the no-op tracer too", async () => {
    const incoming = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01";
    await makeProxy()(req("/api/health", { headers: { traceparent: incoming } }));
    expect(String(received[0]?.headers["traceparent"]).split("-")[1]).toBe(
      "0af7651916cd43dd8448eb211c80319c",
    );
  });

  it("registers no exporter without an OTLP endpoint", () => {
    expect(setupWebTelemetry({}).exporting).toBe(false);
    expect(setupWebTelemetry({ OTEL_EXPORTER_OTLP_ENDPOINT: "" }).exporting).toBe(false);
    expect(
      setupWebTelemetry({
        OTEL_EXPORTER_OTLP_ENDPOINT: "http://127.0.0.1:1",
        OTEL_SDK_DISABLED: "true",
      }).exporting,
    ).toBe(false);
    expect(setupWebTelemetry({ OTEL_EXPORTER_OTLP_ENDPOINT: "http://127.0.0.1:1" }).exporting).toBe(
      true,
    );
  });

  it("names the service belegbot-web unless OTEL_SERVICE_NAME is set", () => {
    expect(webResourceAttributes({})["service.name"]).toBe("belegbot-web");
    expect(webResourceAttributes({ OTEL_SERVICE_NAME: "custom" })["service.name"]).toBe("custom");
    const attrs = webResourceAttributes({
      RAILWAY_ENVIRONMENT_NAME: "tax-return-app-pr-42",
      APP_ENV: "production",
      GIT_SHA: "",
      RAILWAY_GIT_COMMIT_SHA: "abc123",
    });
    expect(attrs["deployment.environment.name"]).toBe("tax-return-app-pr-42");
    expect(attrs["vcs.ref.head.revision"]).toBe("abc123");
  });
});

describe("startup log lines", () => {
  it("warns once when API_INTERNAL_URL is missing in production", () => {
    const lines = startupLogLines({ NODE_ENV: "production" });
    expect(lines.filter((l) => l.level === "warning")).toHaveLength(1);
    expect(lines.map((l) => l.event)).toContain("otel export disabled");
  });

  it("is quiet when configured", () => {
    expect(
      startupLogLines({
        NODE_ENV: "production",
        API_INTERNAL_URL: "http://api.railway.internal:8000",
        OTEL_EXPORTER_OTLP_ENDPOINT: "https://otlp.example/otlp",
      }),
    ).toEqual([]);
  });
});
