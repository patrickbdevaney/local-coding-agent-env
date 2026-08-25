# Route an OpenAI-compatible chat request across a ladder of heterogeneo

_2026-08-25 15:41_

**Goal:** Route an OpenAI-compatible chat request across a ladder of heterogeneous inference providers where the last rung is a local GPU that must never be bypassed, cloud rungs are free-tier endpoints that rate-limit unpredictably, and the caller is a streaming HTTP client that must not observe a failover

**Files summarised:** router/gateway.py
**Blind design by:** stealth/ox-alpha
**Elapsed:** 490.7s

## Verdict

see report

## Delta

# DELTA REPORT

**1. Commit criterion: first content delta vs first byte — design better. CRITICAL.**
The implementation commits the moment the first byte arrives. Free-tier providers routinely emit headers, SSE comments, or role-preamble chunks and then stall or die; at that instant the stream is committed and the client eats a hung or truncated answer with no recourse (the implementation's own summary concedes mid-stream failure is unhandled). Committing on the first *content-bearing* event, behind a capped pre-commit buffer with a per-rung TTFT deadline, converts that entire class into silent descent. As built, "invisible failover" leaks on the most common provider pathology.

**2. Post-commit continuity: splice vs truncate — design better. CRITICAL.**
Any mid-stream drop (reset, 5xx after start, provider kill) produces a truncated response. Assistant-prefix continuation, with tool-call args staged until `ToolCallEnd`, turns those into seamless handoffs. This is the most expensive piece of my design, but the alternative is user-visible corruption of answers.

**3. Per-attempt deadlines with reserved terminal budget — design better. CRITICAL.**
No deadlines are described anywhere in the implementation. An upstream that accepts the connection and goes silent blocks the request indefinitely, and the "local floor" invariant then holds only as long as the client happens to wait. Budgeted per-rung deadlines with an untouchable `local_reserve` make terminal reachability hold under adversarial slowness; even a coarse socket read-timeout would close most of the gap.

**4. Admission control: adaptive windows vs bare exponential backoff — design better. VALUABLE.**
Penalties without inflight limits mean every concurrent request piles onto whichever model is currently healthy; when it 429s, they all penalize it simultaneously and stampede the next rung, burning quota across providers and dumping the load on the GPU. AIMD windows with fair-FIFO release bound concurrency and pace recovery. Compounding it: one penalty curve covers 429/5xx/connection errors alike, so a single exhausted credential can shadow-ban a healthy model.

**5. Live pool discovery vs static ladder — implementation better. VALUABLE.**
Free OpenRouter models appear and vanish hourly. My immutable static ladder rots: rungs begin 404ing and every request silently degrades to local-only. The 15-minute catalogue refresh with BOOTSTRAP fallback keeps the ladder populated. Plainly: the implementation wins here; my structural assertion (last rung is local) survives, but the ordering source must be live.

**6. Task-shaped ladders (`build`/`plan`/`sweep`/`distill`) — implementation better. VALUABLE.**
A single global quality-order forces one latency/quality tradeoff on every request. Local-first for `build` buys interactive latency and offline resilience; strongest-cloud-first for `plan` buys answer quality. My design had no concept of per-task descent orders.

**7. Tool-loop detection nudge — implementation better. VALUABLE.**
I had nothing for repeated identical tool calls; an agent can loop indefinitely, burning quota and wall-clock. The 14-message scan with a system nudge is a cheap mitigation (soft, as they note — a hard stop after N repeats would be stronger — but the failure mode is at least addressed where mine ignored it).

_(the report was cut off here by the output limit; item 8 is incomplete and has been removed)_

## Blind design

_Written before the implementation was disclosed. Preserved so the delta can be
audited: if the design is bad, the delta is worthless._

# Ladder Router with Invisible Failover

## 1. Core approach

Treat the router as a **transactional stream proxy**: each request descends a statically ordered ladder, and the client-visible stream comes into existence via a **two-phase commit** — no byte is written to the socket until some provider proves liveness by delivering its first *content* delta; after commit, one owner-pump streams onward and can still splice to deeper rungs mid-stream by assistant-prefix continuation. Rate limiting is done as **congestion control** (per-rung adaptive concurrency windows, AIMD/gradient), not configured quotas, because free-tier limits are unknown and nonstationary. The local GPU is structurally privileged — no breaker, no shedding, a *reserved* share of the client deadline — so descent can always terminate there.

Rejected alternative, for the record: hedging/racing providers (Tail-at-Scale style). Free-tier quota is the scarce resource; racing multiplies 429 pressure exactly when capacity is lowest. Deterministic descent + adaptive windows is quota-minimal.

## 2. Data structures

| Structure | Fields | Why it's right here |
|---|---|---|
| `Ladder: Vec<Rung>` (immutable, copy-on-write reload) | `Rung { id, kind: Cloud\|Local, adapter: Box<dyn ProviderAdapter>, limiter: AdaptiveWindow, breaker: Option<CircuitBreaker> }` | The ladder is a strict total order — an array gives O(1) descent per step, and immutability makes "last element is Local" a **load-time structural assertion**, not a runtime hope. |
| `AdaptiveWindow` | `limit: f32, inflight, rtt_ewma, rtt_min_long, cooldown_until, fair_fifo: Fifo<PermitWaiter>` | Free-tier limits are hidden and time-varying → this is a feedback-control problem, not a configuration problem. O(1) acquire/release; the gradient/AIMD update needs only these scalars. |
| `StreamEvent` enum + `ProviderAdapter` trait | `Delta, ToolCallBegin{id,name}, ToolCallArgsChunk, ToolCallEnd, Usage, Finish(reason), Done`; adapter: `send(CanonicalRequest, deadline) -> UpstreamStream` and `classify(wire_event) -> Signal` | Quarantines heterogeneity at the boundary. Failover, splicing, and the OpenAI-emitting serializer are written **once**, against canonical events. |
| `Attempt` (RAII, one per probe) | `rung_idx, permit: OwnedPermit, deadline, committed: bool, pre_commit_buf: BytesMut (capped), assembled: AssistantPartial{content, completed_tool_calls}` | The unit of atomicity: everything needed to replay invisibly, splice by continuation, and release resources on cancellation lives in one value whose `Drop` closes the upstream connection. |
| `Budget` | `total, per_rung[k], local_reserve` | The no-bypass guarantee is a **time-allocation** property. Without reserving time for the terminal rung, k slow-failing cloud rungs can exhaust the client's patience before the GPU is ever tried — the guarantee would be aspirational. |

## 3. Algorithm

```
setup (O(|body|)): parse → CanonicalRequest
  local_reserve = max(3 × p95_local_TTFT, 15 s)
  distribute (total − ε − local_reserve) across cloud rungs

for i in 0..k-1 (cloud rungs):
  1. breaker open?                     → descend            O(1)
  2. permit = limiter.acquire(budget[i])   # fair-FIFO wait
     wait timeout                      → descend, NO failure signal
                                          (your queue ≠ their fault)
  3. adapter.send(); await first CONTENT-bearing canonical event,
     deadline = budget[i] − ε:
       transport err | 5xx | 429 | malformed | buf>HWM | timeout
         → cancel upstream; limiter.on_failure():
             limit *= β (β≈0.5–0.9)
             cooldown = clamp(Retry-After, 1 s, 60 s) else EWMA-backed
           breaker.record_failure(); descend
       first Delta → COMMIT
  4. COMMIT: set committed (one-way); spawn pump owning the client socket:
       write 200 / text-event-stream; replay pre_commit_buf;
       loop: wire → canonical → serialize → write;
             limiter.on_success(): limit += δ (or gradient step), update rtts
     Cost: O(1)/event → O(m) total, m = events streamed.
  5. mid-stream failure post-commit → SPLICE:
       request' = messages ++ [assistant(assembled.content)]
                  ++ completed_tool_calls        # never an open tool call
       resume ladder at i+1 with same discipline;
       hand the new upstream to the SAME pump via channel —
       client-visible sequence never resets.
       optionally pace the first few continued tokens to mask the gap.

rung k (local): no acquire, no breaker; FIFO executor; deadline advisory only.
  hardware error → 3 backoff retries → 503 canonical error.
  This is the SOLE client-visible failure path, reachable only when the
  GPU itself cannot serve.
```

Total per request: **O(k + m)** time, O(capped buffer + partial) space; limiter ops O(1); no global coordination, so the router shards horizontally for free.

## 4. Invariants and enforcement points

- **I1 Terminal reachability** — every request reaches rung k unless served earlier; rung k has no breaker/shedding. *Enforced:* loop structure (final iteration unconditional) + constructor asserts `ladder.last().kind == Local` + local rung built with `breaker=None`.
- **I2 Pre-commit silence** — zero bytes to the client before commit. *Enforced:* only the pump owns the socket, and the pump is spawned only at commit; `pre_commit_buf` is the sole sink before that.
- **I3 Exactly-once stream identity** — one commit per request; splices continue, never restart, the sequence. *Enforced:* one-way `committed` flag; splice feeds the existing pump through a channel.
- **I4 Resource closure** — permits, connections, timers released on every exit (success, failure, client disconnect, panic). *Enforced:* RAII `OwnedPermit` + structured-concurrency attempt tree rooted at the client connection.
- **I5 Signal honesty** — window updates derive only from provider-classified outcomes. *Enforced:* `Signal` is produced solely inside adapter `classify()`; limiter-wait timeouts are explicitly signal-neutral.
- **I6 Budget conservation** — Σ per-rung ≤ total − ε; `local_reserve` untouchable by cloud attempts. *Enforced:* budgets computed once, threaded as per-await deadlines.
- **I7 Dialect containment** — raw provider bytes never cross the adapter boundary. *Enforced:* types — the pump consumes `StreamEvent`, not bytes.

## 5. Non-obvious failure modes

- **Slow-fail masquerade.** Provider returns 200, streams SSE comments/keepalives/role deltas, then stalls — passes every "first byte" check. *Handled:* commit keys on the first **content-bearing canonical event**, not first byte; TTFT deadline per rung; pre-commit buffer high-water mark (~256 KiB) forces abort-and-descend so a chatty broken provider can't pin memory or the request.
- **Recovery stampede.** When a tripped window reopens, every queued waiter wakes simultaneously and re-trips 429 in a synchronized wave; Retry-After can synchronize this across requests indefinitely. *Handled:* fair-FIFO releases permits one at a time as inflight drains (natural pacing), ±20% jitter on cooldown expiry, multiplicative (not stepped) recovery, clamped Retry-After so a buggy header can't silence a rung for minutes.
- **Seam corruption in tool calls.** Failing mid-tool-call and asking another model to "continue" produces duplicated or syntactically invalid JSON arguments. *Handled:* commit granularity is the **event group** — `ToolCallArgsChunk`s stage in a second buffer and flush only at `ToolCallEnd`; a failure inside an open call rewinds to the last closed boundary, and the splice request contains only completed calls, so the successor regenerates that call from scratch, invisibly. Plain-content partials splice by assistant-prefix continuation.

## 6. What a competent implementer gets wrong

1. **Committing on headers or first byte** instead of first content delta — the #1 way "invisible failover" leaks.
2. **Penalizing the window for local wait timeouts** — conflating your own queue pressure with provider rejection poisons the control loop and spirals a healthy rung shut.
3. **Not cancelling the abandoned upstream request** on failover — leaked connections silently burn free-tier quota, which makes the provider look worse, which the limiter then "adapts" to. A compounding loop.
4. **Building separate streaming and non-streaming paths.** Always stream internally; adapt shape at the edge. One pump, one commit protocol.
5. **Applying the total deadline to steady-state streaming** — kills long legitimate generations. Deadlines govern acquisition and failover, not generation lifetime.
6. **Trusting Retry-After unclamped**, or lumping 429s with 5xx (different β, different breaker accounting).
7. **Naïve SSE parsing**: CRLF, multi-line `data:`, missing `[DONE]` (derive `Done` from `finish_reason`), trailing usage-only chunks.
8. Forgetting that a splice changes token accounting: trim history to fit the successor's context; never splice an `n>1` request — regenerate choice-wise.

## Genuinely better-than-obvious, named

1. **Rate limiting as congestion control** (gradient/AIMD adaptive concurrency, à la TCP Vegas / Netflix `concurrency-limits`). Buys: deletes the entire "stale quota config" bug class; converges to true capacity with no discovery runs; heals automatically when free-tier limits shift hourly. Not an asymptotic win — a *category* win.
2. **Two-phase commit over a stream** (silent buffering until first proven delta, then irreversible handoff to a single owner pump). Buys: converts "failover must be invisible to a live HTTP stream" — a distributed-systems nightmare — into a local atomic-flag problem; post-commit continuity becomes a data-structure question (the assembled partial), not a protocol question.
3. **Event-group commit granularity** (stage tool-call args until closed). Buys: makes mid-stream splice always well-defined; eliminates invalid-JSON-at-the-seam entirely.
4. **Deadline reservation for the terminal rung.** Buys: makes "the local GPU is never bypassed" hold under adversarial upstream slowness. This is the subtle one — without it the guarantee is decorative, because k slow-failing cloud rungs can spend the client's patience before descent ever happens.

## Implementation summary given to the architect

**Data Structures**
The system uses a `Pool` class to manage the frontier model list, storing tuples of `(score, model_id, context_length)`. A `health` dictionary tracks per-model cooldowns and failure counts. A global `threading.Lock` protects shared state (health, key rotation, logging). The `FRONTIER_RANK` list contains regex patterns mapping model families to static scores. `BOOTSTRAP` provides a static fallback list.

**Control Flow**
1.  **Initialization:** Loads `.env` before reading settings to prevent port conflicts. Starts a daemon thread that refreshes the `Pool` every 15 minutes by fetching the OpenRouter catalogue, filtering for zero-cost models, and re-ranking them.
2.  **Request Handling:**
    *   **Loop Detection:** Scans the last 14 messages for repeated tool calls (same name/args 3+ times). If found, injects a system nudge into the request.
    *   **Ladder Construction:** Builds a provider/model sequence based on the virtual model (`build`, `plan`, etc.). `build` prioritizes local (twice) then cloud; `plan`/`sweep` prioritize cloud (top model twice) then local; `distill` uses specific Groq models; `search` is opt-in.
    *   **Execution:** Iterates the ladder. For each rung, it constructs the HTTP request, injecting local-specific parameters (temperature 0, thinking disabled) or stripping them for cloud.
    *   **Failover:** On HTTP error or connection failure, it penalizes the model (exponential backoff) and moves to the next rung. On success, it rewards the model (clears penalty) and streams the response to the client.
    *   **Termination:** If all rungs fail, returns a 502 error.

**Complexity**
*   **Pool Refresh:** O(N log N) for sorting the model list, where N is the number of free models.
*   **Loop Detection:** O(W) where W is the window size (14), scanning messages and tool calls.
*   **Ladder Construction:** O(1) for list slicing and concatenation.
*   **Request Processing:** O(1) per attempt, excluding network I/O.

**Invariants**
*   **Local Floor:** The ladder always ends with the local model. The local model is never bypassed; if cloud fails, it falls back to local.
*   **Streaming Integrity:** Once the first byte is received from a provider, the connection is committed. No further failover occurs mid-stream.
*   **Credentials:** one key per provider, supplied by the environment.

**Failure Modes**
*   **Handled:** Upstream 429s (rate limits) trigger exponential backoff. Connection errors trigger failover. Local server restarts are handled by retrying the local model twice in the `build` ladder.
*   **Unhandled:** Mid-stream failures (after the first byte) do not trigger failover; the client receives a truncated response. If the local model is down, the `build` ladder retries it twice before trying cloud, but if cloud is also down, the request fails.

**TODOs/Compromises**
*   **Bootstrap Fallback:** If the live catalogue fetch fails, the system uses a static `BOOTSTRAP` list, which may be outdated.
*   **Key Management:** Relies on environment variables or an optional, absent `keyring.py` module. No dynamic key management.
*   **Search Lane:** The `search` virtual model is disabled by default and requires a paid OpenRouter plugin, which is not fully integrated or tested in the free-tier context.
*   **Loop Nudge:** The intervention is a soft nudge, not a hard block, which may not stop all infinite loops.
