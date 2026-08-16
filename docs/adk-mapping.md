# ADK to Praevisum mapping

Which ADK primitive does what, and - as importantly - what we deliberately don't use.
Rule: nothing goes in to tick a box. If it isn't load-bearing, it's architecture theater and the 30% doesn't get earned.

---

## Load-bearing (use these)

| ADK primitive | Where it lands | Why it's load-bearing |
|---|---|---|
| **Bidi streaming** - `run_live()`, `/run_live` WS endpoint, `LiveRequestQueue`, internal LLM Flow | The front agent's live phone conversation | This *is* the product. Native Gemini Live integration with **natural interruption** means the customer can talk over the agent. Removes the biggest schedule risk - we bridge Twilio media into `LiveRequestQueue` instead of writing a Live API socket. |
| **`ParallelAgent`** - runs sub-agents concurrently and merges | Parts + Dispatch + History fired the moment the router classifies the complaint | The whole latency argument in one primitive. Conversation allows ~300ms; these lookups take seconds. They run **while the customer is still describing the problem**. This is why the system is multi-agent, and ADK expresses it directly. |
| **Agent-as-a-tool** (hierarchy via sub-agents) | Router delegates to equipment specialist / parts / dispatch / history | Natural fit for the roster. Orchestrator + specialists. |
| **`SequentialAgent`** - runs sub-agents in order | Briefing pipeline: gather history, resolve likely components, pick parts, compose, send | Order actually matters here; it's not decorative. |
| **`LoopAgent`** - re-invokes until a condition holds | Commitment keeper re-negotiation: try slot, check constraints, try again until a slot holds or it escalates | Genuinely a loop with a termination condition. |
| **Sessions (events + state)** | One call = one session. State scoped: `temp:` for in-call scratch, `user:` for the customer across calls, `app:` for dealer-wide config | Durable conversation state; survives a Cloud Run restart mid-call. |
| **Memory - Vertex AI RAG corpus, semantic search** | **The complaint history.** "What did the last three visits on this model find, and what parts were consumed" | This is the differentiator's storage layer, and ADK hands it to us. We do **not** hand-roll vector search. Backend is swappable without touching agent code. |
| **Artifacts** - save/load files or binary data per session/user | The nameplate photo the customer sends mid-call | Exactly what artifacts are for. Photo lives on the session, referenced by the model-resolution step. |
| **Callbacks** - before/after agent, model, tool | Guardrails: `before_tool` refuses to promise a slot that isn't actually free; `before_model` redacts customer PII before it leaves | Real safety, not logging. This is where "the agent can never over-commit" is enforced deterministically rather than by prompt. |
| **Runner** | Drives everything; streaming runner for the live call | The event loop. Agent yields Event, runner processes it, updates the session, resumes. |
| **Built-in evaluation framework** | Measure first-time-fix on seeded scenarios, with and without the briefing | ADK's eval "scores sub-agent dispatch, separates ADK-tool failures from custom-tool failures, grades Vertex Search retrieval, and asserts coherence across parallel branches." An **ablation** - same calls, briefing off vs on - is the single most persuasive thing we can put in the video, and it's the one thing almost no entrant will do. |

## Deployment

Start on **Cloud Run** (simpler to debug, and the WebSocket bridge needs control). Move to **Vertex AI Agent Engine** only if there's spare time - it's the more impressive thing to show a judge (managed runtime, Sessions, Memory Bank) but not worth risking the audio path.

## Deliberately NOT using

| ADK feature | Why not |
|---|---|
| **A2A protocol** | Nothing here talks to an agent outside our system. Bolting it on to look modern is exactly the theater we're avoiding. Field count: A2A appears in 14 entries - it's a crowd signal, not a differentiator. |
| **MCP tools** | Optional. Only if the parts/history tools are free to expose at the end. 22 entries already mention MCP. |
| **Custom `BaseAgent`** | The three workflow agents plus agent-as-tool cover every pattern we need. Writing a custom orchestrator is work with no score attached. |

---

## What this means for the plan

Day 1-3 was "build the live audio loop," and it's now smaller: **Twilio inbound feeds the media stream into `LiveRequestQueue`, which drives `run_live()`**. There's an official ADK Gemini Live API Toolkit and a Google codelab to start from, plus a worked example of bridging external audio into ADK bidi over FastAPI.

The parallel-lookup-while-talking behaviour, which is the architectural claim of the whole project, is a `ParallelAgent` - not something we invent.

And the complaint history, which is the *product* differentiator, is ADK long-term memory over a Vertex AI RAG corpus - not something we invent either.

Both of the things that make this project distinctive are directly expressible in the mandated framework. That's the right kind of fit.
