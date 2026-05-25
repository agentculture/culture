#!/usr/bin/env bash
# switch-source.sh — Switch a PyPI package install between pypi / test-pypi / local.
#
# Usage:
#   switch-source.sh <package> pypi
#   switch-source.sh <package> test-pypi [--version VERSION]
#   switch-source.sh <package> local [--path PATH]
#
# Generalised from the original culture-specific change-package skill so any
# AgentCulture sibling that publishes a PyPI package can vendor and use it.

set -euo pipefail

PACKAGE=""
SOURCE=""
VERSION=""
LOCAL_PATH=""

usage() {
  cat <<EOF >&2
Usage: switch-source.sh <package> <pypi|test-pypi|local> [options]

Options:
  --version VERSION   Pin to a specific version (most useful for test-pypi
                      dev builds, e.g. --version 0.4.0.dev42).
  --path PATH         Local source only: path to editable checkout
                      (default: current directory).
  -h, --help          Show this help.
EOF
}

# --- Parse args ---
require_value() {
  local flag="$1" remaining="$2"
  if [[ "$remaining" -lt 2 ]]; then
    echo "Error: $flag requires a value" >&2
    usage
    exit 1
  fi
}

POSITIONAL=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) require_value "$1" "$#"; VERSION="$2"; shift 2 ;;
    --path)    require_value "$1" "$#"; LOCAL_PATH="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    --*)       echo "Unknown option: $1" >&2; usage; exit 1 ;;
    *)         POSITIONAL+=("$1"); shift ;;
  esac
done

if [[ ${#POSITIONAL[@]} -lt 2 ]]; then
  usage
  exit 1
fi

PACKAGE="${POSITIONAL[0]}"
SOURCE="${POSITIONAL[1]}"

# --- Resolve install spec for pinned versions ---
SPEC="$PACKAGE"
if [[ -n "$VERSION" ]]; then
  SPEC="${PACKAGE}==${VERSION}"
fi

case "$SOURCE" in
  pypi)
    echo "Installing $SPEC from production PyPI..."
    uv tool install "$SPEC" --force
    ;;
  test-pypi)
    echo "Installing $SPEC from TestPyPI..."
    uv tool install "$SPEC" \
      --index-url https://test.pypi.org/simple/ \
      --extra-index-url https://pypi.org/simple/ \
      --index-strategy unsafe-best-match \
      --prerelease=allow \
      --force
    ;;
  local)
    if [[ -z "$LOCAL_PATH" ]]; then
      LOCAL_PATH="$(pwd)"
    fi
    if [[ ! -f "$LOCAL_PATH/pyproject.toml" ]]; then
      echo "Error: $LOCAL_PATH does not contain a pyproject.toml" >&2
      exit 1
    fi
    echo "Installing $PACKAGE in editable mode from $LOCAL_PATH..."
    uv tool install --from "$LOCAL_PATH" --editable "$PACKAGE" --force
    ;;
  *)
    echo "Unknown source: $SOURCE (expected pypi, test-pypi, or local)" >&2
    usage
    exit 1
    ;;
esac

# --- Report what is now active ---
echo
echo "Installed tools matching '$PACKAGE':"
uv tool list 2>/dev/null | awk -v pkg="$PACKAGE" 'tolower($1) == tolower(pkg) {print "  " $0}'
