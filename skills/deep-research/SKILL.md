---
name: deep-research
description: Find out how something outside this repository actually behaves — a library's real API, a request field, a spec, a protocol, a version's behaviour, benchmarks, prior art, or whether an approach is already solved. Use this whenever you are about to state an external fact you are not certain of, or write code against an interface you have not verified. Searches the web, reads every candidate page in parallel with cheap models, and saves a cited report.
---

# Deep research

## When this applies

The trigger is not the word "research". It is **you are about to assert
something about the outside world and you are not certain**. Any of these:

- writing code against an API, flag or wire format you have not checked
- "does X support Y?", "what is the field called?", "which version added this?"
- comparing approaches where the answer depends on how a tool really behaves
- being asked why something upstream behaves the way it does
- the user says something is possible and you doubt it, or vice versa

Do it **before** writing the code, not after the code fails. It costs a few
minutes and nothing else. Guessing an API wrong costs an entire debugging cycle.

## How

Call `deep_research` with a **precise, self-contained question**. It does not see
this conversation, so put the context inside the question:

> Bad: "how do I disable it?"
> Good: "What request field does llama.cpp's OpenAI-compatible server accept to
> disable Qwen3 thinking mode, and what is the vLLM equivalent?"

Leave `breadth` and `max_pages` unset — it sizes itself to whichever lanes are
live. It takes 1–5 minutes.

## After it returns

Answer in your own words. Say what the evidence shows, where sources disagree,
and what is still unknown. Cite what it found. Tell the user the path of the
saved report under `.agent/research/`.

If what you learned changes how the current work should be built, say so, and
record it with `memory_write` so the next session does not re-derive it.

## When not to

Do not use it for anything answerable from this repository — read the code. Do
not use it to look up something you already reliably know. Do not call it twice
on the same question with reworded phrasing; if the first answer was thin, say
what is missing instead.
