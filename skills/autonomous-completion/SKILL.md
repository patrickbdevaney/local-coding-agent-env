---
name: autonomous-completion
description: Work a multi-step task through to a verified finish without stopping to ask permission to continue. Use when the request is plainly multi-step and the user has not asked to be consulted — "get the suite green", "migrate all of these", "make it work", "finish this" — or when the user has said to keep going until done.
---

# Working to completion

## The default is to continue

When a task is plainly multi-step and nobody asked to be consulted, do not stop
after the first step to ask whether to proceed. That is the single most annoying
failure mode there is: it converts a task into a conversation.

- Where something is ambiguous, make the reasonable choice, note it, keep going.
- Where you hit a wall, change approach rather than retrying the same thing.
- Where you have to decide something structural, decide it and record it with
  `memory_write(kind="decision")` including what you rejected.

## Verify, do not hope

Local tokens are unmetered. Spend them on checking rather than on confidence:

- run the tests, and read the output rather than the exit code alone
- run the code on a real input
- when a test fails, work out **which side is wrong** before changing either —
  fix the production artefact, not the assertion that caught it
- re-read your own diff before declaring done

"It should work" is not a result.

## Stop conditions

Stop and hand back when:

- the work is done **and verified** — say what changed, in one short paragraph
- you are genuinely blocked — say exactly what blocks you and what you tried
- a decision is truly the user's to make and the options materially diverge
  (a real cost or risk tradeoff, not a naming preference)

Never report partial work as complete. If three of four things are done, say
which one is not and why.

## Ask only when it matters

A question is warranted when getting it wrong would waste substantial work or do
something irreversible. It is not warranted for anything you could determine by
reading a file, running a command, or picking the obvious default.
