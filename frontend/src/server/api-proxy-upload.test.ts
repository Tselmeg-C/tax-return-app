// @vitest-environment node
import http from "node:http";
import type { AddressInfo } from "node:net";
import { randomBytes } from "node:crypto";

import { trace } from "@opentelemetry/api";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { createApiProxy, type ApiProxy } from "./api-proxy";

// Uploads through the proxy (#6). Scaled down 40x: a 5 MB body over ~1 s against a
// 750 ms timeout stands for 40 s against the live 30 s.
const SCALE = 40;
const TIMEOUT_MS = 30_000 / SCALE;

type StubHandler = (req: http.IncomingMessage, res: http.ServerResponse, body: Buffer) => void;

let server: http.Server;
let baseUrl: URL;
let received: { headers: http.IncomingHttpHeaders; body: Buffer }[] = [];
let handler: StubHandler;

beforeAll(async () => {
  server = http.createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => {
      const body = Buffer.concat(chunks);
      received.push({ headers: req.headers, body });
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
});

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

function slowBody(payload: Buffer, totalMs: number, chunks = 20): ReadableStream<Uint8Array> {
  const size = Math.ceil(payload.length / chunks);
  let i = 0;
  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      if (i >= chunks) {
        controller.close();
        return;
      }
      await new Promise((r) => setTimeout(r, totalMs / chunks));
      controller.enqueue(new Uint8Array(payload.subarray(i * size, (i + 1) * size)));
      i += 1;
    },
  });
}

describe("uploads through the proxy", () => {
  it("does not cut off a slow 5 MB upload and delivers it byte-identical", async () => {
    const payload = randomBytes(5 * 1024 * 1024);
    handler = (_req, res) => {
      res.statusCode = 201;
      res.setHeader("content-type", "application/json");
      res.end(JSON.stringify({ ok: true }));
    };
    const started = Date.now();
    const res = await makeProxy({ timeoutMs: TIMEOUT_MS })(
      req("/api/documents", {
        method: "POST",
        body: slowBody(payload, 40_000 / SCALE),
        duplex: "half",
        headers: { "content-type": "application/octet-stream" },
      }),
    );
    expect(Date.now() - started).toBeGreaterThan(TIMEOUT_MS);
    expect(res.status).toBe(201);
    expect(await res.json()).toEqual({ ok: true });
    expect(Buffer.compare(received[0]!.body, payload)).toBe(0);
  });

  it("still answers 504 when the upstream never answers a request with a body", async () => {
    handler = () => {
      /* never respond */
    };
    const started = Date.now();
    const res = await makeProxy({ timeoutMs: 200 })(
      req("/api/slow", {
        method: "POST",
        body: "{}",
        headers: { "content-type": "application/json" },
      }),
    );
    expect(res.status).toBe(504);
    expect(Date.now() - started).toBeLessThan(2000);
  });

  it("forwards X-Filename, passes Content-Disposition back and logs neither", async () => {
    const name = "UTF-8''sentinel-name-M%C3%BCller.pdf";
    handler = (_req, res) => {
      res.setHeader("content-disposition", 'inline; filename="sentinel-dl.pdf"');
      res.end("ok");
    };
    const res = await makeProxy()(
      req("/api/documents/x/file", { headers: { "x-filename": name } }),
    );
    expect(received[0]!.headers["x-filename"]).toBe(name);
    expect(res.headers.get("content-disposition")).toBe('inline; filename="sentinel-dl.pdf"');

    const warnings: string[] = [];
    await makeProxy({
      apiUrl: new URL("http://127.0.0.1:1"),
      warn: (message, fields) => warnings.push(JSON.stringify({ message, ...fields })),
    })(req("/api/documents", { method: "POST", body: "x", headers: { "x-filename": name } }));
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).not.toMatch(/sentinel/);
  });
});
