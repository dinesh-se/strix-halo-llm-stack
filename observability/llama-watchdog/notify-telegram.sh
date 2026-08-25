#!/usr/bin/env bash
# Standalone Telegram sender, deliberately INDEPENDENT of watchdog.py.
#
# Exists because a watchdog cannot alert on its own death. Grafana used to
# cover this with `ai-stack-watchdog-down` (noDataState: Alerting), and that
# rule died with Grafana on 2026-08-13 — since then a dead watchdog has looked
# exactly like a healthy one. Used by telegram-alert@.service (OnFailure=) and
# by watchdog-heartbeat.service.
set -uo pipefail
ENV_FILE="/home/YOU/strix-halo-llm-stack/observability/llama-watchdog/watchdog.env"
[ -r "$ENV_FILE" ] || { echo "no env file" >&2; exit 1; }
set -a; . "$ENV_FILE"; set +a

MSG="${1:-(no message)}"
[ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ] || {
  echo "telegram not configured" >&2; exit 1; }

# 🔴 --data-urlencode for the TEXT, not -d. `curl -d` form-encodes WITHOUT
# escaping, so a bare `&` in the message ends the field early and silently
# truncates the rest. HTML-escaped content is full of `&gt;`/`&lt;`, so this
# broke every message containing an escaped angle bracket — found 2026-08-25
# when a <pre> block lost its closing tag ("Can't find end tag ... pre").
args=(-d chat_id="${TELEGRAM_CHAT_ID}" -d parse_mode=HTML
      --data-urlencode "text=${MSG}")
# Only send message_thread_id when set — an empty value is rejected by the API.
[ -n "${TELEGRAM_TOPIC_ID:-}" ] && args+=(-d message_thread_id="${TELEGRAM_TOPIC_ID}")

out=$(curl -sS -m 20 -X POST \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" "${args[@]}") || {
  echo "curl failed" >&2; exit 1; }
grep -q '"ok":true' <<<"$out" || { echo "telegram rejected: ${out:0:200}" >&2; exit 1; }
