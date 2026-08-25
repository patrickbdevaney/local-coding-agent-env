"""One correct way to call the router. Both MCP servers use it.

WHY THIS IS NOT THREE LINES OF urllib

Reasoning models return their tokens in a field that is not `content`, and the
field is not spelled the same everywhere: `reasoning_content` on some providers,
`reasoning` on OpenRouter. A caller that reads only `content` sees an empty
string and cannot tell it apart from a model that had nothing to say.

Worse, `max_tokens` bounds reasoning AND answer together. A model given 3000
tokens for a hard design question can spend all 3000 thinking and emit
`finish_reason: "length"` with an empty answer -- a silent, total failure that
costs a full frontier call and 105 seconds. That is not hypothetical; it is why
this module exists, and it produced a sweep report whose "independent design"
section was blank.

So: read every field the answer might be in, and if the model ran out of room
before answering, give it more room and ask again.
"""
import json, os, urllib.request

GATEWAY = os.environ.get("LCA_GATEWAY", "http://127.0.0.1:8787/v1/chat/completions")
UA = {"User-Agent": "lca/1.0", "Content-Type": "application/json"}

def _text(msg):
    return (msg.get("content") or msg.get("reasoning_content") or msg.get("reasoning") or "").strip()

def call(model, prompt, max_tokens=1200, system=None, effort=None, timeout=900):
    """Returns (text, meta). meta carries the model that actually served."""
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    budget = max_tokens
    served, finish = model, None
    for attempt in range(2):
        body = {"model": model, "messages": msgs, "max_tokens": budget, "temperature": 0}
        if effort:
            body["reasoning_effort"] = effort
        r = urllib.request.urlopen(
            urllib.request.Request(GATEWAY, json.dumps(body).encode(), UA), timeout=timeout)
        d = json.load(r)
        served = d.get("model", model)
        ch = (d.get("choices") or [{}])[0]
        finish = ch.get("finish_reason")
        txt = _text(ch.get("message") or {})
        if txt:
            return txt, {"model": served, "finish": finish, "attempts": attempt + 1}
        if finish != "length" or attempt:
            break
        # It thought until it ran out of room. More room, once.
        budget = min(budget * 4, 32000)
    return "", {"model": served, "finish": finish, "attempts": 2}

def llm(model, prompt, max_tokens=1200, system=None, effort=None, timeout=900):
    return call(model, prompt, max_tokens, system, effort, timeout)[0]
