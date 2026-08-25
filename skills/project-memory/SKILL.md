---
name: project-memory
description: Search and record what this project has already established — decisions and their rejected alternatives, prior research, architecture sweeps, gotchas, and the repo symbol map. Use at the start of any non-trivial task before investigating from scratch, and whenever you learn something a future session would otherwise have to rediscover.
---

# Project memory

Everything durable lives in `.agent/`, as markdown that a human can read and git
can diff.

## Read first

Before investigating anything not obvious from the file in front of you:

1. `memory_search` — a previous session may have settled it. Re-deriving a
   settled fact is the most common way a turn gets wasted.
2. `repomap` — find out what exists without reading files to discover structure.
   It is a fraction of the tokens of opening things to look around.

If memory has nothing, say so plainly rather than guessing from the code.

## Write as you go

Record the moment you learn something, not at the end — sessions get truncated
and the unwritten fact is the one that is lost.

- `kind="fact"` — a durable truth: a port, a required request field, an
  invariant, a gotcha that cost you time. It goes in `MEMORY.md`, which is
  loaded into **every** future session, so keep it to one tight, specific line.
- `kind="decision"` — an architectural choice **including what you rejected and
  why**. The rejected option is the valuable half; without it the next session
  reopens the question.
- `kind="journal"` — what you did, after a real piece of work.

Write for someone who has not read this conversation. "Fixed the bug" is
useless; "the router must load .env before reading settings, or LCA_LOCAL_URL is
silently ignored" is not.

## Do not record

Things the code already says. Things true only inside this conversation. If a
fact stops being true, correct the file rather than appending a contradiction.
