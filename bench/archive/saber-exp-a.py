#!/usr/bin/env python3
"""
Experiment A — does `--spec-type ngram-mod` beat MTP on OUR agentic workload,
and does radv wedge under wide speculative batches at depth?

Reddit r/StrixHalo 1vh2hvw claims 121 t/s sustained on a 35B_A3B with ngram-mod
on HIP, and that Vulkan's FA kernel wedges on wide draft batches at depth.
This tests both against the weights we already own (unsloth Qwen3.6-35B-A3B Q8_0,
same file that produced bench/baseline-b10200-np1.md at PP 831 / TG 92.6).

METHOD NOTE — THE ONE THING THAT INVALIDATES THIS BENCH.
The post explicitly warns: do not benchmark ngram by repeating a prompt. ngram-mod
seeds its lookup from context, and with a shared hash across requests a repeated
prompt lets it draft the entire response from a previous run's output. That is the
430 t/s "artifact" row in the post's own table. So: EVERY PROMPT HERE IS UNIQUE.
The shared part is the system+tools preamble (which is what production actually
repeats); the task tail never repeats within or across arms.

Also NO ignore_eos. Forcing n_predict tokens makes the model ramble, rambling is
repetitive, and repetition is exactly what ngram exploits — it would bias every
speculative arm upward. Natural stopping, cap at n_predict, discard degenerate
short generations.
"""
import argparse, json, random, statistics, sys, time
import urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

# ---------------------------------------------------------------- corpus ----
# Agentic shape: a big repetitive preamble (tool schemas + prior turns) followed
# by a unique task. This is the shape ngram-mod is supposed to win on, and it is
# the shape Hermes/pi actually send.

TOOL_SCHEMA = """You are a coding agent operating on a Linux host. You have these tools:

{"name":"read_file","description":"Read a file from disk","parameters":{"type":"object","properties":{"path":{"type":"string"},"offset":{"type":"integer"},"limit":{"type":"integer"}},"required":["path"]}}
{"name":"write_file","description":"Write a file to disk","parameters":{"type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"}},"required":["path","content"]}}
{"name":"edit_file","description":"Exact string replacement in a file","parameters":{"type":"object","properties":{"path":{"type":"string"},"old":{"type":"string"},"new":{"type":"string"},"replace_all":{"type":"boolean"}},"required":["path","old","new"]}}
{"name":"bash","description":"Run a shell command","parameters":{"type":"object","properties":{"command":{"type":"string"},"timeout":{"type":"integer"}},"required":["command"]}}
{"name":"grep","description":"Search file contents with a regex","parameters":{"type":"object","properties":{"pattern":{"type":"string"},"path":{"type":"string"},"glob":{"type":"string"}},"required":["pattern"]}}
{"name":"list_dir","description":"List a directory","parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}

Call tools by emitting a JSON object. Wait for the result before the next call.
Prefer minimal diffs. Never fabricate file contents you have not read.
"""

# Realistic tool-call/result turns. These repeat structurally (that is the point)
# but carry different payloads per sample.
_FILES = [
    ("/home/svc/app/watchdog.py", "python"), ("/home/svc/app/router.py", "python"),
    ("/home/svc/app/metrics.py", "python"), ("/home/svc/config/models.ini", "ini"),
    ("/home/svc/app/queue.py", "python"), ("/home/svc/app/health.py", "python"),
    ("/home/svc/app/session.py", "python"), ("/home/svc/app/cache.py", "python"),
    ("/etc/systemd/system/svc.service", "ini"), ("/home/svc/app/retry.py", "python"),
]

_CODE_BODY = '''
def {fn}(self, {arg}, timeout={to}):
    """{doc}"""
    deadline = time.monotonic() + timeout
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            resp = self._session.post(self._url, json={{"{arg}": {arg}}}, timeout={to})
            if resp.status_code == 200:
                self._metrics.observe("{fn}_ok", attempt)
                return resp.json()
            if resp.status_code in (429, 503):
                time.sleep(min(2 ** attempt, {to}))
                continue
            raise TransientError(f"{fn} got {{resp.status_code}}")
        except (ConnectionError, TimeoutError) as exc:
            self._log.warning("{fn} attempt %d failed: %s", attempt, exc)
            time.sleep(min(2 ** attempt, {to}))
    raise Deadline("{fn} exhausted after %d attempts" % attempt)
'''

_FNS = ["probe_health", "reload_model", "drain_slots", "flush_cache", "rotate_token",
        "collect_metrics", "reap_children", "verify_digest", "resolve_alias", "seal_span"]
