import { getSandbox, type Sandbox } from "@cloudflare/sandbox";

// Re-export the Sandbox Durable Object class so the runtime can instantiate it.
export { Sandbox } from "@cloudflare/sandbox";

type Env = {
  Sandbox: DurableObjectNamespace<Sandbox>;
  SANDBOX_SHARED_SECRET: string;
};

type RunRequest = {
  code: string;
  language?: "python" | "javascript" | "typescript";
  session?: string;
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

    const { code, language = "python", session = "notebook" } = body;
    if (!code) {
      return Response.json({ error: "missing 'code'" }, { status: 400 });
    }

    // `session` IS the isolation boundary: the same name routes to the same
    // container. Today it's a constant ("notebook"); when this is wired into
    // the LangGraph DQA pipeline, pass the run's thread_id for one sandbox
    // per run.
    const sandbox = getSandbox(env.Sandbox, session);

    try {
      const result = await sandbox.runCode(code, { language });
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
