//! Concurrent fetch + text extraction for the research loop.
//!
//! WHY THIS IS RUST AND WHY IT IS ONLY THIS
//!
//! LOOP-STANDARD §15 records the rule: profile before rewriting, because the
//! bottleneck is rarely the language. That rule killed a Rust rewrite of
//! `metamorphic.py`, which evaluates 44 relations in 0.2s and had nothing to
//! gain.
//!
//! This is the case the rule permits. With 18 pooled Groq keys the research
//! constraint moved off inference (3.6M tokens/day) and onto FETCH: several
//! hundred URLs per pass, each a network round trip of 200-2000ms, plus HTML
//! parsing. That is I/O-bound fan-out with a CPU-bound extraction step — the one
//! shape where a compiled binary with real threads beats the alternative
//! decisively, and where Python's per-connection overhead actually shows up.
//!
//! It is deliberately NOT a crawler. No link following, no frontier, no
//! politeness state machine, no JS execution. It takes a list of URLs, fetches
//! them, strips them to text, and prints JSON. Everything that decides WHICH
//! urls — that is the triage step, and it belongs to the model that can judge.
//!
//! `--links` extends that contract without breaking it. It harvests each page's
//! outbound anchors (absolutised, deduped, same-scheme only) and reports them.
//! It still follows nothing. The reason it exists: free search engines return
//! `docs.example.com/en/latest/` when the answer is at
//! `docs.example.com/en/latest/features/reasoning_outputs.html`. The landing
//! page names the right link in its own nav; harvesting anchors lets the caller
//! score them and ask for a second, targeted pass. Retrieval precision without
//! a paid search API — and the choice of what to follow stays with the caller.
//!
//! A crawler would also be the wrong risk: unbounded traversal of the open web
//! from an unattended loop is how you get IP-banned from the institutional
//! sources this research actually depends on.
//!
//! Usage:
//!     echo '["https://a", "https://b"]' | oforge-fetch
//!     oforge-fetch --concurrency 24 < urls.json > docs.json

use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::io::Read;
use std::time::{Duration, Instant};
use url::Url;

