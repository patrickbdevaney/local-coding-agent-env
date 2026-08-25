#!/usr/bin/env python3
"""
clank sweep MCP -- the frontier capability delta, applied without blocking.

THE PROBLEM WITH PLANNER -> EXECUTOR

The obvious way to blend a frontier model with a fast local one is to let the
frontier model plan and the local model execute. Measured on this fleet, that
was 10x the wall clock for an identical result on tasks the local model could
already do: 33s versus 321s, same passing gate. Planning ahead of a capable
executor is mostly cost, and it puts the least reliable, most rate-limited
component directly in the critical path.

THE INVERSION

Let the local model run unblocked and produce work. Then sweep, in two calls:

  1. BLIND DESIGN. The frontier model receives the problem, the constraints and
     the interface -- and NEVER the local model's code. It designs from first
     principles. Withholding the implementation is the whole mechanism: shown
     an existing solution a model reviews it, and review is anchored to what it
     was shown. Shown only the problem, it invents. What we want from the
     scarce, expensive model is the idea it would have had independently.

  2. DELTA. Only now is it shown a summary of what was actually built. It names
     the concrete differences between its independent design and the real one,
     and ranks each by value, explicitly including "no difference that matters".

The summary in step 2 is produced by the LOCAL model from the real files, not
shipped as raw source: it keeps frontier context small, and a model summarising
its own work is the cheapest reliable thing in the fleet.

Output is a markdown report in `.agent/sweeps/`, never an edit. Whether to
apply anything stays a decision, and the record of it survives the session.
"""
import json, sys, os, re, time, urllib.request, subprocess

GATEWAY = os.environ.get("CLANK_GATEWAY", "http://127.0.0.1:8787/v1/chat/completions")
UA = {"User-Agent": "clank-sweep/1.0", "Content-Type": "application/json"}
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memory import memory_write   # noqa: E402  -- sweeps are memory

from llm import call as _call, llm   # noqa: E402

def read_paths(paths, cap=120_000):
    out, total = [], 0
    for p in paths:
        if not os.path.isfile(p):
            continue
        try:
            src = open(p, errors="replace").read()
        except Exception:
            continue
        if total + len(src) > cap:
            src = src[: max(0, cap - total)]
        out.append(f"--- {p} ---\n{src}")
        total += len(src)
        if total >= cap:
            break
    return "\n\n".join(out)

def summarise_local(goal, code):
    """What was built, in prose, by the model that built it."""
    return llm("build", f"""Summarise this implementation for an architect who will NOT see the code.

GOAL THE CODE SERVES: {goal}

Cover, concretely and without praise:
- the data structures and why each was chosen
- the control flow and the order of operations
- the algorithmic complexity of each significant operation
- the invariants it maintains and where it enforces them
- the failure modes it handles, and the ones it does not
- anything left as a TODO, a stub, or a known compromise

Be factual and complete. Do not evaluate it. 500 words maximum.

{code}""", 1600)

BLIND = """You are designing a system from first principles. Design the best solution you can.

PROBLEM
{goal}

{constraints}

Do not ask questions and do not hedge. Produce a concrete design:
1. The core approach, in two or three sentences.
2. The data structures, with the specific reason each is right here.
3. The algorithm, step by step, with complexity.
4. The invariants and where each is enforced.
5. The two or three non-obvious failure modes and how the design handles them.
6. Anything a competent implementer would most likely get wrong.

If there is a genuinely better-than-obvious algorithm or structure for this
problem -- an amortisation, a different asymptotic class, a reformulation that
removes a whole category of bug -- name it explicitly and say what it buys.

Be specific and technical. No preamble."""

DELTA = """You designed a solution to a problem. Here is your design:

--- YOUR INDEPENDENT DESIGN ---
{design}

An implementation now exists, written independently of your design. Here is a
factual summary of it:

--- THE ACTUAL IMPLEMENTATION ---
{summary}

Report the DELTA. For each material difference between your design and the
implementation:

- name it in one line
- say which is better AND WHY, in terms of a concrete consequence (a bug that
  becomes possible, a complexity class, a failure that stops being handled) --
  not in terms of style or preference
- rate it: CRITICAL (correctness or a real failure mode), VALUABLE (materially
  better performance, simplicity or extensibility), or MINOR
- if the implementation is better than your design, say so plainly

Then close with:
- **VERDICT**: the single highest-value change, or the words NO CHANGE WARRANTED
  if the implementation is already at least as good as your design.

Do not list differences that do not matter. An honest short report beats a long
one. If your design was worse, that is the correct finding and you should say it."""

