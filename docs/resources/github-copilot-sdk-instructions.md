# GitHub Copilot SDK — Implementation Reference

> **Status:** Technical Preview (may change in breaking ways)
> **Repository:** <https://github.com/github/copilot-sdk>
> **Docs:** <https://docs.github.com/en/copilot/how-tos/copilot-sdk/sdk-getting-started>
> **Blog announcement:** <https://github.blog/news-insights/company-news/build-an-agent-into-any-app-with-the-github-copilot-sdk/>
> **npm:** <https://www.npmjs.com/package/@github/copilot-sdk>
> **PyPI:** <https://pypi.org/project/github-copilot-sdk/>
> **Community instructions file:** <https://github.com/github/awesome-copilot/blob/main/instructions/copilot-sdk-nodejs.instructions.md>

---

## What It Is

The GitHub Copilot SDK exposes the same agentic engine behind Copilot CLI as a programmable layer. It provides production-tested orchestration: planning, tool invocation, file edits, multi-turn execution loops, MCP server integration, and multi-model routing — without having to build your own agentic runtime.

Available in **Node.js/TypeScript**, **Python**, **Go**, and **.NET**. All SDKs are thin wrappers that communicate with the Copilot CLI server via **JSON-RPC 2.0**.

---

## Architecture

```
Your Application
       ↓
  SDK Client (language-specific)
       ↓ JSON-RPC (stdio transport by default)
  Copilot CLI (server mode — @github/copilot npm package)
       ↓
  LLM Provider (GitHub-hosted models, or BYOK)
```

The SDK manages the CLI process lifecycle automatically. All language SDKs delegate to a common Node.js CLI backend binary distributed via the `@github/copilot` npm package.

Key classes:

- **CopilotClient** — manages the CLI process, connection lifecycle, and telemetry
- **CopilotSession** — represents an independent conversation with state and history

---

## Prerequisites

