#!/usr/bin/env bash
set -euo pipefail

# SonarCloud API client for Claude Code.
# Requires SONAR_TOKEN environment variable.
# Project key resolves from --project flag, then $SONAR_PROJECT.

BASE_URL="https://sonarcloud.io"

# --- Defaults ---
PROJECT="${SONAR_PROJECT:-}"
SEVERITY=""
TYPE=""
LIMIT=25
RAW=false
COMMAND=""
ISSUE_KEY=""
ACCEPT_COMMENT=""

require_value() {
  local flag="$1" remaining="$2"
  if [[ "$remaining" -lt 2 ]]; then
    echo "Error: $flag requires a value" >&2
    echo "Run sonar.sh --help for usage." >&2
    exit 1
  fi
}

# --- Parse args ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    status|issues|metrics|hotspots|accept)
      COMMAND="$1"; shift ;;
    --project|-p)   require_value "$1" "$#"; PROJECT="$2";        shift 2 ;;
    --severity|-s)  require_value "$1" "$#"; SEVERITY="$2";       shift 2 ;;
    --type|-t)      require_value "$1" "$#"; TYPE="$2";           shift 2 ;;
    --limit|-l)     require_value "$1" "$#"; LIMIT="$2";          shift 2 ;;
    --raw|-r)       RAW=true;                                     shift ;;
    --issue|-i)     require_value "$1" "$#"; ISSUE_KEY="$2";      shift 2 ;;
    --comment|-c)   require_value "$1" "$#"; ACCEPT_COMMENT="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: sonar.sh <command> [options]"
      echo ""
      echo "Commands:"
      echo "  status    Quality gate status (pass/fail)"
      echo "  issues    List code issues (bugs, vulnerabilities, code smells)"
      echo "  metrics   Key code metrics (coverage, bugs, duplication, LOC)"
      echo "  hotspots  Security hotspots"
      echo "  accept    Mark an OPEN issue as ACCEPTED with a rationale comment"
      echo ""
      echo "Options:"
      echo "  --project, -p KEY       Project key (default: \$SONAR_PROJECT)"
      echo "  --severity, -s LEVEL    Filter: BLOCKER, CRITICAL, MAJOR, MINOR, INFO"
      echo "  --type, -t TYPE         Filter: BUG, VULNERABILITY, CODE_SMELL"
      echo "  --limit, -l N           Max results (default: 25)"
      echo "  --raw, -r               Output raw JSON"
      echo "  --issue, -i KEY         Issue key (required for 'accept')"
      echo "  --comment, -c TEXT      Rationale comment (required for 'accept')"
      echo "  --help, -h              Show this help"
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

# --- Validate ---
if [[ -z "${SONAR_TOKEN:-}" ]]; then
  echo "Error: SONAR_TOKEN environment variable is not set." >&2
  exit 1
fi

if [[ -z "$COMMAND" ]]; then
  echo "Error: No command provided." >&2
  echo "Usage: sonar.sh <status|issues|metrics|hotspots|accept> [options]" >&2
  exit 1
fi

if [[ -z "$PROJECT" ]]; then
  echo "Error: No project key. Set \$SONAR_PROJECT or pass --project KEY." >&2
  exit 1
fi

# --- API helper ---
api_get() {
  local endpoint="$1"
  curl -s -f -H "Authorization: Bearer $SONAR_TOKEN" "${BASE_URL}${endpoint}"
}

# --- Commands ---

cmd_status() {
  local response
  response=$(api_get "/api/qualitygates/project_status?projectKey=${PROJECT}")

  if [[ "$RAW" == "true" ]]; then
    echo "$response" | jq .
    return
  fi

  local status conditions
  status=$(echo "$response" | jq -r '.projectStatus.status')
  conditions=$(echo "$response" | jq -r '.projectStatus.conditions[]? | "  \(.metricKey): \(.actualValue) (threshold: \(.errorThreshold), status: \(.status))"')

  if [[ "$status" == "OK" ]]; then
    echo "Quality Gate: PASSED"
  elif [[ "$status" == "ERROR" ]]; then
    echo "Quality Gate: FAILED"
  else
    echo "Quality Gate: $status"
  fi

  if [[ -n "$conditions" ]]; then
    echo ""
    echo "Conditions:"
    echo "$conditions"
  fi
}

cmd_issues() {
  local params="componentKeys=${PROJECT}&ps=${LIMIT}"
  [[ -n "$SEVERITY" ]] && params="${params}&severities=${SEVERITY}"
  [[ -n "$TYPE" ]] && params="${params}&types=${TYPE}"

  local response
  response=$(api_get "/api/issues/search?${params}")

  if [[ "$RAW" == "true" ]]; then
    echo "$response" | jq .
    return
  fi

  local total
  total=$(echo "$response" | jq -r '.total')
  echo "Issues: $total total (showing up to $LIMIT)"
  echo ""

  echo "$response" | jq -r '.issues[]? | "[\(.severity)] \(.type) — \(.message)\n  File: \(.component | split(":")[1]? // .component)\n  Line: \(.line // "n/a")  Rule: \(.rule)\n"'
}

# Convert SonarCloud numeric rating to letter grade
rating_letter() {
  case "$1" in
    1|1.0) echo "A" ;;
    2|2.0) echo "B" ;;
    3|3.0) echo "C" ;;
    4|4.0) echo "D" ;;
    5|5.0) echo "E" ;;
    *) echo "$1" ;;
  esac
}

