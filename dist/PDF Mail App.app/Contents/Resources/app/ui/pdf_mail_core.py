from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT_DIR / "scripts"
MERGE_BIN = Path("/System/Library/Automator/Combine PDF Pages.action/Contents/MacOS/join")
MERGE_BATCH_SIZE = 200
STAMP_BATCH_FILE_COUNT = 500

sys.path.insert(0, str(SCRIPTS_DIR))
from sort_pdfs_by_pages import get_page_count  # noqa: E402
from stamp_text_every_n_pages import parse_page_list  # noqa: E402


DEFAULT_PERMIT_TEXT = "\n".join(
    [
        "First Class",
        "US Postage Paid",
        "Metairie, LA",
        "Permit #184",
    ]
)

STAMP_DEFAULTS = {
    "font_name": "Arial",
    "font_size": 6.5,
    "tracking": 0.1,
    "line_spacing_adjust": -0.8,
    "box_padding_x": 2,
    "box_padding_y": 0,
    "box_border_width": 0.5,
    "box_border_color": "0,0,0",
    "box_fill_color": "255,255,255,1",
    "color": "0,0,0",
}


class ProcessingError(Exception):
    pass


@dataclass
class WhiteoutRect:
    x: float
    top_y: float
    width: float
    height: float


@dataclass
class JobConfig:
    mode: str
    source_path: Path
    permit_text: str
    box_x: float
    box_top_y: float
    insert_blanks: bool
    blank_interval: int | None
    output_path: Path
    stamp_pages: list[int]
    whiteout_rectangles: list[WhiteoutRect]


