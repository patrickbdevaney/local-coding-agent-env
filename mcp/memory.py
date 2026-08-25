#!/usr/bin/env python3
"""
clank memory MCP -- durable project memory as markdown a human can read.

WHY NOT A VECTOR DATABASE

The corpus this serves is one project's notes, decisions, research and symbol
map: a few hundred KB of highly technical markdown, dense with exact identifiers
(`chat_template_kwargs`, `LOOP_TRIP`, `nemotron-3-ultra`). Those identifiers are
precisely what lexical retrieval is best at and what embeddings blur. A vector
store would add a daemon, a model, an index to rebuild and a failure mode, in
exchange for worse recall on the queries that actually get asked.

So: BM25 over markdown chunks plus a regex symbol map. No daemon, no embedding
server, nothing else to keep alive, and the whole memory is `git diff`-able.

Everything lands in `.agent/` inside the working repo:

    MEMORY.md        durable project facts, one line each
    repomap.md       compact symbol map -- the cheap "what exists"
    research/*.md    every deep-research run, sources and provenance
    sweeps/*.md      every frontier sweep
    decisions/*.md   architectural decisions, and what was rejected
    journal.md       append-only log of what was done

Accumulation is the point: the useful output of a workflow is a file, not a
scrollback.
"""
import json, sys, os, re, math, time, subprocess
from collections import Counter

def agent_dir():
    """.agent/ at the git root if there is one, else the working directory."""
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=5)
        base = top.stdout.strip() if top.returncode == 0 and top.stdout.strip() else os.getcwd()
    except Exception:
        base = os.getcwd()
    d = os.path.join(base, ".agent")
    for sub in ("", "research", "sweeps", "decisions"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    return d

def slug(s, n=60):
    return (re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "untitled")[:n]

# --- lexical index -----------------------------------------------------------

TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}|\d+\.\d+")

def tokenize(s):
    out = []
    for t in TOKEN.findall(s):
        t = t.lower()
        out.append(t)
        # snake_case and camelCase carry their parts as separate search terms:
        # a query for "template kwargs" must reach `chat_template_kwargs`.
        if "_" in t:
            out.extend(p for p in t.split("_") if len(p) > 1)
        else:
            parts = re.findall(r"[a-z]+|\d+", re.sub(r"(?<!^)(?=[A-Z])", "_", t)) if any(c.isupper() for c in t) else []
            out.extend(p for p in parts if len(p) > 1)
    return out

def chunks_of(path, text):
    """Split markdown on headings, then hard-cap. Headings are the natural unit;
    the cap only exists so one enormous section cannot dominate a result set."""
    parts, cur, head = [], [], ""
    for ln in text.splitlines():
        if ln.startswith("#") and cur:
            parts.append((head, "\n".join(cur))); cur, head = [], ln.strip("# ").strip()
        elif ln.startswith("#"):
            head = ln.strip("# ").strip()
        cur.append(ln)
    if cur:
        parts.append((head, "\n".join(cur)))
    out = []
    for h, body in parts:
        for i in range(0, max(len(body), 1), 2400):
            seg = body[i:i + 2400]
            if seg.strip():
                out.append({"path": path, "head": h, "text": seg})
    return out

def corpus():
    d = agent_dir()
    docs = []
    for root, dirs, files in os.walk(d):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        for f in sorted(files):
            if not f.endswith(".md"):
                continue
            p = os.path.join(root, f)
            try:
                docs.extend(chunks_of(os.path.relpath(p, os.path.dirname(d)), open(p, errors="replace").read()))
            except Exception:
                pass
    return docs