_ARGS = ["model_id", "slot_id", "span", "token", "digest", "alias", "path", "pid"]
_DOCS = ["Probe the child endpoint and return its parsed status.",
         "Ask the router to reload the named model, waiting for readiness.",
         "Drain in-flight work from a slot before eviction.",
         "Flush the prompt cache for one model without touching others.",
         "Rotate the service token and re-seal dependent spans."]

_TASKS = [
    "The retry loop above double-counts the first attempt when the deadline has already passed. Show the exact line and the minimal patch.",
    "Under what interleaving can two callers both observe status 200 and then both write the same span? Give the sequence.",
    "Rewrite the backoff so it is jittered and bounded, without changing the function signature.",
    "The metrics counter is incremented only on success. Explain what that hides in a crash-loop and fix it.",
    "Identify every place a TimeoutError would be silently swallowed, and rank them by production impact.",
    "This function can loop forever if the clock jumps backward. Prove it, then patch it.",
    "Convert the synchronous retry into an async one preserving the same deadline semantics.",
    "The exception message uses %% formatting inside an f-string context. Find it and correct it.",
    "Explain why attempt is used for the metric value and whether that is the right observation.",
    "Add a circuit breaker that opens after N consecutive Deadline raises. Keep it under 20 lines.",
    "Which of these calls is not idempotent, and what breaks when the supervisor retries it?",
    "Write the unit test that would have caught the off-by-one in the attempt counter.",
    "The sleep happens before the deadline re-check. Quantify the worst-case overshoot.",
    "Refactor to remove the duplicated sleep expression without changing behavior.",
    "Explain how this interacts with a supervisor that sends SIGTERM during the sleep.",
    "There is a race between _session reuse and token rotation. Describe it concretely.",
    "Give the smallest change that makes the failure observable in structured logs.",
    "What happens if timeout is 0? Trace the control flow and state the result.",
    "Propose an alternative that uses a monotonic budget object instead of a deadline float.",
    "The 429 branch does not honor Retry-After. Add support without breaking the loop bound.",
    "Which line is responsible for the unbounded memory growth under sustained 503s?",
    "Explain the difference in observed behavior between ConnectionError and TransientError here.",
    "Rewrite the docstring so it states the failure modes and the exceptions raised.",
    "Add cancellation support so a caller can abort mid-retry. Show only the diff.",
]


def _turn(rng, i):
    """One synthetic tool-call round-trip: repetitive frame, varying payload."""
    path, kind = rng.choice(_FILES)
    fn = rng.choice(_FNS)
    body = _CODE_BODY.format(fn=fn, arg=rng.choice(_ARGS), to=rng.choice([5, 10, 15, 30, 60]),
                             doc=rng.choice(_DOCS))
    return (
        f'\n\nASSISTANT: {{"tool":"read_file","arguments":{{"path":"{path}","offset":{i*40},"limit":40}}}}\n'
        f'TOOL RESULT (read_file {path}):\n```{kind}\n{body}```\n'
        f'ASSISTANT: {{"tool":"grep","arguments":{{"pattern":"{fn}","path":"/home/svc/app"}}}}\n'
        f'TOOL RESULT (grep): {path}:{12 + i}:    {fn}(...)  # {rng.randint(1000,9999)} hits scanned\n'
    )


def build_prompt(seed, target_tokens):
    """Unique prompt of roughly target_tokens, agentic shape. ~3.6 chars/token."""
    rng = random.Random(seed)
    parts = [TOOL_SCHEMA]
    budget = target_tokens * 36 // 10
    i = 0
    while sum(len(p) for p in parts) < budget:
        parts.append(_turn(rng, i))
        i += 1
    task = rng.choice(_TASKS)
    # unique tail — guarantees no cross-request response reuse
    parts.append(f"\n\nUSER (request {seed}, ref {rng.randint(10**6, 10**7)}): {task}\n\nASSISTANT:")
    return "".join(parts)


# ---------------------------------------------------------------- driver ----

def call(url, prompt, n_predict, timeout):
    body = json.dumps({
        "prompt": prompt,
        "n_predict": n_predict,
        "temperature": 0.2,
        "top_p": 0.95,
        "cache_prompt": True,   # production contract; post says the same
    }).encode()
    req = urllib.request.Request(url + "/completion", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "wall": time.time() - t0}
    t = data.get("timings", {}) or {}
    out = data.get("content", "") or ""
    return {
        "wall": time.time() - t0,
        # prompt_ms is server-side prefill = TTFT for an interactive turn. This is
        # the metric that decides "fast enough for daytime chat", more than TG.
        "ttft_ms": t.get("prompt_ms"),
        "prompt_n": t.get("prompt_n"),
        "predicted_n": t.get("predicted_n"),
        "pp": t.get("prompt_per_second"),
        "tg": t.get("predicted_per_second"),
        "draft_n": t.get("draft_n"),
        "draft_acc": t.get("draft_n_accepted"),
        "content": out,
        "stop_type": data.get("stop_type"),
    }


