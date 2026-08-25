# local-coding-agent-env — what this is meant to be

A coding agent that substitutes for Claude Code, driven by **local inference by
default** and **cloud inference only where it buys something**, at $0 marginal
cost.

This document is the specification. It is written before the implementation and
is the thing the implementation is judged against.

---

## 1. The constraint that shapes everything

One RTX 3090 running Qwen3.8-27B-W4A16 at 262,144 context, ~111–142 tok/s with
a DFlash2 draft model. That is the floor and the workhorse. It is not a
frontier model, but it is *unmetered*: roughly 500K tokens/hour, all day, free.

Everything else is additive and must be **strictly optional**:

| Lane | What it is | Why it exists | If it's gone |
|---|---|---|---|
| local | Qwen3.8-27B on the 3090 | does ~all the work | **nothing works — this is the floor** |
| frontier | best currently-free OpenRouter endpoint | capability delta on hard architecture | degrade to local, keep going |
| fanout | Groq, `qwen/qwen3.6-27b` + `gpt-oss-20b` | wide *parallel* map over documents | degrade to a narrow local sweep |

The invariant: **no lane above the floor may be load-bearing.** A run must
complete with every cloud key revoked. This is not a fallback story bolted on
afterward; it is the acceptance criterion.

## 2. What the cloud lanes are actually for

### 2.1 Groq is the *width* lane, not the *quality* lane

Per-key Groq limits for `qwen/qwen3.6-27b` and `openai/gpt-oss-20b` are
30 RPM / 1K RPD / 8K TPM / 200K TPD. Individually that is nothing. In parallel
it is the thing the 3090 physically cannot do: the 3090 serves **one request at
a time**, so 30 documents is 30 sequential decodes. Groq turns that into one
round trip.

So Groq gets exactly the workload with that shape: **map a cheap, mechanical
transform over many documents at once** — distilling fetched web pages down to
rubric-relevant evidence. Latency per call is irrelevant; count is everything.
Quality per call is irrelevant too, because the step is extraction, not
reasoning.

When Groq is unavailable the fallback is not "the same thing, slower". It is a
**narrower, more precise local sweep**: fewer pages, chosen better. Precision
substitutes for breadth. That is the honest degradation, and the link-scoring
hop (§4) is what makes it viable.

### 2.2 The frontier lane is a *delta*, and it must never block

The naive interleave is planner → executor: a frontier model plans, the local
model executes. Measured, that was **10× wall-clock for an identical result**
on tasks the local model could already do (33s vs 321s, same passing gate).
Planning ahead of a capable executor is mostly cost.

So invert it. The local model runs at full speed, unblocked, and produces work.
*Then*, intermittently, the frontier model sweeps:

1. **Blind design.** The frontier model is given the *problem and constraints
   only* — never the local model's code. It designs from first principles.
   Withholding the implementation is the entire point: shown an existing
   solution, a model reviews it; shown only the problem, it invents. Anchoring
   is the thing being engineered out.
2. **Delta.** Only now is it shown a summary of what was actually built. It
   names the concrete difference between its independent design and the real
   one, and ranks each difference by value.

The output is a markdown sweep report, not an edit. The local model applies
what is worth applying. This yields Qwen's speed with an intermittent injection
of frontier-tier architectural judgment, and it never sits in the critical path.

**Rule: run local first. Escalate on evidence of failure — a red gate, a stuck
loop, an architectural fork — never in anticipation of it.**

### 2.3 "Best available free endpoint" is a runtime query, not a constant

Free frontier endpoints churn: they appear, get hammered, get rate-limited, and
get withdrawn. `stealth/ox-alpha` is free *for now*. GLM 5.3, DeepSeek V4 Pro,
Qwen3.8-2.4T and Kimi K3 may be free tomorrow and not today.

