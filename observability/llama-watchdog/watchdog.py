#!/usr/bin/env python3
"""llama-watchdog — detect, recover from, and measure GPU device-lost hangs.

WHY THIS EXISTS
---------------
gfx1151 (Strix Halo) has a known, currently unfixed amdgpu bug: under concurrent
decode the ring times out, Vulkan returns VK_ERROR_DEVICE_LOST, and llama-server
never rebuilds the lost device. It keeps running and keeps 500ing forever.

llama-swap only notices a model dying if the *process exits*, which it does not,
so nothing restarts it. On 2026-08-01 both models sat wedged for ~20 minutes
returning errors while every liveness check reported healthy. Recovery beats
prevention here, because the bug is upstream and unfixed.

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

TTL SAFETY: probe ONLY models already listed by /running, never by name.
Waking an idle model would re-create the 2026-05-23 bug where a 15s Prometheus
scrape reset llama-swap's idle timer and defeated `ttl` entirely. For the same
reason every model-directed request here goes DIRECT to the llama-server port,
never through llama-swap's /upstream proxy — direct requests never reach
llama-swap, so they structurally cannot reset the idle timer.

NETWORK: llama-swap sits on llama-stack_default (172.20.x) and the upstream
llama-servers bind ports inside that network only. VictoriaMetrics is on
ai-stack (172.23.x) and CANNOT route there (verified: wget times out). The host
can reach both. That is why this runs as a systemd --user service on the host
and not as a container.
"""
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

SWAP = os.environ.get("LLAMA_SWAP_URL", "http://127.0.0.1:9292")
# Host-side address of the llama-swap container, where the upstream
# llama-server processes actually listen. Resolved at startup if unset.
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
RECOVERY_COOLDOWN = float(os.environ.get("RECOVERY_COOLDOWN", "900"))

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")

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
    "alerts_failed": 0,   # counter
    "last_recovery": 0.0,
    "loop_errors": 0,
    "hindsight_up": 1,          # gauge, starts optimistic like probe_ok does
    "hindsight_consec_fail": 0,
    "hindsight_alerted": False,  # avoid re-alerting every interval while down
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
    """IP of the llama-swap container on its own network."""
    if UPSTREAM_HOST:
        return UPSTREAM_HOST
    import subprocess
    try:
        out = subprocess.run(
            ["docker", "inspect", "llama-swap", "--format",
             "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.split()
        if out:
            return out[0]
    except Exception as e:  # noqa: BLE001
        log(f"WARN could not resolve llama-swap IP: {e}")
    return "127.0.0.1"


def running_models(host: str) -> list[dict]:
    """Models llama-swap currently has loaded, with their upstream ports.

    /running is a llama-swap API endpoint, not a model proxy route, so polling
    it does not count as model activity for ttl purposes.
    """
    status, body = http(f"{SWAP}/running", timeout=10)
    if status != 200:
        return []
    try:
        running = json.loads(body).get("running", [])
    except json.JSONDecodeError:
        return []
    out = []
    for m in running:
        if m.get("state") != "ready":
            continue
        port = urllib.parse.urlparse(m.get("proxy", "")).port
        if port:
            out.append({"model": m["model"], "port": port,
                        "url": f"http://{host}:{port}"})
    return out


def probe(m: dict) -> tuple[bool, bool, float, str]:
    """Decode one real token. Returns (ok, device_lost, latency, detail)."""
    body = json.dumps({
        "prompt": "ok",
        "n_predict": 1,
        "temperature": 0,
        "cache_prompt": True,
    }).encode()
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
    status, text = http(f"{m['url']}/slots", timeout=10)
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


def slot_busy_advancing(before: tuple | None, after: tuple | None) -> bool:
    """True when the server is demonstrably doing real work, not wedged.

    Requires a slot still processing AND the counters to have moved. Returning
    False on an unreadable /slots is deliberate: an unavailable endpoint must
    fall through to the old fail-and-recover path rather than silently disarm
    the watchdog.
    """
    if before is None or after is None or not after:
        return False
    if not any(s[1] for s in after):
        return False
    return before != after


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


def telegram(msg: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        log("WARN telegram not configured, alert dropped")
        with _lock:
            _state["alerts_failed"] += 1
        return
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT, "text": msg, "parse_mode": "HTML",
    }).encode()
    status, body = http(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data=payload, timeout=20,
    )
    if status != 200:
        log(f"WARN telegram send failed: {status} {body[:200]}")
        with _lock:
            _state["alerts_failed"] += 1


def recover(model: str, host: str) -> bool:
    """Unload the wedged model and re-warm it.

    ⚠️ POST /api/models/unload/<model>, NEVER GET /unload.
    In llama-swap v234 `GET /unload` is NOT read-only and unloads EVERY model.
    An agent enumerating routes called it during this investigation and took
    both production models down for ~55s. Per-model POST is the only safe form.
    """
    log(f"RECOVER {model}: unloading")
    status, body = http(f"{SWAP}/api/models/unload/{urllib.parse.quote(model)}",
                        data=b"", method="POST", timeout=120)
    if status not in (200, 202, 204):
        log(f"RECOVER {model}: unload returned {status} {body[:200]}")

    log(f"RECOVER {model}: re-warming")
    warm = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "ok"}],
        "max_tokens": 1,
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode()
    t0 = time.perf_counter()
    # Through llama-swap on purpose: this one must go via the proxy so that
    # llama-swap is the thing that starts the process back up.
    status, body = http(f"{SWAP}/v1/chat/completions", data=warm, timeout=600)
    dt = time.perf_counter() - t0
    ok = status == 200
    log(f"RECOVER {model}: re-warm {'ok' if ok else 'FAILED'} in {dt:.1f}s"
        f"{'' if ok else ' ' + body[:200]}")
    return ok


