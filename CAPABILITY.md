# What was verified, and what was not

Everything below was run on 2026-08-25 against the real fleet: Qwen3.8-27B on an
RTX 3090 over Tailscale, OpenRouter's free tier, and Groq. Nothing here is
projected.

## 1. The lanes resolve

```
build    -> local:qwen38-27b                  0.65s   tried: [local:200]
plan     -> openrouter:stealth/ox-alpha       5.10s   tried: [glm-5.2:429, glm-5.2:429, ox-alpha:200]
distill  -> groq:openai/gpt-oss-20b           1.32s   tried: [gpt-oss-20b:200]
```

The `plan` row is the design working, not a fault: a free endpoint refused
twice, was cooled down, and the next rung answered — without the caller
noticing. Streaming and non-streaming were both verified end to end, including
frontier streaming through to `[DONE]`.

## 2. A real agentic task, start to finish

`lca "Read spec.md and do exactly what it says..."` in an empty git repo, with a
spec for a clock-injected `TokenBucket`:

- wrote `ratelimit.py` and `test_ratelimit.py`
- ran the suite, 5/5 pass
- called the memory tool and recorded the clock-injection invariant
- reported accurately, without overclaiming

**11 model calls, 76s of model time, ~82K tokens — 9 local, 1 frontier, 1 Groq.**
At the local tier that is unmetered.

`lca loop` was then given a follow-up (`peek(now)`, jitter-free `reset(now)`,
tests for both). It refactored the shared refill logic into a `_refill` helper,
added six tests, ran the full suite to 11/11, recorded a decision, and emitted
the completion sentinel. **The loop exited on its own after one iteration.**

## 3. Research, free and cited

Question: *what exact JSON field disables Qwen3 thinking in llama.cpp's
OpenAI-compatible server, and what is the vLLM equivalent?*

It returned the correct field (`chat_template_kwargs: {"enable_thinking": false}`),
the correct server flag (`--chat-template-kwargs`), the vLLM `extra_body` form,
and the actual Jinja line in the chat template that implements it — from **eight
real sources**, including deep pages inside the vLLM docs and two specific
llama.cpp issues. Not one landing page.

That is the two-hop link harvest working. No paid search API was involved, and
no engine was capable of finding those pages directly.

## 4. The sweep found real defects in its own router

`sweep` was pointed at `router/gateway.py`. `stealth/ox-alpha` produced a design
without seeing the code, then diffed it.

It rated three findings CRITICAL and **four points where the implementation beat
its own design** (live pool discovery, task-shaped ladders, the tool-loop nudge,
and per-task descent order). A sweep that only ever finds fault is not measuring
anything; this one conceded.

Two findings were applied the same day:

| finding | fix |
|---|---|
| Committing the client on the **first byte**, when free-tier providers routinely emit an empty preamble and then die — so the most common provider pathology was invisible to the failover and logged as success | bounded 16KB pre-commit probe; descend unless the rung produces actual content |
| No time reserved for the local floor: three slow cloud rungs could spend the client's whole budget, so "the ladder ends at local" held only in principle | `LOCAL_RESERVE=240s`; cloud rungs are skipped once the budget is spent and a local rung remains |

One was recorded and deliberately **not** applied: assistant-prefix splicing to
continue a stream that dies *after* commit. It is correct and it is the right
long-term answer, but it needs the streaming path rebuilt around an owner-pump
with tool-call arguments staged until `ToolCallEnd`. The pre-commit probe
removes the common case at a fraction of the cost. It is written down in
`.agent/decisions/` so the next session inherits the reasoning rather than
rediscovering it.

## 5. Four bugs the build itself surfaced

| bug | why it mattered | fix |
|---|---|---|
| Settings read **before** `.env` was loaded | `LCA_LOCAL_URL` silently ignored; the local route pointed at a port occupied by SearXNG, so failures looked like malformed responses rather than misconfiguration | load `.env` first, then read every setting from it |
| `reasoning` vs `reasoning_content` | OpenRouter spells it `reasoning`. Reading only `content` returned `""` — indistinguishable from a model with nothing to say | `mcp/llm.py` reads all three fields |
| `max_tokens` bounds reasoning *and* answer | ox-alpha spent all 3000 tokens thinking, returned `finish_reason: length` with empty content, and the sweep's "independent design" section came out **blank** after a 105s frontier call | retry once at 4× budget; and never run the delta against an empty design — fall to local and **label the report as not a capability delta** |
| Sweep report could be cut off before its verdict | verdict silently reported as "see report" | fall back to the highest-severity finding, and say the report was truncated |

The third is the one worth dwelling on. The frontier model, handed an empty
design section, **refused to fabricate a delta** — it said the section was
empty, named the two honest options, and declined to invent a design to critique
against. That refusal is what surfaced the bug. A more agreeable model would
have produced a plausible report and the defect would still be there.

## 6. What is not verified

- **Long-horizon sessions.** Compaction, resume and fork are opencode features
  and were not exercised past a few turns here. The context window is 262K
  native, so the pressure arrives late, but "it holds over a week-long project"
  is a claim this run does not support.
- **Concurrency.** The 3090 serves one request at a time. Wide *research*
  fan-out is what Groq exists for; wide *coding* fan-out (several agents editing
  at once) will serialise. Sequential coding — the actual workload — is
  unaffected.
- **Free-tier stability.** Measured today: glm-5.2 429'd immediately, ox-alpha
  served every request. Both facts have a shelf life, which is precisely why the
  pool is discovered and health-ranked rather than configured.
- **The local server is young software.** It is a custom CUDA server, still
  being written. The supervisor script and the UTF-8 streaming fix came out of
  it crashing.