class PDFMailProcessor:
    def __init__(self, logger):
        self.logger = logger

    def process(self, config: JobConfig) -> Path:
        with tempfile.TemporaryDirectory(prefix="pdf_mail_app_", dir="/tmp") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            if config.mode == "folder":
                base_input = self.process_folder_batches(config, temp_dir)
            else:
                base_input = config.source_path
                stamped_pages = config.stamp_pages

            final_source = base_input
            if config.mode != "folder":
                stamped_path = temp_dir / "stamped.pdf"
                self.stamp_pdf(
                    input_pdf=base_input,
                    output_pdf=stamped_path,
                    permit_text=config.permit_text,
                    box_x=config.box_x,
                    box_top_y=config.box_top_y,
                    page_list=stamped_pages,
                    whiteout_rectangles=config.whiteout_rectangles,
                )
                final_source = stamped_path

            if config.insert_blanks and config.blank_interval:
                blanked_path = temp_dir / "blanked.pdf"
                self.insert_blank_pages(
                    input_pdf=final_source,
                    output_pdf=blanked_path,
                    interval=config.blank_interval,
                )
                final_source = blanked_path

            config.output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(final_source, config.output_path)
            self.logger(f"Saved output to {config.output_path}")
            return config.output_path

    def process_folder_batches(self, config: JobConfig, temp_dir: Path) -> Path:
        pdf_files = self.collect_folder_pdfs(
            config.source_path,
            exclude_path=config.output_path,
        )
        batches = [
            pdf_files[index : index + STAMP_BATCH_FILE_COUNT]
            for index in range(0, len(pdf_files), STAMP_BATCH_FILE_COUNT)
        ]
        self.logger(
            f"Processing folder in {len(batches)} stamping batch(es)"
        )

        stamped_batches: list[Path] = []
        for batch_number, batch_files in enumerate(batches, start=1):
            batch_dir = temp_dir / f"stamp_batch_{batch_number}"
            batch_dir.mkdir(parents=True, exist_ok=True)
            first_pages = self.compute_first_pages(batch_files)
            merged_path = batch_dir / "merged.pdf"
            stamped_path = batch_dir / "stamped.pdf"
            self.logger(
                f"Processing stamp batch {batch_number}/{len(batches)} with {len(batch_files)} PDF files"
            )
            self.merge_pdfs(batch_files, merged_path, batch_dir / "merge_work")
            self.stamp_pdf(
                input_pdf=merged_path,
                output_pdf=stamped_path,
                permit_text=config.permit_text,
                box_x=config.box_x,
                box_top_y=config.box_top_y,
                page_list=first_pages,
                whiteout_rectangles=config.whiteout_rectangles,
            )
            stamped_batches.append(stamped_path)

        final_merged_path = temp_dir / "folder_mode_stamped.pdf"
        if len(stamped_batches) == 1:
            shutil.copyfile(stamped_batches[0], final_merged_path)
            self.logger("Single stamp batch completed; no final merge needed")
        else:
            self.logger("Merging stamped batches into final PDF")
            self.merge_pdfs(stamped_batches, final_merged_path, temp_dir / "final_merge")
        return final_merged_path

    def collect_folder_pdfs(self, folder: Path, exclude_path: Path | None = None) -> list[Path]:
        pdf_files = sorted(
            path
            for path in folder.iterdir()
            if path.is_file()
            and path.suffix.lower() == ".pdf"
            and (exclude_path is None or path.resolve() != exclude_path.resolve())
        )
        if not pdf_files:
            raise ProcessingError(f"No PDF files found in {folder}")
        self.logger(f"Found {len(pdf_files)} PDF files in {folder}")
        return pdf_files

    def compute_first_pages(self, pdf_files: list[Path]) -> list[int]:
        first_pages: list[int] = []
        next_page = 1
        for pdf_path in pdf_files:
            page_count = get_page_count(pdf_path)
            first_pages.append(next_page)
            next_page += page_count
        self.logger(
            f"Stamping first page of each merged source PDF: {','.join(map(str, first_pages))}"
        )
        return first_pages

    def merge_pdfs(self, pdf_files: list[Path], output_path: Path, temp_dir: Path) -> None:
        if not MERGE_BIN.exists():
            raise ProcessingError(f"PDF merge tool not found: {MERGE_BIN}")
        temp_dir.mkdir(parents=True, exist_ok=True)

        self.logger("Merging PDFs")
        current_files = list(pdf_files)
        round_number = 1
        while len(current_files) > MERGE_BATCH_SIZE:
            self.logger(f"Merge round {round_number}: {len(current_files)} files")
            batch_outputs: list[Path] = []
            for batch_index, start in enumerate(
                range(0, len(current_files), MERGE_BATCH_SIZE),
                start=1,
            ):
                chunk = current_files[start : start + MERGE_BATCH_SIZE]
                chunk_output = temp_dir / f"merge_round_{round_number}_chunk_{batch_index}.pdf"
                self.run_command(
                    [str(MERGE_BIN), "-o", str(chunk_output), *[str(path) for path in chunk]],
                    f"Merging chunk {batch_index} with {len(chunk)} files",
                )
                batch_outputs.append(chunk_output)
            current_files = batch_outputs
            round_number += 1

        self.run_command(
            [str(MERGE_BIN), "-o", str(output_path), *[str(path) for path in current_files]],
            f"Writing merged PDF to {output_path}",
        )

    def stamp_pdf(
        self,
        input_pdf: Path,
        output_pdf: Path,
        permit_text: str,
        box_x: float,
        box_top_y: float,
        page_list: list[int],
        whiteout_rectangles: list[WhiteoutRect],
    ) -> None:
        page_list_arg = ",".join(str(page) for page in page_list)
        whiteout_rectangles_arg = json.dumps(
            [
                {
                    "x": rectangle.x,
                    "top_y": rectangle.top_y,
                    "width": rectangle.width,
                    "height": rectangle.height,
                }
                for rectangle in whiteout_rectangles
            ]
        )
        command = [
            "python3",
            "-u",
            str(SCRIPTS_DIR / "stamp_text_every_n_pages.py"),
            str(input_pdf),
            "--output",
            str(output_pdf),
            "--text",
            permit_text,
            "--page-list",
            page_list_arg,
            "--font-size",
            str(STAMP_DEFAULTS["font_size"]),
            "--font-name",
            str(STAMP_DEFAULTS["font_name"]),
            "--tracking",
            str(STAMP_DEFAULTS["tracking"]),
            "--line-spacing-adjust",
            str(STAMP_DEFAULTS["line_spacing_adjust"]),
            "--color",
            str(STAMP_DEFAULTS["color"]),
            "--draw-box",
            "--box-padding-x",
            str(STAMP_DEFAULTS["box_padding_x"]),
            "--box-padding-y",
            str(STAMP_DEFAULTS["box_padding_y"]),
            "--box-border-color",
            str(STAMP_DEFAULTS["box_border_color"]),
            "--box-fill-color",
            str(STAMP_DEFAULTS["box_fill_color"]),
            "--box-border-width",
            str(STAMP_DEFAULTS["box_border_width"]),
            "--box-x",
            str(box_x),
            "--box-top-y",
            str(box_top_y),
            "--cover-rectangles-json",
            whiteout_rectangles_arg,
        ]
        self.run_command(command, f"Stamping permit box on pages {page_list_arg}")

    def insert_blank_pages(self, input_pdf: Path, output_pdf: Path, interval: int) -> None:
        command = [
            "python3",
            "-u",
            str(SCRIPTS_DIR / "insert_blank_pages.py"),
            str(input_pdf),
            "--output",
            str(output_pdf),
            "--interval",
            str(interval),
        ]
        self.run_command(command, f"Inserting blank pages every {interval} pages")

    def run_command(self, command: list[str], description: str) -> None:
        self.logger(description)
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            line = line.rstrip()
            if line:
                self.logger(line)
        return_code = process.wait()
        if return_code != 0:
            raise ProcessingError(
                f"Command failed with exit code {return_code}: {' '.join(command)}"
            )


