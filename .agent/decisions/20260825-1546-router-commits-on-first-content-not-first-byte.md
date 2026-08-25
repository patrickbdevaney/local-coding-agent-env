# Router commits on first content, not first byte

_2026-08-25 15:46_

Found by the harness's own `sweep` tool, run against `router/gateway.py`
(.agent/sweeps/20260825-1541-*.md). The blind design was produced by
`stealth/ox-alpha` without ever seeing the code, which is why it surfaced a
class of failure the implementation had no concept of.

**The defect.** The router committed the client to a rung the moment the first
byte arrived. Free-tier providers routinely accept the connection, emit headers
and an empty role-preamble chunk, and then stall or die. At that instant the
ladder has already been abandoned, so the client gets a hung or truncated answer
with no recourse -- and it is logged as a success. The most common provider
pathology was the one case the failover could not see.

**The fix.** A bounded pre-commit read (16KB) that descends unless the rung
produces actual content. `has_content()` is deliberately permissive and
bytewise: it runs on a partial buffer that may end mid-token, and a false
negative costs one more read while a false positive commits the client to a dead
rung.

**Also applied: a reserved local budget.** `LOCAL_RESERVE=240s` of the request
budget is held back for the floor. "The ladder always ends at local" is only
true if there is still time left to reach it; without the reserve, three slow
cloud rungs could consume the client's entire patience and the guarantee held
only in principle.

**Rejected from the same report:** assistant-prefix splicing to continue a
stream that dies after commit. It is correct and it is the right long-term
answer, but it requires staging tool-call arguments until ToolCallEnd and
rewriting the streaming path around an owner-pump. The pre-commit probe removes
the common case at a fraction of the cost; splicing stays open.

**Conceded by the frontier model, and worth recording:** it rated live pool
discovery, task-shaped ladders, and the tool-loop nudge as points where the
implementation beat its own design. A sweep that only ever finds fault is not
measuring anything.