def bm25(query, docs, k=6):
    """Textbook BM25. k1=1.5, b=0.75 -- the defaults are fine for a corpus this
    size and tuning them would be fitting noise."""
    if not docs:
        return []
    toks = [tokenize(d["text"] + " " + d["head"]) for d in docs]
    N = len(docs)
    avg = sum(len(t) for t in toks) / N
    df = Counter()
    for t in toks:
        df.update(set(t))
    q = tokenize(query)
    k1, b = 1.5, 0.75
    scored = []
    for i, t in enumerate(toks):
        tf, dl, s = Counter(t), len(t), 0.0
        for w in q:
            f = tf.get(w, 0)
            if not f:
                continue
            idf = math.log(1 + (N - df[w] + 0.5) / (df[w] + 0.5))
            s += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avg))
        if s > 0:
            scored.append((s, i))
    scored.sort(reverse=True)
    return [dict(docs[i], score=round(s, 2)) for s, i in scored[:k]]

# --- repo map ----------------------------------------------------------------

SKIP_DIR = {".git", "node_modules", "target", "__pycache__", "venv", ".venv", "dist",
            "build", ".next", ".agent", "vendor", ".cargo", "site-packages"}
SYMBOL = {
    ".py":   re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+(\w+)", re.M),
    ".rs":   re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?(?:fn|struct|enum|trait|impl)\s+(\w+)", re.M),
    ".ts":   re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|interface|type|const)\s+(\w+)", re.M),
    ".js":   re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|const)\s+(\w+)", re.M),
    ".go":   re.compile(r"^\s*(?:func|type)\s+\(?\w*\s*\*?\w*\)?\s*(\w+)", re.M),
    ".sh":   re.compile(r"^\s*(?:function\s+)?(\w+)\s*\(\)\s*\{", re.M),
    ".c":    re.compile(r"^\s*[\w*]+\s+(\w+)\s*\([^;]*\)\s*\{", re.M),
}
SYMBOL[".tsx"] = SYMBOL[".ts"]; SYMBOL[".jsx"] = SYMBOL[".js"]
SYMBOL[".h"] = SYMBOL[".cpp"] = SYMBOL[".cc"] = SYMBOL[".c"]

def build_repomap(max_files=400, per_file=14):
    base = os.path.dirname(agent_dir())
    lines, nfiles = [], 0
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIR and not d.startswith("."))
        for f in sorted(files):
            ext = os.path.splitext(f)[1]
            if ext not in SYMBOL:
                continue
            p = os.path.join(root, f)
            try:
                if os.path.getsize(p) > 900_000:
                    continue
                src = open(p, errors="replace").read()
            except Exception:
                continue
            syms = list(dict.fromkeys(SYMBOL[ext].findall(src)))
            rel = os.path.relpath(p, base)
            head = ""
            m = re.search(r'^\s*(?:"""|//!|#\s|/\*\*)\s*(.+)', src)
            if m:
                head = m.group(1).strip().strip('"').strip("*").strip()[:90]
            lines.append(f"- `{rel}` ({src.count(chr(10))+1} lines){' — ' + head if head else ''}"
                         + (f"\n  - {', '.join(syms[:per_file])}" if syms else ""))
            nfiles += 1
            if nfiles >= max_files:
                break
        if nfiles >= max_files:
            break
    body = ("# Repo map\n\nGenerated %s. %d files.\nRegenerate with the `repomap` tool "
            "after structural changes.\n\n%s\n" % (time.strftime("%Y-%m-%d %H:%M"), nfiles, "\n".join(lines)))
    path = os.path.join(agent_dir(), "repomap.md")
    open(path, "w").write(body)
    return path, nfiles

# --- writes ------------------------------------------------------------------

def memory_write(kind, title, body):
    d = agent_dir()
    stamp = time.strftime("%Y-%m-%d %H:%M")
    if kind == "fact":
        p = os.path.join(d, "MEMORY.md")
        if not os.path.exists(p):
            open(p, "w").write("# Project memory\n\nDurable facts. One per line. "
                               "Loaded at the start of every session.\n\n")
        # A fact already recorded is not written twice: this file is read into
        # every session's context and duplication is a direct context tax.
        cur = open(p).read()
        line = f"- **{title}** — {body.strip()}"
        if line.split(" — ")[0] in cur:
            return p, "already recorded"
        open(p, "a").write(line + "\n")
        return p, "appended"
    if kind == "journal":
        p = os.path.join(d, "journal.md")
        open(p, "a").write(f"\n## {stamp} — {title}\n\n{body.strip()}\n")
        return p, "appended"
    sub = {"decision": "decisions", "research": "research", "sweep": "sweeps"}.get(kind, "decisions")
    p = os.path.join(d, sub, f"{time.strftime('%Y%m%d-%H%M')}-{slug(title)}.md")
    open(p, "w").write(f"# {title}\n\n_{stamp}_\n\n{body.strip()}\n")
    return p, "written"

