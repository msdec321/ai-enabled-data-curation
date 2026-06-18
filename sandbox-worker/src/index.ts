import { getSandbox, Sandbox as BaseSandbox } from "@cloudflare/sandbox";

// Subclass so we can set a sleep-after default. Containers are meant to be
// ephemeral-per-run: the broker passes a unique session id per agent run and
// destroys it when the run ends. `sleepAfter` is belt-and-suspenders — a
// container that somehow escapes explicit destroy still goes idle quickly
// instead of staying warm indefinitely.
export class Sandbox extends BaseSandbox {
  sleepAfter = "3m";
}

type Env = {
  Sandbox: DurableObjectNamespace<Sandbox>;
  SANDBOX_SHARED_SECRET: string;
};

type RunRequest = {
  // "run" (default) executes code; "destroy" tears the container down.
  action?: "run" | "destroy";
  code?: string;
  language?: "python" | "javascript" | "typescript";
  session?: string;
  // Environment variables to expose to the run (e.g. a DB login the broker
  // injects). Kept out of `code` so secrets don't sit in the code body.
  env?: Record<string, string>;
};

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (request.method !== "POST") {
      return new Response("POST only", { status: 405 });
    }

    // Simple shared-secret auth so the endpoint isn't open to the world.
    if (request.headers.get("authorization") !== `Bearer ${env.SANDBOX_SHARED_SECRET}`) {
      return new Response("unauthorized", { status: 401 });
    }

    let body: RunRequest;
    try {
      body = await request.json<RunRequest>();
    } catch {
      return Response.json({ error: "invalid JSON body" }, { status: 400 });
    }

    const { action = "run", language = "python", session = "notebook", env: envVars } = body;

    // `session` IS the isolation boundary: the same name routes to the same
    // container. The broker passes one unique id per agent run, so each run
    // gets its own container.
    const sandbox = getSandbox(env.Sandbox, session);

    // ── Teardown: destroy this run's container ──
    if (action === "destroy") {
      try {
        await sandbox.destroy();
        return Response.json({ destroyed: true, session });
      } catch (e: any) {
        console.error("sandbox destroy failed:", e?.stack ?? e);
        return Response.json({ destroyed: false, session, error: String(e?.message ?? e) });
      }
    }

    // ── Run code ──
    const code = body.code;
    if (!code) {
      return Response.json({ error: "missing 'code'" }, { status: 400 });
    }

    // Inject env vars via a prelude that sets os.environ, rather than relying on
    // the code-interpreter kernel inheriting them. Values are JSON-encoded into
    // a Python string literal (double-stringify), so this is injection-safe.
    let toRun = code;
    if (envVars && Object.keys(envVars).length > 0 && language === "python") {
      const prelude =
        `import os, json as _json\n` +
        `os.environ.update(_json.loads(${JSON.stringify(JSON.stringify(envVars))}))\n`;
      toRun = prelude + code;
    }

    try {
      const result = await sandbox.runCode(toRun, { language });
      return Response.json({
        stdout: result.logs.stdout.join("\n"),
        stderr: result.logs.stderr.join("\n"),
        results: result.results, // rich outputs: text, png, json, ...
        error: result.error ?? null,
      });
    } catch (e: any) {
      // Surface the real failure instead of a bare 1101 "Worker threw".
      console.error("sandbox runCode failed:", e?.stack ?? e);
      return Response.json(
        {
          error: {
            name: e?.name ?? "Error",
            message: String(e?.message ?? e),
            stack: e?.stack ?? null,
          },
        },
        { status: 500 },
      );
    }
  },
};
