#!/bin/zsh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="PDF Mail App.app"
DIST_DIR="${ROOT_DIR}/dist"
APP_BUNDLE="${DIST_DIR}/${APP_NAME}"
ZIP_PATH="${DIST_DIR}/PDF Mail App.zip"
CONTENTS_DIR="${APP_BUNDLE}/Contents"
MACOS_DIR="${CONTENTS_DIR}/MacOS"
RESOURCES_DIR="${CONTENTS_DIR}/Resources"
APP_RESOURCES_DIR="${RESOURCES_DIR}/app"

rm -rf "${APP_BUNDLE}"
rm -f "${ZIP_PATH}"
mkdir -p "${MACOS_DIR}" "${APP_RESOURCES_DIR}"

cp "${ROOT_DIR}/macos/Info.plist" "${CONTENTS_DIR}/Info.plist"
printf 'APPL????' > "${CONTENTS_DIR}/PkgInfo"
cp "${ROOT_DIR}/macos/launcher.sh" "${MACOS_DIR}/PDF Mail App"
chmod +x "${MACOS_DIR}/PDF Mail App"

cp -R "${ROOT_DIR}/scripts" "${APP_RESOURCES_DIR}/"
cp -R "${ROOT_DIR}/ui" "${APP_RESOURCES_DIR}/"
cp "${ROOT_DIR}/README.md" "${APP_RESOURCES_DIR}/README.md"

rm -rf "${APP_RESOURCES_DIR}/scripts/__pycache__" "${APP_RESOURCES_DIR}/ui/__pycache__"

echo "Built ${APP_BUNDLE}"
/usr/bin/xattr -cr "${APP_BUNDLE}" 2>/dev/null || true
(
  cd "${DIST_DIR}"
  COPYFILE_DISABLE=1 /usr/bin/zip -qry -X "${ZIP_PATH:t}" "${APP_NAME}"
)
echo "Built ${ZIP_PATH}"
