"""Text extraction from various file formats for LightRAG ingestion."""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path


def extract_text(file_path: str | Path) -> str:
    """Extract plain text from any supported file format."""
    p = Path(file_path)
    ext = p.suffix.lower()

    if ext in (".txt", ".md", ".json"):
        return p.read_text(encoding="utf-8", errors="replace")
    elif ext in (".html", ".htm"):
        return _extract_html(p)
    elif ext == ".docx":
        return _extract_docx(p)
    elif ext == ".docm":
        return _extract_docx(p)
    elif ext in (".xlsx", ".xlsm"):
        return _extract_xlsx(p)
    elif ext == ".csv":
        return _extract_csv(p)
    elif ext == ".pptx":
        return _extract_pptx(p)
    elif ext == ".pdf":
        return _extract_pdf(p)
    else:
        try:
            return p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""


def _extract_html(p: Path) -> str:
    from bs4 import BeautifulSoup
    html = p.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    # Remove scripts, styles, and nav elements
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    return _clean(text)


def _extract_docx(p: Path) -> str:
    try:
        from docx import Document
        doc = Document(str(p))
        parts = []
        for para in doc.paragraphs:
            if para.text.strip():
                parts.append(para.text)
        # Also extract table text
        for table in doc.tables:
            for row in table.rows:
                row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                if row_text:
                    parts.append(row_text)
        return _clean("\n".join(parts))
    except Exception as e:
        return f"[docx extraction error: {e}]"


def _extract_xlsx(p: Path) -> str:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(p), read_only=True, data_only=True)
        parts = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            parts.append(f"[Sheet: {sheet_name}]")
            for row in ws.iter_rows(values_only=True):
                row_parts = [str(v).strip() for v in row if v is not None and str(v).strip()]
                if row_parts:
                    parts.append(" | ".join(row_parts))
        wb.close()
        return _clean("\n".join(parts))
    except Exception as e:
        return f"[xlsx extraction error: {e}]"


def _extract_csv(p: Path) -> str:
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        reader = csv.reader(io.StringIO(text))
        parts = []
        for row in reader:
            row_text = " | ".join(v.strip() for v in row if v.strip())
            if row_text:
                parts.append(row_text)
        return _clean("\n".join(parts))
    except Exception as e:
        return f"[csv extraction error: {e}]"


def _extract_pptx(p: Path) -> str:
    try:
        from pptx import Presentation
        prs = Presentation(str(p))
        parts = []
        for slide_num, slide in enumerate(prs.slides, 1):
            slide_texts = []
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    slide_texts.append(shape.text.strip())
                # Extract table text
                if shape.has_table:
                    for row in shape.table.rows:
                        row_parts = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                        if row_parts:
                            slide_texts.append(" | ".join(row_parts))
            if slide_texts:
                parts.append(f"[Slide {slide_num}]")
                parts.extend(slide_texts)
        return _clean("\n".join(parts))
    except Exception as e:
        return f"[pptx extraction error: {e}]"


def _extract_pdf(p: Path) -> str:
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(p))
        parts = []
        for page in doc:
            parts.append(page.get_text())
        doc.close()
        return _clean("\n".join(parts))
    except ImportError:
        return f"[PDF extraction requires PyMuPDF: {p.name}]"
    except Exception as e:
        return f"[pdf extraction error: {e}]"


def _clean(text: str) -> str:
    """Remove excessive blank lines and normalize whitespace."""
    lines = [line.strip() for line in text.splitlines()]
    # Collapse 3+ consecutive blank lines into 2
    result = []
    blank_count = 0
    for line in lines:
        if not line:
            blank_count += 1
            if blank_count <= 2:
                result.append("")
        else:
            blank_count = 0
            result.append(line)
    return "\n".join(result).strip()


def find_all_data_files(data_root: str | Path) -> list[tuple[str, str]]:
    """Find all indexable files under data_root.

    Returns list of (absolute_path, relative_path) tuples.
    Skips index.html, JSON manifests, build scripts, and test source files.
    """
    data_root = Path(data_root)
    SKIP_PATTERNS = {
        "index.html", "README.md", "build_cards.py", "build_validation.py",
        "gen_attachment_pages.py", "gen_official_specs.py",
    }
    SKIP_EXTENSIONS = {".js", ".json", ".py", ".sh", ".txt", ".lock", ".yml", ".yaml"}
    SUPPORTED_EXTENSIONS = {
        ".html", ".htm", ".md", ".docx", ".docm",
        ".xlsx", ".xlsm", ".csv", ".pptx", ".pdf",
    }

    results = []
    for fp in sorted(data_root.rglob("*")):
        if not fp.is_file():
            continue
        if fp.name in SKIP_PATTERNS:
            continue
        ext = fp.suffix.lower()
        if ext in SKIP_EXTENSIONS:
            continue
        if ext not in SUPPORTED_EXTENSIONS:
            continue
        # Skip .test.js would already be caught above, but also skip src/ dirs
        rel = str(fp.relative_to(data_root))
        # Skip raw source files (not cards); only ingest cards from project/
        if "project/" in rel and "/cards/" not in rel and "/src/" in rel:
            continue
        # Skip raw validation test files — ingest only cards
        if "validation/" in rel and "/cards/" not in rel and rel.endswith(".test.js"):
            continue
        results.append((str(fp), rel))

    return results