So the frontier pool is **discovered at runtime** from the provider's own model
list, filtered to zero-cost, ranked by a capability table with a heuristic
fallback for models the table has never seen, and annotated with live health
(a model that just 429'd is skipped until its cooldown expires). Greedy: always
try the best available; fall down the ladder on failure; terminate at local.

Hardcoding model names is a bug, not a configuration choice.

## 3. It must feel exactly like Claude Code

Non-negotiable, because the whole point is drop-in substitution:

- **Permissions skipped by default.** No approval prompts. It is my machine.
- **`clank` opens a conversation.** I type into it. It is not a command I have
  to phrase as an argument before I have started thinking.
- **`--resume` with a picker.** Select a past conversation, continue it.
- **Everything lives inside the conversation.** `/loop`, `/research`, `/sweep`,
  `/harness` are slash commands typed where I am already working — not separate
  binaries invoked from a shell I had to exit to.
- **It routes itself.** The commands are for being deliberate. Asking in plain
  language for something that needs current external facts should trigger
  research; asking for something plainly multi-step should run to completion
  without stopping to ask permission to continue.
- **Automatic interleaving.** I never choose a model.
- **Long context handled honestly** — compaction, transcripts, resume.
- **It can work on itself** by ordinary means: traversing its own source with
  `ls`, `grep` and `cat`, and reading its own call log as evidence. Not only
  through a special mode.

## 4. Retrieval must be free and local

Measured: SearXNG's upstream engines are, in this deployment, mostly CAPTCHA'd
or suspended, and the survivors return *landing pages* for technical queries
regardless of phrasing. A free search engine reliably identifies the right
**site**; it does not identify the right **page**.

The fix is two-hop, and it needs no paid API:

1. The planner already knows where the docs live (`docs.vllm.ai`,
   `github.com/ggml-org/llama.cpp`). Seed with those roots.
2. Fetch the roots and **harvest their own navigation anchors**, score the
   anchors against the query (anchor text weighted over URL slug, rare terms
   over common, at least two distinct terms required), and take a second
   targeted hop.

The site's own nav names the right page. This replaced a paid search API and
ranked the correct deep page #1 on first try. Fetching and extraction are a
Rust binary because that step is I/O fan-out plus CPU-bound HTML stripping —
the one shape where a compiled binary is decisively right.

## 5. Memory and attestation

A long-horizon project needs the agent to remember across sessions, and I need
to be able to read what it decided without replaying a transcript.

Everything durable lands in `.agent/` inside the working repo, **as markdown a
human can read and git can diff**:

```
.agent/
  MEMORY.md        durable project facts, one line each, loaded every session
  repomap.md       compact symbol map of the repo — the cheap "what exists"
  research/*.md    every deep-research run, with sources and provenance
  sweeps/*.md      every frontier sweep: blind design, delta, verdict
  decisions/*.md   architectural decisions, why, and what was rejected
  journal.md       append-only log of what was done
```

Retrieval over that corpus is **BM25 over markdown plus the repo symbol map** —
no embedding server, no vector DB, no daemon, nothing else to keep alive. The
corpus is small and lexical; keyword retrieval over it is not a compromise.

Accumulation is the point: the useful output of every workflow is a file, not a
scrollback.

## 6. It must be able to improve itself

The harness, the router, the research pipeline and this document are all in one
repo. `clank self` points the agent at that repo with its own memory, research and
sweep tools available. The frontier sweep applies to the harness the same way it
applies to any other code.

## 7. Provenance, and one thing deliberately left out

Every model call is logged with its full attempt ladder — what was tried, what
failed, what served, how long, how many tokens. A run is reconstructable after
the fact.

**Key handling:** the committed code reads exactly one key per provider
(`OPENROUTER_API_KEY`, `GROQ_API_KEY`). Multi-account key rotation is against
provider terms and is **not in this repository** — not the logic, not the
config, not the documentation. The code exposes a single seam,
`local/keyring.py` (gitignored, absent by default), and works correctly and
completely without it.

## 8. Acceptance criteria

1. `clank` in any directory opens a working conversation, no flags, no prompts.
2. `clank --resume` lists past conversations and resumes a selected one.
3. `/loop`, `/research`, `/sweep` and `/harness` work from inside it.
4. Unplug every cloud key: everything still works.
5. A research request produces a cited answer **and** a markdown file, with no
   paid API anywhere in the path.
6. A sweep produces a blind design that was written without seeing the code.
7. Loop mode runs to completion without handback and stops on its own.
8. Every call is in the log with its ladder.
