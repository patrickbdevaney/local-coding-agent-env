# local-coding-agent-env

A coding agent that substitutes for Claude Code, driven by a **local model by
default** and cloud models **only where they buy something**, at $0 marginal
cost.

The design and its rationale are in [DESIGN.md](DESIGN.md). This file is how to
use it.

```
clank                    open the conversation here
clank "add retries to X" open it with that prompt already typed
clank --resume           pick a past conversation and continue it
clank -c                 continue the last one
clank -p "..."           headless: run it, print the answer, exit
clank self               open the conversation on clank's own source
clank status | doctor    what is up, and which model would answer right now
```

Inside the conversation:

| | |
|---|---|
| `/loop` | work to completion without handing back |
| `/research` | search, read, synthesise, save a cited report |
| `/sweep` | frontier architecture review of what you just built |
| `/harness` | improve clank itself |
| `/remember` `/recall` `/map` | project memory and the symbol map |

**You do not have to reach for those.** The commands are for when you want to be
deliberate. Routing normally happens on its own, through **skills** — see below.

Permissions are skipped by default. That is deliberate, and it is most of why
this feels like a tool rather than a dialogue.

---

## The shape

```
             opencode  (TUI, sessions, resume, compaction, transcripts)
                 │
                 ▼
        router :8787   OpenAI-compatible.  Ladders, health, loop guard, provenance.
                 │
    ┌────────────┼──────────────────┐
    ▼            ▼                  ▼
  build        plan / sweep       distill
  LOCAL ✱      best free          wide parallel map
  then cloud   frontier           (many docs at once)
               then LOCAL ✱       then LOCAL ✱

  Retrieval is not a lane. Search runs against a local SearXNG, pages are
  fetched by a local Rust binary, and the reading is done by `distill`.
  There is no paid search path anywhere in this system.
```

✱ **Every ladder terminates at the local model.** Revoke every cloud key and
everything still resolves. That is the acceptance criterion, not a fallback
story — it has been verified twice by accident, once when a credential bug 401'd
every remote route and every run still completed.

## Install

```bash
git clone <this repo> && cd local-coding-agent-env
cargo build --release --manifest-path fetcher/Cargo.toml
cp .env.example .env && $EDITOR .env          # at minimum: CLANK_LOCAL_URL
ln -s "$PWD/bin/clank" ~/.local/bin/clank
clank doctor
```

`clank` starts the router on demand and, if `CLANK_SSH_HOST` is set, will start the
model server over SSH before doing anything else. There is no daemon to babysit.

