const json = (data, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
      "referrer-policy": "no-referrer",
      "x-frame-options": "DENY",
    },
  });

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method !== "GET" && request.method !== "HEAD") {
      return json({ ok: false, error: "method_not_allowed" }, 405);
    }

    if (url.pathname === "/" || url.pathname === "/api/v1") {
      return json({
        name: "HIJAZ AI Infrastructure",
        version: "0.1.0",
        status: "online",
        endpoints: ["/health", "/api/v1/status"],
      });
    }

    if (url.pathname === "/health" || url.pathname === "/api/v1/health") {
      try {
        const row = await env.DB
          .prepare("SELECT value FROM platform_meta WHERE key = ?")
          .bind("schema_version")
          .first();

        return json({
          ok: Boolean(row),
          service: "hijaz-ai-edge",
          database: row ? "connected" : "not_ready",
          schema_version: row?.value ?? null,
        });
      } catch {
        return json(
          { ok: false, service: "hijaz-ai-edge", database: "unavailable" },
          503,
        );
      }
    }

    if (url.pathname === "/api/v1/status") {
      try {
        const metadata = await env.DB
          .prepare("SELECT key, value FROM platform_meta ORDER BY key")
          .all();

        const counts = await env.DB
          .prepare(
            "SELECT (SELECT COUNT(*) FROM audit_events) AS audit_events, " +
              "(SELECT COUNT(*) FROM agent_tasks) AS agent_tasks",
          )
          .first();

        return json({
          ok: true,
          platform: "HIJAZ AI",
          version: "0.1.0",
          metadata: metadata.results,
          counts,
          storage: { database: "D1", config: "KV" },
          mode: "free-tier-foundation",
        });
      } catch {
        return json({ ok: false, error: "status_unavailable" }, 503);
      }
    }

    return json({ ok: false, error: "not_found" }, 404);
  },
};