cmd_metrics() {
  local metric_keys="bugs,vulnerabilities,code_smells,coverage,duplicated_lines_density,ncloc,sqale_rating,reliability_rating,security_rating,alert_status"

  local response
  response=$(api_get "/api/measures/component?component=${PROJECT}&metricKeys=${metric_keys}")

  if [[ "$RAW" == "true" ]]; then
    echo "$response" | jq .
    return
  fi

  echo "Metrics for: $PROJECT"
  echo ""

  echo "$response" | jq -r '.component.measures[]? | "\(.metric): \(.value)"' | while IFS=: read -r key val; do
    key=$(echo "$key" | xargs)
    val=$(echo "$val" | xargs)
    case "$key" in
      alert_status)
        echo "  Quality Gate:     $val" ;;
      bugs)
        echo "  Bugs:             $val" ;;
      vulnerabilities)
        echo "  Vulnerabilities:  $val" ;;
      code_smells)
        echo "  Code Smells:      $val" ;;
      coverage)
        echo "  Coverage:         ${val}%" ;;
      duplicated_lines_density)
        echo "  Duplication:      ${val}%" ;;
      ncloc)
        echo "  Lines of Code:    $val" ;;
      sqale_rating)
        echo "  Maintainability:  $(rating_letter "$val")" ;;
      reliability_rating)
        echo "  Reliability:      $(rating_letter "$val")" ;;
      security_rating)
        echo "  Security:         $(rating_letter "$val")" ;;
      *)
        echo "  $key: $val" ;;
    esac
  done
}

cmd_hotspots() {
  local params="projectKey=${PROJECT}&ps=${LIMIT}"

  local response
  response=$(api_get "/api/hotspots/search?${params}")

  if [[ "$RAW" == "true" ]]; then
    echo "$response" | jq .
    return
  fi

  local total
  total=$(echo "$response" | jq -r '.paging.total')
  echo "Security Hotspots: $total total (showing up to $LIMIT)"
  echo ""

  echo "$response" | jq -r '.hotspots[]? | "[\(.vulnerabilityProbability)] \(.message)\n  File: \(.component | split(":")[1]? // .component)\n  Line: \(.line // "n/a")  Status: \(.status)\n"'
}

cmd_accept() {
  if [[ -z "$ISSUE_KEY" ]]; then
    echo "Error: 'accept' requires --issue KEY" >&2
    exit 1
  fi
  if [[ -z "$ACCEPT_COMMENT" ]]; then
    echo "Error: 'accept' requires --comment 'rationale text'" >&2
    exit 1
  fi

  # 1. Transition issue to ACCEPTED.
  #
  # culture-divergence: validate the transition HTTP status (mirroring the
  # add_comment step below). The canonical script POSTs with `-s` and never
  # checks the response, so a failed transition (bad token, wrong state) is
  # silently reported as success with status "UNKNOWN". Offered upstream to
  # guildmaster.
  local transition_resp transition_status
  transition_resp=$(curl -sS -w $'\n%{http_code}' -H "Authorization: Bearer $SONAR_TOKEN" \
    -X POST "${BASE_URL}/api/issues/do_transition" \
    -d "issue=${ISSUE_KEY}&transition=accept")
  transition_status=$(printf '%s\n' "$transition_resp" | tail -n 1)
  transition_resp=$(printf '%s\n' "$transition_resp" | sed '$d')
  if [[ "$transition_status" -lt 200 || "$transition_status" -ge 300 ]]; then
    echo "Error: failed to transition issue $ISSUE_KEY to ACCEPTED (HTTP $transition_status)" >&2
    echo "Response body:" >&2
    printf '%s\n' "$transition_resp" >&2
    exit 1
  fi

  local status
  status=$(echo "$transition_resp" | jq -r '.issue.issueStatus // "UNKNOWN"')

  # 2. Attach rationale comment (URL-encode via jq).
  local encoded comment_resp comment_status
  encoded=$(jq -rn --arg s "$ACCEPT_COMMENT" '$s|@uri')
  comment_resp=$(curl -sS -w $'\n%{http_code}' -H "Authorization: Bearer $SONAR_TOKEN" \
    -X POST "${BASE_URL}/api/issues/add_comment" \
    -d "issue=${ISSUE_KEY}&text=${encoded}")
  comment_status=$(printf '%s\n' "$comment_resp" | tail -n 1)
  if [[ "$comment_status" -lt 200 || "$comment_status" -ge 300 ]]; then
    echo "Error: failed to attach rationale comment (HTTP $comment_status)" >&2
    echo "Response body:" >&2
    printf '%s\n' "$comment_resp" | sed '$d' >&2
    exit 1
  fi

  if [[ "$RAW" == "true" ]]; then
    echo "$transition_resp" | jq .
  else
    echo "Issue $ISSUE_KEY -> $status"
  fi
}

# --- Dispatch ---
case "$COMMAND" in
  status)   cmd_status   ;;
  issues)   cmd_issues   ;;
  metrics)  cmd_metrics  ;;
  hotspots) cmd_hotspots ;;
  accept)   cmd_accept   ;;
  *)        echo "Unknown command: $COMMAND" >&2; exit 1 ;;
esac
