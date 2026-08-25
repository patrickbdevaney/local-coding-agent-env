# Project memory

Durable facts. One per line. Loaded at the start of every session.

- **Local request shape** — The local server needs {temperature:0, chat_template_kwargs:{enable_thinking:false}} or speculation is disabled and decode drops from 142 to 46 tok/s.
- **Reasoning field is spelled `reasoning` on OpenRouter** — OpenRouter returns thinking tokens in `message.reasoning`, not `reasoning_content`. A caller reading only `content` gets an empty string and cannot distinguish it from a model with nothing to say. Worse, max_tokens bounds reasoning AND answer together: ox-alpha spent all 3000 tokens thinking and returned finish_reason=length with empty content. mcp/llm.py reads all three fields and retries once with 4x the budget on a length-truncated empty answer.
- **The local floor is the acceptance criterion, not a fallback** — Every route ladder in router/gateway.py terminates at the local model. Verified twice by accident: once when quoted .env values 401'd every remote route and every run still completed, and once when ox-alpha 429'd ~40% of calls and the pool degraded without a single failure.
