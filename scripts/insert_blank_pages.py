#!/usr/bin/env python3

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path


SWIFT_SOURCE = r"""
import CoreGraphics
import Foundation
import PDFKit

enum BlankInsertFailure: Error, LocalizedError {
    case invalidNumber(String)
    case openFailed(String)
    case contextFailed
    case pageBuildFailed(Int)
    case writeFailed(String)

    var errorDescription: String? {
        switch self {
        case .invalidNumber(let value):
            return "Invalid numeric value: \(value)"
        case .openFailed(let path):
            return "Could not open PDF: \(path)"
        case .contextFailed:
            return "Could not create PDF drawing context"
        case .pageBuildFailed(let index):
            return "Could not rebuild page \(index + 1)"
        case .writeFailed(let path):
            return "Could not write output PDF: \(path)"
        }
    }
}

func parseDouble(_ value: String) throws -> Double {
    guard let number = Double(value) else {
        throw BlankInsertFailure.invalidNumber(value)
    }
    return number
}

func clonePage(from page: PDFPage, pageIndex: Int) throws -> PDFPage {
    let bounds = page.bounds(for: .mediaBox)
    var mediaBox = CGRect(origin: .zero, size: bounds.size)
    let data = NSMutableData()

    guard let consumer = CGDataConsumer(data: data as CFMutableData),
          let context = CGContext(consumer: consumer, mediaBox: &mediaBox, nil)
    else {
        throw BlankInsertFailure.contextFailed
    }

    context.beginPDFPage([
        kCGPDFContextMediaBox as String: mediaBox
    ] as CFDictionary)

    if let pageRef = page.pageRef {
        context.saveGState()
        let transform = pageRef.getDrawingTransform(.mediaBox, rect: mediaBox, rotate: 0, preserveAspectRatio: true)
        context.concatenate(transform)
        context.drawPDFPage(pageRef)
        context.restoreGState()
    } else {
        page.draw(with: .mediaBox, to: context)
    }

    context.endPDFPage()
    context.closePDF()

    guard let rebuilt = PDFDocument(data: data as Data),
          let rebuiltPage = rebuilt.page(at: 0)
    else {
        throw BlankInsertFailure.pageBuildFailed(pageIndex)
    }

    return rebuiltPage
}

func blankPage(size: CGSize) throws -> PDFPage {
    var mediaBox = CGRect(origin: .zero, size: size)
    let data = NSMutableData()

    guard let consumer = CGDataConsumer(data: data as CFMutableData),
          let context = CGContext(consumer: consumer, mediaBox: &mediaBox, nil)
    else {
        throw BlankInsertFailure.contextFailed
    }

    context.beginPDFPage([
        kCGPDFContextMediaBox as String: mediaBox
    ] as CFDictionary)
    context.endPDFPage()
    context.closePDF()

    guard let rebuilt = PDFDocument(data: data as Data),
          let rebuiltPage = rebuilt.page(at: 0)
    else {
        throw BlankInsertFailure.pageBuildFailed(0)
    }

    return rebuiltPage
}

do {
    let arguments = CommandLine.arguments
    let inputPath = arguments[1]
    let outputPath = arguments[2]
    let interval = Int(try parseDouble(arguments[3]))

    let inputURL = URL(fileURLWithPath: inputPath)
    let outputURL = URL(fileURLWithPath: outputPath)

    guard let source = PDFDocument(url: inputURL) else {
        throw BlankInsertFailure.openFailed(inputPath)
    }

    let destination = PDFDocument()
    for pageIndex in 0..<source.pageCount {
        guard let page = source.page(at: pageIndex) else {
            throw BlankInsertFailure.pageBuildFailed(pageIndex)
        }

        let clonedPage = try clonePage(from: page, pageIndex: pageIndex)
        destination.insert(clonedPage, at: destination.pageCount)

        if interval > 0 && (pageIndex + 1) % interval == 0 {
            let blank = try blankPage(size: page.bounds(for: .mediaBox).size)
            destination.insert(blank, at: destination.pageCount)
        }
    }

    guard destination.write(to: outputURL) else {
        throw BlankInsertFailure.writeFailed(outputPath)
    }
} catch {
    fputs((error.localizedDescription + "\n"), stderr)
    exit(1)
}
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Insert a blank page after every N pages in a PDF."
    )
    parser.add_argument("input_pdf", help="Path to the input PDF.")
    parser.add_argument("--output", required=True, help="Output PDF path.")
    parser.add_argument(
        "--interval",
        required=True,
        type=int,
        help="Insert one blank page after every N pages.",
    )
    return parser


def run_swift(input_path: Path, output_path: Path, interval: int) -> None:
    module_cache = Path("/tmp/swift-module-cache")
    module_cache.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".swift",
        prefix="insert_blank_pages_",
        dir="/tmp",
        delete=False,
    ) as swift_file:
        swift_path = Path(swift_file.name)
        swift_file.write(SWIFT_SOURCE)

    command = [
        "swift",
        "-module-cache-path",
        str(module_cache),
        str(swift_path),
        str(input_path),
        str(output_path),
        str(interval),
    ]

    try:
        subprocess.run(command, check=True)
    finally:
        swift_path.unlink(missing_ok=True)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    input_path = Path(args.input_pdf).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    if not input_path.is_file():
        print(f"Input PDF not found: {input_path}", file=sys.stderr)
        return 1
    if args.interval <= 0:
        print("Interval must be greater than 0", file=sys.stderr)
        return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    run_swift(input_path=input_path, output_path=output_path, interval=args.interval)
    print(f"Blank-page PDF written to: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
