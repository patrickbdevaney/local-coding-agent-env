#!/usr/bin/env python3
"""
lca router -- an OpenAI-compatible gateway in front of a heterogeneous fleet.

It exists to make one property true: THE AGENT NEVER FAILS FOR LACK OF A CLOUD
MODEL. The local endpoint is the floor. Everything above it is additive. With
every cloud key revoked, every virtual model still resolves and every run still
completes -- slower, and without the frontier delta, but completely.

Virtual models exposed to any OpenAI-compatible client:

    build    executor          LOCAL -> free frontier      (the default; ~all work)
    plan     planner/observer  best free frontier -> LOCAL
    sweep    architecture      best free frontier -> LOCAL (long output)
    distill  cheap wide map    groq fanout -> LOCAL        (many docs at once)
    search   web-plugin lane   OPT-IN, costs money, off by default

WHY THE FRONTIER POOL IS DISCOVERED AND NOT CONFIGURED

Free endpoints churn: they appear, get hammered, get withdrawn. Any hardcoded
list is wrong within weeks -- and wrong silently, since a withdrawn model 404s
and looks like a transient failure. So the pool is fetched from the provider's
own catalogue, filtered to genuinely zero-cost, ranked by a capability table
with a heuristic for models the table has never seen, and annotated with live
health so a model that just rate-limited is skipped until it cools down.
Greedy: always try the best currently-available model, fall down on failure,
terminate at local.

KEYS: this file reads exactly ONE key per provider. See keys() below.
"""
import http.server, socketserver, json, os, time, hashlib, threading, re, sys
import urllib.request, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG  = os.path.join(ROOT, "logs", "calls.jsonl")

def load_env(path):
    e = {}
    if os.path.exists(path):
        for ln in open(path):
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                # Values are frequently quoted in .env files. An unstripped quote
                # becomes part of the bearer token and every remote route 401s.
                e[k.strip()] = v.strip().strip('"').strip("'")
    return e

# .env is loaded BEFORE any setting is read from it. An earlier version read the
# settings at import time and the .env afterwards, which silently ignored
# LCA_LOCAL_URL and pointed the local route at the default port -- occupied here
# by an unrelated service, so every local call failed as a malformed response
# rather than as a misconfiguration.
ENV = load_env(os.path.join(ROOT, ".env"))
ENV.update(os.environ)

PORT        = int(ENV.get("LCA_PORT", "8787"))
LOCAL_URL   = ENV.get("LCA_LOCAL_URL", "http://127.0.0.1:8080/v1")
LOCAL_MODEL = ENV.get("LCA_LOCAL_MODEL", "qwen38-27b")
# A dead upstream must fail fast enough to fall down the ladder. At 900s a
# server that died mid-stream hung the client for fifteen minutes instead.
TIMEOUT     = int(ENV.get("LCA_TIMEOUT", "300"))
ENABLE_PAID_SEARCH = ENV.get("LCA_PAID_SEARCH", "0") == "1"

def keys(provider):
    """Credentials for a provider, best first.

    One key per provider, from the environment. `local/keyring.py` is an
    optional, gitignored, absent-by-default seam for deployments that supply
    their own credential source; nothing in this repository provides one, and
    everything here is correct and complete without it.
    """
    try:
        sys.path.insert(0, os.path.join(ROOT, "local"))
        import keyring                                  # type: ignore
        ks = [k for k in keyring.keys(provider) if k]
        if ks:
            return ks
    except Exception:
        pass
    k = ENV.get({"openrouter": "OPENROUTER_API_KEY", "groq": "GROQ_API_KEY"}.get(provider, ""))
    return [k] if k else []

_lock = threading.Lock()

def next_key(provider):
    """The credential to use for this call: the first one `keys()` offers.

    Selection policy lives entirely in `keys()`, not here. This function is
    deliberately trivial so that the router contains no notion of having more
    than one credential per provider.
    """
    ks = keys(provider)
    return ks[0] if ks else None

# --- local request shape -----------------------------------------------------
#
# The local server is greedy-with-speculative-draft only when the harness asks
# for it. Clients do not send chat_template_kwargs, so Qwen's non-thinking
# sampling defaults get applied to a temperature-0 request -- which distorts
# code output and disables speculation outright, since the acceptance rule is
# argmax equality. This one dict is worth ~3x decode throughput.
LOCAL_INJECT = {"temperature": 0, "chat_template_kwargs": {"enable_thinking": False}}