1. **Install the Copilot CLI:** Follow [the CLI installation guide](https://docs.github.com/en/copilot/how-tos/set-up/install-copilot-cli), or ensure `copilot` is available in your PATH.
2. **Authentication:** Either a GitHub Copilot subscription (Free tier available with limited usage, Pro/Pro+/Business/Enterprise for full access), **or** BYOK with your own API keys (no GitHub auth needed).
3. **Billing:** Each prompt counts towards your premium request quota (unless using BYOK, which bills through your provider).

---

## Installation

| SDK | Install Command |
|-----|----------------|
| **Node.js / TypeScript** | `npm install @github/copilot-sdk` |
| **Python** | `pip install github-copilot-sdk` |
| **Go** | `go get github.com/github/copilot-sdk/go` |
| **.NET** | `dotnet add package GitHub.Copilot.SDK` |

For telemetry support in Python: `pip install copilot-sdk[telemetry]`

---

## Quick Start — Node.js/TypeScript

### Minimal Example (sendAndWait)

```typescript
import { CopilotClient } from "@github/copilot-sdk";

const client = new CopilotClient();
const session = await client.createSession({ model: "gpt-4.1" });

const response = await session.sendAndWait({ prompt: "What is 2 + 2?" });
console.log(response?.data.content);

await client.stop();
process.exit(0);
```

Run: `npx tsx index.ts`

### Full Lifecycle (event-based)

```typescript
import { CopilotClient, approveAll } from "@github/copilot-sdk";

// Create and start client
const client = new CopilotClient();
await client.start();

// Create a session (onPermissionRequest is required)
const session = await client.createSession({
  model: "gpt-5",
  onPermissionRequest: approveAll,
});

// Wait for response using typed event handlers
const done = new Promise<void>((resolve) => {
  session.on("assistant.message", (event) => {
    console.log(event.data.content);
  });
  session.on("session.idle", () => {
    resolve();
  });
});

// Send a message and wait for completion
await session.send({ prompt: "What is 2+2?" });
await done;

// Clean up
await session.destroy();
await client.stop();
```

### Streaming

```typescript
import { CopilotClient } from "@github/copilot-sdk";

const client = new CopilotClient();
const session = await client.createSession({
  model: "gpt-4.1",
  streaming: true,
});

session.on("assistant.message_delta", (event) => {
  process.stdout.write(event.data.deltaContent);
});

session.on("session.idle", () => {
  console.log(); // New line when done
});

await session.sendAndWait({ prompt: "Tell me a short joke" });
await client.stop();
process.exit(0);
```

---

## Quick Start — Python

### Minimal Example

```python
import asyncio
from copilot import CopilotClient
from copilot.session import PermissionHandler

async def main():
    client = CopilotClient()
    await client.start()

    session = await client.create_session(
        on_permission_request=PermissionHandler.approve_all,
        model="gpt-4.1"
    )

    response = await session.send_and_wait({"prompt": "What is 2 + 2?"})
    print(response.data.content)

    await client.stop()

asyncio.run(main())
```

### Streaming (Python)

```python
import asyncio
import sys
from copilot import CopilotClient
from copilot.session import PermissionHandler
from copilot.generated.session_events import SessionEventType

async def main():
    client = CopilotClient()
    await client.start()

    session = await client.create_session(
        on_permission_request=PermissionHandler.approve_all,
        model="gpt-4.1",
        streaming=True
    )

    def handle_event(event):
        if event.type == SessionEventType.ASSISTANT_MESSAGE_DELTA:
            sys.stdout.write(event.data.delta_content)
            sys.stdout.flush()
        if event.type == SessionEventType.SESSION_IDLE:
            print()  # New line when done

    session.on(handle_event)
    await session.send_and_wait({"prompt": "Tell me a short joke"})
    await client.stop()

asyncio.run(main())
```

---

## Quick Start — Go

```go
package main

import (
    "context"
    "fmt"
    "log"
    copilot "github.com/github/copilot-sdk/go"
)

func main() {
    ctx := context.Background()
    client := copilot.NewClient(nil)

    if err := client.Start(ctx); err != nil {
        log.Fatal(err)
    }
    defer client.Stop()

    session, err := client.CreateSession(ctx, &copilot.SessionConfig{Model: "gpt-4.1"})
    if err != nil {
        log.Fatal(err)
    }

    response, err := session.SendAndWait(ctx, copilot.MessageOptions{Prompt: "What is 2 + 2?"})
    if err != nil {
        log.Fatal(err)
    }

    fmt.Println(*response.Data.Content)
}
```

---

## CopilotClient Configuration

```typescript
new CopilotClient(options?: CopilotClientOptions)
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `cliPath` | `string` | `"copilot"` from PATH | Path to CLI executable |
| `cliArgs` | `string[]` | `[]` | Extra arguments prepended before SDK-managed flags |
| `cliUrl` | `string` | — | URL of existing CLI server (e.g., `"localhost:8080"`, `"http://127.0.0.1:9000"`, or just `"8080"`). When provided, client won't spawn a process |
| `port` | `number` | `0` (random) | Server port |
| `useStdio` | `boolean` | `true` | Use stdio transport instead of TCP |
| `logLevel` | `string` | `"debug"` | Log level |
| `autoStart` | `boolean` | `true` | Auto-start server |
| `autoRestart` | `boolean` | `true` | Auto-restart on crash |
| `cwd` | `string` | `process.cwd()` | Working directory for the CLI process |
| `env` | `object` | `process.env` | Environment variables for the CLI process |
| `telemetry` | `TelemetryConfig` | — | OpenTelemetry configuration |

### Explicit lifecycle control

```typescript
const client = new CopilotClient({ autoStart: false });
await client.start();
// Use client...
await client.stop();    // Graceful shutdown
// await client.forceStop(); // When stop() takes too long
```

---

## Session Configuration

```typescript
const session = await client.createSession({
  model: "gpt-5",                 // Required (always required with BYOK)
  streaming: true,                 // Enable streaming responses
  tools: [...],                    // Custom tool definitions
  systemMessage: { content: "..." }, // Custom system prompt
  availableTools: ["tool1", "tool2"], // Whitelist built-in tools
  excludedTools: ["tool3"],        // Blacklist built-in tools
  provider: { ... },              // BYOK provider config
  onPermissionRequest: approveAll, // Required permission handler
});
```

### Key session methods

- `session.send({ prompt: "..." })` — Send a message (non-blocking, use events)
- `session.sendAndWait({ prompt: "..." }, timeout?)` — Send and wait for idle, returns `Promise<AssistantMessageEvent | null>`
- `session.destroy()` — Clean up session resources
- `session.on(eventType, handler)` — Subscribe to events (returns unsubscribe function)

---

## Permission Handling

An `onPermissionRequest` handler is **required** for every session. It's called before the agent executes each tool.

### Auto-approve all

```typescript
import { CopilotClient, approveAll } from "@github/copilot-sdk";

const session = await client.createSession({
  model: "gpt-5",
  onPermissionRequest: approveAll,
});
```

### Custom permission logic

```typescript
import type { PermissionRequest, PermissionRequestResult } from "@github/copilot-sdk";

const session = await client.createSession({
  model: "gpt-5",
  onPermissionRequest: (request: PermissionRequest, invocation): PermissionRequestResult => {
    // request.kind — what type of operation:
    //   "shell"       — executing a shell command
    //   "write"       — writing or editing a file
    //   "read"        — reading a file
    //   "mcp"         — calling an MCP tool
    //   "custom-tool" — calling one of your registered tools
    //   "url"         — fetching a URL
    //   "memory"      — storing/retrieving persistent session memory
    //   "hook"        — invoking a server-side hook
    //   (additional kinds may be added; include a default case)

    // request.toolCallId — the tool call that triggered this

    // Return: { allowed: true } or { allowed: false, reason: "..." }
    if (request.kind === "shell") {
      return { allowed: false, reason: "Shell commands not permitted" };
    }
    return { allowed: true };
  },
});
```

---

## Custom Tools

Define tools that Copilot can invoke during its agentic loop.

### With Zod schemas

```typescript
import { z } from "zod";
import { CopilotClient, defineTool } from "@github/copilot-sdk";

const session = await client.createSession({
  model: "gpt-5",
  tools: [
    defineTool("lookup_issue", {
      description: "Fetch issue details from our tracker",
      parameters: z.object({
        id: z.string().describe("Issue identifier"),
      }),
      handler: async ({ id }) => {
        const issue = await fetchIssue(id);
        return issue; // Any JSON-serializable value
      },
    }),
  ],
});
```

### With raw JSON Schema (no Zod)

```typescript
defineTool("lookup_issue", {
  description: "Fetch issue details from our tracker",
  parameters: {
    type: "object",
    properties: {
      id: { type: "string", description: "Issue identifier" },
    },
    required: ["id"],
  },
  handler: async ({ id }) => {
    return await fetchIssue(id);
  },
});
```

### Tool handler return types

Handlers can return:

- Any **JSON-serializable value** (automatically wrapped)
- A **simple string**
- A **`ToolResultObject`** for full control over result metadata

### Overriding built-in tools

If you register a tool with the same name as a built-in CLI tool (e.g., `edit_file`, `read_file`), the SDK will throw an error **unless** you explicitly opt in:

```typescript
defineTool("edit_file", {
  overridesBuiltInTool: true,
  // ...
});
```

---

## Custom System Message

```typescript
const session = await client.createSession({
  model: "gpt-5",
  systemMessage: {
    content: `
      <workflow_rules>
      - Always check for security vulnerabilities
      - Suggest performance improvements when applicable
      </workflow_rules>
    `,
  },
});
```

---

## BYOK (Bring Your Own Key)

BYOK allows using the Copilot SDK with your own API keys, bypassing GitHub Copilot authentication. When using BYOK, the `model` parameter is **always required**.

### Supported providers

| Provider | `type` value | Notes |
|----------|-------------|-------|
| OpenAI | `"openai"` | Default for OpenAI-compatible APIs |
| Azure AI Foundry | `"azure"` | Must use `"azure"`, NOT `"openai"` for Azure endpoints |
| Anthropic | `"anthropic"` | For Claude API endpoints |
| AWS Bedrock | — | Via OpenAI-compatible interface |
| Google AI Studio | — | Via OpenAI-compatible interface |
| xAI | — | Via OpenAI-compatible interface |
| Any OpenAI-compatible | `"openai"` | Custom endpoints, Ollama, etc. |

### OpenAI

```typescript
const session = await client.createSession({
  model: "gpt-4",
  provider: {
    type: "openai",
    baseUrl: "https://api.openai.com",
    apiKey: process.env.OPENAI_API_KEY,
  },
});
```

### Anthropic

```typescript
const session = await client.createSession({
  model: "claude-sonnet-4-20250514",
  provider: {
    type: "anthropic",
    baseUrl: "https://api.anthropic.com",
    apiKey: process.env.ANTHROPIC_API_KEY,
  },
});
```

### Azure AI Foundry

```typescript
const session = await client.createSession({
  model: "gpt-4",
  provider: {
    type: "azure",        // MUST be "azure", NOT "openai"
    baseUrl: "https://my-resource.openai.azure.com", // Just the host, no path
    apiKey: process.env.AZURE_OPENAI_KEY,
    azure: {
      apiVersion: "2024-10-21",
    },
  },
});
```

> **CRITICAL:** Do not include `/openai/v1` in the `baseUrl` — the SDK handles path construction automatically.

### Bearer token authentication

```typescript
provider: {
  type: "openai",
  baseUrl: "https://my-custom-endpoint.example.com/v1",
  bearerToken: process.env.MY_BEARER_TOKEN, // Sets Authorization header
}
```

> When both `bearerToken` and `apiKey` are provided, `bearerToken` takes precedence. The token is static — the SDK does not refresh it.

### Custom model listing (BYOK)

When using BYOK, the CLI may not know what models your provider supports. Supply a custom handler:

```typescript
import { CopilotClient } from "@github/copilot-sdk";
import type { ModelInfo } from "@github/copilot-sdk";

const client = new CopilotClient({
  onListModels: async (): Promise<ModelInfo[]> => {
    return [
      { id: "my-custom-model", name: "My Custom Model" },
      // ...
    ];
  },
});
```

### BYOK behavior differences

- **Model availability** — only models supported by your provider
- **Rate limiting** — subject to your provider's rate limits, not Copilot's
- **Usage tracking** — tracked by your provider, not GitHub
- **Premium requests** — do NOT count against Copilot quotas

---

## Built-In Tool Configuration

By default, the SDK operates with `--allow-all`, enabling all first-party tools (file system, Git, web requests). You can customize:

```typescript
const session = await client.createSession({
  model: "gpt-5",
  availableTools: ["read_file", "edit_file", "shell"], // Whitelist
  excludedTools: ["web_request"],                       // Blacklist
});
```

---

## Copilot Skills

You can point sessions at Copilot Skill definition files:

```python
session = await client.create_session({
    "model": "claude-sonnet-4.5",
    "streaming": True,
    "skill_directories": ["./.copilot_skills/pr-analyzer/SKILL.md"]
})
```

---

## OpenTelemetry

### Basic setup

```typescript
const client = new CopilotClient({
  telemetry: {
    otlpEndpoint: "http://localhost:4318",
  },
});
```

The CLI automatically emits spans for every operation. Trace context is propagated bidirectionally:

- **SDK → CLI:** `traceparent` and `tracestate` headers included in RPC calls
- **CLI → SDK:** When the CLI invokes tool handlers, trace context from the CLI's span is propagated so tool code runs under the correct parent span

### File-based tracing

```typescript
const client = new CopilotClient({
  telemetry: {
    filePath: "./traces.jsonl",
    exporterType: "file",
  },
});
```

### Python telemetry

```bash
pip install copilot-sdk[telemetry]  # provides opentelemetry-api
```

### .NET telemetry

No extra dependencies — uses built-in `System.Diagnostics.Activity`.

---

## Event Types

| Event | Description |
|-------|-------------|
| `assistant.message` | Complete assistant response |
| `assistant.message_delta` | Streaming chunk (has `event.data.deltaContent`) |
| `session.idle` | Session finished processing (use to resolve promises) |

### Event listener pattern

```typescript
// The on() method returns an unsubscribe function
const unsubscribe = session.on("assistant.message_delta", (event) => {
  process.stdout.write(event.data.deltaContent);
});

// Later: unsubscribe();
```

### Generic event listener

```typescript
session.on((event) => {
  if (event.type === "assistant.message") {
    console.log(event.data.content);
  } else if (event.type === "session.idle") {
    // done
  }
});
```

---

## Resource Cleanup Pattern

Always use try-finally:

```typescript
const client = new CopilotClient();
try {
  await client.start();
  const session = await client.createSession({ model: "gpt-5" });
  try {
    await session.sendAndWait({ prompt: "Hello!" });
  } finally {
    await session.destroy();
  }
} finally {
  await client.stop();
}
```

### Helper functions

```typescript
async function withClient<T>(fn: (client: CopilotClient) => Promise<T>): Promise<T> {
  const client = new CopilotClient();
  try {
    await client.start();
    return await fn(client);
  } finally {
    await client.stop();
  }
}

async function withSession<T>(
  client: CopilotClient,
  fn: (session: CopilotSession) => Promise<T>,
): Promise<T> {
  const session = await client.createSession({ model: "gpt-5" });
  try {
    return await fn(session);
  } finally {
    await session.destroy();
  }
}

// Usage
await withClient(async (client) => {
  await withSession(client, async (session) => {
    await session.send({ prompt: "Hello!" });
  });
});
```

---

## Connecting to an External CLI Server

Instead of the SDK spawning a CLI process, you can connect to one already running:

```typescript
const client = new CopilotClient({
  cliUrl: "localhost:8080",  // or "http://127.0.0.1:9000" or just "8080"
});
```

When `cliUrl` is provided, the client won't spawn a process.

---

## GitHub Actions Integration

Example: automated daily analysis using the Python SDK in a GitHub Actions workflow:

```yaml
name: Daily PR Analysis
on:
  schedule:
    - cron: '0 0 * * 1-5'  # Monday-Friday at UTC 00:00
  workflow_dispatch:

jobs:
  analyze:
    runs-on: ubuntu-latest
    steps:
      - name: Setup and Run Analysis
        env:
          COPILOT_GITHUB_TOKEN: ${{ secrets.COPILOT_GITHUB_TOKEN }}
        run: |
          npm i -g github/copilot
          pip install github-copilot-sdk --break-system-packages
          python pr_trigger_v2.py
```

---

## Agentic Workflow System (gh-aw)

The repo also contains a compiler/runtime for creating AI-powered GitHub Actions workflows from markdown files. Markdown files in `.github/workflows/*.md` compile to `.github/workflows/*.lock.yml`.

Key concepts:

- **Activation job** — sanitizes GitHub context before AI processing
- **Agent job** — executes the AI agent with configured engines (Copilot CLI, Claude, or custom), MCP servers, and sandboxes
- **Safe output jobs** — execute GitHub API mutations in separate jobs with minimal, operation-specific permissions

The agent runs with **read-only permissions**; write operations are isolated in dedicated jobs that validate structured outputs.

---

## Key Constraints & Gotchas

1. **Technical Preview** — APIs may change in breaking ways before stable release.
2. **CLI dependency** — The Copilot CLI must be installed separately. The SDK communicates with it in server mode.
3. **BYOK requires `model`** — When using a custom provider, you must always specify the model explicitly.
4. **Azure endpoints** — Must use `type: "azure"`, not `type: "openai"`. Don't include `/openai/v1` in the URL.
5. **Permission handler required** — `onPermissionRequest` is mandatory for session creation.
6. **Default is allow-all** — By default the SDK enables all first-party tools (file system, Git, web). Configure `availableTools`/`excludedTools` to restrict.
7. **Binary dependency** — The `@github/copilot` npm package ships platform-specific CLI binaries. Check which architectures are supported for your target platform.
8. **BYOK auth is key-based only** — Microsoft Entra ID, managed identities, and third-party identity providers are NOT supported for BYOK.
9. **Bearer tokens are static** — The SDK does not refresh bearer tokens. If your token expires, create a new session.

---

## Source Links

| Resource | URL |
|----------|-----|
| SDK Repository | <https://github.com/github/copilot-sdk> |
| Getting Started (GitHub Docs) | <https://docs.github.com/en/copilot/how-tos/copilot-sdk/sdk-getting-started> |
| Node.js README | <https://github.com/github/copilot-sdk/tree/main/nodejs> |
| Python README | <https://github.com/github/copilot-sdk/tree/main/python> |
| Go README | <https://github.com/github/copilot-sdk/tree/main/go> |
| .NET README | <https://github.com/github/copilot-sdk/tree/main/dotnet> |
| BYOK Documentation | <https://github.com/github/copilot-sdk/blob/main/docs/auth/byok.md> |
| Blog Announcement | <https://github.blog/news-insights/company-news/build-an-agent-into-any-app-with-the-github-copilot-sdk/> |
| npm Package | <https://www.npmjs.com/package/@github/copilot-sdk> |
| PyPI Package | <https://pypi.org/project/github-copilot-sdk/> |
| Copilot CLI Install Guide | <https://docs.github.com/en/copilot/how-tos/set-up/install-copilot-cli> |
| Awesome Copilot Instructions | <https://github.com/github/awesome-copilot/blob/main/instructions/copilot-sdk-nodejs.instructions.md> |
| DeepWiki Architecture Analysis | <https://deepwiki.com/github/copilot-sdk> |
| Practical Example (Microsoft) | <https://techcommunity.microsoft.com/blog/azuredevcommunityblog/building-agents-with-github-copilot-sdk-a-practical-guide-to-automated-tech-upda/4488948> |