# --- MCP ---------------------------------------------------------------------

TOOLS = [
 {"name": "memory_search",
  "description": "Search this project's durable memory: past decisions, research reports, frontier sweeps, the journal and the repo symbol map. Call this BEFORE investigating anything from scratch — the answer may already have been established and written down in an earlier session.",
  "inputSchema": {"type": "object", "properties": {
      "query": {"type": "string"}, "k": {"type": "integer", "description": "results (default 6)"}},
    "required": ["query"]}},
 {"name": "memory_write",
  "description": "Record something durable in .agent/ as markdown. kind=fact for a one-line project truth (goes in MEMORY.md, loaded every session); kind=decision for an architectural choice and what was rejected; kind=journal for what was just done. Use this whenever you establish something a future session would otherwise have to rediscover.",
  "inputSchema": {"type": "object", "properties": {
      "kind": {"type": "string", "enum": ["fact", "decision", "journal", "research", "sweep"]},
      "title": {"type": "string"}, "body": {"type": "string"}},
    "required": ["kind", "title", "body"]}},
 {"name": "repomap",
  "description": "Regenerate and return the compact symbol map of this repository (files, top-level functions/classes, one-line purpose). Far cheaper than reading files to find out what exists. Refresh after structural changes.",
  "inputSchema": {"type": "object", "properties": {}}},
 {"name": "memory_list",
  "description": "List everything currently in project memory: facts, decisions, research reports and sweeps, with dates.",
  "inputSchema": {"type": "object", "properties": {}}},
]

def call(name, a):
    if name == "memory_search":
        hits = bm25(a["query"], corpus(), int(a.get("k", 6)))
        if not hits:
            return "No memory matches. This project has nothing recorded on that yet."
        return "\n\n".join(f"### {h['path']} :: {h['head']} (score {h['score']})\n{h['text'][:1600]}" for h in hits)
    if name == "memory_write":
        p, how = memory_write(a["kind"], a["title"], a["body"])
        return f"{how}: {p}"
    if name == "repomap":
        p, n = build_repomap()
        return open(p).read()[:60000]
    if name == "memory_list":
        d, out = agent_dir(), []
        for root, _, files in os.walk(d):
            for f in sorted(files):
                if f.endswith(".md"):
                    p = os.path.join(root, f)
                    out.append(f"{os.path.relpath(p, d):<52} {os.path.getsize(p):>7}B  "
                               f"{time.strftime('%Y-%m-%d', time.localtime(os.path.getmtime(p)))}")
        return "\n".join(out) or "(memory empty)"
    return "unknown tool"

def handle(req):
    m, i = req.get("method"), req.get("id")
    if m == "initialize":
        return {"jsonrpc": "2.0", "id": i, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
            "serverInfo": {"name": "clank-memory", "version": "1.0.0"}}}
    if m == "tools/list":
        return {"jsonrpc": "2.0", "id": i, "result": {"tools": TOOLS}}
    if m == "tools/call":
        p = req["params"]
        try:
            txt = call(p["name"], p.get("arguments") or {})
            return {"jsonrpc": "2.0", "id": i, "result": {"content": [{"type": "text", "text": txt}]}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": i, "result": {
                "content": [{"type": "text", "text": f"ERROR: {e}"}], "isError": True}}
    return None if i is None else {"jsonrpc": "2.0", "id": i, "error": {"code": -32601, "message": m}}

if __name__ == "__main__":
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