#[derive(Serialize, Deserialize, Debug)]
struct Doc {
    url: String,
    ok: bool,
    status: u16,
    /// Extracted plain text, capped. The consumer is a model with a token
    /// budget, so shipping a megabyte of markup would be actively harmful.
    text: String,
    chars: usize,
    ms: u128,
    error: Option<String>,
    /// Outbound anchors, populated only under `--links`. Empty otherwise so the
    /// default output stays small.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    links: Vec<Link>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
struct Link {
    url: String,
    /// Anchor text. This is the signal that makes scoring work — a nav entry
    /// reading "Reasoning Outputs" is what identifies the right page.
    text: String,
}

/// Harvest `<a href=...>` pairs and resolve them against the page URL.
///
/// Hand-rolled for the same reason `to_text` is: the consumer tolerates an
/// imperfect anchor and a full DOM parse would add a dependency for nothing.
/// Bytewise throughout, so non-ASCII anchors survive.
fn to_links(html: &str, base: &str) -> Vec<Link> {
    let base_url = match Url::parse(base) { Ok(u) => u, Err(_) => return Vec::new() };
    let lower = html.to_ascii_lowercase();
    let low = lower.as_bytes();
    let bytes = html.as_bytes();
    let mut out: Vec<Link> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    let mut i = 0usize;

    while let Some(rel) = find(&low[i..], b"<a ") {
        let tag_start = i + rel;
        let tag_end = match find(&low[tag_start..], b">") { Some(e) => tag_start + e, None => break };
        let tag = &lower[tag_start..tag_end];

        // href="..." | href='...' | href=bare
        let href = tag.find("href").and_then(|h| {
            let rest = &tag[h + 4..];
            let eq = rest.find('=')?;
            let v = rest[eq + 1..].trim_start();
            let (q, body) = match v.as_bytes().first() {
                Some(b'"') => ('"', &v[1..]),
                Some(b'\'') => ('\'', &v[1..]),
                _ => (' ', v),
            };
            let end = if q == ' ' { body.find(|c: char| c.is_whitespace()).unwrap_or(body.len()) }
                      else { body.find(q)? };
            // Recover original case from the same offsets in the unlowered source.
            // `body` is a subslice of `lower`, so this difference is already the
            // absolute offset — adding `tag_start` again would double-count it.
            let off = body.as_ptr() as usize - lower.as_ptr() as usize;
            std::str::from_utf8(&bytes[off..off + end]).ok().map(|s| s.to_string())
        });

        // Anchor text: everything up to the closing </a>.
        let text = match find(&low[tag_end..], b"</a>") {
            Some(e) => truncate_chars(&to_text(&html[tag_end + 1..tag_end + e]), 120),
            None => String::new(),
        };

        if let Some(h) = href {
            let h = h.trim();
            if !h.is_empty() && !h.starts_with('#') && !h.starts_with("javascript:")
               && !h.starts_with("mailto:") {
                if let Ok(abs) = base_url.join(h) {
                    if abs.scheme() == "http" || abs.scheme() == "https" {
                        let mut a = abs.clone();
                        a.set_fragment(None);
                        let key = a.to_string();
                        if seen.insert(key.clone()) {
                            out.push(Link { url: key, text });
                        }
                    }
                }
            }
        }
        i = tag_end + 1;
        if out.len() >= 400 { break; }   // a nav-heavy page is not worth unbounded harvest
    }
    out
}

/// Strip HTML to readable text.
///
/// Deliberately a small hand-rolled pass rather than a full DOM parse: the
/// consumer is a language model, which tolerates imperfect whitespace and does
/// not tolerate script bodies or 400KB of inline CSS. Removing the two tags
/// that carry non-prose content gets almost all of the benefit of a readability
/// algorithm at a fraction of the complexity and with no extra dependency.
fn to_text(html: &str) -> String {
    // Operate on BYTES throughout, then validate once at the end.
    //
    // The first version sliced the string by byte offset (`lower[i..]`) and
    // pushed `bytes[i] as char`. Both are wrong on any non-ASCII page and both
    // panic or mangle: real sources returned Arabic and a curly apostrophe
    // within seconds of the first run. Byte-slicing a `str` requires a char
    // boundary, and casting a UTF-8 continuation byte to `char` reinterprets it
    // as Latin-1.
    let bytes = html.as_bytes();
    let lower = html.to_ascii_lowercase();
    let low = lower.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(bytes.len() / 4);
    let mut i = 0usize;
    let mut in_tag = false;

    while i < bytes.len() {
        // Skip whole <script>/<style> bodies, not merely their tags.
        let mut skipped = true;
        while skipped && i < bytes.len() {
            skipped = false;
            for (open, close) in [(&b"<script"[..], &b"</script>"[..]),
                                  (&b"<style"[..], &b"</style>"[..])] {
                if low[i..].starts_with(open) {
                    match find(&low[i..], close) {
                        Some(end) => i += end + close.len(),
                        None => i = bytes.len(),
                    }
                    skipped = true;
                    break;
                }
            }
        }
        if i >= bytes.len() {
            break;
        }
        match bytes[i] {
            b'<' => in_tag = true,
            b'>' => {
                in_tag = false;
                out.push(b' ');
            }
            c if !in_tag => out.push(c),
            _ => {}
        }
        i += 1;
    }

    // Multi-byte sequences survive intact because they were copied bytewise;
    // `from_utf8_lossy` only replaces genuinely invalid input.
    let decoded = String::from_utf8_lossy(&out);

    let mut text = String::with_capacity(decoded.len());
    let mut last_space = true;
    for ch in decoded.chars() {
        if ch.is_whitespace() {
            if !last_space {
                text.push(' ');
                last_space = true;
            }
        } else {
            text.push(ch);
            last_space = false;
        }
    }
    text.trim().to_string()
}

/// First index of `needle` in `haystack`, bytewise.
fn find(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    if needle.is_empty() || haystack.len() < needle.len() {
        return None;
    }
    haystack.windows(needle.len()).position(|w| w == needle)
}

/// Truncate to at most `cap` CHARACTERS, never splitting one.
///
/// `String::truncate` takes a byte index and panics off a char boundary — the
/// same class of bug as the slicing above, and it would have fired on the first
/// non-ASCII page long enough to reach the cap.
fn truncate_chars(text: &str, cap: usize) -> String {
    match text.char_indices().nth(cap) {
        Some((idx, _)) => text[..idx].to_string(),
        None => text.to_string(),
    }
}

fn fetch(url: &str, cap: usize, want_links: bool) -> Doc {
    let started = Instant::now();
    let agent = ureq::AgentBuilder::new()
        .timeout_connect(Duration::from_secs(10))
        .timeout_read(Duration::from_secs(25))
        .redirects(5)
        .build();

    // A real UA and a plain Accept: institutional sites frequently 403 a
    // default client, and those are the sources this research needs most.
    let response = agent
        .get(url)
        .set("User-Agent",
             "Mozilla/5.0 (compatible; oforge-research/0.1; +https://github.com/khulyl/O-Forge)")
        .set("Accept", "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8")
        .call();

    match response {
        Ok(res) => {
            let status = res.status();
            let mut body = String::new();
            // Bound the read: an unbounded take() on a hostile or enormous
            // response is how an unattended fetcher eats its own memory.
            let read = res
                .into_reader()
                .take(8 * 1024 * 1024)
                .read_to_string(&mut body);
            match read {
                Ok(_) => {
                    let links = if want_links { to_links(&body, url) } else { Vec::new() };
                    let text = truncate_chars(&to_text(&body), cap);
                    let chars = text.chars().count();
                    Doc { url: url.into(), ok: true, status, text, chars,
                          ms: started.elapsed().as_millis(), error: None, links }
                }
                Err(e) => Doc { url: url.into(), ok: false, status, text: String::new(),
                                chars: 0, ms: started.elapsed().as_millis(),
                                error: Some(format!("read: {e}")), links: Vec::new() },
            }
        }
        Err(ureq::Error::Status(code, _)) => Doc {
            url: url.into(), ok: false, status: code, text: String::new(), chars: 0,
            ms: started.elapsed().as_millis(), error: Some(format!("http {code}")), links: Vec::new(),
        },
        Err(e) => Doc {
            url: url.into(), ok: false, status: 0, text: String::new(), chars: 0,
            ms: started.elapsed().as_millis(), error: Some(e.to_string()), links: Vec::new(),
        },
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let flag = |name: &str, default: usize| -> usize {
        args.iter()
            .position(|a| a == name)
            .and_then(|i| args.get(i + 1))
            .and_then(|v| v.parse().ok())
            .unwrap_or(default)
    };
    let concurrency = flag("--concurrency", 16);
    let cap = flag("--cap", 40_000);
    let want_links = args.iter().any(|a| a == "--links");

    let mut input = String::new();
    if std::io::stdin().read_to_string(&mut input).is_err() || input.trim().is_empty() {
        eprintln!("usage: echo '[\"https://...\"]' | lca-fetch [--concurrency N] [--cap CHARS] [--links]");
        std::process::exit(2);
    }
    let urls: Vec<String> = match serde_json::from_str(&input) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("stdin must be a JSON array of URL strings: {e}");
            std::process::exit(2);
        }
    };

    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(concurrency)
        .build()
        .expect("thread pool");

    let started = Instant::now();
    let docs: Vec<Doc> = pool.install(|| urls.par_iter().map(|u| fetch(u, cap, want_links)).collect());

    let ok = docs.iter().filter(|d| d.ok).count();
    eprintln!(
        "fetched {}/{} in {:.1}s ({} threads, {} chars)",
        ok, docs.len(), started.elapsed().as_secs_f64(), concurrency,
        docs.iter().map(|d| d.chars).sum::<usize>()
    );
    println!("{}", serde_json::to_string(&docs).expect("serialize"));
}
