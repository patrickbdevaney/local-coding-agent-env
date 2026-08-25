#!/usr/bin/env python3
"""
clank research MCP -- deep research for a token-poor fleet, at zero marginal cost.

THE EXPENSIVE THING IS NOT SEARCHING, IT IS READING

A documentation page is 5-15K tokens and most of it is navigation. Reading a
dozen of them is 100K+ tokens of frontier context to answer a question whose
real answer is four paragraphs. So the frontier model never sees a page here:

    PLAN       one `plan` call    -> diverse queries + authoritative seed roots + a rubric
    SEED       free engines + the planner's own seeds
    EXPAND     harvest the seeds' OWN NAVIGATION, score it, take a second hop
    DISTILL    wide parallel map: every page -> <=220 words of rubric-relevant
               evidence, or the literal token IRRELEVANT
    SYNTHESIZE one `plan` call over distilled evidence only (~2-4K tokens)

WHY THE SECOND HOP EXISTS

Measured: free search engines are, in this deployment, mostly CAPTCHA'd or
suspended, and the survivors return LANDING PAGES for technical queries no
matter how the query is phrased -- `docs.example.com/en/latest/` when the answer
is at `.../features/reasoning_outputs.html`. Rephrasing does not fix it; three
differently-worded queries returned byte-identical results.

But a free engine reliably finds the right SITE, and the site's own nav names
the right PAGE. So harvest anchors from the seeds, score them against the query,
and fetch the winners. That replaced a paid search API outright, and ranked the
correct deep page first on the query that had defeated every engine.

WHY DISTILL IS THE WIDE LANE

The local GPU serves one request at a time, so N pages is N sequential decodes.
The `distill` route fans out to a hosted provider where per-call latency is
irrelevant and only count matters -- the one workload shape the local box
physically cannot do. When that lane is unavailable the fallback is NOT the same
thing slower; it is deliberately NARROWER: fewer pages, chosen more precisely by
the link scorer, distilled locally. Precision substitutes for breadth.
"""
import json, sys, os, subprocess, urllib.request, urllib.parse, concurrent.futures as cf, re, time