def sweep(goal, paths=None, summary=None, constraints=""):
    t0 = time.time()
    paths = paths or []
    code = read_paths(paths) if paths else ""
    if not summary:
        if not code:
            return {"error": "sweep needs either `paths` to summarise or an explicit `summary`."}
        summary = summarise_local(goal, code)

    # Step 1 runs with NO knowledge of the implementation. This ordering is the
    # mechanism, not a convenience -- it is why the design is independent.
    blind = BLIND.format(
        goal=goal,
        constraints=("HARD CONSTRAINTS\n" + constraints) if constraints else
                    "There are no stated constraints beyond correctness and clarity.")
    design, meta = _call("sweep", blind, 12000)
    designer = meta.get("model", "?")
    if not design:
        # Never run the delta against an empty design. Asked to diff against
        # nothing, a good model correctly refuses and a bad one invents a design
        # and then critiques the code for not matching its own invention. Fall to
        # the local model and SAY SO -- a labelled local sweep is honest; an
        # unlabelled empty one is worse than no sweep at all.
        design, meta = _call("build", blind, 6000)
        designer = meta.get("model", "local") + "  (frontier unavailable — this is NOT a capability delta)"
    if not design:
        return {"error": "no design could be produced; both the frontier and local routes returned nothing."}
    delta = llm("sweep", DELTA.format(design=design, summary=summary), 14000)

    # The model is asked to end with **VERDICT**, and usually does. When the
    # report runs long it can be cut off before it gets there, so fall back to
    # the highest-severity finding it did produce rather than reporting nothing.
    verdict = ""
    m = re.search(r"\*\*VERDICT\*\*:?\s*(.+?)(?:\n\n|$)", delta, re.S)
    if m:
        verdict = " ".join(m.group(1).split())[:400]
    if not verdict:
        c = re.search(r"^.*\bCRITICAL\b.*$", delta, re.M)
        verdict = ("(report truncated before its verdict) highest-severity finding: "
                   + " ".join(c.group(0).split())[:300]) if c else "see report"

    body = f"""**Goal:** {goal}

**Files summarised:** {', '.join(paths) if paths else '(summary supplied directly)'}
**Blind design by:** {designer}
**Elapsed:** {round(time.time()-t0,1)}s

## Verdict

{verdict}

## Delta

{delta}

## Blind design

_Written before the implementation was disclosed. Preserved so the delta can be
audited: if the design is bad, the delta is worthless._

{design}

## Implementation summary given to the architect

{summary}
"""
    path, _ = memory_write("sweep", goal[:70], body)
    return {"verdict": verdict, "delta": delta, "design": design, "path": path,
            "designer": designer, "seconds": round(time.time() - t0, 1)}

TOOLS = [
 {"name": "sweep",
  "description": "Frontier architecture sweep. Give it the GOAL the code serves and the file paths. It has the best available frontier model design a solution from first principles WITHOUT seeing the code (so the design is independent, not a review), then reports the concrete delta against what was actually built, ranked by value. Use after finishing a non-trivial implementation, when stuck on an architectural fork, or before committing to a structure that is hard to change. Writes a markdown report to .agent/sweeps/. Slow (60-300s) — never call it in a tight loop.",
  "inputSchema": {"type": "object", "properties": {
      "goal": {"type": "string", "description": "the problem the code solves, stated WITHOUT reference to how it was solved"},
      "paths": {"type": "array", "items": {"type": "string"}, "description": "files that implement it"},
      "summary": {"type": "string", "description": "optional: supply the implementation summary yourself instead of having it generated"},
      "constraints": {"type": "string", "description": "hard constraints the design must respect (interfaces, dependencies, performance targets)"}},
    "required": ["goal"]}},
]

def handle(req):
    m, i = req.get("method"), req.get("id")
    if m == "initialize":
        return {"jsonrpc": "2.0", "id": i, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
            "serverInfo": {"name": "clank-sweep", "version": "1.0.0"}}}
    if m == "tools/list":
        return {"jsonrpc": "2.0", "id": i, "result": {"tools": TOOLS}}
    if m == "tools/call":
        p = req["params"]; a = p.get("arguments") or {}
        try:
            out = sweep(a["goal"], a.get("paths"), a.get("summary"), a.get("constraints", ""))
            txt = out.get("error") or (
                f"VERDICT: {out['verdict']}\n\nreport: {out['path']} ({out['seconds']}s)\n\n"
                f"--- DELTA ---\n{out['delta']}")
            return {"jsonrpc": "2.0", "id": i, "result": {"content": [{"type": "text", "text": txt}]}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": i, "result": {
                "content": [{"type": "text", "text": f"ERROR: {e}"}], "isError": True}}
    return None if i is None else {"jsonrpc": "2.0", "id": i, "error": {"code": -32601, "message": m}}

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        goal = sys.argv[2]
        out = sweep(goal, sys.argv[3:])
        print(out.get("error") or f"VERDICT: {out['verdict']}\n\nreport: {out['path']}")
        sys.exit(0)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle(json.loads(line))
        except Exception as e:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(e)}}
        if resp:
            sys.stdout.write(json.dumps(resp) + "\n"); sys.stdout.flush()