Requires [opencode](https://opencode.ai) and Python 3.8+. No other runtime
dependency: the router, both MCP servers and the memory index are pure standard
library.

## The three lanes, and what each is for

### local — the floor and the workhorse

Qwen3.8-27B on one RTX 3090 at 262K context, ~111–142 tok/s. It does
essentially all the work: reading, editing, running tests, iterating. It is
unmetered, so it is the right place to spend tokens on *verification* — running
the code, running the tests, checking its own output.

The router injects the request shape the server actually needs
(`temperature: 0` plus `chat_template_kwargs: {enable_thinking: false}`). Clients
do not send that, and without it speculative decoding is disabled outright,
because the acceptance rule is argmax equality. It is worth about 3× decode.

### frontier — a delta, applied without blocking

The obvious blend is planner → executor. Measured, that was **10× the wall
clock for an identical result**: 33s versus 321s, same passing gate. Planning
ahead of a capable executor is mostly cost.

So it is inverted. The local model runs unblocked. Then `sweep` runs, in two
calls:

1. **Blind design** — the frontier model gets the problem, the constraints and
   the interface, and *never the code*. It designs from first principles.
2. **Delta** — only now is it shown a summary of what was actually built, and it
   names the differences, ranked CRITICAL / VALUABLE / MINOR, explicitly
   including "no change warranted".

Withholding the implementation is the whole mechanism. Shown a solution, a model
reviews it, and review is anchored to what it was shown. Shown only the problem,
it invents. Both halves land in `.agent/sweeps/` so the design can be audited
against the delta — if the blind design is bad, the delta is worthless, and you
can see that.

**The routing rule: run local first. Escalate on evidence of failure — a red
gate, a stuck loop, an architectural fork — never in anticipation of it.**

Which frontier model? Whichever is best and answering *right now*. The pool is
fetched from the provider catalogue, filtered to genuinely zero-cost, ranked by
a capability table (with a context-window heuristic for models it has never
seen), and annotated with live health so a model that just 429'd is skipped
until it cools. Hardcoding names is a bug: free endpoints appear and are
withdrawn constantly, and a withdrawn model 404s and looks transient.

```
$ clank status
frontier ladder (best first):
   92  stealth/ox-alpha                                  1048576
   88  minimax/minimax-m3:free                           1048576
   86  nvidia/nemotron-3-ultra-550b-a55b:free            1000000
   80  z-ai/glm-5.2:free                                  256000  cooling 90s
```

### distill — width, not quality

The local GPU serves one request at a time, so twenty documents is twenty
sequential decodes. The `distill` route fans out to a hosted provider where
per-call latency is irrelevant and only *count* matters. That is the one
workload shape the local box physically cannot do, and it is exactly what
research needs: map a mechanical extraction over many pages at once.

When that lane is unavailable, research does not do the same thing slower. It
goes **narrower**: fewer pages, chosen more precisely, distilled locally.
`deep_research` asks the router which lanes are live and sizes itself
accordingly.

## Research is free, and local

Measured: free search engines mostly CAPTCHA or suspend under load, and the
survivors return **landing pages** for technical queries no matter how you
phrase them — three differently-worded queries returned byte-identical results.

But a free engine reliably finds the right *site*, and **the site's own
navigation names the right page**. So `deep_research` fetches the seeds, harvests
their anchors, scores them against the query (anchor text over URL slug, rare
terms over common, at least two distinct terms required) and takes a second
targeted hop. That replaced a paid search API outright and ranked the correct
deep page first on the query every engine had failed.

Fetching is a small Rust binary because that step is I/O fan-out plus CPU-bound
HTML stripping. It is deliberately **not** a crawler — it follows nothing; the
caller decides every URL.

Every run writes a full report with sources and provenance to
`.agent/research/`.

## Skills: why you never have to name a command

The slash commands are the explicit path. The default path is `skills/`, which
is what makes this feel like a tool rather than a menu.

Each skill is a markdown file whose **description is a trigger condition**, not a
label. The model sees those descriptions and reaches for the right one on its
own:

| skill | fires when |
|---|---|
| `deep-research` | you are about to assert something external you have not verified |
| `architecture-sweep` | the *shape* of the solution is the risk |
| `project-memory` | before investigating from scratch; whenever a durable fact is learned |
| `autonomous-completion` | the task is plainly multi-step and nobody asked to be consulted |
| `remote-machines` | the thing to observe or change lives on another host |
| `agent-fleet` | work should run in the background, or another agent needs supervising |

The descriptions are deliberately written as *situations*, not topics — "you are
about to state an external fact you are not certain of" rather than "web
search". A topic label only fires when the user says the topic; a situation
fires when the situation occurs, which is the whole point.

Measured: asked only *"is the GPU box busy right now, and what's it running?"* —
no command, no mention of SSH — it used `remote-machines`, connected over
Tailscale, read `nvidia-smi` and `ps`, and recorded a durable fact about the host
without being asked to.

Add your own by dropping a directory with a `SKILL.md` into `skills/`.

## Memory

Durable memory lives in `.agent/` inside your repo, as markdown you can read and
git can diff:

```
.agent/
  MEMORY.md        durable facts, one per line — loaded into every session
  repomap.md       compact symbol map: the cheap "what exists"
  research/*.md    every research run, with sources
  sweeps/*.md      every frontier sweep: blind design, delta, verdict
  decisions/*.md   architectural choices, and what was rejected
  journal.md       what was done
```

Retrieval is BM25 over that corpus plus the symbol map. No vector database, no
embedding server, no index to rebuild. The corpus is small and dense with exact
identifiers (`chat_template_kwargs`, `LOOP_TRIP`) — which is precisely what
lexical retrieval is best at and what embeddings blur.

`clank init` sets it up in a project. The agent is instructed
([PROTOCOL.md](PROTOCOL.md)) to search memory before investigating and to write
facts down as it learns them.

## Reliability

Three failure modes are handled in the harness rather than hoped away:

**Tool-repetition loops.** A local 27B refetched one identical URL forty-plus
times and burned an entire turn budget. Small models do this whenever a tool
returns something they cannot use — they retry it verbatim instead of changing
approach. Nothing errors, so nothing surfaces; the run just dies of timeout. The
router catches it, because it is the only component that sees the whole message
array each turn: three identical calls in a fourteen-message window triggers a
system intervention. It **nudges rather than blocks** — refusing would strand a
model that legitimately needs a retry. In the run that discovered it, the
unguarded version never finished and the guarded one passed in 705s.

**Dead upstreams.** A 900s timeout hung a client for fifteen minutes on a server
that died mid-stream. Upstream timeout is 300s: a dead route must fail fast
enough to fall down the ladder.

**Silent misconfiguration.** `.env` values are unquoted on load (a quote in a
bearer token 401s every remote route), and settings are read *after* the `.env`
is loaded — the reverse order silently ignored `CLANK_LOCAL_URL` and pointed the
local route at a port occupied by an unrelated service.

Every call is logged to `logs/calls.jsonl` with its full attempt ladder — what
was tried, what failed, what served, how long, how many tokens. `clank status`
prints the recent ones.

## Long sessions

opencode handles context: auto-compaction on overflow with a dedicated
summarisation prompt, `/compact` on demand, session resume, fork, undo/redo,
transcript export, and a live context meter. Sliding-window loss is mostly moot
anyway at 262K native — which costs 4.5 GiB of KV at int4, 18 KiB/token.

## Keys

The committed code reads **one key per provider**: `OPENROUTER_API_KEY` and
`GROQ_API_KEY`. Both are optional; with neither, everything runs on the local
model. There is a single seam, `local/keyring.py`, which is gitignored and absent
from this repository; nothing here provides one and everything works without it.

## Files

| path | what |
|---|---|
| `bin/clank` | the CLI: open, resume, status, doctor |
| `opencode.json` → `command` | the in-conversation commands: `/loop`, `/research`, `/sweep`, `/harness` |
| `router/gateway.py` | ladders, live frontier pool, health, loop guard, provenance |
| `mcp/research.py` | plan → seed → link-harvest → distill → synthesise |
| `mcp/memory.py` | BM25 memory, repo map, markdown attestation |
| `mcp/sweep.py` | blind frontier design, then ranked delta |
| `fetcher/` | Rust: parallel fetch, text extraction, anchor harvest |
| `opencode.json` | provider, models, agents, MCP wiring, permissions |
| `PROTOCOL.md` | standing instructions given to the agent every session |
| `skills/` | situation-triggered capabilities — how routing happens without a command |