ROOT    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATEWAY = os.environ.get("CLANK_GATEWAY", "http://127.0.0.1:8787/v1/chat/completions")
SEARX   = os.environ.get("CLANK_SEARXNG", "http://127.0.0.1:8080/search")
UA      = {"User-Agent": "clank-research/1.0", "Content-Type": "application/json"}
FETCH_BIN = os.environ.get("CLANK_FETCH_BIN",
                           os.path.join(ROOT, "fetcher", "target", "release", "clank-fetch"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memory import memory_write   # noqa: E402  -- research reports are memory
from llm import llm as _llm      # noqa: E402

def wide_lane():
    """Is the parallel distill lane actually available right now?

    Asked at the gateway rather than assumed, because breadth and page count are
    chosen from the answer: with the wide lane, read broadly; without it, read
    few pages very well.
    """
    try:
        r = urllib.request.urlopen(GATEWAY.replace("/chat/completions", "/health"), timeout=5)
        return json.load(r).get("groq_keys", 0) > 0
    except Exception:
        return False

def llm(model, prompt, max_tokens=1200, system=None):
    # effort=low on distill: it is extraction, not reasoning, and a model that
    # emits a think block regardless spends the per-minute budget on prose
    # nobody reads.
    return _llm(model, prompt, max_tokens, system, effort=("low" if model == "distill" else None))

# A 40-query burst gets brave/duckduckgo/startpage CAPTCHA'd within seconds. The
# fix is to spread each query over a union of engines and rotate which union a
# given query uses, so no single engine ever sees the burst.
ENGINE_GROUPS = [
    "bing,duckduckgo,brave,mojeek",
    "yep,startpage,qwant,google",
    "github,arxiv,stackexchange,wikipedia",
    "bing,yep,mojeek,wikipedia",
]

def search(q, n=8, group=0):
    out, seen = [], set()
    for k in range(len(ENGINE_GROUPS)):
        eng = ENGINE_GROUPS[(group + k) % len(ENGINE_GROUPS)]
        url = SEARX + "?" + urllib.parse.urlencode({"q": q, "format": "json", "engines": eng})
        try:
            d = json.load(urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": UA["User-Agent"]}), timeout=45))
        except Exception:
            continue
        for r in d.get("results", []):
            u = r.get("url")
            if u and u not in seen:
                seen.add(u)
                out.append({"url": u, "title": r.get("title", ""), "snippet": (r.get("content") or "")[:300]})
        if len(out) >= n:
            break
        time.sleep(0.4)
    return out[:n]

def fetch_many(urls, links=False, cap=40000, concurrency=16):
    """Fetch a batch through the local Rust fetcher: no browser, no Docker, no
    service to keep alive. Returns [{url, ok, text, links}]."""
    if not urls:
        return []
    try:
        pr = subprocess.run(
            [FETCH_BIN, "--concurrency", str(concurrency), "--cap", str(cap)] + (["--links"] if links else []),
            input=json.dumps(list(urls)), capture_output=True, text=True, timeout=300)
        return json.loads(pr.stdout) if pr.stdout.strip() else []
    except Exception:
        return []

def fetch(url):
    d = fetch_many([url])
    return d[0]["text"] if d and d[0].get("ok") else ""

STOP = set("the a an of to in for and or is are be how what which with on at by from do does "
           "use using can i my your it its this that as not no you we they".split())

def terms(q):
    return [w for w in re.findall(r"[a-z0-9_./+-]{2,}", q.lower()) if w not in STOP]

def score_link(link, tset, seed_hosts):
    """Rank a harvested anchor against the query.

    Anchor text outweighs the URL slug because a nav entry reading 'Reasoning
    Outputs' identifies the target far better than a path fragment, and rare
    multi-word terms (`enable_thinking`) outweigh common ones because they are
    what discriminates.

    Two guards, both from measured failures. An anchor whose text IS its own
    href carries no editorial signal -- scoring it as prose let a car forum
    outrank the vLLM documentation. And one common term matching is noise, so a
    link must match at least two distinct query terms to survive at all.
    """
    at = link.get("text", "").lower().strip()
    u = link.get("url", "")
    if at.startswith("http") or len(at) < 3:
        at = ""
    ut = u.lower().replace("/", " ").replace("-", " ").replace("_", " ").replace(".", " ")
    sc, matched = 0.0, set()
    for t in tset:
        w = 1.0 + min(len(t), 20) / 10.0
        if t in at:
            sc += 2.0 * w; matched.add(t)
        if t in ut:
            sc += 1.0 * w; matched.add(t)
    if len(matched) < 2:
        return -1
    try:
        host = urllib.parse.urlparse(u).netloc
    except Exception:
        host = ""
    if host in seed_hosts:
        sc += 4.0                                  # staying on an authoritative site beats wandering
    if re.search(r"\.(png|jpg|jpeg|svg|css|js|zip|tar|gz|ico|woff2?)($|\?)", u):
        return -1
    if re.search(r"/(login|signup|pricing|privacy|terms|cookie|advertis|/tags?/|/category/)", u):
        return -1
    return sc

def deep_research(question, breadth=None, max_pages=None, write=True):
    t0 = time.time()
    wide = wide_lane()
    # Breadth is chosen from the lane that is actually available, not from a
    # constant: without parallel distill, reading 14 pages sequentially on one
    # GPU is slower than reading 6 well-chosen ones and is not more accurate.
    breadth   = breadth   or (6 if wide else 4)
    max_pages = max_pages or (14 if wide else 6)
    workers   = 12 if wide else 2
    trace = {"question": question, "wide_lane": wide, "breadth": breadth, "max_pages": max_pages}

    # 1. PLAN
    plan_raw = llm("plan", f"""Research question: {question}

Return ONLY a JSON object, no prose, no markdown fence:
{{"queries": ["<{breadth} diverse web search queries>"],
  "seed_urls": ["<3-6 URLs you believe actually exist and are authoritative for
    this question: official documentation sites, the GitHub repo, a spec page.
    Give the SITE or SECTION root, not a guessed deep link -- e.g.
    https://docs.vllm.ai/en/latest/ , https://github.com/ggml-org/llama.cpp .
    A root that exists is far more useful than a deep link that 404s.>"],
  "rubric": "<what a complete answer must contain, under 50 words>"}}

Put "queries" first, then "seed_urls", then "rubric".""", 1100)

    queries, rubric, plan_seeds = [], "", []
    m = re.search(r"\{.*\}", plan_raw, re.S)
    if m:
        try:
            plan = json.loads(m.group(0))
            queries = [q for q in (plan.get("queries") or []) if isinstance(q, str)][:breadth]
            rubric = plan.get("rubric") or ""
            plan_seeds = [u for u in (plan.get("seed_urls") or []) if isinstance(u, str) and u.startswith("http")][:6]
        except Exception:
            pass
    # Repair path. The plan is frequently cut off mid-rubric, which leaves no
    # closing brace and defeats a whole-object parse -- but the queries are
    # complete by then, and they are the part that matters. Salvage them.
    if not queries:
        blk = re.search(r'"queries"\s*:\s*\[(.*?)(?:\]|$)', plan_raw, re.S)
        if blk:
            queries = re.findall(r'"([^"]{8,300})"', blk.group(1))[:breadth]
    if not plan_seeds:
        plan_seeds = re.findall(r'"(https?://[^"\s]{10,200})"', plan_raw)[:6]
    if not rubric:
        rb = re.search(r'"rubric"\s*:\s*"([^"]{10,})', plan_raw, re.S)
        rubric = rb.group(1) if rb else "A direct, correct, cited answer to the question."
    if not queries:
        queries = [question]
    trace.update(queries=queries, rubric=rubric[:200], plan_seeds=plan_seeds)

    # 2. SEED
    with cf.ThreadPoolExecutor(max_workers=3) as ex:   # throttled: engines suspend on bursts
        hits = [h for lst in ex.map(lambda iq: search(iq[1], 8, group=iq[0]), list(enumerate(queries))) for h in lst]
    seen, seeds = set(), []
    # The planner's own seeds go first. A model reliably knows vLLM's docs live
    # at docs.vllm.ai; what it cannot know is which page inside holds the answer.
    # Engines are for discovering sources we could not have named.
    for u in plan_seeds + [h["url"] for h in hits]:
        if u not in seen:
            seen.add(u); seeds.append(u)
    seeds = seeds[:max_pages]
    trace["seed_urls"] = len(seeds)
    if not seeds:
        return {"answer": "No seed URLs: the planner produced none and no search engine responded.",
                "sources": [], "trace": trace}

    # 3. EXPAND -- the step that replaces a paid search API
    tset = set(terms(question)) | {t for q in queries for t in terms(q)}
    seed_docs = fetch_many(seeds, links=True, cap=40000)
    seed_hosts = {urllib.parse.urlparse(d["url"]).netloc for d in seed_docs if d.get("ok")}
    cand = {}
    for d in seed_docs:
        for l in d.get("links", []):
            u = l["url"]
            if u in seen:
                continue
            sc = score_link(l, tset, seed_hosts)
            if sc > 0 and sc > cand.get(u, (0, None))[0]:
                cand[u] = (sc, l.get("text", ""))
    ranked = sorted(cand.items(), key=lambda kv: -kv[1][0])[:max_pages]
    hop2_docs = fetch_many([u for u, _ in ranked], links=False, cap=60000)
    trace["links_harvested"] = sum(len(d.get("links", [])) for d in seed_docs)
    trace["hop2_top"] = [{"url": u, "score": round(v[0], 1), "anchor": v[1][:60]} for u, v in ranked[:5]]

    pages = [d for d in (seed_docs + hop2_docs) if d.get("ok") and len(d.get("text", "")) >= 400]
    trace["pages_fetched"] = len(pages)

    # 4. DISTILL -- every page is read, just not by the expensive model
    diag = {"irrelevant": 0, "error": 0}
    def one(d):
        try:
            ev = llm("distill", f"""RUBRIC: {rubric}
QUESTION: {question}

Below is one web page. Extract ONLY the content that bears on the rubric.
Quote exact figures, code, flags and names. Max 220 words. If the page contains
nothing relevant, reply with the single word IRRELEVANT.

--- PAGE ({d['url']}) ---
{d['text'][:60000]}""", 400)
        except Exception:
            diag["error"] += 1; return None
        if not ev.strip():
            diag["error"] += 1; return None
        if "IRRELEVANT" in ev[:40].upper():
            diag["irrelevant"] += 1; return None
        return {"url": d["url"], "evidence": ev.strip()}
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        ev = [e for e in ex.map(one, pages) if e]
    trace["pages_with_evidence"] = len(ev); trace["dropped"] = dict(diag)
    if not ev:
        return {"answer": "Pages were fetched but none contained rubric-relevant evidence.",
                "sources": [], "trace": trace}

    # 5. SYNTHESIZE -- the frontier model sees only distilled evidence
    bundle = "\n\n".join(f"[{i+1}] {e['url']}\n{e['evidence']}" for i, e in enumerate(ev))
    answer = llm("plan", f"""QUESTION: {question}
RUBRIC FOR A COMPLETE ANSWER: {rubric}

You have {len(ev)} pieces of evidence, each already extracted from a source page.
Write the answer. Cite as [n]. Be specific and concrete -- exact flags, figures,
version numbers, code. If the evidence is insufficient or contradictory, say so
explicitly and name what is missing. Do not pad.

--- EVIDENCE ---
{bundle}""", 2200)
    if not (answer or "").strip():
        # A reasoning route occasionally returns nothing usable. Retry, then fall
        # to local, which is always available.
        answer = llm("build", f"QUESTION: {question}\n\nAnswer from this evidence, cite as [n].\n\n{bundle}", 2200)

    trace["seconds"] = round(time.time() - t0, 1)
    trace["evidence_chars"] = len(bundle)
    out = {"answer": answer, "sources": [e["url"] for e in ev], "trace": trace}

    if write:
        body = (f"**Question:** {question}\n\n**Rubric:** {rubric}\n\n## Answer\n\n{answer}\n\n"
                "## Sources\n\n" + "\n".join(f"{i+1}. {e['url']}" for i, e in enumerate(ev)) +
                "\n\n## Evidence\n\n" + bundle +
                "\n\n## Provenance\n\n```json\n" + json.dumps(trace, indent=2) + "\n```\n")
        try:
            out["path"], _ = memory_write("research", question[:70], body)
        except Exception:
            pass
    return out

TOOLS = [
 {"name": "deep_research",
  "description": "Answer a question that needs current external information — APIs, specs, library behaviour, benchmarks, prior art — by searching the web, reading every candidate page with cheap parallel models, and synthesising a cited answer. Automatically saves a full markdown report with sources and provenance to .agent/research/. Use this instead of guessing from memory whenever the answer depends on how something actually works today. Takes 1-5 minutes.",
  "inputSchema": {"type": "object", "properties": {
      "question": {"type": "string"},
      "breadth": {"type": "integer", "description": "search queries; omit to auto-size to available capacity"},
      "max_pages": {"type": "integer", "description": "pages to read; omit to auto-size"}},
    "required": ["question"]}},
 {"name": "web_fetch",
  "description": "Fetch one URL as clean plain text. Use when you already know the exact page you need.",
  "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
]

def handle(req):
    m, i = req.get("method"), req.get("id")
    if m == "initialize":
        return {"jsonrpc": "2.0", "id": i, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
            "serverInfo": {"name": "clank-research", "version": "1.0.0"}}}
    if m == "tools/list":
        return {"jsonrpc": "2.0", "id": i, "result": {"tools": TOOLS}}
    if m == "tools/call":
        p = req["params"]; a = p.get("arguments") or {}
        try:
            if p["name"] == "deep_research":
                o = deep_research(a["question"], a.get("breadth"), a.get("max_pages"))
                txt = (o["answer"] + "\n\n--- sources ---\n" + "\n".join(o.get("sources", [])) +
                       (f"\n\nreport saved: {o['path']}" if o.get("path") else "") +
                       "\n\n--- trace ---\n" + json.dumps(o["trace"]))
            elif p["name"] == "web_fetch":
                txt = fetch(a["url"])[:100000] or "(empty or fetch failed)"
            else:
                txt = "unknown tool"
            return {"jsonrpc": "2.0", "id": i, "result": {"content": [{"type": "text", "text": txt}]}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": i, "result": {
                "content": [{"type": "text", "text": f"ERROR: {e}"}], "isError": True}}
    return None if i is None else {"jsonrpc": "2.0", "id": i, "error": {"code": -32601, "message": m}}

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        o = deep_research(" ".join(sys.argv[2:]))
        print(o["answer"]); print("\n--- sources ---")
        for u in o.get("sources", []):
            print(u)
        if o.get("path"):
            print("\nreport:", o["path"])
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
