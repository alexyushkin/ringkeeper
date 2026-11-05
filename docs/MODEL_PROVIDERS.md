# Other model providers: a second worker engine

**Status: proposal for review. Nothing here is built.** Claude stays the default. Checked on
2026-11-05. Anything marked *to confirm* comes from a secondary source, or needs a real test.

## The short version

Add OpenAI's Codex CLI as a second worker engine, connected to Ringkeeper's MCP gateway. The
gateway decides every hold, so the same rules, isolation and audit apply whichever engine
runs. Almost everything else in this document is about what doesn't carry over for free.

Three findings shape the plan:

1. **Codex only speaks the Responses API.** `chat/completions` support was removed in
   February 2026, so a provider without a Responses endpoint can't connect directly. Gemini's
   OpenAI-compatible API is the main casualty, and Together is unconfirmed.
2. **Codex has no spending cap.** Ringkeeper would have to count tokens and stop the worker
   itself, with a price table it maintains.
3. **Holds are safe, but autonomy levels aren't the same.** Ringkeeper limits Claude's tools by
   name. Codex has no such list: only a sandbox and approval modes.

## What stays the same

| | How |
|---|---|
| Holds, name typed, undo | Decided in the gateway, for any caller. Nothing changes. |
| Card and bank number blocking, scrubbing | Also in the gateway. |
| Audit | Every tool call through the gateway is logged. The engine's own shell and file edits are recorded from its event stream, as Claude's are. |
| Same-client rule, scope | The per-task gateway token carries them. |
| Isolation | The same worker process or container, its own home, an allowlisted environment. |
| Git handling, checks, branch per task | In the runner, outside the agent. |

## How Codex would run

The runner gets a second agent next to `worker/ringkeeper_worker/agent.py`. For each session
it writes a private `CODEX_HOME` with a generated `config.toml`, then runs `codex exec --json`:

```toml
model = "gpt-5.5"
model_provider = "openrouter"
model_reasoning_effort = "medium"
approval_policy = "never"           # Ringkeeper holds; nobody is at a prompt
sandbox_mode = "workspace-write"

[mcp_servers.ringkeeper-vendor]
url = "http://gateway/mcp/business"
bearer_token_env_var = "RINGKEEPER_GATEWAY_TOKEN"
required = true                     # refuse to start without the gateway
default_tools_approval_mode = "auto"

[model_providers.openrouter]
name = "OpenRouter"
base_url = "https://openrouter.ai/api/v1"
env_key = "OPENROUTER_API_KEY"
wire_api = "responses"
```

`approval_policy = "never"` is right here because a held call comes back from the gateway as
"held for approval", and the task resumes after you answer, the same way Claude's does.

A private `CODEX_HOME` is the equivalent of today's `setting_sources=[]` and
`strict_mcp_config`: the user's own Codex settings and servers never load. A project's
`.codex/config.toml` only loads for a trusted project, so the generated config must not mark
it trusted. Model providers in a project-local file are ignored anyway (*to confirm*).

## Providers

| Provider | Responses API | Connects directly? | Notes |
|---|---|---|---|
| OpenAI | Native | Yes | The reference case. |
| Azure OpenAI | Yes, at `/openai/v1/` | Yes | Sends its key in an `api-key` header (`env_http_headers`). Fits a client who already pays through Azure. |
| OpenRouter | Yes, stateless | Likely | Rejects `store: true` and `previous_response_id` with a 400. Whether Codex sends either needs a test. |
| Groq | Yes, **beta** | Likely | Same test. A beta API under a client's work is a risk. |
| OCI Generative AI | Yes | Likely | Two base paths in Oracle's docs (`/openai/v1` and `/20231130/actions/v1`). Needs a project OCID, and the key must be in the model's region. Meta models aren't supported on it. |
| Ollama (local) | Yes, from 0.13.3, stateless | Likely | `ollama` is a reserved provider ID in Codex. |
| LM Studio (local) | Yes, from 0.3.29 | Likely | `lmstudio` is reserved too. |
| Together | **Unconfirmed** | Unknown | Its compatibility page doesn't mention it, though its menu lists a Responses page. Needs a direct test. |
| Gemini | **No**, on its OpenAI-compatible API | **No** | A forum reply says it isn't implemented; status unconfirmed since. Options: Gemini models through OpenRouter, a translation proxy, or a third engine. |

**Recommendation:** start with OpenAI, Azure OpenAI and OpenRouter. OpenRouter also reaches
Gemini models without a proxy. Add local models next, for clients whose code can't leave the
building. I wouldn't add a translation proxy: it's one more thing that sees every prompt.

Local models need the worker to reach this computer, which the desktop's process workers can
and the Docker worker can only through `host.docker.internal`. A small local model may also
be too weak for tool use; the comparison below would show that.

## What differs

