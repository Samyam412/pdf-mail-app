#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path


SWIFT_SOURCE = r"""
import CoreGraphics
import CoreText
import Foundation
import PDFKit

enum StampFailure: Error, LocalizedError {
    case invalidNumber(String)
    case invalidPageList(String)
    case invalidRectangles(String)
    case pageOutOfRange(Int, Int)
    case openFailed(String)
    case contextFailed
    case pageBuildFailed(Int)
    case writeFailed(String)

    var errorDescription: String? {
        switch self {
        case .invalidNumber(let value):
            return "Invalid numeric value: \(value)"
        case .invalidPageList(let value):
            return "Invalid page list: \(value)"
        case .invalidRectangles(let value):
            return "Invalid rectangle list: \(value)"
        case .pageOutOfRange(let page, let count):
            return "Page \(page) is out of range for a \(count)-page PDF"
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

struct CoverRect {
    let x: CGFloat
    let topY: CGFloat
    let width: CGFloat
    let height: CGFloat
}

func parseDouble(_ value: String) throws -> Double {
    guard let number = Double(value) else {
        throw StampFailure.invalidNumber(value)
    }
    return number
}

func parseColor(_ value: String) throws -> CGColor {
    let parts = value.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }
    guard parts.count == 3 || parts.count == 4 else {
        throw StampFailure.invalidNumber(value)
    }

    let numbers = try parts.map { part -> CGFloat in
        guard let parsed = Double(part) else {
            throw StampFailure.invalidNumber(value)
        }
        if parsed > 1.0 {
            return CGFloat(parsed / 255.0)
        }
        return CGFloat(parsed)
    }

    let red = numbers[0]
    let green = numbers[1]
    let blue = numbers[2]
    let alpha = numbers.count == 4 ? numbers[3] : 1.0
    return CGColor(red: red, green: green, blue: blue, alpha: alpha)
}

func parsePageList(_ value: String) throws -> Set<Int>? {
    if value == "auto" || value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
        return nil
    }

    let rawTokens = value.components(separatedBy: CharacterSet(charactersIn: ", \n\t"))
    let tokens = rawTokens.filter { !$0.isEmpty }
    if tokens.isEmpty {
        throw StampFailure.invalidPageList(value)
    }

    var pageSet = Set<Int>()
    for token in tokens {
        guard let pageNumber = Int(token), pageNumber > 0 else {
            throw StampFailure.invalidPageList(value)
        }
        pageSet.insert(pageNumber - 1)
    }
    return pageSet
}

func parseCoverRectangles(_ value: String) throws -> [CoverRect] {
    let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
    if value == "auto" || trimmed.isEmpty {
        return []
    }

    guard let data = trimmed.data(using: .utf8) else {
        throw StampFailure.invalidRectangles(value)
    }

    let rawObject = try JSONSerialization.jsonObject(with: data, options: [])
    guard let rawRectangles = rawObject as? [[String: Any]] else {
        throw StampFailure.invalidRectangles(value)
    }

    return try rawRectangles.map { rectangle in
        guard
            let x = rectangle["x"] as? NSNumber,
            let topY = rectangle["top_y"] as? NSNumber,
            let width = rectangle["width"] as? NSNumber,
            let height = rectangle["height"] as? NSNumber
        else {
            throw StampFailure.invalidRectangles(value)
        }

        return CoverRect(
            x: CGFloat(truncating: x),
            topY: CGFloat(truncating: topY),
            width: CGFloat(truncating: width),
            height: CGFloat(truncating: height)
        )
    }
}

func drawText(
    _ text: String,
    x: CGFloat,
    y: CGFloat,
    fontName: String,
    fontSize: CGFloat,
    tracking: CGFloat,
    color: CGColor,
    in context: CGContext
) {
    let font = CTFontCreateWithName(fontName as CFString, fontSize, nil)
    let attributes: [NSAttributedString.Key: Any] = [
        NSAttributedString.Key(rawValue: kCTFontAttributeName as String): font,
        NSAttributedString.Key(rawValue: kCTKernAttributeName as String): tracking,
        NSAttributedString.Key(rawValue: kCTForegroundColorAttributeName as String): color,
    ]
    let attributed = NSAttributedString(string: text, attributes: attributes)
    let line = CTLineCreateWithAttributedString(attributed)

    context.saveGState()
    context.textMatrix = .identity
    context.textPosition = CGPoint(x: x, y: y)
    CTLineDraw(line, context)
    context.restoreGState()
}

func measureLineWidth(_ text: String, fontName: String, fontSize: CGFloat, tracking: CGFloat) -> CGFloat {
    let font = CTFontCreateWithName(fontName as CFString, fontSize, nil)
    let attributes: [NSAttributedString.Key: Any] = [
        NSAttributedString.Key(rawValue: kCTFontAttributeName as String): font,
        NSAttributedString.Key(rawValue: kCTKernAttributeName as String): tracking,
    ]
    let attributed = NSAttributedString(string: text, attributes: attributes)
    let line = CTLineCreateWithAttributedString(attributed)
    return CGFloat(CTLineGetTypographicBounds(line, nil, nil, nil))
}

func drawMultilineBox(
    _ text: String,
    leftMargin: CGFloat,
    topMargin: CGFloat,
    fixedBoxX: CGFloat?,
    fixedBoxTopY: CGFloat?,
    fixedBoxWidth: CGFloat?,
    fixedBoxHeight: CGFloat?,
    fontName: String,
    fontSize: CGFloat,
    tracking: CGFloat,
    lineSpacingAdjust: CGFloat,
    textColor: CGColor,
    boxPaddingX: CGFloat,
    boxPaddingY: CGFloat,
    borderColor: CGColor,
    fillColor: CGColor,
    borderWidth: CGFloat,
    pageHeight: CGFloat,
    in context: CGContext
) {
    let rawLines = text.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
    let lines = rawLines.isEmpty ? [""] : rawLines
    let font = CTFontCreateWithName(fontName as CFString, fontSize, nil)
    let ascent = CTFontGetAscent(font)
    let descent = CTFontGetDescent(font)
    let lineGap: CGFloat = lineSpacingAdjust
    let maxLineWidth = lines.map { measureLineWidth($0, fontName: fontName, fontSize: fontSize, tracking: tracking) }.max() ?? 0

    let lineAdvance = ascent + descent + lineGap
    let autoBoxWidth = maxLineWidth + (boxPaddingX * 2)
    let autoBoxHeight = (CGFloat(lines.count) * (ascent + descent)) + (CGFloat(max(lines.count - 1, 0)) * lineGap) + (boxPaddingY * 2)
    let boxWidth = fixedBoxWidth ?? autoBoxWidth
    let boxHeight = fixedBoxHeight ?? autoBoxHeight
    let boxTopY = fixedBoxTopY ?? (pageHeight - topMargin)
    let boxX = fixedBoxX ?? leftMargin
    let boxRect = CGRect(
        x: boxX,
        y: boxTopY - boxHeight,
        width: boxWidth,
        height: boxHeight
    )

    context.saveGState()
    context.setFillColor(fillColor)
    context.fill(boxRect)
    context.setStrokeColor(borderColor)
    context.setLineWidth(borderWidth)
    context.stroke(boxRect)
    context.restoreGState()

    let lineAdvanceToUse: CGFloat
    let firstBaselineY: CGFloat
    if fixedBoxHeight != nil {
        let contentHeight = boxHeight - (boxPaddingY * 2)
        let dynamicAdvance = contentHeight / CGFloat(max(lines.count, 1))
        lineAdvanceToUse = dynamicAdvance
        firstBaselineY = boxRect.maxY - boxPaddingY - ascent
    } else {
        lineAdvanceToUse = lineAdvance
        firstBaselineY = boxRect.maxY - boxPaddingY - ascent
    }

    for (index, lineText) in lines.enumerated() {
        let lineWidth = measureLineWidth(lineText, fontName: fontName, fontSize: fontSize, tracking: tracking)
        let textX = boxRect.minX + ((boxWidth - lineWidth) / 2.0)
        let textY = firstBaselineY - (CGFloat(index) * lineAdvanceToUse)
        drawText(lineText, x: textX, y: textY, fontName: fontName, fontSize: fontSize, tracking: tracking, color: textColor, in: context)
    }
}

func drawFilledRect(
    x: CGFloat,
    topY: CGFloat,
    width: CGFloat,
    height: CGFloat,
    fillColor: CGColor,
    in context: CGContext
) {
    let rect = CGRect(x: x, y: topY - height, width: width, height: height)
    context.saveGState()
    context.setFillColor(fillColor)
    context.fill(rect)
    context.restoreGState()
}

func shouldStamp(pageIndex: Int, startIndex: Int, step: Int, explicitPages: Set<Int>?) -> Bool {
    if let explicitPages = explicitPages {
        return explicitPages.contains(pageIndex)
    }
    if pageIndex < startIndex {
        return false
    }
    return (pageIndex - startIndex) % step == 0
}

func stampedPage(
    from page: PDFPage,
    pageIndex: Int,
    text: String,
    fontName: String,
    fontSize: CGFloat,
    tracking: CGFloat,
    lineSpacingAdjust: CGFloat,
    color: CGColor,
    leftMargin: CGFloat,
    topMargin: CGFloat,
    fixedBoxX: CGFloat?,
    fixedBoxTopY: CGFloat?,
    fixedBoxWidth: CGFloat?,
    fixedBoxHeight: CGFloat?,
    drawBox: Bool,
    coverBoxX: CGFloat?,
    coverBoxTopY: CGFloat?,
    coverBoxWidth: CGFloat?,
    coverBoxHeight: CGFloat?,
    coverBoxColor: CGColor,
    coverRectangles: [CoverRect],
    boxPaddingX: CGFloat,
    boxPaddingY: CGFloat,
    boxBorderColor: CGColor,
    boxFillColor: CGColor,
    boxBorderWidth: CGFloat,
    startIndex: Int,
    step: Int,
    explicitPages: Set<Int>?
) throws -> PDFPage {
    let bounds = page.bounds(for: .mediaBox)
    var mediaBox = CGRect(origin: .zero, size: bounds.size)
    let data = NSMutableData()

    guard let consumer = CGDataConsumer(data: data as CFMutableData),
          let context = CGContext(consumer: consumer, mediaBox: &mediaBox, nil)
    else {
        throw StampFailure.contextFailed
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

    if shouldStamp(pageIndex: pageIndex, startIndex: startIndex, step: step, explicitPages: explicitPages) {
        if let coverX = coverBoxX,
           let coverTopY = coverBoxTopY,
           let coverWidth = coverBoxWidth,
           let coverHeight = coverBoxHeight {
            drawFilledRect(
                x: coverX,
                topY: coverTopY,
                width: coverWidth,
                height: coverHeight,
                fillColor: coverBoxColor,
                in: context
            )
        }
        for rectangle in coverRectangles {
            drawFilledRect(
                x: rectangle.x,
                topY: rectangle.topY,
                width: rectangle.width,
                height: rectangle.height,
                fillColor: coverBoxColor,
                in: context
            )
        }
        if drawBox {
            drawMultilineBox(
                text,
                leftMargin: leftMargin,
                topMargin: topMargin,
                fixedBoxX: fixedBoxX,
                fixedBoxTopY: fixedBoxTopY,
                fixedBoxWidth: fixedBoxWidth,
                fixedBoxHeight: fixedBoxHeight,
                fontName: fontName,
                fontSize: fontSize,
                tracking: tracking,
                lineSpacingAdjust: lineSpacingAdjust,
                textColor: color,
                boxPaddingX: boxPaddingX,
                boxPaddingY: boxPaddingY,
                borderColor: boxBorderColor,
                fillColor: boxFillColor,
                borderWidth: boxBorderWidth,
                pageHeight: mediaBox.height,
                in: context
            )
        } else {
            let x = leftMargin
            let y = max(fontSize, mediaBox.height - topMargin - fontSize)
            drawText(text, x: x, y: y, fontName: fontName, fontSize: fontSize, tracking: tracking, color: color, in: context)
        }
    }

    context.endPDFPage()
    context.closePDF()

    guard let rebuilt = PDFDocument(data: data as Data),
          let rebuiltPage = rebuilt.page(at: 0)
    else {
        throw StampFailure.pageBuildFailed(pageIndex)
    }

    return rebuiltPage
}

do {
    let arguments = CommandLine.arguments
    let inputPath = arguments[1]
    let outputPath = arguments[2]
    let text = arguments[3]
    let fontSize = try CGFloat(parseDouble(arguments[4]))
    let fontName = arguments[5]
    let tracking = try CGFloat(parseDouble(arguments[5 + 1]))
    let lineSpacingAdjust = try CGFloat(parseDouble(arguments[7]))
    let color = try parseColor(arguments[8])
    let leftMargin = try CGFloat(parseDouble(arguments[9]))
    let topMargin = try CGFloat(parseDouble(arguments[10]))
    let startPage = Int(arguments[11]) ?? 1
    let step = Int(arguments[12]) ?? 3
    let drawBox = (arguments[13] as NSString).boolValue
    let boxPaddingX = try CGFloat(parseDouble(arguments[14]))
    let boxPaddingY = try CGFloat(parseDouble(arguments[15]))
    let boxBorderColor = try parseColor(arguments[16])
    let boxFillColor = try parseColor(arguments[17])
    let boxBorderWidth = try CGFloat(parseDouble(arguments[18]))
    let fixedBoxX = arguments[19] == "auto" ? nil : try CGFloat(parseDouble(arguments[19]))
    let fixedBoxTopY = arguments[20] == "auto" ? nil : try CGFloat(parseDouble(arguments[20]))
    let fixedBoxWidth = arguments[21] == "auto" ? nil : try CGFloat(parseDouble(arguments[21]))
    let fixedBoxHeight = arguments[22] == "auto" ? nil : try CGFloat(parseDouble(arguments[22]))
    let coverBoxX = arguments[23] == "auto" ? nil : try CGFloat(parseDouble(arguments[23]))
    let coverBoxTopY = arguments[24] == "auto" ? nil : try CGFloat(parseDouble(arguments[24]))
    let coverBoxWidth = arguments[25] == "auto" ? nil : try CGFloat(parseDouble(arguments[25]))
    let coverBoxHeight = arguments[26] == "auto" ? nil : try CGFloat(parseDouble(arguments[26]))
    let coverBoxColor = try parseColor(arguments[27])
    let coverRectangles = try parseCoverRectangles(arguments[28])
    let explicitPages = try parsePageList(arguments[29])

    let startIndex = max(0, startPage - 1)
    let inputURL = URL(fileURLWithPath: inputPath)
    let outputURL = URL(fileURLWithPath: outputPath)

    guard let source = PDFDocument(url: inputURL) else {
        throw StampFailure.openFailed(inputPath)
    }
    if let explicitPages = explicitPages {
        for pageIndex in explicitPages where pageIndex >= source.pageCount {
            throw StampFailure.pageOutOfRange(pageIndex + 1, source.pageCount)
        }
    }

    let destination = PDFDocument()
    for pageIndex in 0..<source.pageCount {
        guard let page = source.page(at: pageIndex) else {
            throw StampFailure.pageBuildFailed(pageIndex)
        }
        let rebuiltPage = try stampedPage(
            from: page,
            pageIndex: pageIndex,
            text: text,
            fontName: fontName,
            fontSize: fontSize,
            tracking: tracking,
            lineSpacingAdjust: lineSpacingAdjust,
            color: color,
            leftMargin: leftMargin,
            topMargin: topMargin,
            fixedBoxX: fixedBoxX,
            fixedBoxTopY: fixedBoxTopY,
            fixedBoxWidth: fixedBoxWidth,
            fixedBoxHeight: fixedBoxHeight,
            drawBox: drawBox,
            coverBoxX: coverBoxX,
            coverBoxTopY: coverBoxTopY,
            coverBoxWidth: coverBoxWidth,
            coverBoxHeight: coverBoxHeight,
            coverBoxColor: coverBoxColor,
            coverRectangles: coverRectangles,
            boxPaddingX: boxPaddingX,
            boxPaddingY: boxPaddingY,
            boxBorderColor: boxBorderColor,
            boxFillColor: boxFillColor,
            boxBorderWidth: boxBorderWidth,
            startIndex: startIndex,
            step: step,
            explicitPages: explicitPages
        )
        destination.insert(rebuiltPage, at: destination.pageCount)
    }

    guard destination.write(to: outputURL) else {
        throw StampFailure.writeFailed(outputPath)
    }
} catch {
    fputs((error.localizedDescription + "\n"), stderr)
    exit(1)
}
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stamp text repeatedly on selected pages of a PDF."
    )
    parser.add_argument("input_pdf", help="Path to the input PDF.")
    parser.add_argument(
        "--output",
        required=True,
        help="Output PDF path.",
    )
    parser.add_argument(
        "--text",
        required=True,
        help="Text to draw on matching pages.",
    )
    parser.add_argument(
        "--start-page",
        type=int,
        default=1,
        help="First 1-based page number to stamp. Default: 1.",
    )
    parser.add_argument(
        "--step",
        type=int,
        default=3,
        help="Stamp every N pages starting from --start-page. Default: 3.",
    )
    parser.add_argument(
        "--page-list",
        help="Comma or whitespace separated 1-based page numbers to stamp. Overrides --start-page/--step.",
    )
    parser.add_argument(
        "--font-size",
        type=float,
        default=18.0,
        help="Font size in points. Default: 18.",
    )
    parser.add_argument(
        "--font-name",
        default="Helvetica-Bold",
        help="macOS font name. Default: Helvetica-Bold.",
    )
    parser.add_argument(
        "--tracking",
        type=float,
        default=0.0,
        help="Extra letter spacing in PDF points. Default: 0.",
    )
    parser.add_argument(
        "--line-spacing-adjust",
        type=float,
        default=0.0,
        help="Extra vertical spacing between lines in PDF points. Can be negative.",
    )
    parser.add_argument(
        "--color",
        default="255,0,0",
        help="Text color as 'R,G,B' or 'R,G,B,A'. Values can be 0-255 or 0-1. Default: red.",
    )
    parser.add_argument(
        "--left-margin",
        type=float,
        default=24.0,
        help="Distance from the left edge in PDF points. Default: 24.",
    )
    parser.add_argument(
        "--top-margin",
        type=float,
        default=24.0,
        help="Distance from the top edge in PDF points. Default: 24.",
    )
    parser.add_argument(
        "--draw-box",
        action="store_true",
        help="Draw the text inside a bordered box.",
    )
    parser.add_argument(
        "--box-padding-x",
        type=float,
        default=10.0,
        help="Horizontal padding inside the box in PDF points. Default: 10.",
    )
    parser.add_argument(
        "--box-padding-y",
        type=float,
        default=8.0,
        help="Vertical padding inside the box in PDF points. Default: 8.",
    )
    parser.add_argument(
        "--box-border-color",
        default="0,0,0",
        help="Border color as 'R,G,B' or 'R,G,B,A'. Default: black.",
    )
    parser.add_argument(
        "--box-fill-color",
        default="1,1,1,1",
        help="Box fill color as 'R,G,B' or 'R,G,B,A'. Default: white.",
    )
    parser.add_argument(
        "--box-border-width",
        type=float,
        default=1.0,
        help="Border line width in PDF points. Default: 1.",
    )
    parser.add_argument(
        "--box-x",
        type=float,
        help="Exact left X coordinate of the box in PDF points.",
    )
    parser.add_argument(
        "--box-top-y",
        type=float,
        help="Exact top Y coordinate of the box in PDF points.",
    )
    parser.add_argument(
        "--box-width",
        type=float,
        help="Exact box width in PDF points.",
    )
    parser.add_argument(
        "--box-height",
        type=float,
        help="Exact box height in PDF points.",
    )
    parser.add_argument(
        "--cover-box-x",
        type=float,
        help="Optional left X coordinate of a filled rectangle used to cover old content.",
    )
    parser.add_argument(
        "--cover-box-top-y",
        type=float,
        help="Optional top Y coordinate of a filled rectangle used to cover old content.",
    )
    parser.add_argument(
        "--cover-box-width",
        type=float,
        help="Optional width of a filled rectangle used to cover old content.",
    )
    parser.add_argument(
        "--cover-box-height",
        type=float,
        help="Optional height of a filled rectangle used to cover old content.",
    )
    parser.add_argument(
        "--cover-box-color",
        default="255,255,255,1",
        help="Fill color for the optional cover rectangle. Default: white.",
    )
    parser.add_argument(
        "--cover-rectangles-json",
        help="JSON array of additional filled rectangles with x, top_y, width, and height.",
    )
    return parser


def run_swift(
    input_path: Path,
    output_path: Path,
    text: str,
    font_size: float,
    font_name: str,
    tracking: float,
    line_spacing_adjust: float,
    color: str,
    left_margin: float,
    top_margin: float,
    start_page: int,
    step: int,
    draw_box: bool,
    box_padding_x: float,
    box_padding_y: float,
    box_border_color: str,
    box_fill_color: str,
    box_border_width: float,
    box_x: float | None,
    box_top_y: float | None,
    box_width: float | None,
    box_height: float | None,
    cover_box_x: float | None,
    cover_box_top_y: float | None,
    cover_box_width: float | None,
    cover_box_height: float | None,
    cover_box_color: str,
    cover_rectangles_json: str | None,
    page_list: str | None,
) -> None:
    module_cache = Path("/tmp/swift-module-cache")
    module_cache.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".swift",
        prefix="stamp_every_n_pages_",
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
        text,
        str(font_size),
        font_name,
        str(tracking),
        str(line_spacing_adjust),
        color,
        str(left_margin),
        str(top_margin),
        str(start_page),
        str(step),
        "true" if draw_box else "false",
        str(box_padding_x),
        str(box_padding_y),
        box_border_color,
        box_fill_color,
        str(box_border_width),
        "auto" if box_x is None else str(box_x),
        "auto" if box_top_y is None else str(box_top_y),
        "auto" if box_width is None else str(box_width),
        "auto" if box_height is None else str(box_height),
        "auto" if cover_box_x is None else str(cover_box_x),
        "auto" if cover_box_top_y is None else str(cover_box_top_y),
        "auto" if cover_box_width is None else str(cover_box_width),
        "auto" if cover_box_height is None else str(cover_box_height),
        cover_box_color,
        cover_rectangles_json or "auto",
        page_list or "auto",
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
    if args.page_list:
        try:
            parse_page_list(args.page_list)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)

    run_swift(
        input_path=input_path,
        output_path=output_path,
        text=args.text,
        font_size=args.font_size,
        font_name=args.font_name,
        tracking=args.tracking,
        line_spacing_adjust=args.line_spacing_adjust,
        color=args.color,
        left_margin=args.left_margin,
        top_margin=args.top_margin,
        start_page=args.start_page,
        step=args.step,
        draw_box=args.draw_box,
        box_padding_x=args.box_padding_x,
        box_padding_y=args.box_padding_y,
        box_border_color=args.box_border_color,
        box_fill_color=args.box_fill_color,
        box_border_width=args.box_border_width,
        box_x=args.box_x,
        box_top_y=args.box_top_y,
        box_width=args.box_width,
        box_height=args.box_height,
        cover_box_x=args.cover_box_x,
        cover_box_top_y=args.cover_box_top_y,
        cover_box_width=args.cover_box_width,
        cover_box_height=args.cover_box_height,
        cover_box_color=args.cover_box_color,
        cover_rectangles_json=args.cover_rectangles_json,
        page_list=args.page_list,
    )
    print(f"Stamped PDF written to: {output_path}")
    return 0


def parse_page_list(value: str) -> list[int]:
    tokens = [token for token in re.split(r"[\s,]+", value.strip()) if token]
    if not tokens:
        raise ValueError("Page list cannot be empty")

    page_numbers: set[int] = set()
    for token in tokens:
        try:
            page_number = int(token)
        except ValueError as exc:
            raise ValueError(f"Invalid page number: {token}") from exc
        if page_number <= 0:
            raise ValueError(f"Page number must be positive: {page_number}")
        page_numbers.add(page_number)
    return sorted(page_numbers)


if __name__ == "__main__":
    raise SystemExit(main())
