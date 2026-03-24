# PDF Mail App

Standalone macOS app project for preparing mail PDFs in a browser UI.

## What it includes

- Browser UI for folder and single-PDF workflows
- PDF stamping
- Optional blank-page insertion
- First-page preview with coordinate picking

## Requirements

- macOS
- Python 3
- Swift command line tools available as `swift`

This app uses macOS-native PDF tooling, so it is not intended to run on Linux hosting platforms as-is.

## Run

```bash
cd /path/to/pdf-mail-app
python3 ui/pdf_tool_ui_server.py --host 127.0.0.1 --port 8765
```

Then open `http://127.0.0.1:8765`.

## Build The macOS App

```bash
cd /path/to/pdf-mail-app
./tools/build_macos_app.sh
```

This creates `dist/PDF Mail App.app`, which you can open directly in Finder.
It also creates `dist/PDF Mail App.zip` for a single-file download.

## Project layout

- `ui/pdf_tool_ui_server.py`: browser server and API
- `ui/pdf_mail_core.py`: processing pipeline
- `ui/static/`: frontend
- `scripts/`: PDF processing helpers
- `macos/`: app bundle metadata and launcher
- `tools/build_macos_app.sh`: bundle builder