# --- frontier pool -----------------------------------------------------------
#
# Ranking is by capability class, not by name. The patterns describe FAMILIES so
# that a newly-freed model in a known-strong family is picked up automatically,
# and anything unrecognised still gets a defensible score from its context
# window rather than being ignored.
FRONTIER_RANK = [
    (r"kimi.*k[3-9]",                    100),   # ~2.8T
    (r"deepseek.*v[4-9]",                 98),   # ~1.7T
    (r"qwen3?\.?[89].*(a\d\d+b|\d{3,}b)", 96),   # the 2.4T A96B, NOT the local 27B
    (r"glm-?5\.[3-9]|glm-?[6-9]",          94),   # 744B class
    (r"glm-?5\.?2",                       80),   # previous generation, and the free tier
                                                 # of it is rate-limited hard enough that it
                                                 # 429s twice before ox-alpha answers at all
    (r"ox-alpha",                         92),
    (r"minimax/minimax-m[3-9]",           88),
    (r"nemotron-3-ultra",                 86),   # 550B A55B
    (r"inkling(?!-small)",                84),
    (r"nemotron-3-super",                 78),   # 120B A12B
    (r"laguna-s",                         74),   # coding-specialised
    (r"minimax/minimax-m2",               70),
    (r"north-mini-code",                  66),
    (r"dots-3",                           60),
    (r"inkling-small",                    58),
    (r"laguna-xs",                        56),
    (r"gemma-4-31b",                      50),
]
# Never route reasoning at these: they are not general chat models, or they are
# too small to beat the local 27B and would be a downgrade dressed as a fallback.
FRONTIER_DENY = re.compile(
    r"lyria|whisper|embed|guard|safety|moderation|image|video|audio|tts|"
    r"omni|-vl|vision|openrouter/free|lfm|-2b|-3b|-4b|nano", re.I)

BOOTSTRAP = [  # used only until the first live catalogue fetch succeeds
    ("stealth/ox-alpha", 1048576), ("minimax/minimax-m3:free", 1048576),
    ("nvidia/nemotron-3-ultra-550b-a55b:free", 1000000),
    ("thinkingmachines/inkling:free", 1048576), ("z-ai/glm-5.2:free", 256000),
    ("nvidia/nemotron-3-super-120b-a12b:free", 262144),
]

def rank_model(mid, ctx):
    for pat, score in FRONTIER_RANK:
        if re.search(pat, mid, re.I):
            return score
    # Unseen model: score from what we can actually observe. Context window is a
    # decent proxy for tier, and an explicit parameter count in the name is a
    # better one. Deliberately capped below every known family so a stranger
    # never displaces a model we have measured.
    s = 30.0 + min(ctx, 1_048_576) / 52_000.0
    if re.search(r"\d{3,}b|\dt\b|a\d\d+b", mid, re.I):
        s += 8.0
    return min(s, 55.0)

