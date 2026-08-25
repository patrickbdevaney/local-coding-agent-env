---
name: architecture-sweep
description: Get an independent architectural design from the strongest available frontier model and a ranked diff against what was actually built. Use after finishing a non-trivial implementation, when stuck at a design fork that the evidence does not settle, or before committing to a structure that will be expensive to change later. The frontier model never sees the code, so it produces the design it would have reached independently rather than a review.
---

# Architecture sweep

## What makes this different from a code review

The frontier model is given **the problem, the constraints and the interface —
never the code**. Then, and only then, it is shown a summary of what was built
and asked for the delta.

That ordering is the entire mechanism. Shown a solution, a model reviews it, and
review is anchored to what it was shown. Shown only the problem, it invents.
What you want from the scarce, expensive model is the idea it would have had on
its own.

## When this applies

- you have just finished something non-trivial and want to know what a stronger
  model would have done differently
- you are at a fork and the evidence does not settle it
- you are about to commit to a structure that is expensive to unwind (a schema,
  a protocol, a concurrency model, a public interface)
- something works but you suspect the shape is wrong

Not for routine edits. Not in a loop. It is slow (60–300s) and it is the scarce
resource.

## How

Call `sweep` with:

- `goal` — **the problem, never your solution.** "Make the cache evict LRU"
  leaks the answer and reduces this to a code review. "Bound memory for a cache
  with time-varying access patterns" gets you an independent design.
- `paths` — the files that implement it. A local model summarises them; the
  frontier model sees only the summary, which is what keeps this cheap.
- `constraints` — interfaces you cannot change, dependencies you cannot add,
  performance targets. Real constraints only.

## After it returns

Judge the findings — you have read the code and it has not. For each, say
whether you agree and why. Apply what is worth applying. Then record what you
deliberately did **not** apply and the reason, with `memory_write`: a finding
rejected for a good reason is worth as much as one you took, and without the
record the next session will re-litigate it.

If the report says the implementation is already at least as good, that is a
real result. Report it as one.