def probe_loop(host: str) -> None:
    while True:
        try:
            models = running_models(host)
            for m in models:
                name = m["model"]
                slots_before = slots_progress(m)
                ok, lost, dt, detail = probe(m)

                # A timeout behind a long prefill is not a wedge. With
                # --parallel 1 a single 85k-token prompt owns the only slot for
                # minutes, so this one-token probe cannot land and times out
                # while the model is perfectly healthy. Recovering on that would
                # unload the model and destroy the in-flight request it was
                # queued behind. A device-lost signature is never excused this
                # way — that is a real fault whatever the slots say.
                if not ok and not lost:
                    if slot_busy_advancing(slots_before, slots_progress(m)):
                        with _lock:
                            _state["probe_ok"][name] = 1
                            _state["probe_latency"][name] = dt
                            _state["probe_busy"] += 1
                        log(f"PROBE {name} BUSY after {dt:.1f}s — slots "
                            f"advancing, model healthy, not counting a failure")
                        continue

                with _lock:
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
                    since_recovery = time.time() - _state["last_recovery"]

                log(f"PROBE {name} FAILED ({fails}/{FAIL_THRESHOLD}) "
                    f"device_lost={lost} {dt:.1f}s {detail}")

                if fails < FAIL_THRESHOLD:
                    continue
                if since_recovery < RECOVERY_COOLDOWN:
                    log(f"SKIP recovery for {name}: cooldown "
                        f"{RECOVERY_COOLDOWN - since_recovery:.0f}s remaining")
                    continue

                with _lock:
                    _state["last_recovery"] = time.time()
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
        alerts_failed = _state["alerts_failed"]
        loop_errors = _state["loop_errors"]
        hindsight_up = _state["hindsight_up"]
        hindsight_fail = _state["hindsight_consec_fail"]

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
        "# HELP llama_watchdog_alerts_failed_total Telegram alerts that could not be delivered",
        "# TYPE llama_watchdog_alerts_failed_total counter",
        "# HELP llama_watchdog_loop_errors_total Unhandled errors caught in the background loops",
        "# TYPE llama_watchdog_loop_errors_total counter",
        "# HELP llama_watchdog_models_loaded Models llama-swap currently reports as ready",
        "# TYPE llama_watchdog_models_loaded gauge",
        f"llama_watchdog_recoveries_total {recoveries}",
        f"llama_watchdog_device_lost_total {device_lost}",
        f"llama_watchdog_probe_failures_total {failures}",
        f"llama_watchdog_probe_busy_total {busy}",
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
    log(f"llama-watchdog starting: upstream={host} swap={SWAP} "
        f"probe={PROBE_INTERVAL}s scrape={SCRAPE_INTERVAL}s "
        f"threshold={FAIL_THRESHOLD} cooldown={RECOVERY_COOLDOWN}s "
        f"hindsight={HINDSIGHT_URL or '(disabled)'} "
        f"hindsight_probe={HINDSIGHT_PROBE_INTERVAL}s "
        f"listen={ADDR[0]}:{ADDR[1]}")
    if "--once" in sys.argv:
        # Diagnostic mode: probe + scrape once, print the exposition, exit.
        for mm in running_models(host):
            ok, lost, dt, detail = probe(mm)
            log(f"probe {mm['model']}: ok={ok} device_lost={lost} {dt:.2f}s {detail}")
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
    if HINDSIGHT_URL:
        threading.Thread(target=hindsight_loop, daemon=True).start()
    HTTPServer(ADDR, Handler).serve_forever()
