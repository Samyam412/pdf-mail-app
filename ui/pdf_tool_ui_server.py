#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import posixpath
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from pdf_mail_core import (
    DEFAULT_PERMIT_TEXT,
    ProcessingError,
    PDFMailProcessor,
    ROOT_DIR,
    build_job_config,
    resolve_default_output,
)


STATIC_DIR = ROOT_DIR / "ui" / "static"


@dataclass
class Job:
    id: str
    mode: str
    source_path: str
    output_path: str
    status: str = "queued"
    logs: list[str] = field(default_factory=list)
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


def json_response(handler: SimpleHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    encoded = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.end_headers()
    handler.wfile.write(encoded)


def binary_response(
    handler: SimpleHTTPRequestHandler,
    payload: bytes,
    content_type: str,
    status: int = 200,
) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(payload)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(payload)


def read_json_body(handler: SimpleHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def resolve_user_path(raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = ROOT_DIR / candidate
    return candidate.resolve()


def list_directory(raw_path: str | None) -> dict[str, Any]:
    base = resolve_user_path(raw_path or str(ROOT_DIR))
    if not base.exists():
        raise FileNotFoundError(f"Path does not exist: {base}")
    if not base.is_dir():
        raise NotADirectoryError(f"Not a directory: {base}")

    entries = []
    for entry in sorted(base.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        if entry.name.startswith(".") and entry.name not in {".", ".."}:
            continue
        entry_type = "directory" if entry.is_dir() else "file"
        entries.append(
            {
                "name": entry.name,
                "path": str(entry),
                "type": entry_type,
                "size": entry.stat().st_size if entry.is_file() else None,
            }
        )

    return {
        "path": str(base),
        "parent": str(base.parent) if base != base.parent else None,
        "entries": entries,
    }


def app_config() -> dict[str, Any]:
    return {
        "root": str(ROOT_DIR),
        "defaults": {
            "mode": "folder",
            "box_x": 450,
            "box_top_y": 756,
            "insert_blanks": False,
            "blank_interval": 3,
            "single_pdf_step": 3,
            "permit_text": DEFAULT_PERMIT_TEXT,
        },
    }


def apple_script_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def run_osascript(lines: list[str]) -> str:
    command = ["osascript"]
    for line in lines:
        command.extend(["-e", line])

    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        if "User canceled" in stderr:
            raise ProcessingError("Picker cancelled") from exc
        raise ProcessingError(stderr or "Picker failed") from exc

    return result.stdout.strip()


def pick_folder(initial_path: Path | None = None) -> str:
    lines = []
    if initial_path is not None:
        lines.append(f"set defaultLocation to POSIX file {apple_script_string(str(initial_path))}")
        lines.append('set chosenFolder to choose folder with prompt "Choose source folder" default location defaultLocation')
    else:
        lines.append('set chosenFolder to choose folder with prompt "Choose source folder"')
    lines.append("POSIX path of chosenFolder")
    return run_osascript(lines)


def pick_pdf(initial_path: Path | None = None) -> str:
    lines = []
    if initial_path is not None:
        lines.append(f"set defaultLocation to POSIX file {apple_script_string(str(initial_path))}")
        lines.append('set chosenFile to choose file with prompt "Choose source PDF" of type {"pdf"} default location defaultLocation')
    else:
        lines.append('set chosenFile to choose file with prompt "Choose source PDF" of type {"pdf"}')
    lines.append("POSIX path of chosenFile")
    return run_osascript(lines)


def pick_output_pdf(suggested_path: Path | None = None) -> str:
    lines = []
    if suggested_path is not None:
        parent = suggested_path.parent
        name = suggested_path.name
        lines.append(f"set defaultLocation to POSIX file {apple_script_string(str(parent))}")
        lines.append(
            f'set chosenFile to choose file name with prompt "Save output PDF" default location defaultLocation default name {apple_script_string(name)}'
        )
    else:
        lines.append('set chosenFile to choose file name with prompt "Save output PDF"')
    lines.append("POSIX path of chosenFile")
    return run_osascript(lines)


def resolve_preview_pdf(mode: str, source_path: Path) -> Path:
    if mode == "folder":
        pdf_files = sorted(
            path
            for path in source_path.iterdir()
            if path.is_file() and path.suffix.lower() == ".pdf"
        )
        if not pdf_files:
            raise ProcessingError(f"No PDF files found in {source_path}")
        return pdf_files[0]
    if source_path.is_file() and source_path.suffix.lower() == ".pdf":
        return source_path
    raise ProcessingError("Preview requires a valid PDF source")


def render_pdf_preview_png(pdf_path: Path) -> bytes:
    swift_source = r"""
import AppKit
import Foundation
import PDFKit

let inputPath = CommandLine.arguments[1]
let inputURL = URL(fileURLWithPath: inputPath)

guard let document = PDFDocument(url: inputURL), let page = document.page(at: 0) else {
    fputs("Could not open PDF preview source\n", stderr)
    exit(1)
}

let bounds = page.bounds(for: .mediaBox)
let targetSize = NSSize(width: ceil(bounds.width), height: ceil(bounds.height))
let image = page.thumbnail(of: targetSize, for: .mediaBox)

guard
    let tiff = image.tiffRepresentation,
    let bitmap = NSBitmapImageRep(data: tiff),
    let pngData = bitmap.representation(using: .png, properties: [:])
else {
    fputs("Could not render PDF preview\n", stderr)
    exit(1)
}

FileHandle.standardOutput.write(pngData)
"""

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".swift",
        prefix="pdf_preview_",
        dir="/tmp",
        delete=False,
    ) as swift_file:
        swift_path = Path(swift_file.name)
        swift_file.write(swift_source)

    module_cache = Path("/tmp/swift-module-cache")
    module_cache.mkdir(parents=True, exist_ok=True)

    command = [
        "swift",
        "-module-cache-path",
        str(module_cache),
        str(swift_path),
        str(pdf_path),
    ]

    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace").strip()
        raise ProcessingError(stderr or "Could not render PDF preview") from exc
    finally:
        swift_path.unlink(missing_ok=True)

    return result.stdout


def record_log(job_id: str, message: str) -> None:
    if not message:
        return
    with JOBS_LOCK:
        job = JOBS[job_id]
        job.logs.append(message)


def run_job(job_id: str, payload: dict[str, Any]) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        job.status = "running"
        job.started_at = time.time()

    try:
        config = build_job_config(
            mode=str(payload["mode"]),
            source_path=resolve_user_path(str(payload["source_path"])),
            permit_text=str(payload["permit_text"]),
            box_x=float(payload["box_x"]),
            box_top_y=float(payload["box_top_y"]),
            insert_blanks=bool(payload.get("insert_blanks", False)),
            blank_interval=(
                int(payload["blank_interval"])
                if str(payload.get("blank_interval", "")).strip()
                else None
            ),
            output_path=resolve_user_path(str(payload["output_path"])),
            single_pdf_step=(
                int(payload["single_pdf_step"])
                if str(payload.get("single_pdf_step", "")).strip()
                else None
            ),
            whiteout_rectangles=list(payload.get("whiteout_rectangles", [])),
        )
        processor = PDFMailProcessor(lambda message: record_log(job_id, message))
        processor.process(config)
    except Exception as exc:
        with JOBS_LOCK:
            job = JOBS[job_id]
            job.status = "failed"
            job.finished_at = time.time()
            job.error = str(exc)
            job.logs.append(str(exc))
        return

    with JOBS_LOCK:
        job = JOBS[job_id]
        job.status = "completed"
        job.finished_at = time.time()


def create_job(payload: dict[str, Any]) -> Job:
    source_path = resolve_user_path(str(payload["source_path"]))
    output_path = resolve_user_path(str(payload["output_path"]))
    job = Job(
        id=uuid.uuid4().hex[:12],
        mode=str(payload["mode"]),
        source_path=str(source_path),
        output_path=str(output_path),
    )
    with JOBS_LOCK:
        JOBS[job.id] = job
    thread = threading.Thread(target=run_job, args=(job.id, payload), daemon=True)
    thread.start()
    return job


class PDFToolHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        parsed_path = urlparse(path).path
        cleaned = posixpath.normpath(parsed_path)
        relative = cleaned.lstrip("/")
        if relative in {"", "index.html"}:
            return str(STATIC_DIR / "index.html")
        return str(STATIC_DIR / relative)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/config":
            json_response(self, app_config())
            return
        if parsed.path == "/api/list":
            params = parse_qs(parsed.query)
            target = params.get("path", [str(ROOT_DIR)])[0]
            try:
                payload = list_directory(target)
            except Exception as exc:
                json_response(self, {"error": str(exc)}, status=400)
                return
            json_response(self, payload)
            return
        if parsed.path == "/api/suggest-output":
            params = parse_qs(parsed.query)
            mode = params.get("mode", ["folder"])[0]
            source = params.get("source", [""])[0]
            try:
                source_path = resolve_user_path(source)
                suggested = resolve_default_output(mode, source_path)
            except Exception as exc:
                json_response(self, {"error": str(exc)}, status=400)
                return
            json_response(self, {"output_path": str(suggested)})
            return
        if parsed.path == "/api/pick-source-folder":
            params = parse_qs(parsed.query)
            initial = params.get("initial_path", [""])[0]
            initial_path = resolve_user_path(initial) if initial else ROOT_DIR
            try:
                chosen = pick_folder(initial_path)
            except Exception as exc:
                json_response(self, {"error": str(exc)}, status=400)
                return
            json_response(self, {"path": chosen})
            return
        if parsed.path == "/api/pick-source-pdf":
            params = parse_qs(parsed.query)
            initial = params.get("initial_path", [""])[0]
            initial_path = resolve_user_path(initial) if initial else ROOT_DIR
            if initial_path.is_file():
                initial_path = initial_path.parent
            try:
                chosen = pick_pdf(initial_path)
            except Exception as exc:
                json_response(self, {"error": str(exc)}, status=400)
                return
            json_response(self, {"path": chosen})
            return
        if parsed.path == "/api/pick-output-pdf":
            params = parse_qs(parsed.query)
            suggested = params.get("suggested_path", [""])[0]
            suggested_path = resolve_user_path(suggested) if suggested else None
            try:
                chosen = pick_output_pdf(suggested_path)
            except Exception as exc:
                json_response(self, {"error": str(exc)}, status=400)
                return
            json_response(self, {"path": chosen})
            return
        if parsed.path == "/api/preview-first-page":
            params = parse_qs(parsed.query)
            mode = params.get("mode", ["folder"])[0]
            source = params.get("source", [""])[0]
            if not source:
                json_response(self, {"error": "Missing source path"}, status=400)
                return
            try:
                source_path = resolve_user_path(source)
                preview_pdf = resolve_preview_pdf(mode, source_path)
                png_bytes = render_pdf_preview_png(preview_pdf)
            except Exception as exc:
                json_response(self, {"error": str(exc)}, status=400)
                return
            binary_response(self, png_bytes, "image/png")
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if job is None:
                json_response(self, {"error": "Job not found"}, status=404)
                return
            json_response(
                self,
                {
                    "id": job.id,
                    "mode": job.mode,
                    "source_path": job.source_path,
                    "output_path": job.output_path,
                    "status": job.status,
                    "logs": job.logs,
                    "started_at": job.started_at,
                    "finished_at": job.finished_at,
                    "error": job.error,
                },
            )
            return
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/run":
            json_response(self, {"error": "Not found"}, status=404)
            return

        try:
            payload = read_json_body(self)
            required_fields = {
                "mode",
                "source_path",
                "permit_text",
                "box_x",
                "box_top_y",
                "insert_blanks",
                "output_path",
                "single_pdf_step",
            }
            missing = sorted(field for field in required_fields if field not in payload)
            if missing:
                raise ProcessingError(f"Missing fields: {', '.join(missing)}")
            job = create_job(payload)
        except Exception as exc:
            json_response(self, {"error": str(exc)}, status=400)
            return

        json_response(
            self,
            {
                "job_id": job.id,
                "status": job.status,
                "output_path": job.output_path,
            },
            status=HTTPStatus.ACCEPTED,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Local browser UI for the PDF mail workflow."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind. Default: 8765")
    args = parser.parse_args()

    if not STATIC_DIR.exists():
        raise SystemExit(f"Static UI directory not found: {STATIC_DIR}")

    server = ThreadingHTTPServer((args.host, args.port), PDFToolHandler)
    print(f"PDF Mail UI running at http://{args.host}:{args.port}")
    print(f"Root folder: {ROOT_DIR}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
