#!/bin/zsh

set -u
unsetopt BGNICE 2>/dev/null || true

APP_NAME="PDF Mail App"
PORT="${PDF_MAIL_APP_PORT:-8765}"
URL="http://127.0.0.1:${PORT}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONTENTS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
RESOURCES_DIR="${CONTENTS_DIR}/Resources"
APP_ROOT="${RESOURCES_DIR}/app"
SUPPORT_DIR="${HOME}/Library/Application Support/PDF Mail App"
LOG_DIR="${HOME}/Library/Logs/PDF Mail App"
SUPPORT_DIR="${PDF_MAIL_APP_SUPPORT_DIR:-${SUPPORT_DIR}}"
LOG_DIR="${PDF_MAIL_APP_LOG_DIR:-${LOG_DIR}}"
PID_FILE="${SUPPORT_DIR}/server.pid"
LOG_FILE="${LOG_DIR}/server.log"
NO_OPEN="${PDF_MAIL_APP_NO_OPEN:-0}"

mkdir -p "${SUPPORT_DIR}" "${LOG_DIR}"

open_app_url() {
  if [[ "${NO_OPEN}" == "1" ]]; then
    return 0
  fi
  /usr/bin/open "${URL}"
}

show_error() {
  local message="$1"
  if [[ "${NO_OPEN}" == "1" ]]; then
    print -u2 -- "${APP_NAME}: ${message}"
    return 0
  fi
  /usr/bin/osascript \
    -e 'on run argv' \
    -e 'display alert (item 1 of argv) message (item 2 of argv) as critical' \
    -e 'end run' \
    -- "${APP_NAME}" "${message}"
}

find_python() {
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  return 1
}

server_ready() {
  /usr/bin/curl -fsS "${URL}/api/config" >/dev/null 2>&1
}

cleanup_stale_pid() {
  if [[ -f "${PID_FILE}" ]]; then
    local pid
    pid="$(cat "${PID_FILE}")"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1; then
      return 0
    fi
    rm -f "${PID_FILE}"
  fi
}

start_server() {
  local python_bin="$1"
  local user_root="${HOME}/Downloads"
  if [[ ! -d "${user_root}" ]]; then
    user_root="${HOME}"
  fi

  export PDF_MAIL_USER_ROOT="${user_root}"
  nohup "${python_bin}" "${APP_ROOT}/ui/pdf_tool_ui_server.py" --host 127.0.0.1 --port "${PORT}" >>"${LOG_FILE}" 2>&1 &
  echo $! > "${PID_FILE}"
}

main() {
  local python_bin
  cleanup_stale_pid

  if server_ready; then
    open_app_url
    exit 0
  fi

  if ! python_bin="$(find_python)"; then
    show_error "python3 was not found. Install Python 3, then open ${APP_NAME} again."
    exit 1
  fi

  start_server "${python_bin}"

  local attempt
  for attempt in {1..50}; do
    if server_ready; then
      open_app_url
      exit 0
    fi
    sleep 0.2
  done

  show_error "The local server did not start. Check ${LOG_FILE} for details."
  exit 1
}

main "$@"
