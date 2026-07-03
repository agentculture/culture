#!/usr/bin/env bash
#
# always-on-probe.sh — verify the "always-on spark mesh" announcement end to end.
#
# Checks (each prints PASS / FAIL / SKIP; the script exits non-zero if any FAIL):
#   1. units      — the five CLI-provisioned culture units + the chat tunnel are active
#   2. fail-fast  — `culture server start` with a bad mesh-config exits 78 (EX_CONFIG),
#                   so RestartPreventExitStatus=78 parks the unit instead of looping
#   3. console    — https://chat.agentculture.org answers 200 via a CF Access service token
#   4. media      — an image and an audio clip upload, come back byte-identical from
#                   their public capability URL (media.public_base_url is the public origin)
#
# The live checks (3, 4) need a Cloudflare Access service token in the environment:
#     export CF_ACCESS_CLIENT_ID=...    CF_ACCESS_CLIENT_SECRET=...
# Without them, checks 3 and 4 SKIP (not FAIL) so 1 and 2 still run headless.
#
# This probe measures the DEPLOYED tool's behavior. It records fully green only
# once these engine fixes ship to spark (`uv tool upgrade culture` + unit reinstall).
#
# Usage:  scripts/always-on-probe.sh            # spark defaults
#         CULTURE_CONSOLE_URL=... CULTURE_BIN=... scripts/always-on-probe.sh
set -uo pipefail

CONSOLE_URL="${CULTURE_CONSOLE_URL:-https://chat.agentculture.org}"
CULTURE_BIN="${CULTURE_BIN:-culture}"
SYSTEMCTL="${CULTURE_SYSTEMCTL:-systemctl --user}"
FIXTURES="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/probe-fixtures"

# The five CLI-provisioned culture units (server + console + three agents).
CULTURE_UNITS=(
    culture-server-spark.service
    culture-console-spark.service
    culture-agent-spark-culture.service
    culture-agent-spark-agentirc.service
    culture-agent-spark-colleague.service
)
TUNNEL_UNIT="cloudflared-chat.service"

pass=0 fail=0 skip=0
ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$*"; pass=$((pass + 1)); }
no()   { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; fail=$((fail + 1)); }
sk()   { printf '  \033[33mSKIP\033[0m  %s\n' "$*"; skip=$((skip + 1)); }

have_creds() { [ -n "${CF_ACCESS_CLIENT_ID:-}" ] && [ -n "${CF_ACCESS_CLIENT_SECRET:-}" ]; }
cf_hdrs=(-H "CF-Access-Client-Id: ${CF_ACCESS_CLIENT_ID:-}"
         -H "CF-Access-Client-Secret: ${CF_ACCESS_CLIENT_SECRET:-}")

echo "== 1. units active =="
for u in "${CULTURE_UNITS[@]}" "$TUNNEL_UNIT"; do
    state=$($SYSTEMCTL is-active "$u" 2>/dev/null || echo unknown)
    if [ "$state" = active ]; then ok "$u"; else no "$u is '$state' (expected active)"; fi
done

echo "== 2. fail-fast: bad mesh-config exits 78 =="
# A path that cannot exist; the config-resolution boundary must exit 78 before binding.
# Use a throwaway --name so the running mesh server's pidfile does not short-circuit
# this at the already-running guard (exit 1) before the config check is reached.
bad_path="/nonexistent-$$-always-on-probe.yaml"
timeout 20 "$CULTURE_BIN" server start --name "always-on-probe-$$" \
    --mesh-config "$bad_path" --foreground >/dev/null 2>&1
code=$?
if [ "$code" -eq 78 ]; then
    ok "bad mesh-config -> exit 78 (EX_CONFIG)"
else
    no "bad mesh-config -> exit $code (expected 78; is the fix deployed?)"
fi

echo "== 3. console 200 via CF Access service token =="
if have_creds; then
    http=$(curl -sS -m 15 -o /dev/null -w '%{http_code}' "${cf_hdrs[@]}" "$CONSOLE_URL/" 2>/dev/null || echo 000)
    if [ "$http" = 200 ]; then ok "$CONSOLE_URL -> 200"
    else no "$CONSOLE_URL -> $http (1033 = tunnel down; 403 = bad/again service token)"; fi
else
    sk "console 200 (set CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET)"
fi

echo "== 4. media round-trip (byte-identical via public capability URL) =="
roundtrip() { # $1 fixture, $2 label
    local fx="$1" label="$2" resp url out
    if [ ! -f "$fx" ]; then no "$label fixture missing: $fx"; return; fi
    resp=$(curl -sS -m 30 "${cf_hdrs[@]}" -F "file=@$fx" "$CONSOLE_URL/upload" 2>/dev/null)
    url=$(printf '%s' "$resp" | jq -r '.url // empty' 2>/dev/null)
    if [ -z "$url" ]; then no "$label upload returned no url (response: ${resp:-<empty>})"; return; fi
    # media.public_base_url must make the URL the public origin, not 127.0.0.1.
    case "$url" in
        "$CONSOLE_URL"/*) : ;;
        *) no "$label capability url is not public: $url"; return ;;
    esac
    out=$(mktemp)
    curl -sS -m 30 "$url" -o "$out" 2>/dev/null   # capability URL is auth-exempt by design
    if cmp -s "$fx" "$out"; then ok "$label round-trip byte-identical ($url)"; else no "$label round-trip differs from source"; fi
    rm -f "$out"
}
if have_creds; then
    if command -v jq >/dev/null 2>&1; then
        roundtrip "$FIXTURES/probe.png" image
        roundtrip "$FIXTURES/probe.wav" audio
    else
        no "media round-trip needs 'jq' (not found on PATH)"
    fi
else
    sk "media round-trip (needs CF Access service-token creds)"
fi

echo
echo "== summary: ${pass} passed, ${fail} failed, ${skip} skipped =="
[ "$fail" -eq 0 ]