| | Claude Code today | Codex | What Ringkeeper has to do |
|---|---|---|---|
| Spending cap | `max_budget_usd` | None found | Count tokens from the event stream, price them from a table, and stop the worker. Per provider, and it goes stale. |
| Autonomy | `disallowed_tools`, by tool name | `sandbox_mode` and approvals only | Map autonomy levels to sandbox modes. A read-only project is `read-only`. Anything finer can't be matched, so the screen must say so. |
| Shell and network | Container, tools by autonomy | Same container, plus its own sandbox | Check the two sandboxes don't conflict in a container. Network off in `workspace-write` may block what a task needs. |
| Instructions | Preset prompt plus appended note | `AGENTS.md`, plus config | Ringkeeper's headless note goes in the generated home. A project's `CLAUDE.md` isn't read; its `AGENTS.md` is. |
| Skills | `.claude/skills` | Its own skills format | Not shared. Skills written for Claude won't load, and the two would need separate copies. |
| Subagents | Task tool | Different, version-dependent (*to confirm*) | Treat as unavailable at first. Prompts mustn't assume them. |
| Effort | `low` to `max` | `model_reasoning_effort`, which varies by model | A map per model. An unsupported level has to step down, not fail. |
| Asking you a question | In-process MCP server with hooks | No equivalent | Move `ask_user` to an HTTP tool on the gateway. Claude could use it too. |
| Resuming | `resume` with a session ID | `codex exec resume` (*to confirm*) | Check a held task really continues. Ringkeeper's resume-after-approval depends on it. |
| Event stream | SDK messages | `--json` lines, schema *to confirm* | A second parser into the same task events. This is the largest single piece of work. |
| Prompt caching | Automatic | Varies by provider | Costs differ more than list prices suggest. |
| Sign-in | Key, subscription or cloud | Key per provider | Keys in the vault, passed only to that session. |

## Choosing models per role

Settings › Models already sets one model per role. It would become an engine and a model:

| Role | Proposal |
|---|---|
| Router, Help assistant | **Stay on Claude.** They use the Agent SDK, need dependable structured answers, and run on every message. |
| Architect | Stay on Claude at first. Move it only if the comparison says so. |
| Workers (small, normal, big tasks) | Any engine. This is where cost and fit differ. |

A project and an agent can override this, as they do today. Each client should also have a
list of **allowed engines**: a provider sees that client's code, so that's a data decision,
not only a cost one. Claude stays the default everywhere, including for new clients.

## Fallback

Off by default.

- **Same provider, another model:** on a 429 or 5xx, automatic, and shown on the task.
- **Another provider or engine:** only between sessions, never in the middle of one, and
  only to one on the client's allowed list. Each engine's transcript is its own, so a
  fallback starts a new session with a summary, which can lose detail.
- A failure that isn't about capacity (a refused key, a held call, a failed check) never
  triggers a fallback.
- The task and the audit say which engine did what, and spend counts against the same cap.

## Comparing them: ten tasks

The same ten tasks, from the same commit, on Claude and on each candidate, with the same cap.
Run on made-up data, in a scratch install.

| # | Task | What it shows |
|---|---|---|
| 1 | Fix a bug with a failing test | Basic loop |
| 2 | Add a small feature across two files | Normal work |
| 3 | Refactor without changing behaviour | Care, and running checks |
| 4 | Change spanning API, web and tests | Bigger context |
| 5 | Find why a test is flaky | Investigation |
| 6 | Read a database and write a report | Read tools |
| 7 | Edit a CRM record through the gateway | A held call, then resuming |
| 8 | An instruction to delete a record | A name-typed hold is respected |
| 9 | A prompt injection planted in a record | Doesn't act on it |
| 10 | A vague request | Asking a question, not guessing |

Measure: whether the checks pass, whether you'd merge it (read blind, without knowing the
engine), cost, time, gateway calls and refusals, and retries. Tasks 7 to 9 are pass or fail:
**an engine that gets a hold wrong doesn't qualify, whatever it costs.** Three runs each,
since one run proves little.

These runs cost real money, so I'd give you the total before starting.

## Suggested order

1. Move `ask_user` to the gateway. Useful on its own.
2. Run Codex on one provider behind a setting, with a stub event stream test.
3. Read its event stream into task events; check resuming after a hold.
4. Add the spending cap from token counts.
5. Run the ten tasks. **Decide here whether to go on.**
6. Add the other providers, per-role choice, allowed engines and fallback.

Steps 1 to 4 are the real cost. Adding a provider afterwards is mostly a config entry.

## Decisions for you

1. **Go ahead at all?** This is a lot of new surface, and every engine is something to keep
   working when a vendor changes it.
2. **Which providers first?** I suggest OpenAI, Azure OpenAI and OpenRouter.
3. **Gemini:** through OpenRouter, or skip it?
4. **Bundling:** ship the Codex CLI in the app, or install it on first use? Its license and
   the app's size need checking before it's bundled.
5. **Spending cap:** is a cap from a price table good enough, or should an engine with no
   real cap be off for client work?
6. **Allowed engines per client:** should a new client start with Claude only?
7. **Budget for the comparison runs.**

## Sources

- [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
- [Deprecating chat/completions in Codex](https://github.com/openai/codex/discussions/7782)
- [Azure OpenAI Responses API](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/responses)
- [OpenRouter Responses API](https://openrouter.ai/docs/api/reference/responses/overview)
- [Groq Responses API](https://console.groq.com/docs/responses-api)
- [OCI Responses API](https://docs.oracle.com/en-us/iaas/Content/generative-ai/oci-openai.htm)
  and [its API keys](https://docs.oracle.com/en-us/iaas/Content/generative-ai/api-keys.htm)
- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
- [LM Studio 0.3.29](https://lmstudio.ai/blog/lmstudio-v0.3.29)
- [Gemini OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai?hl=en) and
  [a forum reply on Responses](https://discuss.ai.google.dev/t/openai-responses-api-compatibility/90136)
- [Together OpenAI compatibility](https://docs.together.ai/docs/openai-api-compatibility)
