"""Render a PDF into deterministic PNG pages for the SOP document preview."""

from __future__ import annotations

import argparse
from pathlib import Path

import pymupdf


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    parser.add_argument("--dpi", type=int, default=110)
    args = parser.parse_args()

    if not args.input.is_file():
        raise FileNotFoundError(f"PDF preview source does not exist: {args.input}")
    if args.dpi <= 0:
        raise ValueError("Preview DPI must be positive.")

    args.output_directory.mkdir(parents=True, exist_ok=True)
    scale = args.dpi / 72
    with pymupdf.open(args.input) as document:
        if not document.page_count:
            raise ValueError("PDF preview source has no pages.")
        matrix = pymupdf.Matrix(scale, scale)
        for page_number, page in enumerate(document, start=1):
            page.get_pixmap(matrix=matrix, alpha=False).save(
                args.output_directory / f"page-{page_number:03d}.png"
            )


if __name__ == "__main__":
    main()