class Pool:
    """Live, health-aware ranking of free frontier endpoints."""
    def __init__(self):
        self.models = [(rank_model(m, c), m, c) for m, c in BOOTSTRAP]
        self.models.sort(reverse=True)
        self.health = {}          # model -> {"cool_until": ts, "fails": int}
        self.refreshed = 0.0
        self.source = "bootstrap"

    def refresh(self):
        key = next_key("openrouter")
        if not key:
            return
        try:
            r = urllib.request.urlopen(urllib.request.Request(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": "Bearer " + key, "User-Agent": "lca-router/1.0"}), timeout=30)
            data = json.load(r).get("data", [])
        except Exception:
            return                # keep the previous pool; never degrade on a fetch blip
        out = []
        for m in data:
            mid, p = m.get("id", ""), (m.get("pricing") or {})
            try:
                if float(p.get("prompt", 1)) != 0 or float(p.get("completion", 1)) != 0:
                    continue
            except Exception:
                continue
            if FRONTIER_DENY.search(mid):
                continue
            ctx = m.get("context_length") or 0
            if ctx < 100_000:     # below this it cannot hold an agentic transcript
                continue
            out.append((rank_model(mid, ctx), mid, ctx))
        if out:
            out.sort(reverse=True)
            with _lock:
                self.models, self.refreshed, self.source = out, time.time(), "live"

    def available(self, n=4):
        """Best n models not currently cooling down, best first."""
        now, out = time.time(), []
        for _, mid, _ in self.models:
            h = self.health.get(mid)
            if h and h["cool_until"] > now:
                continue
            out.append(mid)
            if len(out) >= n:
                break
        if not out:               # everything is cooling: take the best two anyway
            out = [m for _, m, _ in self.models[:2]]
        return out

    def penalise(self, mid, status):
        """Cool a model down after a failure. Exponential, capped, per-model.

        429 is the common case and is upstream congestion, not a broken model,
        so the cooldown is short and clears on the next success.
        """
        with _lock:
            h = self.health.setdefault(mid, {"cool_until": 0.0, "fails": 0})
            h["fails"] += 1
            base = 45 if status == 429 else 120
            h["cool_until"] = time.time() + base * (2 ** min(h["fails"] - 1, 3))

    def reward(self, mid):
        with _lock:
            self.health.pop(mid, None)

POOL = Pool()

def _refresher():
    while True:
        POOL.refresh()
        time.sleep(900)
threading.Thread(target=_refresher, daemon=True).start()

def ladder(virtual):
    """Build the attempt ladder for a virtual model, freshly, per request."""
    fr = POOL.available(4)
    L = ("local", LOCAL_MODEL)
    if virtual == "build":
        # Local first and twice: a single transient local failure (a restart, a
        # dropped connection) should not silently promote the run to a cloud
        # model with different behaviour.
        return [L, L] + [("openrouter", m) for m in fr[:2]] + [L]
    if virtual in ("plan", "sweep"):
        # Frontier first, and the top model twice: retries land on a different
        # moment, and 429s here are congestion rather than refusal.
        top = fr[:1] * 2 + fr[1:4]
        return [("openrouter", m) for m in top] + [L]
    if virtual == "distill":
        # gpt-oss first: it honours reasoning_effort, and distillation is
        # extraction, not reasoning. Models that emit a <think> block regardless
        # spend the per-minute token budget on prose nobody reads.
        return [("groq", "openai/gpt-oss-20b"), ("groq", "qwen/qwen3.6-27b"),
                ("groq", "openai/gpt-oss-120b"), L]
    if virtual == "search":
        if not ENABLE_PAID_SEARCH:
            return [L]
        return [("openrouter", m) for m in fr[:3]] + [L]
    return [L]

VIRTUALS = ["build", "plan", "sweep", "distill", "search"]

# Bytes to read while proving a rung is alive before committing the client to it.
PRECOMMIT_BYTES = int(ENV.get("LCA_PRECOMMIT_BYTES", "16384"))
# Wall-clock reserved for the local floor. Descent through cloud rungs stops
# once this much of the request budget is gone, because "the ladder always ends
# at local" is only true if there is still time left to reach it. Without this,
# three slow cloud rungs can consume the client's entire patience and the
# guarantee holds only in principle.
LOCAL_RESERVE = int(ENV.get("LCA_LOCAL_RESERVE", "240"))
CLOUD_BUDGET  = int(ENV.get("LCA_CLOUD_BUDGET", "420"))

CONTENT_RE = re.compile(
    rb'"(?:content|reasoning_content|reasoning|tool_calls|text)"\s*:\s*(?!(?:""|null|\[\s*\])[,}])')

def has_content(buf):
    """Has this upstream actually produced anything a client can use yet?

    Deliberately bytewise and permissive: it runs on a partial buffer that may
    end mid-token, and a false negative merely means reading a little more,
    while a false positive commits the client to a rung that may be dead.
    """
    if b"[DONE]" in buf:
        return True
    return bool(CONTENT_RE.search(buf))

# --- runtime guard: tool-call repetition -------------------------------------
#
# Measured: a local 27B in an agentic loop refetched the identical URL forty-plus
# times and burned its whole turn budget without progressing. Smaller models fall
# into this whenever a tool returns something they cannot use -- they retry it
# verbatim rather than changing approach. Nothing downstream breaks, so nothing
# surfaces an error; the run just dies of timeout with no diagnosis.
#
# The gateway is the right place to catch it because it sees the entire message
# array every turn, which no single tool call can. The intervention is a nudge,
# not a block: refusing the call would strand a model that legitimately needs a
# retry, whereas naming the loop lets it choose differently.
LOOP_WINDOW, LOOP_TRIP = 14, 3

def _tool_signatures(messages):
    sigs = []
    for m in messages[-LOOP_WINDOW:]:
        for tc in (m.get("tool_calls") or []):
            fn = tc.get("function") or {}
            sigs.append((fn.get("name"), fn.get("arguments")))
        if m.get("role") == "tool" and m.get("name"):
            sigs.append((m.get("name"), None))
    return sigs

def detect_loop(messages):
    counts = {}
    for sig in _tool_signatures(messages):
        if sig[0] is None or sig[1] is None:
            continue
        counts[sig] = counts.get(sig, 0) + 1
        if counts[sig] >= LOOP_TRIP:
            return sig
    return None

LOOP_NUDGE = (
    "SYSTEM INTERVENTION: you have now called `{name}` with identical arguments "
    "{n} times and received the same result each time. Repeating it will not "
    "produce a different answer. Do one of these instead: (a) use what you "
    "already have and proceed, (b) call a DIFFERENT tool or use different "
    "arguments, or (c) state plainly that the information is unavailable and "
    "continue with the task. Do not call `{name}` with those arguments again.")

def endpoint(prov):
    if prov == "local":      return LOCAL_URL.rstrip("/") + "/chat/completions", None
    if prov == "openrouter": return "https://openrouter.ai/api/v1/chat/completions", next_key("openrouter")
    if prov == "groq":       return "https://api.groq.com/openai/v1/chat/completions", next_key("groq")
    raise ValueError(prov)

def logline(rec):
    with _lock:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")

class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        p = self.path.rstrip("/")
        if p.endswith("/models"):
            self._json(200, {"object": "list", "data": [
                {"id": m, "object": "model", "owned_by": "lca", "context_length": 262144}
                for m in VIRTUALS]})
        elif p.endswith("/health"):
            now = time.time()
            self._json(200, {
                "local": LOCAL_URL, "pool_source": POOL.source,
                "pool_age_s": round(now - POOL.refreshed, 1) if POOL.refreshed else None,
                "openrouter_keys": len(keys("openrouter")), "groq_keys": len(keys("groq")),
                "frontier": [{"model": m, "rank": round(s, 1), "ctx": c,
                              "cooling_s": round(max(0, POOL.health.get(m, {}).get("cool_until", 0) - now), 1)}
                             for s, m, c in POOL.models[:8]],
                "next": POOL.available(4)})
        else:
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(n))
        except Exception:
            return self._json(400, {"error": "bad json"})

        virtual = req.get("model", "build")
        if virtual not in VIRTUALS:
            virtual = "build"
        looped = detect_loop(req.get("messages") or [])
        if looped:
            req = dict(req)
            req["messages"] = list(req.get("messages") or []) + [
                {"role": "system", "content": LOOP_NUDGE.format(name=looped[0], n=LOOP_TRIP)}]

        stream = bool(req.get("stream"))
        phash = hashlib.sha256(json.dumps(req.get("messages", []), sort_keys=True).encode()).hexdigest()[:16]
        attempts, t0 = [], time.time()

        rungs = ladder(virtual)
        for ix, (prov, model) in enumerate(rungs):
            # Stop spending the budget on cloud rungs while the floor is still
            # unreached. Skipping ahead is always safe: local is the last rung.
            if prov != "local" and time.time() - t0 > CLOUD_BUDGET - LOCAL_RESERVE \
                    and any(p == "local" for p, _ in rungs[ix:]):
                attempts.append({"provider": prov, "model": model, "status": "skipped",
                                 "err": "cloud budget spent, reserving time for the local floor"})
                continue
            url, key = endpoint(prov)
            if prov != "local" and not key:
                attempts.append({"provider": prov, "model": model, "status": "nokey"})
                continue
            body = dict(req); body["model"] = model
            if prov == "local":
                body.update(LOCAL_INJECT)
                body.pop("plugins", None); body.pop("reasoning_effort", None)
            else:
                body.pop("chat_template_kwargs", None)
            if virtual == "search" and prov == "openrouter":
                body.setdefault("plugins", [{"id": "web", "max_results": 8}])
            # Groq fronts its API with Cloudflare, which rejects urllib's default
            # User-Agent outright with error 1010. Any normal UA passes.
            hdr = {"Content-Type": "application/json", "User-Agent": "lca-router/1.0"}
            if key:
                hdr["Authorization"] = "Bearer " + key
            if attempts and attempts[-1].get("model") == model:
                time.sleep(0.8)
            ta = time.time()
            try:
                r = urllib.request.urlopen(
                    urllib.request.Request(url, json.dumps(body).encode(), hdr), timeout=TIMEOUT)
            except urllib.error.HTTPError as e:
                if prov == "openrouter":
                    POOL.penalise(model, e.code)
                attempts.append({"provider": prov, "model": model, "status": e.code,
                                 "err": e.read()[:180].decode("utf8", "replace"),
                                 "s": round(time.time() - ta, 2)})
                continue
            except Exception as e:
                if prov == "openrouter":
                    POOL.penalise(model, 0)
                attempts.append({"provider": prov, "model": model, "status": "conn",
                                 "err": str(e)[:180], "s": round(time.time() - ta, 2)})
                continue

            # --- pre-commit read -------------------------------------------
            #
            # Do NOT commit on the first byte. Free-tier providers routinely
            # accept the connection, emit headers and an empty role-preamble
            # chunk, and then stall or die. Committing at that instant hands the
            # client a hung or truncated answer with no recourse, because the
            # ladder has already been abandoned. Commit only once a rung has
            # proved liveness by producing actual content -- until then a
            # failure is still recoverable by descending.
            #
            # The buffer is bounded: this is a liveness probe, not a spool.
            prebuf, evidence, dead = b"", False, None
            try:
                while not evidence and len(prebuf) < PRECOMMIT_BYTES:
                    chunk = r.read(4096)
                    if not chunk:
                        break
                    prebuf += chunk
                    evidence = has_content(prebuf)
            except Exception as e:
                dead = str(e)[:120]
            if not evidence:
                # Nothing usable ever arrived. Treat exactly like a connection
                # failure and keep descending -- this is the case that used to
                # look like success.
                if prov == "openrouter":
                    POOL.penalise(model, 0)
                attempts.append({"provider": prov, "model": model, "status": "empty",
                                 "err": dead or f"no content in first {len(prebuf)}B",
                                 "s": round(time.time() - ta, 2)})
                try:
                    r.close()
                except Exception:
                    pass
                continue

            if prov == "openrouter":
                POOL.reward(model)
            # Liveness proved -> commit to this route and stream it through.
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream" if stream else "application/json")
            self.send_header("Transfer-Encoding", "chunked"); self.end_headers()
            usage, total = None, 0
            try:
                pending = prebuf
                while True:
                    chunk = pending or r.read(8192)
                    pending = b""
                    if not chunk:
                        break
                    total += len(chunk)
                    if b'"usage"' in chunk:
                        for ln in chunk.split(b"\n"):
                            ln = ln.strip()
                            if ln.startswith(b"data: "):
                                ln = ln[6:]
                            if ln in (b"", b"[DONE]"):
                                continue
                            try:
                                u = json.loads(ln).get("usage")
                                if u:
                                    usage = u
                            except Exception:
                                pass
                    self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n"); self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n"); self.wfile.flush()
            except Exception as e:
                attempts.append({"provider": prov, "model": model, "status": "midstream", "err": str(e)[:120]})
            attempts.append({"provider": prov, "model": model, "status": 200, "s": round(time.time() - ta, 2)})
            logline({"ts": time.time(), "virtual": virtual, "chosen": f"{prov}:{model}",
                     "prompt_sha": phash, "stream": stream, "bytes": total, "usage": usage,
                     "total_s": round(time.time() - t0, 2), "attempts": attempts,
                     "loop_nudge": looped[0] if looped else None})
            return

        logline({"ts": time.time(), "virtual": virtual, "chosen": None, "prompt_sha": phash,
                 "total_s": round(time.time() - t0, 2), "attempts": attempts})
        self._json(502, {"error": {"message": "all routes exhausted", "attempts": attempts}})

class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True; allow_reuse_address = True

if __name__ == "__main__":
    POOL.refresh()
    print(f"lca router :{PORT}  local={LOCAL_URL}  pool={POOL.source} "
          f"({len(POOL.models)})  or={len(keys('openrouter'))} groq={len(keys('groq'))}", flush=True)
    print("  frontier: " + ", ".join(POOL.available(4)), flush=True)
    S(("127.0.0.1", PORT), H).serve_forever()
