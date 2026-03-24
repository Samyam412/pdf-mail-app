#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path


PAGES_OBJECT_RE = re.compile(
    r"\b\d+\s+\d+\s+obj\b(.*?)\bendobj\b",
    re.DOTALL,
)
COUNT_RE = re.compile(r"/Count\s+(\d+)\b")
PAGE_TOKEN_RE = re.compile(r"/Type\s*/Page\b")


def get_page_count(pdf_path: Path) -> int:
    text = pdf_path.read_bytes().decode("latin-1", errors="ignore")

    for match in PAGES_OBJECT_RE.finditer(text):
        obj = match.group(1)
        if "/Type /Pages" not in obj:
            continue

        count_match = COUNT_RE.search(obj)
        if count_match:
            return int(count_match.group(1))

    page_matches = PAGE_TOKEN_RE.findall(text)
    if page_matches:
        return len(page_matches)

    raise ValueError("Could not determine page count")


def unique_destination(destination: Path) -> Path:
    if not destination.exists():
        return destination

    stem = destination.stem
    suffix = destination.suffix
    parent = destination.parent
    counter = 1

    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def move_pdf(pdf_path: Path, destination_dir: Path, dry_run: bool) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = unique_destination(destination_dir / pdf_path.name)

    if not dry_run:
        shutil.move(str(pdf_path), str(destination))

    return destination


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Move 2-page and 3-page PDFs into separate folders."
    )
    parser.add_argument(
        "source",
        nargs="?",
        default=".",
        help="Folder that contains the PDF files. Defaults to the current folder.",
    )
    parser.add_argument(
        "--two-page-dir",
        default="2_page_pdfs",
        help="Destination folder for 2-page PDFs.",
    )
    parser.add_argument(
        "--three-page-dir",
        default="3_page_pdfs",
        help="Destination folder for 3-page PDFs.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be moved without changing any files.",
    )
    args = parser.parse_args()

    source_dir = Path(args.source).expanduser().resolve()
    if not source_dir.is_dir():
        print(f"Source folder does not exist: {source_dir}", file=sys.stderr)
        return 1

    two_page_dir = source_dir / args.two_page_dir
    three_page_dir = source_dir / args.three_page_dir

    pdf_files = sorted(
        path
        for path in source_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".pdf"
    )

    moved_two_page = 0
    moved_three_page = 0
    skipped = 0
    failed = 0

    for pdf_path in pdf_files:
        try:
            page_count = get_page_count(pdf_path)
        except Exception as exc:
            failed += 1
            print(f"FAILED  {pdf_path.name}: {exc}")
            continue

        if page_count == 2:
            destination = move_pdf(pdf_path, two_page_dir, args.dry_run)
            moved_two_page += 1
            print(f"2-PAGE  {pdf_path.name} -> {destination.name}")
        elif page_count == 3:
            destination = move_pdf(pdf_path, three_page_dir, args.dry_run)
            moved_three_page += 1
            print(f"3-PAGE  {pdf_path.name} -> {destination.name}")
        else:
            skipped += 1

    print()
    print(f"Scanned: {len(pdf_files)} PDF files")
    print(f"Moved to {two_page_dir.name}: {moved_two_page}")
    print(f"Moved to {three_page_dir.name}: {moved_three_page}")
    print(f"Skipped (not 2 or 3 pages): {skipped}")
    print(f"Failed: {failed}")

    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
