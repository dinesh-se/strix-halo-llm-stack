#!/usr/bin/env python3
"""llama-watchdog — detect, recover from, and measure GPU device-lost hangs.

WHY THIS EXISTS
---------------
gfx1151 (Strix Halo) has a known, currently unfixed amdgpu bug: under concurrent
decode the ring times out, Vulkan returns VK_ERROR_DEVICE_LOST, and llama-server
never rebuilds the lost device. It keeps running and keeps 500ing forever.

The orchestrator only notices a model dying if the *process exits*, which it does
not, so nothing restarts it. On 2026-08-01 both models sat wedged for ~20 minutes
returning errors while every liveness check reported healthy. Recovery beats
prevention here, because the bug is upstream and unfixed.

PORTED 2026-08-06 from llama-swap to llama.cpp's own router server
(`llama-router.service`, :9292). Endpoint mapping:
    GET  /running                     -> GET  /models   (status.value=="loaded",
                                                         child port from status.args)
    POST /api/models/unload/<model>   -> POST /models/unload  {"model": ...}
    re-warm via /v1/chat/completions  -> POST /models/load    {"model": ...}
Metric names and the model= label schema are UNCHANGED, so scrape.yml and
alert-rules.yaml needed no edits.

THREE JOBS, ONE LOOP
--------------------
1. PROBE    a real completion against every loaded model (see WEDGE DETECTION)
2. RECOVER  unload + re-warm on device-lost, and alert to Telegram
3. RELAY    re-export each model's llamacpp:* metrics on :9611 with a model=
            label, which is the only way VictoriaMetrics can see them at all
            (see NETWORK below)

WEDGE DETECTION: /health AND /v1/models BOTH PASS ON A WEDGED SERVER.
That was proven during the incident — the HTTP layer is fine, it is the Vulkan
device that is gone. The only probe that distinguishes them is one that actually
decodes a token. Hence a real n_predict=1 completion, not a liveness endpoint.

NEVER PROBE A MODEL BY NAME: probe ONLY models already reported `loaded`.
⚠️ The REASON changed on 2026-08-06 and is now STRONGER, so do not "simplify"
this away. Under llama-swap the danger was that a named request reset the proxy
idle timer and defeated `ttl` (the 2026-05-23 bug, where a 15s Prometheus scrape
made `ttl` inert). Under the router the danger is worse: a request naming an
unloaded model triggers an AUTO-LOAD, and for DS4 that is 3-11 minutes and
~98 GiB — which would also fight the image-gen eviction wrapper, whose entire
job is keeping DS4 unloaded while sd-cli runs.

NETWORK: the router runs with --network host, so its child llama-servers listen
on EPHEMERAL ports on the host (44449, 52779, ... — re-read them every cycle,
never cache). That is exactly why --network host is mandatory: under bridge
networking those ports are unpublished and /slots would be unreachable, and
/slots is the only thing distinguishing "wedged" from "mid-prefill". DS4's
100k-token prefill takes 751s; without /slots this watchdog would unload it
every ~4.5 minutes. VictoriaMetrics (ai-stack, 172.23.x) still cannot reach the
children, which is why this runs as a systemd --user service on the host and
relays their metrics rather than letting VM scrape them.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

# 2026-08-06: llama-swap was replaced by llama.cpp's own router server
# (`llama-router.service`). LLAMA_SWAP_URL is still honoured as a fallback so
# that restoring an old watchdog.env is harmless.
ROUTER = os.environ.get("LLAMA_ROUTER_URL",
                        os.environ.get("LLAMA_SWAP_URL", "http://127.0.0.1:9292"))
# Host-side address of the child llama-server processes. The router runs with
# --network host, so children listen on ephemeral ports in the HOST namespace
# and 127.0.0.1 is correct. (Under llama-swap this required a
# `docker inspect llama-swap` to find a bridge IP; that is gone.)
UPSTREAM_HOST = os.environ.get("LLAMA_UPSTREAM_HOST", "")
ADDR = ("0.0.0.0", int(os.environ.get("WATCHDOG_PORT", "9611")))

PROBE_INTERVAL = float(os.environ.get("PROBE_INTERVAL", "60"))
SCRAPE_INTERVAL = float(os.environ.get("SCRAPE_INTERVAL", "15"))
PROBE_TIMEOUT = float(os.environ.get("PROBE_TIMEOUT", "90"))
# Consecutive failed probes before we call it wedged. >1 so that a probe merely
# queued behind a long prefill (the 35b runs --parallel 1; a 68k-token prefill
# takes ~145s) is never mistaken for a dead device.
FAIL_THRESHOLD = int(os.environ.get("FAIL_THRESHOLD", "3"))
# Never recover more than once per this window — a recovery loop against a
# genuinely dead GPU would be worse than sitting still and alerting.
# ⚠️ 1800, raised from 900 on 2026-08-06. DS4 cold-loads in 3–11 minutes, so a
# 900s cooldown could fire a second "recovery" while the first reload was still
# in progress, cycling a healthy model forever.
RECOVERY_COOLDOWN = float(os.environ.get("RECOVERY_COOLDOWN", "1800"))
# 🔴 Do not probe a model for this long after it first appears `loaded`.
# Guards against the watchdog "recovering" a model that was merely still coming
# up — which never converges, because each recovery restarts the same load.
# 300s, not the 900 first written: MEASURED 2026-08-06 that the router only
# flips a model to `loaded` once its child actually answers (a completion
# succeeded immediately after the flip, and the separate `loading` state covers
# the 3-11 min DS4 window). So a long grace would blind the watchdog for no
# benefit. This is margin for the gap between "child up" and "first token", not
# for the load itself. Note a probe queued behind a long prefill is already
# handled separately and better, by slot_busy_advancing().
PROBE_GRACE_SECONDS = float(os.environ.get("PROBE_GRACE_SECONDS", "300"))
# 🔴 30, not the 10 this was hardcoded to until 2026-08-07. /slots is served off
# the child's main loop, which during a long prefill is busy for one whole
# ubatch at a time — MEASURED 17s per 2048-token chunk at 70k context on DS4. A
# 10s timeout therefore failed roughly half the time *because the model was
# working*, and each failure was scored as evidence it was wedged. That is what
# unloaded a healthy DS4 at 14:14:03 on 2026-08-07, 468s into a 75.7k-token
# prefill that was 92% done.
SLOTS_TIMEOUT = float(os.environ.get("SLOTS_TIMEOUT", "30"))
# Consecutive cycles where /slots was UNREADABLE (not "not advancing") that we
# will excuse before falling through to the normal failure path. Bounds the risk
# of the inference in slot_busy_advancing()'s docstring being wrong.
MAX_INCONCLUSIVE = int(os.environ.get("MAX_INCONCLUSIVE", "5"))

# ---------------------------------------------------------------------------
# HEAVY-MODEL MUTEX
# ---------------------------------------------------------------------------
# The router has NO memory awareness and --models-max caps model COUNT, not
# size. On 2026-08-07 a Hermes /model switch made the next chat request trigger
# `ensure_model` for DS4 (~98 GiB) while qwen3.6-35b (~34 GiB) was resident; 90
# seconds later the kernel OOM-killed the incumbent. Nothing in the router prevents
# this, and it is not limited to manual swaps —
# HINDSIGHT_API_REFLECT_LLM_MODEL=deepseek-v4-flash means a background reflect
# can trigger the same autoload with nobody at the keyboard.
#
# We deliberately do NOT fix this with --no-models-autoload: that would break
# Hermes' /model command (the first request after a switch would 4xx instead of
# loading). Instead we let the autoload happen and evict the incumbent out from
# under it. The race margin is large — the OOM landed 90s into a 3-11 minute
# load, and this poll evicts within ~5s.
HEAVY_MODELS = [s.strip() for s in os.environ.get(
    "HEAVY_MODELS", "qwen3.6-35b,deepseek-v4-flash").split(",") if s.strip()]
HEAVY_MUTEX = os.environ.get("HEAVY_MUTEX", "1") == "1"
HEAVY_MUTEX_INTERVAL = float(os.environ.get("HEAVY_MUTEX_INTERVAL", "3"))
# Per-model floor between evictions, so a model that keeps being re-requested
# cannot drive an unload/autoload flap loop.
HEAVY_EVICT_COOLDOWN = float(os.environ.get("HEAVY_EVICT_COOLDOWN", "60"))

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
# Forum-topic id inside TELEGRAM_CHAT. Empty = post to the group's General
# thread, i.e. exactly the pre-2026-08-25 behaviour, so leaving it unset cannot
# break an existing deployment. Telegram calls this `message_thread_id`; it is
# the number in a topic message link (t.me/c/<internal>/<TOPIC>/<msg>).
TELEGRAM_TOPIC_ID = os.environ.get("TELEGRAM_TOPIC_ID", "").strip()

# ---------------------------------------------------------------------------
# HOST HEALTH (added 2026-08-25) — memory pressure + systemd unit liveness.
#
# Why this lives here and not in Prometheus/Grafana: Grafana was retired
# 2026-08-13 and VictoriaMetrics is next, so alert RULES needed a new home.
# This watchdog already owns a Telegram sender, Restart=always and four
# independent loops, which makes it the natural place. It reads /proc directly,
# so it needs no exporter and keeps working after VM and node-exporter go.
#
# 🔴 Thresholds are grounded in MEASURED behaviour of this box (2026-08-25), not
# guessed: healthy sat at 5-7 GiB MemAvailable, and the state the user reported
# as "Hermes isn't replying" was 3.6-3.8 GiB. 3.0 GiB therefore sits below normal
# operation but above the observed-bad band.
HEALTH_INTERVAL = float(os.environ.get("HEALTH_INTERVAL", "60"))
HEALTH_FAIL_THRESHOLD = int(os.environ.get("HEALTH_FAIL_THRESHOLD", "3"))
MEM_AVAIL_MIN_GIB = float(os.environ.get("MEM_AVAIL_MIN_GIB", "3.0"))
SWAP_USED_MAX_FRAC = float(os.environ.get("SWAP_USED_MAX_FRAC", "0.80"))
# Direct-reclaim inefficiency: pgscan_direct/pgsteal_direct as a RATE over the
# interval, never the cumulative totals (those only ever show a lifetime
# average and would never recover once tripped). On 2026-08-25 the thrashing
# box measured 6.0x; healthy reclaim is close to 1.0.
RECLAIM_RATIO_MAX = float(os.environ.get("RECLAIM_RATIO_MAX", "3.0"))
# Ignore the ratio unless real scanning happened this interval, otherwise a
# handful of pages produces a meaningless ratio on an idle box.
RECLAIM_MIN_SCAN = int(os.environ.get("RECLAIM_MIN_SCAN", "10000"))

# systemd units whose death should page. NOTE hindsight-daemon.service is
# deliberately ABSENT: hindsight_loop() already probes its /health and requires
# database=connected, which also catches a running-but-wedged daemon that
# `is-active` would happily call "active". Adding it here would only
# double-alert on the strictly weaker signal.
HEALTH_UNITS = [u.strip() for u in os.environ.get(
    "HEALTH_UNITS",
    "llama-router.service,hermes-gateway.service,"
    "firecrawl-proxy.socket,hermes-dashboard-proxy.socket",
).split(",") if u.strip()]

# Hindsight memory daemon (hindsight-daemon.service, :9177). Probe-only — no
# recovery action here, systemd (Restart=always) owns restarts. This exists
# because Hindsight has twice gone unnoticed for hours: down 22h on
# 2026-07-31 (venv rebuild dropped hindsight-all), then looping ~130 restarts
# on 2026-08-01 evening (a since-fixed dual-supervisor bug where the Hermes
# plugin's local_embedded mode SIGTERMed this unit's daemon on every session
# init). Neither incident tripped any existing alert.
#
# Opt-in, not opt-out: empty means "not deployed here, don't probe or alert
# on it." A hardcoded default would force every adopter of this watchdog to
# either run Hindsight or eat spurious down-alerts for a service they never
# installed. Set HINDSIGHT_URL explicitly (e.g. http://127.0.0.1:9177) to
# enable — see watchdog.env.example.
HINDSIGHT_URL = os.environ.get("HINDSIGHT_URL", "")
HINDSIGHT_PROBE_INTERVAL = float(os.environ.get("HINDSIGHT_PROBE_INTERVAL", "60"))
# Consecutive failed probes before alerting — same rationale as FAIL_THRESHOLD,
# so a single slow health check doesn't page.
HINDSIGHT_FAIL_THRESHOLD = int(os.environ.get("HINDSIGHT_FAIL_THRESHOLD", "3"))

# Signatures of a lost Vulkan device, as they appear in llama-server's error
# body. Matched case-insensitively against the response text.
DEVICE_LOST_PATTERNS = re.compile(
    r"ErrorDeviceLost|VK_ERROR_DEVICE_LOST|device lost|failed to decode|decode\(\) failed",
    re.IGNORECASE,
)

_lock = threading.Lock()
_state = {
    "metrics": {},        # model -> raw prometheus text from that llama-server
    "probe_ok": {},       # model -> 1/0
    "probe_latency": {},  # model -> seconds
    "consec_fail": {},    # model -> int
    "recoveries": 0,      # counter
    "device_lost": 0,     # counter
    "probe_failures": 0,  # counter
    "probe_busy": 0,      # counter — probes that timed out behind real work
    "probe_inconclusive": 0,  # counter — cycles where /slots was UNREADABLE, so
                          # we could neither confirm progress nor a wedge
    "consec_inconclusive": {},  # model -> int, capped by MAX_INCONCLUSIVE
    "heavy_evictions": 0,  # counter — heavy-model mutex unloads performed
    "heavy_coresident": 0,  # gauge — 1 while two heavy models overlap
    "heavy_since": {},    # model -> timestamp first seen loaded (mutex tiebreak)
    "last_evict": {},     # model -> timestamp of last mutex eviction
    "alerts_failed": 0,   # counter
    "last_recovery": {},  # model -> timestamp. Per-model: on 2026-08-04 a full
                          # GPU reset wedged the 27b AND 35b together; a shared
                          # cooldown let the 27b's recovery block the 35b's for
                          # 15 more minutes of 500s.
    "first_seen": {},     # model -> timestamp it was first observed `loaded`.
                          # Drives PROBE_GRACE_SECONDS; cleared when a model
                          # leaves the loaded set so a reload restarts the grace.
    "loop_errors": 0,
    "hindsight_up": 1,          # gauge, starts optimistic like probe_ok does
    "hindsight_consec_fail": 0,
    "hindsight_alerted": False,  # avoid re-alerting every interval while down
    # -- host health (2026-08-25) --
    "mem_avail_bytes": 0,      # gauge
    "swap_used_frac": 0.0,     # gauge
    "reclaim_ratio": 0.0,      # gauge, rate-based over HEALTH_INTERVAL
    "mem_pressure": 0,         # gauge 1/0 — currently over threshold
    "mem_consec_fail": 0,
    "mem_alerted": False,
    "unit_up": {},             # unit -> 1/0
    "unit_consec_fail": {},    # unit -> int
    "unit_alerted": {},        # unit -> bool
    "_reclaim_prev": None,     # (pgscan, pgsteal) from the previous sample
}


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def http(url: str, data: bytes | None = None, timeout: float = 10,
         method: str | None = None) -> tuple[int, str]:
    """Return (status, body). Never raises for HTTP errors — a wedged
    llama-server answers 500 with the device-lost text in the body, and that
    body is exactly what we need to read."""
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 - connection refused, timeout, DNS
        return 0, f"{type(e).__name__}: {e}"


def resolve_upstream_host() -> str:
    """Host-side address of the router's child llama-server processes.

    The router runs with --network host (mandatory: children get ephemeral
    ports, and under bridge networking they would be unpublished and /slots
    unreachable), so children are simply on loopback.
    """
    return UPSTREAM_HOST or "127.0.0.1"


def running_models(host: str) -> list[dict]:
    """Models the router currently has LOADED, with their child ports.

    GET /models is a router control endpoint, not a model proxy route, so
    polling it never counts as model activity.

    Shape (verified 2026-08-06 against llama.cpp router build 10283):
      {"data": [{"id": "...", "aliases": [],
                 "status": {"value": "unloaded"|"loading"|"loaded",
                            "failed": bool|None, "exit_code": int|None,
                            "args": ["/usr/bin/llama-server", ..., "--port", "44449", ...]}}]}

    `status.value == "loaded"` replaces llama-swap's `state == "ready"`, and the
    child port is parsed out of the resolved argv rather than from a `proxy`
    URL. Note the port is EPHEMERAL — it changes on every reload, so it must be
    re-read each cycle and never cached.
    """
    status, body = http(f"{ROUTER}/models", timeout=10)
    if status != 200:
        return []
    try:
        data = json.loads(body).get("data", [])
    except json.JSONDecodeError:
        return []
    out = []
    for m in data:
        st = m.get("status") or {}
        if st.get("value") != "loaded":
            continue
        args = st.get("args") or []
        port = 0
        if "--port" in args:
            try:
                port = int(args[args.index("--port") + 1])
            except (IndexError, ValueError):
                port = 0
        if port:
            out.append({"model": m["id"], "port": port,
                        "url": f"http://{host}:{port}"})
    return out


def probe(m: dict, id_slot: int | None = None) -> tuple[bool, bool, float, str]:
    """Decode one real token. Returns (ok, device_lost, latency, detail).

    ``id_slot`` PINS the probe to one slot, and matters more than it looks.
    This prompt is 2 tokens of raw /completion, so it scores f_sim ~0 against
    any real conversation and llama.cpp falls through to LRU slot selection —
    which means the probe lands on whichever slot is idle and OVERWRITES its
    cached prefix. On 2026-08-07 that was DS4's slot 1 at 14:07:00, 14:09:55 and
    14:12:33, i.e. we were destroying a long conversation's prompt cache roughly
    once a minute and making the user pay a full re-prefill for it.

    Pinning is safe because llama.cpp checks LCP similarity BEFORE LRU, so an
    ongoing conversation reliably re-selects its own slot. Probing the last slot
    therefore leaves slot 0 permanently undisturbed on a --parallel 2 model. A
    device-lost fault is device-wide, so one slot is enough to detect it.
    """
    payload = {
        "prompt": "ok",
        "n_predict": 1,
        "temperature": 0,
        "cache_prompt": True,
    }
    if id_slot is not None:
        payload["id_slot"] = id_slot
    body = json.dumps(payload).encode()
    t0 = time.perf_counter()
    status, text = http(f"{m['url']}/completion", data=body, timeout=PROBE_TIMEOUT)
    dt = time.perf_counter() - t0
    lost = bool(DEVICE_LOST_PATTERNS.search(text))
    if status == 200 and not lost:
        return True, False, dt, "ok"
    return False, lost, dt, f"status={status} {text[:200]}"


def slots_progress(m: dict) -> tuple | None:
    """Forward-progress signature of the server's slots, or None if unreadable.

    Only meaningful when compared against an earlier snapshot: a busy-but-healthy
    server advances these counters between two reads, while a GPU-wedged one
    holds them frozen with ``is_processing`` still true. That difference is the
    only thing separating "working" from "hung" — /health and /v1/models both
    return 200 in either case.

    ``n_decoded`` moved under ``next_token`` in newer llama.cpp builds; both
    shapes are read so a container bump can't silently blind this check.
    """
    status, text = http(f"{m['url']}/slots", timeout=SLOTS_TIMEOUT)
    if status != 200:
        return None
    try:
        slots = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(slots, list):
        return None
    sig = []
    for s in slots:
        if not isinstance(s, dict):
            continue
        nt = s.get("next_token")
        if isinstance(nt, list) and nt and isinstance(nt[0], dict):
            nt = nt[0]
        elif not isinstance(nt, dict):
            nt = {}
        sig.append((
            s.get("id"),
            bool(s.get("is_processing")),
            s.get("n_prompt_tokens_processed"),
            nt.get("n_decoded", s.get("n_decoded")),
        ))
    return tuple(sig)


def pin_slot(sig: tuple | None) -> int | None:
    """Slot id to pin probes to: the highest one. See probe()'s docstring.

    Derived from the /slots snapshot the probe loop already took, so this costs
    no extra request.
    """
    if not sig:
        return None
    ids = [s[0] for s in sig if isinstance(s[0], int)]
    return max(ids) if ids else None


def slot_busy_advancing(before: tuple | None, after: tuple | None) -> bool:
    """True when the server is demonstrably doing real work, not wedged.

    Requires a slot still processing AND the counters to have moved. An
    unreadable /slots (``None``) is NOT handled here — see slots_readable().
    """
    if before is None or after is None or not after:
        return False
    if not any(s[1] for s in after):
        return False
    return before != after


def slots_readable(before: tuple | None, after: tuple | None) -> bool:
    """Whether we actually got two /slots snapshots to compare.

    🔴 THIS INVERTS THE PRE-2026-08-07 RULE, deliberately. The old code treated
    an unreadable /slots as evidence of a wedge and let it count toward
    FAIL_THRESHOLD. That is backwards:

      - A device-lost server FAILS DECODE FAST and keeps serving HTTP — that is
        the whole premise of this watchdog ("/health AND /v1/models BOTH PASS on
        a wedged server"). Its main loop spins, so /slots answers promptly with
        FROZEN counters, and slot_busy_advancing() correctly returns False.
      - A healthy server mid-prefill is busy for one whole ubatch at a time
        (~17s per chunk at 70k ctx on DS4), so /slots is exactly what times out.

    So an unreadable /slots is evidence of a BUSY main loop, not a dead one, and
    scoring it as a failure is what killed a healthy DS4 on 2026-08-07. The
    caller therefore treats it as INCONCLUSIVE — capped by MAX_INCONCLUSIVE so a
    genuinely unreachable child still reaches the recovery path.
    """
    return before is not None and after is not None


def scrape(m: dict) -> str | None:
    status, text = http(f"{m['url']}/metrics", timeout=10)
    return text if status == 200 else None


def hindsight_healthy() -> tuple[bool, str]:
    """Probe the Hindsight memory daemon's /health.

    Same "trust the payload, not just the status code" lesson as the model
    probes: a 200 with an unexpected body (e.g. a different service that
    happens to be listening) must not read as healthy. See the :8888/SearXNG
    landmine in local_ai_memory_stack memory for exactly this failure mode.
    """
    status, body = http(f"{HINDSIGHT_URL}/health", timeout=3)
    if status != 200:
        return False, f"HTTP {status}: {body[:150]}"
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return False, f"non-JSON body: {body[:150]}"
    if payload.get("status") == "healthy" and payload.get("database") == "connected":
        return True, ""
    return False, f"unexpected payload: {body[:150]}"


def _tg_post(chat: str, msg: str, html: bool) -> tuple:
    body_obj = {"chat_id": chat, "text": msg}
    if html:
        body_obj["parse_mode"] = "HTML"
    if TELEGRAM_TOPIC_ID:
        body_obj["message_thread_id"] = TELEGRAM_TOPIC_ID
    return http(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data=json.dumps(body_obj).encode(), timeout=20,
    )


def telegram(msg: str) -> None:
    """Send an alert, recovering from the two delivery failures seen in prod.

    🔴 Both of these silently ate alerts for weeks before 2026-08-25, because a
    failed ALERT has no way to alert about itself — the Grafana rule that used
    to watch `alerts_failed` died with Grafana on 2026-08-13.

    1. Group->supergroup migration. Enabling forum topics re-issues the chat id
       and the old one is rejected forever after. **50 alerts were dropped in 30
       days.** Telegram returns the replacement as `migrate_to_chat_id`, so we
       follow it and latch it for the rest of the process rather than dropping.
    2. HTML parse errors. Alert text interpolates exception strings, and one
       containing `<urlopen ...>` was read as a start tag ("Unsupported start
       tag urlopen", 2026-08-24). Retry as plain text: a slightly ugly alert
       that arrives beats a pretty one that does not.
    """
    global TELEGRAM_CHAT
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        log("WARN telegram not configured, alert dropped")
        with _lock:
            _state["alerts_failed"] += 1
        return

    status, body = _tg_post(TELEGRAM_CHAT, msg, True)
    if status == 200:
        return

    if "migrate_to_chat_id" in body:
        try:
            new_chat = str(json.loads(body)["parameters"]["migrate_to_chat_id"])
        except Exception:  # noqa: BLE001
            new_chat = ""
        if new_chat:
            log(f"WARN telegram chat migrated {TELEGRAM_CHAT} -> {new_chat}; "
                f"following it. PERSIST THIS in watchdog.env or every restart "
                f"re-learns it after another dropped alert.")
            TELEGRAM_CHAT = new_chat
            status, body = _tg_post(TELEGRAM_CHAT, msg, True)
            if status == 200:
                return

    if "can't parse entities" in body or "parse entities" in body:
        log(f"WARN telegram HTML rejected ({body[:120]}); resending as plain text")
        status, body = _tg_post(TELEGRAM_CHAT, re.sub(r"<[^>]+>", "", msg), False)
        if status == 200:
            return

    log(f"WARN telegram send failed: {status} {body[:200]}")
    with _lock:
        _state["alerts_failed"] += 1


def model_state(model: str) -> str:
    """Router's lifecycle value for one model: loaded|loading|unloaded|absent|unknown.

    ⚠️ Reads `status.value` ONLY. `status.failed` is a sticky flag from the last
    load attempt, not a liveness signal — a previously OOM-killed heavy model
    reports failed=true exit_code=1 from the 2026-08-07 kill while sitting quietly at
    value=unloaded. Conflating them would make every later decision wrong.
    """
    status, body = http(f"{ROUTER}/models", timeout=10)
    if status != 200:
        return "unknown"
    try:
        for m in json.loads(body).get("data", []):
            if m.get("id") == model:
                return (m.get("status") or {}).get("value") or "unknown"
    except (json.JSONDecodeError, AttributeError):
        return "unknown"
    return "absent"


def wait_unloaded(model: str, timeout: float = 180.0) -> bool:
    """Block until the router stops reporting `model` as loaded.

    The router's unload is ASYNC: it returns as soon as the child is told to
    exit. Issuing the reload immediately is what made recovery fail on
    2026-08-07 —

        14:14:03 RECOVER deepseek-v4-flash: unloading
        14:14:03 RECOVER deepseek-v4-flash: loading
        14:14:03 RECOVER deepseek-v4-flash: load FAILED in 0.0s
                 {"code":400,"message":"model is already running"}

    — and it would have failed that way on every recovery this watchdog ever
    attempted. Requires an explicit non-loaded state; "unknown" (router
    unreachable) must never read as success.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = model_state(model)
        if state in ("unloaded", "absent"):
            return True
        time.sleep(2)
    return False


def recover(model: str, host: str) -> bool:
    """Unload the wedged model and load it again.

    ⚠️ ALWAYS pass an explicit model. The router's unload takes a JSON body;
    a no-argument call was MEASURED on 2026-08-06 to return 500 and unload
    nothing, so it fails safe — but the guard below is kept deliberately,
    because llama-swap's equivalent (`GET /unload`) was NOT read-only and once
    took both production models down for ~55s when an agent enumerated routes.
    Do not remove the guard on the grounds that the current server is safe.
    """
    if not model:
        log("RECOVER refused: empty model name")
        return False

    log(f"RECOVER {model}: unloading")
    payload = json.dumps({"model": model}).encode()
    status, body = http(f"{ROUTER}/models/unload", data=payload, method="POST",
                        timeout=120)
    if status not in (200, 202, 204):
        # 400 "model is not running" just means it died on its own first.
        log(f"RECOVER {model}: unload returned {status} {body[:200]}")

    if not wait_unloaded(model):
        log(f"RECOVER {model}: still loaded after 180s — ABORTING reload "
            f"(a load now can only return 400 'model is already running')")
        return False

    log(f"RECOVER {model}: loading")
    t0 = time.perf_counter()
    # Explicit /models/load, not a fake inference request. The router owns
    # lifecycle now, so asking it directly is both clearer and avoids burning a
    # real completion. Timeout must cover DS4's 3-11 min cold load.
    status, body = http(f"{ROUTER}/models/load", data=payload, method="POST",
                        timeout=900)
    dt = time.perf_counter() - t0
    ok = status in (200, 202, 204)
    log(f"RECOVER {model}: load {'ok' if ok else 'FAILED'} in {dt:.1f}s"
        f"{'' if ok else ' ' + body[:200]}")
    return ok


def probe_loop(host: str) -> None:
    while True:
        try:
            models = running_models(host)
            now = time.time()
            with _lock:
                seen = _state["first_seen"]
                live = {m["model"] for m in models}
                for gone in [k for k in seen if k not in live]:
                    del seen[gone]          # reset grace on unload/reload
                for m in models:
                    seen.setdefault(m["model"], now)
            for m in models:
                name = m["model"]
                # 🔴 LOAD GRACE. The router flips a model to `loaded` as soon as
                # its child process is up, but DS4 needs minutes more before it
                # will answer. Probing through that window produces
                # FAIL_THRESHOLD consecutive timeouts and "recovers" a model
                # that was only ever loading — and since each recovery restarts
                # the same load, it never converges.
                with _lock:
                    age = now - _state["first_seen"].get(name, now)
                if age < PROBE_GRACE_SECONDS:
                    with _lock:
                        _state["probe_ok"][name] = 1     # optimistic while loading
                        _state["consec_fail"][name] = 0
                    continue
                slots_before = slots_progress(m)
                ok, lost, dt, detail = probe(m, pin_slot(slots_before))

                # A timeout behind a long prefill is not a wedge. With
                # --parallel 1 a single 85k-token prompt owns the only slot for
                # minutes, so this one-token probe cannot land and times out
                # while the model is perfectly healthy. Recovering on that would
                # unload the model and destroy the in-flight request it was
                # queued behind. A device-lost signature is never excused this
                # way — that is a real fault whatever the slots say.
                if not ok and not lost:
                    slots_after = slots_progress(m)
                    if slot_busy_advancing(slots_before, slots_after):
                        with _lock:
                            _state["probe_ok"][name] = 1
                            _state["probe_latency"][name] = dt
                            _state["probe_busy"] += 1
                            _state["consec_inconclusive"][name] = 0
                        log(f"PROBE {name} BUSY after {dt:.1f}s — slots "
                            f"advancing, model healthy, not counting a failure")
                        continue

                    # /slots unreadable: we know nothing. Excuse it up to
                    # MAX_INCONCLUSIVE times — see slots_readable()'s docstring
                    # for why this is the safe direction to err in.
                    if not slots_readable(slots_before, slots_after):
                        with _lock:
                            n = _state["consec_inconclusive"].get(name, 0) + 1
                            _state["consec_inconclusive"][name] = n
                            _state["probe_inconclusive"] += 1
                            _state["probe_latency"][name] = dt
                        if n <= MAX_INCONCLUSIVE:
                            log(f"PROBE {name} INCONCLUSIVE ({n}/{MAX_INCONCLUSIVE}) "
                                f"after {dt:.1f}s — /slots unreadable, cannot "
                                f"distinguish busy from wedged; not counting a failure")
                            continue
                        log(f"PROBE {name} INCONCLUSIVE {n}x (> {MAX_INCONCLUSIVE}) "
                            f"— falling through to the failure path")

                with _lock:
                    _state["consec_inconclusive"][name] = 0
                    _state["probe_ok"][name] = 1 if ok else 0
                    _state["probe_latency"][name] = dt
                    if ok:
                        _state["consec_fail"][name] = 0
                        continue
                    _state["consec_fail"][name] = _state["consec_fail"].get(name, 0) + 1
                    _state["probe_failures"] += 1
                    fails = _state["consec_fail"][name]
                    if lost:
                        _state["device_lost"] += 1
                    since_recovery = time.time() - _state["last_recovery"].get(name, 0.0)

                log(f"PROBE {name} FAILED ({fails}/{FAIL_THRESHOLD}) "
                    f"device_lost={lost} {dt:.1f}s {detail}")

                if fails < FAIL_THRESHOLD:
                    continue
                if since_recovery < RECOVERY_COOLDOWN:
                    log(f"SKIP recovery for {name}: cooldown "
                        f"{RECOVERY_COOLDOWN - since_recovery:.0f}s remaining")
                    continue

                with _lock:
                    _state["last_recovery"][name] = time.time()
                    _state["recoveries"] += 1
                telegram(
                    f"🔴 <b>llama-watchdog</b>\nModel <b>{name}</b> is wedged "
                    f"({fails} failed probes, device_lost={lost}).\n"
                    f"<code>{detail[:300]}</code>\nAttempting unload + re-warm."
                )
                ok = recover(name, host)
                with _lock:
                    _state["consec_fail"][name] = 0
                telegram(
                    f"{'🟢' if ok else '🔴'} <b>llama-watchdog</b>\n"
                    f"Recovery of <b>{name}</b> "
                    f"{'succeeded' if ok else 'FAILED — needs manual attention'}."
                )
        except Exception as e:  # noqa: BLE001 - loop must never die
            with _lock:
                _state["loop_errors"] += 1
            log(f"ERROR probe_loop: {type(e).__name__}: {e}")
        time.sleep(PROBE_INTERVAL)


def heavy_states() -> dict[str, str] | None:
    """{model: status.value} for HEAVY_MODELS only, or None if unreadable.

    Separate from running_models() because that filters to `loaded` models with
    a resolvable child port, and the whole point here is to catch a model while
    it is still `loading` — before its memory is committed.
    """
    status, body = http(f"{ROUTER}/models", timeout=5)
    if status != 200:
        return None
    try:
        data = json.loads(body).get("data", [])
    except (json.JSONDecodeError, AttributeError):
        return None
    out = {}
    for m in data:
        mid = m.get("id")
        if mid in HEAVY_MODELS:
            out[mid] = (m.get("status") or {}).get("value") or "unknown"
    return out


def pick_eviction_victim(states: dict[str, str], since: dict[str, float]) -> str | None:
    """Which heavy model to unload when two are in memory at once.

    Rules, in order:
      1. Never evict a model that is `loading` — that is the one just requested,
         and killing it mid-load would leave the caller with nothing.
      2. Prefer evicting a `loaded` incumbent when something else is `loading`.
      3. If several are `loaded` (no clear newcomer), evict the one resident
         LONGEST and keep the most recent arrival, which is the better proxy for
         what the user actually wants right now.
    Returns None when there is nothing to do.

    ⚠️ The HEAVY_MODELS filter is applied HERE as well as in heavy_states(), not
    only there. gemma4-e4b is load-bearing for every consumer on the box (Hermes
    title-gen and compression, Hindsight retain and consolidation), so "we only
    ever pass it heavy models" must not be the single thing standing between it
    and an unload.
    """
    states = {k: v for k, v in states.items() if k in HEAVY_MODELS}
    active = {k: v for k, v in states.items() if v in ("loading", "loaded")}
    if len(active) < 2:
        return None
    loaded = [k for k, v in active.items() if v == "loaded"]
    if not loaded:
        return None            # all still loading; rule 1 leaves nothing to evict
    if len(loaded) == len(active):
        # No newcomer to protect — evict the longest-resident.
        return min(loaded, key=lambda k: since.get(k, 0.0))
    # Something is loading: evict the incumbent that has been resident longest.
    return min(loaded, key=lambda k: since.get(k, 0.0))


def heavy_mutex_loop() -> None:
    """Enforce "at most one heavy model resident" — the 2026-08-07 OOM fix.

    See the HEAVY_MODELS block up top for why this lives here rather than being
    solved with --no-models-autoload.
    """
    if not HEAVY_MUTEX or len(HEAVY_MODELS) < 2:
        log(f"heavy-model mutex DISABLED (enabled={HEAVY_MUTEX}, "
            f"models={HEAVY_MODELS})")
        return
    log(f"heavy-model mutex armed for {HEAVY_MODELS} "
        f"(poll {HEAVY_MUTEX_INTERVAL}s, cooldown {HEAVY_EVICT_COOLDOWN}s)")
    while True:
        try:
            states = heavy_states()
            if states is None:
                time.sleep(HEAVY_MUTEX_INTERVAL)
                continue

            now = time.time()
            with _lock:
                since = _state["heavy_since"]
                for mid, val in states.items():
                    if val == "loaded":
                        since.setdefault(mid, now)
                    else:
                        since.pop(mid, None)
                active = [k for k, v in states.items() if v in ("loading", "loaded")]
                _state["heavy_coresident"] = 1 if len(active) > 1 else 0
                snapshot = dict(since)
                last_evict = dict(_state["last_evict"])

            victim = pick_eviction_victim(states, snapshot)
            if not victim:
                time.sleep(HEAVY_MUTEX_INTERVAL)
                continue
            if now - last_evict.get(victim, 0.0) < HEAVY_EVICT_COOLDOWN:
                time.sleep(HEAVY_MUTEX_INTERVAL)
                continue

            others = [f"{k}={v}" for k, v in states.items() if k != victim]
            log(f"HEAVY-MUTEX: {len(active)} heavy models resident "
                f"({', '.join(f'{k}={v}' for k, v in states.items())}) — "
                f"evicting {victim}")
            with _lock:
                _state["last_evict"][victim] = now
                _state["heavy_evictions"] += 1
            payload = json.dumps({"model": victim}).encode()
            status, body = http(f"{ROUTER}/models/unload", data=payload,
                                method="POST", timeout=120)
            ok = status in (200, 202, 204)
            log(f"HEAVY-MUTEX: unload {victim} "
                f"{'ok' if ok else f'FAILED {status} {body[:200]}'}")
            telegram(
                f"{'🟡' if ok else '🔴'} <b>llama-watchdog</b>\n"
                f"Two heavy models were resident at once "
                f"({', '.join(others)}) — this is the shape that OOM-killed "
                f"a resident model on 2026-08-07.\nEvicted <b>{victim}</b>: "
                f"{'ok' if ok else f'FAILED ({status})'}."
            )
        except Exception as e:  # noqa: BLE001 - loop must never die
            with _lock:
                _state["loop_errors"] += 1
            log(f"ERROR heavy_mutex_loop: {type(e).__name__}: {e}")
        time.sleep(HEAVY_MUTEX_INTERVAL)


def scrape_loop(host: str) -> None:
    while True:
        try:
            fresh = {}
            for m in running_models(host):
                text = scrape(m)
                if text:
                    fresh[m["model"]] = text
            with _lock:
                _state["metrics"] = fresh
        except Exception as e:  # noqa: BLE001
            with _lock:
                _state["loop_errors"] += 1
            log(f"ERROR scrape_loop: {type(e).__name__}: {e}")
        time.sleep(SCRAPE_INTERVAL)


def hindsight_loop() -> None:
    """Independent probe loop for the Hindsight memory daemon.

    Deliberately probe-only: hindsight-daemon.service already has
    Restart=always, so this loop's job is visibility (metric + alert), not
    recovery — a second recovery path here would just be a second place for
    the two-supervisor bug class to recur.
    """
    while True:
        try:
            ok, detail = hindsight_healthy()
            recovered_alert = False
            fails = 0
            should_alert = False
            with _lock:
                _state["hindsight_up"] = 1 if ok else 0
                if ok:
                    if _state["hindsight_alerted"]:
                        recovered_alert = True
                    _state["hindsight_consec_fail"] = 0
                    _state["hindsight_alerted"] = False
                else:
                    _state["hindsight_consec_fail"] += 1
                    fails = _state["hindsight_consec_fail"]
                    should_alert = (
                        fails >= HINDSIGHT_FAIL_THRESHOLD
                        and not _state["hindsight_alerted"]
                    )
                    if should_alert:
                        _state["hindsight_alerted"] = True

            if recovered_alert:
                telegram(
                    "🟢 <b>llama-watchdog</b>\nHindsight memory daemon "
                    "(:9177) is healthy again."
                )
            if not ok:
                log(f"PROBE hindsight FAILED ({fails}/{HINDSIGHT_FAIL_THRESHOLD}) {detail}")
                if should_alert:
                    telegram(
                        f"🔴 <b>llama-watchdog</b>\nHindsight memory daemon "
                        f"(:9177) has failed {fails} consecutive health probes.\n"
                        f"<code>{detail[:300]}</code>\n"
                        f"systemd should be restarting it (Restart=always) — "
                        f"check <code>journalctl --user -u hindsight-daemon</code> "
                        f"if this persists."
                    )
        except Exception as e:  # noqa: BLE001 - loop must never die
            with _lock:
                _state["loop_errors"] += 1
            log(f"ERROR hindsight_loop: {type(e).__name__}: {e}")
        time.sleep(HINDSIGHT_PROBE_INTERVAL)



def _proc_kv(path: str, sep: str) -> dict:
    """Parse /proc/meminfo or /proc/vmstat into {key: int}. Values in meminfo
    are kB; vmstat values are page counts. Callers convert."""
    out = {}
    with open(path) as f:
        for line in f:
            k, _, v = line.partition(sep)
            v = v.strip().split()
            if v:
                try:
                    out[k.strip()] = int(v[0])
                except ValueError:
                    pass
    return out


def host_memory() -> dict:
    """MemAvailable, swap fill, and direct-reclaim efficiency for THIS interval.

    🔴 The reclaim figure MUST be a rate. pgscan_direct/pgsteal_direct are
    monotonic counters, so their raw quotient is a lifetime average that would
    never fall back below the threshold once the box had thrashed even once.
    """
    mi = _proc_kv("/proc/meminfo", ":")
    vm = _proc_kv("/proc/vmstat", " ")
    swap_total = mi.get("SwapTotal", 0)
    swap_used = swap_total - mi.get("SwapFree", 0)
    scan, steal = vm.get("pgscan_direct", 0), vm.get("pgsteal_direct", 0)

    with _lock:
        prev = _state["_reclaim_prev"]
        _state["_reclaim_prev"] = (scan, steal)
    ratio = 0.0
    if prev:
        d_scan, d_steal = scan - prev[0], steal - prev[1]
        # Only meaningful when real scanning happened; also guards a counter
        # reset across a reboot producing a negative delta.
        if d_scan >= RECLAIM_MIN_SCAN and d_steal >= 0:
            ratio = d_scan / max(d_steal, 1)
    return {
        "avail": mi.get("MemAvailable", 0) * 1024,
        "swap_frac": (swap_used / swap_total) if swap_total else 0.0,
        "reclaim_ratio": ratio,
    }


def unit_active(unit: str) -> bool:
    """True when systemd reports the user unit active. Sockets report active
    while merely listening, which is exactly the liveness we want for the
    on-demand proxies — their backing .service is SUPPOSED to be inactive."""
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", unit],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() == "active"
    except Exception:  # noqa: BLE001 - treat an unreadable systemd as unknown
        return True     # fail-OPEN: never page because systemctl itself hiccuped


def health_loop() -> None:
    """Memory pressure + systemd unit liveness -> Telegram, debounced.

    Same debounce contract as hindsight_loop: one alert on the way down after
    HEALTH_FAIL_THRESHOLD consecutive bad samples, one on the way back up, and
    nothing in between.
    """
    while True:
        try:
            m = host_memory()
            avail_gib = m["avail"] / (1024 ** 3)
            reasons = []
            if avail_gib < MEM_AVAIL_MIN_GIB:
                reasons.append(f"MemAvailable {avail_gib:.1f} GiB "
                               f"(< {MEM_AVAIL_MIN_GIB} GiB)")
            if m["swap_frac"] > SWAP_USED_MAX_FRAC:
                reasons.append(f"swap {m['swap_frac']*100:.0f}% full "
                               f"(> {SWAP_USED_MAX_FRAC*100:.0f}%)")
            if m["reclaim_ratio"] > RECLAIM_RATIO_MAX:
                reasons.append(f"direct reclaim {m['reclaim_ratio']:.1f}x "
                               f"(> {RECLAIM_RATIO_MAX}x) — scanning far more "
                               f"than it can free")
            bad = bool(reasons)

            recovered = False
            should_alert = False
            with _lock:
                _state["mem_avail_bytes"] = m["avail"]
                _state["swap_used_frac"] = m["swap_frac"]
                _state["reclaim_ratio"] = m["reclaim_ratio"]
                _state["mem_pressure"] = 1 if bad else 0
                if bad:
                    _state["mem_consec_fail"] += 1
                    if (_state["mem_consec_fail"] >= HEALTH_FAIL_THRESHOLD
                            and not _state["mem_alerted"]):
                        _state["mem_alerted"] = True
                        should_alert = True
                else:
                    if _state["mem_alerted"]:
                        recovered = True
                    _state["mem_consec_fail"] = 0
                    _state["mem_alerted"] = False

            if should_alert:
                telegram(
                    "🔴 <b>llama-watchdog</b>\nHost memory pressure:\n"
                    + "\n".join(f"• {r}" for r in reasons)
                    + "\n\nGTT is unswappable, so the kernel can only reclaim "
                      "hermes/hindsight/llama-router working sets — which is what "
                      "makes replies stall. Check GTT first:\n"
                      "<code>cat /sys/class/drm/card1/device/mem_info_gtt_used</code>"
                )
            if recovered:
                telegram("🟢 <b>llama-watchdog</b>\nHost memory pressure cleared "
                         f"(MemAvailable {avail_gib:.1f} GiB).")

            for unit in HEALTH_UNITS:
                up = unit_active(unit)
                u_alert = u_recovered = False
                with _lock:
                    _state["unit_up"][unit] = 1 if up else 0
                    if up:
                        if _state["unit_alerted"].get(unit):
                            u_recovered = True
                        _state["unit_consec_fail"][unit] = 0
                        _state["unit_alerted"][unit] = False
                    else:
                        c = _state["unit_consec_fail"].get(unit, 0) + 1
                        _state["unit_consec_fail"][unit] = c
                        if (c >= HEALTH_FAIL_THRESHOLD
                                and not _state["unit_alerted"].get(unit)):
                            _state["unit_alerted"][unit] = True
                            u_alert = True
                if u_alert:
                    log(f"HEALTH unit DOWN: {unit}")
                    telegram(f"🔴 <b>llama-watchdog</b>\nUnit <b>{unit}</b> is not "
                             f"active.\n<code>systemctl --user status {unit}</code>")
                if u_recovered:
                    telegram(f"🟢 <b>llama-watchdog</b>\nUnit <b>{unit}</b> is "
                             f"active again.")
        except Exception as e:  # noqa: BLE001 - loop must never die
            with _lock:
                _state["loop_errors"] += 1
            log(f"ERROR health_loop: {type(e).__name__}: {e}")
        time.sleep(HEALTH_INTERVAL)


_METRIC_LINE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+(.+)$")


def relabel(text: str, model: str) -> list[str]:
    """Add model="<name>" to every sample, dropping HELP/TYPE (re-emitted once)."""
    out = []
    label = f'model="{model}"'
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = _METRIC_LINE.match(line)
        if not m:
            continue
        name, labels, value = m.group(1), m.group(2), m.group(3)
        if labels:
            inner = labels[1:-1].strip()
            labels = "{" + (f"{inner},{label}" if inner else label) + "}"
        else:
            labels = "{" + label + "}"
        out.append(f"{name}{labels} {value}")
    return out


def render() -> str:
    with _lock:
        metrics = dict(_state["metrics"])
        probe_ok = dict(_state["probe_ok"])
        latency = dict(_state["probe_latency"])
        consec = dict(_state["consec_fail"])
        recoveries = _state["recoveries"]
        device_lost = _state["device_lost"]
        failures = _state["probe_failures"]
        busy = _state["probe_busy"]
        inconclusive = _state["probe_inconclusive"]
        heavy_evictions = _state["heavy_evictions"]
        heavy_coresident = _state["heavy_coresident"]
        alerts_failed = _state["alerts_failed"]
        loop_errors = _state["loop_errors"]
        hindsight_up = _state["hindsight_up"]
        hindsight_fail = _state["hindsight_consec_fail"]
        mem_avail = _state["mem_avail_bytes"]
        swap_frac = _state["swap_used_frac"]
        reclaim_ratio = _state["reclaim_ratio"]
        mem_pressure = _state["mem_pressure"]
        unit_up = dict(_state["unit_up"])

    out = [
        "# HELP llama_watchdog_probe_success Whether the last real-completion probe succeeded (1/0)",
        "# TYPE llama_watchdog_probe_success gauge",
        "# HELP llama_watchdog_probe_latency_seconds Latency of the last probe",
        "# TYPE llama_watchdog_probe_latency_seconds gauge",
        "# HELP llama_watchdog_consecutive_failures Consecutive failed probes",
        "# TYPE llama_watchdog_consecutive_failures gauge",
        "# HELP llama_watchdog_recoveries_total Recovery attempts performed",
        "# TYPE llama_watchdog_recoveries_total counter",
        "# HELP llama_watchdog_device_lost_total Probes whose response matched a device-lost signature",
        "# TYPE llama_watchdog_device_lost_total counter",
        "# HELP llama_watchdog_probe_failures_total Failed probes of any kind",
        "# TYPE llama_watchdog_probe_failures_total counter",
        "# HELP llama_watchdog_probe_busy_total Probes that timed out behind real work and were not counted as failures",
        "# TYPE llama_watchdog_probe_busy_total counter",
        "# HELP llama_watchdog_probe_inconclusive_total Probes where /slots was unreadable, so busy could not be distinguished from wedged",
        "# TYPE llama_watchdog_probe_inconclusive_total counter",
        "# HELP llama_watchdog_heavy_evictions_total Heavy-model mutex evictions performed",
        "# TYPE llama_watchdog_heavy_evictions_total counter",
        "# HELP llama_watchdog_heavy_coresident Whether two heavy models are resident at once (1/0)",
        "# TYPE llama_watchdog_heavy_coresident gauge",
        "# HELP llama_watchdog_alerts_failed_total Telegram alerts that could not be delivered",
        "# TYPE llama_watchdog_alerts_failed_total counter",
        "# HELP llama_watchdog_loop_errors_total Unhandled errors caught in the background loops",
        "# TYPE llama_watchdog_loop_errors_total counter",
        "# HELP llama_watchdog_models_loaded Models the router currently reports as loaded",
        "# TYPE llama_watchdog_models_loaded gauge",
        f"llama_watchdog_recoveries_total {recoveries}",
        f"llama_watchdog_device_lost_total {device_lost}",
        f"llama_watchdog_probe_failures_total {failures}",
        f"llama_watchdog_probe_busy_total {busy}",
        f"llama_watchdog_probe_inconclusive_total {inconclusive}",
        f"llama_watchdog_heavy_evictions_total {heavy_evictions}",
        f"llama_watchdog_heavy_coresident {heavy_coresident}",
        f"llama_watchdog_alerts_failed_total {alerts_failed}",
        f"llama_watchdog_loop_errors_total {loop_errors}",
        f"llama_watchdog_models_loaded {len(metrics)}",
    ]
    # Only emitted when HINDSIGHT_URL is configured — an unconfigured deploy
    # should not publish a gauge that reads as "1 = healthy" for a service
    # that was never asked to be watched.
    if HINDSIGHT_URL:
        out.extend([
            "# HELP llama_watchdog_hindsight_up Whether the Hindsight memory daemon's last /health probe succeeded (1/0)",
            "# TYPE llama_watchdog_hindsight_up gauge",
            "# HELP llama_watchdog_hindsight_consecutive_failures Consecutive failed Hindsight health probes",
            "# TYPE llama_watchdog_hindsight_consecutive_failures gauge",
            f"llama_watchdog_hindsight_up {hindsight_up}",
            f"llama_watchdog_hindsight_consecutive_failures {hindsight_fail}",
        ])
    for model, v in probe_ok.items():
        out.append(f'llama_watchdog_probe_success{{model="{model}"}} {v}')
    for model, v in latency.items():
        out.append(f'llama_watchdog_probe_latency_seconds{{model="{model}"}} {v:.4f}')
    for model, v in consec.items():
        out.append(f'llama_watchdog_consecutive_failures{{model="{model}"}} {v}')

    # Relayed llamacpp:* series. HELP/TYPE are emitted from whichever model we
    # see first; they are identical across models.
    seen_meta = False
    body = []
    for model, text in metrics.items():
        if not seen_meta:
            out.extend(l for l in text.splitlines() if l.startswith("#"))
            seen_meta = True
        body.extend(relabel(text, model))
    out.extend(body)
    out += [
        "# HELP llama_watchdog_mem_available_bytes Host MemAvailable",
        "# TYPE llama_watchdog_mem_available_bytes gauge",
        f"llama_watchdog_mem_available_bytes {mem_avail}",
        "# HELP llama_watchdog_swap_used_ratio Fraction of swap in use",
        "# TYPE llama_watchdog_swap_used_ratio gauge",
        f"llama_watchdog_swap_used_ratio {swap_frac:.4f}",
        "# HELP llama_watchdog_reclaim_ratio pgscan_direct/pgsteal_direct over the last interval (rate, not lifetime)",
        "# TYPE llama_watchdog_reclaim_ratio gauge",
        f"llama_watchdog_reclaim_ratio {reclaim_ratio:.3f}",
        "# HELP llama_watchdog_mem_pressure Host memory currently over threshold (1/0)",
        "# TYPE llama_watchdog_mem_pressure gauge",
        f"llama_watchdog_mem_pressure {mem_pressure}",
        "# HELP llama_watchdog_unit_up systemd user unit is active (1/0)",
        "# TYPE llama_watchdog_unit_up gauge",
    ]
    for unit, v in sorted(unit_up.items()):
        out.append(f'llama_watchdog_unit_up{{unit="{unit}"}} {v}')
    return "\n".join(out) + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/metrics":
            self.send_response(404)
            self.end_headers()
            return
        payload = render().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_a, **_k):
        pass


if __name__ == "__main__":
    host = resolve_upstream_host()
    log(f"llama-watchdog starting: upstream={host} router={ROUTER} "
        f"probe={PROBE_INTERVAL}s scrape={SCRAPE_INTERVAL}s "
        f"threshold={FAIL_THRESHOLD} cooldown={RECOVERY_COOLDOWN}s "
        f"hindsight={HINDSIGHT_URL or '(disabled)'} "
        f"hindsight_probe={HINDSIGHT_PROBE_INTERVAL}s "
        f"slots_timeout={SLOTS_TIMEOUT}s max_inconclusive={MAX_INCONCLUSIVE} "
        f"listen={ADDR[0]}:{ADDR[1]}")
    if "--once" in sys.argv:
        # Diagnostic mode: probe + scrape once, print the exposition, exit.
        for mm in running_models(host):
            pin = pin_slot(slots_progress(mm))
            ok, lost, dt, detail = probe(mm, pin)
            log(f"probe {mm['model']}: ok={ok} device_lost={lost} {dt:.2f}s "
                f"id_slot={pin} {detail}")
            t = scrape(mm)
            with _lock:
                _state["probe_ok"][mm["model"]] = 1 if ok else 0
                _state["probe_latency"][mm["model"]] = dt
                if t:
                    _state["metrics"][mm["model"]] = t
        if HINDSIGHT_URL:
            h_ok, h_detail = hindsight_healthy()
            log(f"probe hindsight: ok={h_ok} {h_detail}")
            with _lock:
                _state["hindsight_up"] = 1 if h_ok else 0
        print(render())
        sys.exit(0)
    threading.Thread(target=probe_loop, args=(host,), daemon=True).start()
    threading.Thread(target=scrape_loop, args=(host,), daemon=True).start()
    threading.Thread(target=heavy_mutex_loop, daemon=True).start()
    threading.Thread(target=health_loop, daemon=True).start()
    if HINDSIGHT_URL:
        threading.Thread(target=hindsight_loop, daemon=True).start()
    HTTPServer(ADDR, Handler).serve_forever()
