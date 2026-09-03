"""OCR package: receipt / price-list image scanning (PaddleOCR, optional).

The heavy engine is imported lazily so that ``import ocr`` never pulls in
paddle/cv2 on hosts where they aren't installed (slim free-Render builds)
- :mod:`app` imports :mod:`ocr.ocr` inside a try/except and serves a clear
503 when the engine is missing. The names below resolve on first access.
"""
_LAZY_EXPORTS = {
    "preprocess_image",
    "run_ocr",
    "extract_table",
    "extract_text",
    "extract_products",
    "OCRResult",
    "MarketPriceItem",
}


def __getattr__(name):
    if name in _LAZY_EXPORTS:
        from . import ocr as _ocr

        return getattr(_ocr, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = sorted(_LAZY_EXPORTS)