def gibberish_score(text):
    """Cheap degeneracy detector for the depth/wedge arm.

    Two failure modes the thread reports: doom-loops (same line repeated) and
    JSON gibberish. Returns (repeat_ratio, nonascii_ratio)."""
    if not text.strip():
        return 1.0, 0.0
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    rep = 0.0
    if lines:
        rep = 1.0 - (len(set(lines)) / len(lines))
    na = sum(1 for c in text if ord(c) > 127) / max(1, len(text))
    return rep, na


def med(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return statistics.median(xs) if xs else float("nan")


def run_throughput(url, tag, n, depth, n_predict, concurrency, timeout, seed0):
    prompts = [build_prompt(seed0 + i, depth) for i in range(n)]
    assert len(set(prompts)) == len(prompts), "prompts must all be unique"
    t0 = time.time()
    if concurrency == 1:
        res = [call(url, p, n_predict, timeout) for p in prompts]
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            res = list(ex.map(lambda p: call(url, p, n_predict, timeout), prompts))
    wall = time.time() - t0

    errs = [r for r in res if "error" in r]
    ok = [r for r in res if "error" not in r]
    good = [r for r in ok if (r.get("predicted_n") or 0) >= 16]

    total_pred = sum(r.get("predicted_n") or 0 for r in ok)
    acc = None
    dn = sum(r.get("draft_n") or 0 for r in ok)
    da = sum(r.get("draft_acc") or 0 for r in ok)
    if dn:
        acc = da / dn

    degen = [gibberish_score(r["content"]) for r in ok]
    worst_rep = max((d[0] for d in degen), default=0.0)

    return {
        "tag": tag, "mode": "throughput", "n": n, "depth": depth,
        "concurrency": concurrency, "n_predict": n_predict,
        "errors": len(errs), "error_samples": [e["error"] for e in errs[:3]],
        "discarded_short": len(ok) - len(good),
        "prompt_n_med": med([r["prompt_n"] for r in good]),
        "ttft_ms_med": med([r["ttft_ms"] for r in good]),
        "pp_med": med([r["pp"] for r in good]),
        "tg_med": med([r["tg"] for r in good]),
        "tg_min": min([r["tg"] for r in good], default=float("nan")),
        "tg_max": max([r["tg"] for r in good], default=float("nan")),
        "wall_s": wall,
        "sustained_tok_s": total_pred / wall if wall else float("nan"),
        "total_predicted": total_pred,
        "draft_acceptance": acc,
        "worst_repeat_ratio": worst_rep,
    }


def run_depth(url, tag, depths, n_predict, timeout, seed0):
    rows = []
    for j, d in enumerate(depths):
        p = build_prompt(seed0 + 9000 + j, d)
        r = call(url, p, n_predict, timeout)
        if "error" in r:
            rows.append({"depth": d, "error": r["error"], "wall": r["wall"]})
            print(f"  depth {d:>7}: ERROR {r['error']}", flush=True)
            continue
        rep, na = gibberish_score(r["content"])
        acc = (r["draft_acc"] / r["draft_n"]) if r.get("draft_n") else None
        rows.append({
            "depth": d, "prompt_n": r["prompt_n"], "predicted_n": r["predicted_n"],
            "ttft_ms": r["ttft_ms"],
            "pp": r["pp"], "tg": r["tg"], "wall": r["wall"],
            "repeat_ratio": rep, "nonascii_ratio": na,
            "draft_acceptance": acc, "stop_type": r["stop_type"],
        })
        print(f"  depth {d:>7}: prompt_n={r['prompt_n']} pp={r['pp']:.1f} tg={r['tg']:.2f} "
              f"pred={r['predicted_n']} rep={rep:.2f} acc={acc if acc is None else round(acc,3)}",
              flush=True)
    return {"tag": tag, "mode": "depth", "rows": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:10098")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--mode", choices=["throughput", "depth"], default="throughput")
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--depth", type=int, default=6000)
    ap.add_argument("--depths", default="3000,13000,40000,100000")
    ap.add_argument("--n-predict", type=int, default=256)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    print(f"== {a.tag} [{a.mode}] ==", flush=True)
    if a.mode == "throughput":
        r = run_throughput(a.url, a.tag, a.n, a.depth, a.n_predict,
                           a.concurrency, a.timeout, a.seed)
        print(json.dumps(r, indent=2))
    else:
        depths = [int(x) for x in a.depths.split(",")]
        r = run_depth(a.url, a.tag, depths, a.n_predict, a.timeout, a.seed)
        print(json.dumps(r, indent=2))

    if a.out:
        with open(a.out, "w") as f:
            json.dump(r, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