def get_pdf_page_count(path: Path) -> int:
    swift_source = r"""
import Foundation
import PDFKit

let path = CommandLine.arguments[1]
let url = URL(fileURLWithPath: path)
guard let doc = PDFDocument(url: url) else {
    fputs("Could not open PDF: \(path)\n", stderr)
    exit(1)
}
print(doc.pageCount)
"""
    module_cache = Path("/tmp/swift-module-cache")
    module_cache.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".swift",
        prefix="pdf_page_count_",
        dir="/tmp",
        delete=False,
    ) as swift_file:
        swift_path = Path(swift_file.name)
        swift_file.write(swift_source)

    command = [
        "swift",
        "-module-cache-path",
        str(module_cache),
        str(swift_path),
        str(path),
    ]

    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        swift_path.unlink(missing_ok=True)

    return int(result.stdout.strip())


def resolve_default_output(mode: str, source_path: Path) -> Path:
    if mode == "folder":
        folder_name = source_path.name or "output"
        return source_path / f"{folder_name}_processed.pdf"
    return source_path.with_name(f"{source_path.stem}_processed.pdf")


def build_job_config(
    *,
    mode: str,
    source_path: Path,
    permit_text: str,
    box_x: float,
    box_top_y: float,
    insert_blanks: bool,
    blank_interval: int | None,
    output_path: Path,
    single_pdf_step: int | None,
    whiteout_rectangles: list[dict[str, object]] | None,
) -> JobConfig:
    if mode not in {"folder", "single_pdf"}:
        raise ProcessingError(f"Unsupported mode: {mode}")
    if not permit_text.strip():
        raise ProcessingError("Permit text cannot be empty")

    if mode == "folder":
        if not source_path.is_dir():
            raise ProcessingError("Folder mode requires a valid folder")
        stamp_pages: list[int] = []
    else:
        if not source_path.is_file() or source_path.suffix.lower() != ".pdf":
            raise ProcessingError("Single PDF mode requires a valid PDF file")
        if single_pdf_step is None:
            raise ProcessingError("Stamp every N pages is required for single PDF mode")
        if single_pdf_step <= 0:
            raise ProcessingError("Stamp every N pages must be greater than 0")
        page_count = get_pdf_page_count(source_path)
        stamp_pages = list(range(1, page_count + 1, single_pdf_step))

    if insert_blanks:
        if blank_interval is None:
            raise ProcessingError("Blank interval is required when blank pages are enabled")
        if blank_interval <= 0:
            raise ProcessingError("Blank interval must be greater than 0")
    else:
        blank_interval = None

    if mode == "single_pdf" and output_path == source_path:
        raise ProcessingError("Output path must be different from the input PDF")

    parsed_rectangles: list[WhiteoutRect] = []
    for rectangle in whiteout_rectangles or []:
        try:
            x = float(rectangle["x"])
            top_y = float(rectangle["top_y"])
            width = float(rectangle["width"])
            height = float(rectangle["height"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProcessingError("Each white rectangle must include numeric x, top_y, width, and height") from exc
        if width <= 0 or height <= 0:
            raise ProcessingError("White rectangle width and height must be greater than 0")
        parsed_rectangles.append(
            WhiteoutRect(
                x=x,
                top_y=top_y,
                width=width,
                height=height,
            )
        )

    return JobConfig(
        mode=mode,
        source_path=source_path,
        permit_text=permit_text.strip(),
        box_x=box_x,
        box_top_y=box_top_y,
        insert_blanks=insert_blanks,
        blank_interval=blank_interval,
        output_path=output_path,
        stamp_pages=stamp_pages,
        whiteout_rectangles=parsed_rectangles,
    )
