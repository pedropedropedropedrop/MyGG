"""Bounded PDF text extraction for models without native PDF support."""
import logging
from pathlib import Path

from constants import (PDF_MAX_FILE_BYTES, PDF_MAX_PAGES, PDF_MAX_TEXT_CHARS,
                       PDF_MAX_PAGE_CONTENT_BYTES)

logger = logging.getLogger(__name__)


def _extract_pages(reader) -> str:
    assert reader is not None
    total = len(reader.pages)
    parts, warnings = [], []
    remaining = PDF_MAX_TEXT_CHARS
    for index in range(min(total, PDF_MAX_PAGES)):
        page = reader.pages[index]
        contents = page.get_contents()
        if contents and len(contents.get_data()) > PDF_MAX_PAGE_CONTENT_BYTES:
            warnings.append(f"Page {index + 1} exceeds the content size limit and was not read.")
            continue
        text = (page.extract_text() or "").strip()
        if not text:
            warnings.append(f"Page {index + 1} has no extractable text; images or scans may need OCR.")
            continue
        parts.append(f"Page {index + 1}:\n{text[:remaining]}")
        remaining -= len(text)
        if remaining <= 0:
            warnings.append("Text truncated at the extraction limit; the remaining content was not read.")
            break
    if total > PDF_MAX_PAGES:
        warnings.append(f"PDF truncated: only the first {PDF_MAX_PAGES} of {total} pages were inspected.")
    if not parts:
        return "No PDF text could be extracted. This may be a scan or image-only document requiring OCR.\n" + "\n".join(warnings)
    header = "PDF text extraction (images and visual layout were not interpreted)."
    return "\n\n".join([header, *warnings, *parts])


def read_pdf_text(path: str | Path) -> str:
    assert isinstance(path, (str, Path)), "PDF path is required"
    path = Path(path)
    try:
        if not path.is_file():
            return "Could not read PDF: the uploaded file is missing."
        if path.stat().st_size > PDF_MAX_FILE_BYTES:
            return f"PDF exceeds the local size limit ({PDF_MAX_FILE_BYTES} bytes); no content was read."
        from pypdf import PdfReader
        with path.open("rb") as stream:
            reader = PdfReader(stream)
            try:
                if reader.is_encrypted and not reader.decrypt(""):
                    return "PDF is encrypted; no content was read. Please provide an unlocked copy."
                return _extract_pages(reader)
            finally:
                reader.close()
    except ImportError:
        logger.exception("PDF extraction dependency is unavailable")
        return "Could not read PDF: local text extraction is unavailable."
    except Exception:
        logger.exception("PDF text extraction failed")
        return "Could not read PDF: the document may be damaged or use unsupported encryption. No complete reading is available."
