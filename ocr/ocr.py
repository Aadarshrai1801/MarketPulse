"""
ocr.py

A production-ready Python OCR module using PaddleOCR PP-OCRv5 and OpenCV.
Designed for high-accuracy extraction of structured market price data from 
noisy, scanned documents, invoices, and market report sheets.

Python Version: 3.10+
"""

import os
import sys
import re
import math
import difflib
import logging
import csv
import json
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Any, Optional, Tuple

import cv2
import numpy as np
import pandas as pd

_CACHED_BOUNDARIES: Optional[Tuple[List[str], List[float]]] = None

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("MarketPriceOCR")

# Initialize PaddleOCR lazily or verify imports
try:
    from paddleocr import PaddleOCR
except ImportError:
    logger.error("PaddleOCR is not installed. Please install paddlepaddle and paddleocr.")
    raise

try:
    import paddle

    GPU_AVAILABLE = (
        paddle.device.is_compiled_with_cuda() #type: ignore
        and paddle.device.cuda.device_count() > 0 #type: ignore
    )
except Exception:
    GPU_AVAILABLE = False

OCR_ENGINE = PaddleOCR(
    use_angle_cls=True,
    lang="en",
    use_gpu=GPU_AVAILABLE,
    show_log=False
)

from difflib import get_close_matches

KNOWN_COUNTRIES = ["India", "Pakistan", "Bangladesh", "China", "Ecuador", "Egypt", "Australia"]
KNOWN_SHIPMENTS = ["Sea", "Air", "Road"]
KNOWN_PACKING = [
    "Carton", "Jute Bag", "Mesh Bag", "PPE Bag", "Open Tray",
    "TC Box", "Sold by Weight", "By Weight", "Box", "Fruit", "Crate"
]

# Lexicon mapping common OCR fragment noise directly to correct terminology
COMMON_TYPO_MAP = {
    "paxislan": "pakistan",
    "mosh bag": "mesh bag",
    "mosh": "mesh",
    "83g": "bag",
    "8ag": "bag",
    "xog": "box",
    "carion": "carton",
    "cangn": "carton",
    "caaon": "carton",
    "carlon": "carton",
    "weighl": "weight",
    "welghi": "weight",
    "crale": "crate",
    "patato": "potato",
    "fruil": "fruit",
    "slack plum": "black plum",
    "sanana": "banana",
    "tenoca": "terioca",
    "equador": "ecuador",
    "playa": "pitaya",
}

# High-confidence dictionary for closed-set product vocabulary spelling normalization
VERIFIED_PRODUCT_WORDS = [
    "Green Peas", "French Beans", "Cluster Beans", "Black Plum", "Kala Jamun",
    "Mango", "Mausami", "Chaunsa", "White", "Carrot", "Sugarcane", "Cubes",
    "Pink", "Potato", "Leaf", "Arum", "Pointed", "Gourd", "Teasle", "Jack",
    "Fruit", "Lemon", "Green", "Taro", "Arbi", "Garlic", "Normal", "Pure",
    "Unwashed", "Onion", "Red", "Radish", "Pomelo", "Honey", "Mandarin",
    "Ginger", "Banana", "Pitaya", "Yellow", "Pink", "White", "Seedless", "Grapes"
]

def correct_phrase_typos(text: str) -> str:
    """
    Scans a raw string for explicit structural OCR character corruptions 
    using an internal typo dictionary map.
    """
    if not text:
        return text
    
    # Lowercase for uniform dictionary matching
    working_text = text.lower()
    for typo, correction in COMMON_TYPO_MAP.items():
        working_text = re.sub(r'\b' + re.escape(typo) + r'\b', correction, working_text)
    
    # Title-case tokens back to their original presentation state
    words = []
    for w, orig in zip(working_text.split(), text.split()):
        # Preserve original capitalization styles if the word wasn't a typo
        if w == orig.lower():
            words.append(orig)
        else:
            words.append(w.capitalize())
            
    return " ".join(words)

def clean_product_spelling(product_text: str) -> str:
    """
    Cleans minor single-character spelling updates in product names 
    without corrupting unlisted unique items.
    """
    if not product_text:
        return product_text
        
    # Phase 1: Direct map translations
    cleaned = correct_phrase_typos(product_text)
    
    # Phase 2: Targeted word token correction
    tokens = cleaned.split()
    final_tokens = []
    
    for token in tokens:
        # Strip trailing noise elements often glued on by OCR lines
        pure_word = re.sub(r"[^a-zA-Z]", "", token)
        if len(pure_word) <= 3: 
            final_tokens.append(token)
            continue
            
        # Match individual token snippets against our known target words
        matches = difflib.get_close_matches(pure_word, VERIFIED_PRODUCT_WORDS, n=1, cutoff=0.80)
        if matches:
            # Reconstruct the token while preserving original non-alpha wrappers like brackets
            restored = token.replace(pure_word, matches[0])
            final_tokens.append(restored)
        else:
            final_tokens.append(token)
            
    return " ".join(final_tokens)

# OCR glyph-shape confusions: on low-quality scans, punctuation
# characters that visually resemble a letter often get OCR'd as that
# punctuation symbol instead of the letter - e.g. a rounded "g" read as
# "(" or "[", a "t" read as ")" or "]". Naively stripping these as pure
# noise (the old behavior) throws away real letter information and
# shortens the token, which is exactly why "Egypt" scanned as something
# like "e[pu)" collapsed down to a near-unrecognizable 3-4 letter
# fragment ("epu") that no longer resembled its source word closely
# enough to match. Substituting the likely intended letter FIRST, before
# any other cleanup, keeps the token's shape and length intact so the
# fuzzy matcher below has something real to work with.
OCR_SYMBOL_TO_LETTER = {
    "(": "g", "[": "g", "{": "g",
    ")": "t", "]": "t", "}": "t",
    "|": "l", "!": "l",
    "9": "g",
    "0": "o",
    "1": "l",
}

def precorrect_ocr_symbols(text: str) -> str:
    """Replace glyph-shape-confusable punctuation with its likely letter."""
    if not text:
        return text
    return "".join(OCR_SYMBOL_TO_LETTER.get(ch, ch) for ch in text)

def correct_country(text: str) -> str:
    """
    Snap a raw, possibly OCR-garbled country token to the closest entry
    in KNOWN_COUNTRIES.

    This intentionally does more work than a single get_close_matches()
    call, because that was the actual source of a nasty failure mode:
    with a low cutoff (0.35) and only two-letter countries in the
    vocabulary, a garbled OCR read of a country that ISN'T even in the
    list (e.g. "Egypt" coming out as something like "eqypl" or "e9upl")
    would still be "closest" to some vocabulary entry - usually "India",
    since it's short and shares common letters - and get silently
    force-corrected to the wrong country. A single similarity ratio
    can't distinguish "confidently this country" from "vaguely
    everything is kind of similar to short words".

    The fix: first run glyph-shape symbol pre-correction (see
    precorrect_ocr_symbols above) so punctuation that's really a
    misread letter gets restored, then score against every candidate
    on two independent measures (character overlap AND length
    similarity), then only accept a correction if the best candidate is
    both a strong absolute match AND clearly ahead of the runner-up. If
    nothing clears that bar, the original text is left untouched - an
    honest miss beats a confident wrong guess.
    """
    if not text:
        return text

    presubbed = precorrect_ocr_symbols(text)
    normalized = re.sub(r"[^a-zA-Z ]", "", presubbed).strip().lower()
    if not normalized:
        return text

    scored = []
    for candidate in KNOWN_COUNTRIES:
        cand_lower = candidate.lower()
        # Character-level similarity (handles transpositions/substitutions).
        char_ratio = difflib.SequenceMatcher(None, normalized, cand_lower).ratio()
        # Length similarity - penalizes e.g. a 2-letter fragment matching
        # a 5-letter country purely on shared letters ("in" vs "India").
        longer = max(len(normalized), len(cand_lower))
        len_ratio = 1 - (abs(len(normalized) - len(cand_lower)) / longer) if longer else 0.0
        combined = (char_ratio * 0.7) + (len_ratio * 0.3)
        scored.append((combined, char_ratio, candidate))

    scored.sort(key=lambda s: s[0], reverse=True)
    best_score, best_char_ratio, best_candidate = scored[0]
    runner_up_score = scored[1][0] if len(scored) > 1 else 0.0

    strong_absolute_match = best_char_ratio >= 0.55 and best_score >= 0.6
    clear_margin_over_runner_up = (best_score - runner_up_score) >= 0.08

    if strong_absolute_match and clear_margin_over_runner_up:
        return best_candidate

    # Not confident enough - don't guess. Return the cleaned-up original
    # text so it's visible for review rather than silently mislabeled.
    return text

@dataclass
class OCRResult:
    """Dataclass holding granular word token information."""
    text: str
    confidence: float
    bbox: List[List[float]]  # [[x1, y1], [x2, y2], [x3, y3], [x4, y4]]
    center_x: float
    center_y: float

@dataclass
class MarketPriceItem:
    """Dataclass holding structured market price row data."""
    country: str = ""
    shipment: str = ""
    product: str = ""
    weight: str = ""
    packing: str = ""
    price: str = ""
    # Set internally whenever a value had to be recovered/guessed rather
    # than read directly (e.g. a fused shipment+product box was split
    # apart). Not part of the exported record - used only to decide
    # whether this row belongs in the review log.
    needs_review: bool = field(default=False, compare=False, repr=False)
    review_reason: str = field(default="", compare=False, repr=False)

# ==========================================
# IMAGE PREPROCESSING FUNCTIONS
# ==========================================

def deskew(image: np.ndarray) -> np.ndarray:
    """
    Automatically detect and correct image skew using contour orientation.
    Compatible with PaddleOCR 2.6.x / 2.7.x.
    """

    logger.info("Deskewing image...")

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Otsu threshold
    thresh = cv2.threshold(
        gray,
        0,
        255,
        cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )[1]

    # Merge nearby characters into text blocks
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (30, 5))
    dilated = cv2.dilate(thresh, kernel, iterations=2)

    contours, _ = cv2.findContours(
        dilated,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE
    )

    angles = []

    for cnt in contours:

        if cv2.contourArea(cnt) < 500:
            continue

        rect = cv2.minAreaRect(cnt)
        angle = rect[-1]

        # Normalize angle
        if angle < -45:
            angle += 90
        elif angle > 45:
            angle -= 90

        angles.append(angle)

    if len(angles) == 0:
        logger.info("No skew detected.")
        return image

    angle = float(np.median(angles))

    logger.info(f"Detected skew angle: {angle:.2f}")

    # Ignore tiny rotations
    if abs(angle) < 0.3:
        return image

    h, w = image.shape[:2]
    center = (w // 2, h // 2)

    M = cv2.getRotationMatrix2D(center, angle, 1.0)

    rotated = cv2.warpAffine(
        image,
        M,
        (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255)
    )

    return rotated

def preprocess_image(image_path: str) -> np.ndarray:
    """
    Image preprocessing optimized for PaddleOCR 2.6.x / 2.7.x.
    Produces clean, high-contrast images while preserving table text.
    """

    logger.info("Starting image preprocessing...")

    if not os.path.exists(image_path):
        raise FileNotFoundError(image_path)

    img = cv2.imread(image_path)

    if img is None:
        raise ValueError("Unable to read image.")

    if img.size == 0:
        raise ValueError("Empty image.")

    # ---------------------------------------------------
    # 1. Deskew
    # ---------------------------------------------------
    img = deskew(img)

    # ---------------------------------------------------
    # 2. Shadow Removal
    # ---------------------------------------------------
    rgb_planes = cv2.split(img)
    result_planes = []

    for plane in rgb_planes:

        dilated = cv2.dilate(
            plane,
            np.ones((7, 7), np.uint8)
        )

        background = cv2.medianBlur(
            dilated,
            21
        )

        diff = 255 - cv2.absdiff(plane, background)

        norm = cv2.normalize(diff, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U #type: ignore
        )

        result_planes.append(norm)

    img = cv2.merge(result_planes)

    # ---------------------------------------------------
    # 3. Denoise
    # ---------------------------------------------------
    img = cv2.fastNlMeansDenoisingColored(
        img,
        None,
        10,
        10,
        7,
        21
    )

    # ---------------------------------------------------
    # 4. CLAHE
    # ---------------------------------------------------
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    clahe = cv2.createCLAHE(
        clipLimit=2.5,
        tileGridSize=(8, 8)
    )

    gray = clahe.apply(gray)

    # ---------------------------------------------------
    # 5. Sharpen
    # ---------------------------------------------------
    sharpen_kernel = np.array([
        [0, -1, 0],
        [-1, 5, -1],
        [0, -1, 0]
    ], dtype=np.float32)

    gray = cv2.filter2D(
        gray,
        -1,
        sharpen_kernel
    )

    # Dynamic Resizing based on height estimate
    h_orig, w_orig = img.shape[:2]
    fx = 3 if h_orig < 1500 else 2
    fy = fx
    gray = cv2.resize(gray, None, fx=fx, fy=fy, interpolation=cv2.INTER_CUBIC)

    # Dynamic Binarization to eliminate faint background watermarks ("COMING SOON")
    thresh = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 21
    )

    # ---------------------------------------------------
    # 8. Morphological Cleanup
    # ---------------------------------------------------
    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (2, 2)
    )

    cleaned = cv2.morphologyEx(
        thresh,
        cv2.MORPH_CLOSE,
        kernel,
        iterations=1
    )

    # ---------------------------------------------------
    # 9. Convert back to BGR
    # ---------------------------------------------------
    processed = cv2.cvtColor(
        cleaned,
        cv2.COLOR_GRAY2BGR
    )

    logger.info("Image preprocessing completed.")

    return processed

# ==========================================
# OCR ENGINE MANAGEMENT
# ==========================================

def run_ocr(processed_img: np.ndarray,
            confidence_threshold: float = 0.45) -> List[OCRResult]:
    """
    Run OCR using PaddleOCR 2.6.x / 2.7.x.
    """

    logger.info("Running PaddleOCR...")

    try:
        ocr_outputs = OCR_ENGINE.ocr(processed_img, cls=True)
    except Exception as e:
        logger.exception(f"OCR failed: {e}")
        raise RuntimeError(f"PaddleOCR execution failed: {e}")

    results: List[OCRResult] = []

    if (
        ocr_outputs is None
        or len(ocr_outputs) == 0
        or ocr_outputs[0] is None
    ):
        logger.warning("No OCR text detected.")
        return results

    for block in ocr_outputs[0]:

        try:

            bbox = block[0]
            text = str(block[1][0]).strip()
            confidence = float(block[1][1])

            if not text:
                continue

            if confidence < confidence_threshold:
                continue

            xs = [float(p[0]) for p in bbox]
            ys = [float(p[1]) for p in bbox]

            results.append(
                OCRResult(
                    text=text,
                    confidence=confidence,
                    bbox=bbox,
                    center_x=sum(xs) / len(xs),
                    center_y=sum(ys) / len(ys),
                )
            )

        except Exception as err:
            logger.warning(f"Skipping malformed OCR block: {err}")
            continue

    logger.info(
        f"Detected {len(results)} OCR tokens above "
        f"{confidence_threshold:.2f} confidence."
    )

    return results

# ==========================================
# SMART PARSING & DATA RECONSTRUCTION
# ==========================================

# ==========================================
# FUZZY VOCABULARY CORRECTION
# ==========================================
# Country, shipment mode, and packing type are all drawn from a small,
# closed set of real-world values (unlike product names, which are
# open-ended). That makes them a great fit for fuzzy-matching the *whole*
# field against a known vocabulary, rather than trying to patch every
# possible letter-swap individually - it catches garbling patterns we
# haven't seen yet, not just the ones already hardcoded above.
#
# Extend the KNOWN_COUNTRIES / KNOWN_SHIPMENTS / KNOWN_PACKING lists
# defined near the top of this file to match your actual supplier
# countries / packing types if they differ.
#
# NOTE: these vocabularies are intentionally defined only once, at the
# top of the file. A second, narrower redefinition used to live here
# (["India", "Pakistan"] only) and silently shadowed the real list -
# any country outside that pair (Egypt, Ecuador, Bangladesh, China,
# Australia, ...) had nowhere valid to match and got force-fitted onto
# whichever of India/Pakistan happened to be least-dissimilar. Keeping
# a single source of truth prevents that class of bug from coming back.


def fuzzy_correct(value: str, vocabulary: List[str], threshold: float = 0.55) -> str:
    """
    Snap `value` to its closest match in a small, known vocabulary if the
    similarity is high enough, otherwise leave it untouched.

    Used for closed-set fields (country, shipment mode, packing type)
    where every valid value is already known in advance, so comparing the
    whole string is far more reliable than patching individual letters -
    e.g. "Paxistan" / "Pgkistan" / "Ind|a" -> "Pakistan" / "India".
    """
    if not value:
        return value

    # Ignore stray punctuation OCR sometimes glues onto these fields
    # (e.g. "Ind|a", "Paxistan:") when comparing, but keep the returned
    # value as the clean canonical form.
    normalized = re.sub(r"[^a-zA-Z ]", "", value).strip().lower()
    if not normalized:
        return value

    best_match = None
    best_ratio = 0.0
    for candidate in vocabulary:
        ratio = difflib.SequenceMatcher(None, normalized, candidate.lower()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = candidate

    if best_match and best_ratio >= threshold:
        return best_match

    # No confident match - better to leave it as-is than guess wrong
    return value


def best_match_ratio(value: str, vocabulary: List[str]) -> float:
    """
    Same normalization/matching logic as fuzzy_correct, but returns the
    raw similarity ratio to the closest vocabulary entry instead of the
    snapped value. Lets callers distinguish "confidently Sea/Air" from
    "technically cleared fuzzy_correct's threshold but is really
    something else" (e.g. a product name that leaked into this field).
    """
    if not value:
        return 0.0
    normalized = re.sub(r"[^a-zA-Z ]", "", value).strip().lower()
    if not normalized:
        return 0.0
    return max(
        (difflib.SequenceMatcher(None, normalized, c.lower()).ratio() for c in vocabulary),
        default=0.0,
    )


def split_shipment_and_product(shipment_text: str, threshold: float = 0.5) -> Tuple[str, str]:
    """
    Occasionally PaddleOCR fuses the short Shipment-By value ("Sea"/"Air")
    together with a short Product name into a single text box - e.g.
    "Sea Ginger", "Sea: Garllte" - since there's little visual gap between
    them for genuinely short product names. When that happens, the whole
    blob lands in the shipment bucket and the product column ends up
    empty ("Unknown Product"). Recover the product name by peeling the
    leading shipment word off.

    Deliberately conservative: only peels off a *short* leading word
    (Sea/Air are 3 letters) that fuzzy-matches the known shipment
    vocabulary above a fairly strict threshold. A looser threshold here
    causes real false positives - e.g. "Dry" vs "Air" scores the same
    similarity as some genuine OCR misreads of "Sea" - so on any row this
    doesn't confidently recognize, it leaves the text untouched rather
    than risk corrupting a legitimate product-only value.
    """
    if not shipment_text:
        return shipment_text, ""

    # Strip stray leading punctuation noise OCR sometimes glues onto the
    # front of a fused box (e.g. ".Iiy Gourd" for "Ivy Gourd") before
    # trying to peel off a leading shipment word - otherwise the regex
    # anchor below never matches at all and recovery silently no-ops.
    cleaned_text = re.sub(r'^[\s:.,]+', '', shipment_text.strip())
    if not cleaned_text:
        return shipment_text, ""

    match = re.match(r'^([^\s:.,]+)[\s:.,]*(.*)$', cleaned_text)
    if not match:
        return shipment_text, ""

    first_word, remainder = match.group(1), match.group(2).strip()

    if not remainder or len(first_word) > 5:
        return shipment_text, ""

    corrected = fuzzy_correct(first_word, KNOWN_SHIPMENTS, threshold)
    if corrected in KNOWN_SHIPMENTS:
        return corrected, remainder

    # The leading word wasn't recognizable as a shipment mode either - the
    # whole field is very likely a lone product name that got bucketed
    # into the shipment column (not a fused shipment+product blob at
    # all). Signal that by returning an EMPTY shipment rather than the
    # untouched original, so the caller can tell "not a shipment value"
    # apart from "no recovery attempted" and fall back to carrying
    # forward the previous row's shipment mode instead.
    return "", cleaned_text


def clean_and_normalize_text(text: str) -> str:
    """
    Applies standard dictionary fixes and corrections for common structural OCR mistakes.
    """
    t = text.strip()
    replacements = {
    "N4": "NA",
    "N/A": "NA",
    "lndia": "India",
    "Indla": "India",
    "lndla": "India",
    "Ind|a": "India",
    "0O": "00",
    "Carion": "Carton",
    "Canon": "Carton",
    "Canion": "Carton",
    "Cartion": "Carton",
    "Carlon": "Carton",
    "Caron": "Carton",
    "Canton": "Carton",
    "Welght": "Weight",
    "Weignt": "Weight",
    "WeIght": "Weight",
    "Welgh": "Weight",
    "Seaa": "Sea",
    "Alr": "Air",
    "Paxistan": "Pakistan",
    "Pgkistan": "Pakistan",
    }
    for k, v in replacements.items():
        t = re.sub(r'\b' + re.escape(k) + r'\b', v, t)

    # Deliberately NOT collapsing a bare "I"/"O" to "1"/"0" here: this
    # function runs on every token regardless of which column it will
    # land in, and a lone "I" or "O" is just as likely to be a garbled
    # fragment of a text field (country/product/packing) as a genuine
    # digit. Blindly rewriting it to "1"/"0" silently turned innocuous
    # spelling mistakes into a stray zero/one that downstream code then
    # either dropped (isdigit() check on the country column) or baked
    # into the wrong field. Digit/letter confusion is instead corrected
    # only where numeric context is actually guaranteed - see
    # fix_numeric_ocr_confusions(), used on the weight/price columns.
    return t


def fix_numeric_ocr_confusions(text: str) -> str:
    """
    Corrects common OCR letter/digit confusions (O/o -> 0, I/l -> 1) for
    fields that are known in advance to be numeric, such as weight or
    price. Safe to apply broadly here specifically because the caller
    guarantees the column is numeric - unlike clean_and_normalize_text,
    which runs on every field including free-text ones.
    """
    if not text:
        return text
    fixed = text.replace("O", "0").replace("o", "0")
    fixed = re.sub(r"(?<=\d)[Il](?=\d|$)", "1", fixed)
    fixed = re.sub(r"^[Il](?=\d)", "1", fixed)
    return fixed

NA_VALUES = {"NA", "N/A", "VALUE", "PRICE", "-", "--", "—", "–", ""}


def parse_price(text: str) -> str:
    """
    Extracts, normalizes, and verifies price strings to standard decimal
    string format.
    """
    stripped = text.strip()

    if stripped.upper() in NA_VALUES or stripped in NA_VALUES:
        return "NA"

    # Fix common OCR digit/letter confusions before parsing.
    cleaned = stripped.replace("O", "0").replace("o", "0")

    # Normalize thousands vs. decimal separators. A lone comma with 1-2
    # trailing digits (e.g. "22,50") is a decimal comma; commas alongside
    # a dot, or several commas (e.g. "1,234.56" or "1,234,567"), are
    # thousands separators and should just be dropped rather than turned
    # into a second decimal point - naively replacing every comma with a
    # dot would truncate "1,234.56" down to "1.234", silently losing the
    # ".56". Bare stray-currency-symbol characters are dropped entirely.
    cleaned = re.sub(r'[^\d.,]', '', cleaned)

    if cleaned.count(",") and cleaned.count("."):
        cleaned = cleaned.replace(",", "")
    elif cleaned.count(",") == 1 and re.search(r',\d{1,2}$', cleaned):
        cleaned = cleaned.replace(",", ".")
    elif cleaned.count(","):
        cleaned = cleaned.replace(",", "")

    match = re.search(r'\d+(?:\.\d+)?', cleaned)
    if not match:
        return ""

    try:
        val = float(match.group(0))
    except ValueError:
        return ""

    return f"{val:.2f}"


def parse_price_fragments(fragments: List[str]) -> str:
    """
    Combine one or more raw price-column token fragments into a single
    parsed price.

    Handles the common single-fragment case directly, and recombines the
    rare case where PaddleOCR splits one price across two boxes on the
    same row (e.g. "36" + ".00", or a currency symbol detected as its own
    box alongside the number) by concatenating the fragments before
    parsing, instead of only keeping whichever fragment arrived last.
    """
    if not fragments:
        return ""

    if len(fragments) == 1:
        return parse_price(fragments[0])

    # If any fragment alone is already a recognizable NA marker and
    # there's exactly one other fragment, prefer treating this as
    # "NA" plus a stray/noise token rather than trying to concatenate
    # them into a nonsense number.
    for frag in fragments:
        if frag.strip().upper() in NA_VALUES:
            return "NA"

    joined = "".join(f.strip() for f in fragments)
    result = parse_price(joined)
    if result:
        return result

    # Concatenation didn't yield a usable number - fall back to the
    # single fragment that looks most like a real price (contains a
    # digit), rather than silently returning nothing.
    for frag in fragments:
        candidate = parse_price(frag)
        if candidate and candidate != "NA":
            return candidate

    return ""

def group_rows(
    ocr_results: List[OCRResult],
    y_tolerance: float = 18.0
) -> List[List[OCRResult]]:
    """
    Group OCR tokens into rows using the average Y coordinate.
    Optimized for structured market price tables.
    """

    if not ocr_results:
        return []

    # Sort by Y coordinate
    tokens = sorted(ocr_results, key=lambda t: t.center_y)

    rows: List[List[OCRResult]] = []
    current_row: List[OCRResult] = [tokens[0]]

    for token in tokens[1:]:

        # Average Y of current row
        avg_y = np.mean([t.center_y for t in current_row])

        if abs(token.center_y - avg_y) <= y_tolerance:

            current_row.append(token)

        else:

            current_row.sort(key=lambda t: t.center_x)

            rows.append(current_row)

            current_row = [token]

    if current_row:
        current_row.sort(key=lambda t: t.center_x)
        rows.append(current_row)

    # Merge tiny rows (caused by OCR splitting)
    merged_rows: List[List[OCRResult]] = []

    for row in rows:

        if (
            merged_rows
            and len(row) <= 2
            and abs(
                np.mean([t.center_y for t in row])
                - np.mean([t.center_y for t in merged_rows[-1]])
            ) < y_tolerance
        ):
            merged_rows[-1].extend(row)
            merged_rows[-1].sort(key=lambda t: t.center_x)

        else:
            merged_rows.append(row)

    logger.info(f"Grouped OCR into {len(merged_rows)} rows.")

    return merged_rows

def is_noise_or_header(row_text: str) -> bool:
    """
    Flags corporate banners, page counts, metadata tags, logos, or informational table metadata headers.
    """
    normalized = row_text.upper()
    noise_patterns = [
        r'PAGE\s+\d+', r'MARKET\s+PRICE\s+UPDATES', r'INVOICE', r'DATE\b', r'WWW\.', 
        r'SCAN\s+THE\s+QR', r'JOIN\s+OUR\s+CHANNEL', r'REPORTING\s+DAILY', r'CONNECT\s+WITH\s+US',
        r'COUNTRY\s+OF\s+ORIGIN', r'SHIPMENT\s+BY', r'WEIGHT\s*\(KG\)', r'PACKING', r'PRICE\s*\(AED\)'
    ]
    for pattern in noise_patterns:
        if re.search(pattern, normalized):
            return True
    return False

def detect_header_columns(rows):
    """
    Detect header row and return X positions of each column.

    Uses fuzzy string matching (in addition to exact substring matching) so
    common OCR misreads of header words - e.g. "Shlpmont" for "Shipment",
    "Prico" for "Price", "Woight" for "Weight" - don't cause the whole
    header row (and therefore the whole table) to be rejected just because
    one letter came out wrong.
    """

    HEADER_KEYWORDS = ["country", "shipment", "product", "weight", "packing", "price"]
    FUZZY_THRESHOLD = 0.72

    columns = {}

    for row in rows:

        for token in row:

            txt = token.text.lower()
            words = re.findall(r"[a-z]+", txt)

            for keyword in HEADER_KEYWORDS:

                if keyword in columns:
                    continue

                # Fast path: exact substring match, no false-positive risk
                if keyword in txt:
                    columns[keyword] = token.center_x
                    continue

                # Fallback: fuzzy match each word in the token against the
                # keyword, to tolerate single-character OCR misreads
                if any(
                    difflib.SequenceMatcher(None, w, keyword).ratio() >= FUZZY_THRESHOLD
                    for w in words
                ):
                    columns[keyword] = token.center_x

        if len(columns) == 6:
            break

    return columns

COLUMN_ORDER = ["country", "shipment", "product", "weight", "packing", "price"]


def learn_column_boundaries(
    data_rows: List[List[OCRResult]],
    header_columns: Dict[str, float],
    max_iterations: int = 25,
) -> Tuple[List[str], List[float], Dict[str, float]]:
    """
    Refine column boundaries using the x-position distribution of the
    ENTIRE document's data tokens, not just the header row.

    The header-only approach breaks whenever a header word's OCR box is
    centered somewhere that doesn't match where the actual data in that
    column sits (e.g. "Product" is centered mid-column, but short product
    names like "Okra (Bhindi)" or "Ivy Gourd" sit close to the column's
    LEFT edge, so their box center can fall to the left of a boundary
    drawn from the header's center - landing them in the Shipment bucket
    instead). This function runs a k-means-style refinement: start from
    the header positions, bucket every data token in the document against
    the current boundaries, recompute each column's centroid as the
    median x of the tokens that landed in it, and repeat until the
    centroids stop moving (or max_iterations is hit). Because this looks
    at the full document rather than a single row, one noisy row can't
    skew it, and it converges on where the data actually lives.

    This is intentionally allowed to run for many iterations / over every
    token in the document - accuracy matters more than speed here, and a
    few extra seconds of convergence is cheap insurance against a whole
    column being mis-detected for every row in the sheet.
    """

    sorted_cols = sorted(COLUMN_ORDER, key=lambda k: header_columns[k])
    centroids = {c: header_columns[c] for c in sorted_cols}

    # Flatten every token in the document's data rows once. Skip bare
    # row-index digits (the unlabeled "#" column at the far left) so they
    # can't drag the "country" centroid leftward.
    all_tokens = [
        tok for row in data_rows for tok in row if not tok.text.strip().isdigit()
    ]

    if not all_tokens:
        boundaries = [
            (centroids[sorted_cols[i]] + centroids[sorted_cols[i + 1]]) / 2
            for i in range(len(sorted_cols) - 1)
        ]
        return sorted_cols, boundaries, centroids

    for _ in range(max_iterations):

        boundaries = [
            (centroids[sorted_cols[i]] + centroids[sorted_cols[i + 1]]) / 2
            for i in range(len(sorted_cols) - 1)
        ]

        def bucket_for(x, _boundaries=boundaries):
            for i, boundary in enumerate(_boundaries):
                if x < boundary:
                    return sorted_cols[i]
            return sorted_cols[-1]

        buckets: Dict[str, List[float]] = {c: [] for c in sorted_cols}
        for tok in all_tokens:
            buckets[bucket_for(tok.center_x)].append(tok.center_x)

        max_shift = 0.0
        new_centroids = {}
        for c in sorted_cols:
            if buckets[c]:
                new_val = float(np.median(buckets[c]))
            else:
                # No data ever landed here - keep the header estimate
                new_val = centroids[c]
            max_shift = max(max_shift, abs(new_val - centroids[c]))
            new_centroids[c] = new_val

        centroids = new_centroids

        if max_shift < 0.5:
            break

    boundaries = [
        (centroids[sorted_cols[i]] + centroids[sorted_cols[i + 1]]) / 2
        for i in range(len(sorted_cols) - 1)
    ]

    logger.info(
        "Learned column centroids from %d document tokens: %s",
        len(all_tokens),
        {c: round(v, 1) for c, v in centroids.items()},
    )

    return sorted_cols, boundaries, centroids


def assign_tokens_to_columns(row, sorted_cols, boundaries):
    """
    Assign each token in a row to its column.

    Two safeguards make this robust against the boundary being imperfect
    for any single token:

    1. Nearest-boundary bucketing using globally-learned boundaries (see
       learn_column_boundaries) rather than boundaries derived only from
       the header row.
    2. STRICT LEFT-TO-RIGHT MONOTONICITY. Columns in this table always
       appear in a fixed left-to-right order, and tokens within a row are
       processed in x-sorted order. So once a token has been placed in
       column i, no later token in that same row is ever allowed to be
       placed in a column before i - if the boundary-based guess says
       otherwise, the token is clamped forward into column i instead.
    """

    def bucket_for(x):
        for i, boundary in enumerate(boundaries):
            if x < boundary:
                return sorted_cols[i]
        return sorted_cols[-1]

    item = MarketPriceItem()

    product = []
    packing = []
    price_parts: List[str] = []

    last_col_idx = 0

    for token in row:
        txt = clean_and_normalize_text(token.text)

        # Evaluate bounding box spans relative to column boundaries
        t_xs = [p[0] for p in token.bbox]
        left_x, right_x = min(t_xs), max(t_xs)
        
        # Fallback/Primary assignment using token properties
        raw_col = bucket_for(token.center_x)

        # Prevent digit/suffix absorption bugs (e.g., "Green Chilli G4" price leakage)
        if raw_col == "price" and not re.search(r'^\d+(?:\.\d{2})?$', token.text) and token.text.upper() not in NA_VALUES:
            if left_x < boundaries[-1]: # If it extends leftward past the last boundary
                raw_col = "packing"
                
        raw_idx = sorted_cols.index(raw_col)

        # Monotonicity clamp: never allow this row's column pointer to
        # move backwards, regardless of what the raw x-bucket says.
        col_idx = max(raw_idx, last_col_idx)
        last_col_idx = col_idx
        col = sorted_cols[col_idx]

        if col == "country":
            if txt.isdigit():
                continue
            item.country += " " + txt

        elif col == "shipment":
            item.shipment += " " + txt

        elif col == "product":
            product.append(txt)

        elif col == "weight":
            item.weight += " " + fix_numeric_ocr_confusions(txt)

        elif col == "packing":
            packing.append(txt)

        elif col == "price":
            if txt:
                price_parts.append(txt)

    # Reconstruct raw strings from field buffers
    raw_country_str = item.country.strip()
    raw_shipment_str = item.shipment.strip()
    raw_packing_str = " ".join(packing).strip()
    raw_product_str = " ".join(product).strip()
    item.weight = item.weight.strip()

    # Apply spelling correction and fuzzy dictionary mapping layers
    item.country = correct_country(raw_country_str)
    item.shipment = fuzzy_correct(raw_shipment_str, KNOWN_SHIPMENTS)
    item.packing = correct_phrase_typos(fuzzy_correct(raw_packing_str, KNOWN_PACKING))
    item.product = clean_product_spelling(raw_product_str)

    # Enhanced decimal recovery matching naked, whole numbers, and fragmented text units
    if not item.price and price_parts:
        item.price = parse_price_fragments(price_parts)

    if not item.price or item.price == "NA":
        candidates = []
        for token in row:
            text_strip = token.text.strip()
            # Catches standard decimals, bare integers, or trailing units missing leading segments
            match = re.search(r"\b\d+(?:\.\d{1,2})?\b", text_strip)
            if match and bucket_for(token.center_x) in ["packing", "price"]:
                candidates.append((token.center_x, match.group()))
            
            # Fallback for exact double-digit decimals matching broken boundaries
            match_decimal = re.search(r"\d+\.\d{2}", text_strip)
            if match_decimal and bucket_for(token.center_x) in ["packing", "price"]:
                candidates.append((token.center_x, match_decimal.group()))
                
        if candidates:
            # Rightmost decimal number is almost always the price
            candidates.sort(key=lambda x: x[0])
            item.price = parse_price(candidates[-1][1])

    # Recover a product name that got fused into the shipment field
    if not item.product and item.shipment:
        original_shipment_value = item.shipment
        recovered_shipment, recovered_product = split_shipment_and_product(item.shipment)
        if recovered_product:
            item.shipment = recovered_shipment
            item.product = clean_product_spelling(recovered_product)
            item.needs_review = True
            if recovered_shipment:
                item.review_reason = (
                    f"split fused '{original_shipment_value}' into "
                    f"shipment='{recovered_shipment}' + product='{recovered_product}'"
                )
            else:
                item.review_reason = (
                    f"'{original_shipment_value}' in shipment bucket did not match "
                    f"Sea/Air - recovered whole as product='{recovered_product}'"
                )

    # Recover a price value that PaddleOCR fused into a single text box with adjacent columns
    if not item.price or item.price == "NA":
        trailing_number = re.compile(r'(\d+(?:\.\d{1,2})?)\s*$')
        for field_name in ("packing", "weight", "product"):
            value = getattr(item, field_name)
            match = trailing_number.search(value)
            if not match:
                continue
            prefix = value[:match.start()].strip()
            if prefix:
                setattr(item, field_name, prefix)
                item.price = parse_price(match.group(1))
                break

    return item

_LAST_REVIEW_LOG: List[Dict[str, Any]] = []


def get_review_log() -> List[Dict[str, Any]]:
    """
    Rows where the pipeline had to fall back to a heuristic (rather than a
    directly-detected value) from the most recent extract_table() call.
    Intended for a human spot-check pass - the goal is zero silent
    errors, not zero flagged rows: an honestly-flagged row beats a
    confidently-wrong one.
    """
    return list(_LAST_REVIEW_LOG)


SHIPMENT_CONFIDENCE_THRESHOLD = 0.72


def extract_table(ocr_results: List[OCRResult]) -> List[Dict[str, Any]]:
    """
    Convert OCR tokens into structured table data using detected or cached column positions.
    """
    global _LAST_REVIEW_LOG, _GLOBAL_LAYOUT_CACHE
    _LAST_REVIEW_LOG = []

    grouped = group_rows(ocr_results)
    if not grouped:
        return []

    # Attempt to locate headers on the current page matrix
    columns = detect_header_columns(grouped)

    # -----------------------------------------------------------------
    # MULTI-PAGE RESILIENCY PATTERN: Resolve Column Boundaries
    # -----------------------------------------------------------------
    if len(columns) < 6:
        if _GLOBAL_LAYOUT_CACHE is not None: #type: ignore
            logger.info("Header absent or incomplete on this page. Utilizing cached layout boundaries from a previous page pass.")
            sorted_cols, boundaries = _GLOBAL_LAYOUT_CACHE #type: ignore
        else:
            logger.warning("Could not map layout boundaries based on headers, and no layout cache exists.")
            return []
    else:
        # Headers were confidently identified; isolate data rows and map coordinates dynamically
        data_rows: List[List[OCRResult]] = []
        header_found = False

        for row in grouped:
            row_text = " ".join(token.text for token in row).lower()
            if not header_found:
                if "country" in row_text and "product" in row_text:
                    header_found = True
                continue
            if is_noise_or_header(row_text):
                continue
            data_rows.append(row)

        # Fallback if structural text was misidentified by noise/watermark artifacts
        if not data_rows:
            data_rows = grouped

        sorted_cols, boundaries, _ = learn_column_boundaries(data_rows, columns)
        
        # Save coordinates to cache for subsequent execution iterations (e.g., Page 2, Page 3)
        _GLOBAL_LAYOUT_CACHE = (sorted_cols, boundaries)

    # -----------------------------------------------------------------
    # PASS 2: Token-to-Column Assignments Using Resolved Layout
    # -----------------------------------------------------------------
    structured_data: List[Dict[str, Any]] = []
    active_item: Optional[MarketPriceItem] = None
    last_valid_shipment: Optional[str] = None
    last_valid_country: Optional[str] = None

    # Determine row parsing posture based on if we are running a headless page
    header_found = False if len(columns) == 6 else True

    for row_num, row in enumerate(grouped, start=1):
        row_text = " ".join(token.text for token in row).lower()
        
        # Avoid processing headers if they are present on the current page matrix
        if len(columns) == 6 and not header_found:
            if "country" in row_text and "product" in row_text:
                header_found = True
            continue
            
        if is_noise_or_header(row_text):
            continue

        parsed_row = assign_tokens_to_columns(row, sorted_cols, boundaries)
        
        # Contextual Country Recovery
        if parsed_row.country in KNOWN_COUNTRIES:
            last_valid_country = parsed_row.country
        elif last_valid_country:
            parsed_row.country = last_valid_country

        # Skip completely blank lines
        if not any([parsed_row.country, parsed_row.shipment, parsed_row.product, parsed_row.weight, parsed_row.packing, parsed_row.price]):
            continue

        # Shipment validation and contextual fallback forward tracking
        original_shipment = parsed_row.shipment
        shipment_ratio = best_match_ratio(original_shipment, KNOWN_SHIPMENTS)
        flagged_reason = None

        if original_shipment and shipment_ratio >= SHIPMENT_CONFIDENCE_THRESHOLD:
            last_valid_shipment = parsed_row.shipment
        elif not original_shipment:
            parsed_row.shipment = last_valid_shipment or ""
        else:
            flagged_reason = "shipment column structural drift"
            if not parsed_row.product:
                parsed_row.product = original_shipment
            parsed_row.shipment = last_valid_shipment or ""

        if parsed_row.needs_review or flagged_reason:
            _LAST_REVIEW_LOG.append({
                "row_number": row_num,
                "reason": flagged_reason or parsed_row.review_reason,
                "resulting_row": asdict(parsed_row),
            })

        # Multiline item string stitching rules
        if active_item and parsed_row.product and not parsed_row.country and (not parsed_row.price or parsed_row.price == "NA"):
            active_item.product += " " + parsed_row.product
            if structured_data:
                structured_data[-1]["product"] = active_item.product
            continue

        active_item = parsed_row
        structured_data.append({
            "country": parsed_row.country,
            "shipment": parsed_row.shipment,
            "product": parsed_row.product,
            "weight": parsed_row.weight,
            "packing": parsed_row.packing,
            "price": parsed_row.price if parsed_row.price else "NA"
        })

    logger.info(f"Extracted {len(structured_data)} structured rows.")
    return structured_data

def extract_products(ocr_results: List[OCRResult]) -> List[str]:
    """
    Isolates and outputs unique verified product name lines discovered in structural layouts.
    """
    table_data = extract_table(ocr_results)
    products = [item["product"] for item in table_data if item["product"].strip()]
    return list(dict.fromkeys(products))

def extract_text(ocr_results: List[OCRResult]) -> str:
    """
    Returns text block layout presentation strings from processing views.
    """
    grouped = group_rows(ocr_results)
    lines = []
    for row in grouped:
        line_str = " ".join([tok.text for tok in row])
        lines.append(line_str)
    return "\n".join(lines)

# ==========================================
# FILE EXPORT HANDLERS
# ==========================================

def export_csv(data: List[Dict[str, Any]], target_path: str = "market_prices.csv") -> None:
    """
    Exports parsed tabular datasets into clean CSV files.
    """
    if not data:
        logger.warning("Empty dataset submitted for CSV file exporting routines.")
        
    fieldnames = ["country", "shipment", "product", "weight", "packing", "price"]
    try:
        with open(target_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in data:
                writer.writerow(row)
        logger.info(f"Successfully exported data to CSV: {target_path}")
    except Exception as e:
        logger.error(f"Failed to export to CSV file layout: {str(e)}")
        raise

def export_excel(data: List[Dict[str, Any]], target_path: str = "market_prices.xlsx") -> None:
    """
    Exports parsed tabular datasets into structured, styled Excel spreadsheets.
    """
    try:
        df = pd.DataFrame(data)
        for col in ["country", "shipment", "product", "weight", "packing", "price"]:
            if col not in df.columns:
                df[col] = ""
                
        df = df[["country", "shipment", "product", "weight", "packing", "price"]]
        df.columns = [c.capitalize() for c in df.columns]
        
        with pd.ExcelWriter(target_path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Market Prices")
        logger.info(f"Successfully exported structured records to Excel: {target_path}")
    except Exception as e:
        logger.error(f"Failed to compile target binary Excel sheet structures: {str(e)}")
        raise

def export_json(data: List[Dict[str, Any]]) -> str:
    """
    Serializes application layout items to structured JSON strings.
    """
    return json.dumps(data, indent=2, ensure_ascii=False)

def export_review_log(target_path: str = "review_log.json") -> None:
    """
    Writes out the rows flagged during the most recent extract_table()
    call - i.e. every row where a value had to be recovered/guessed
    rather than read directly. Empty file means nothing was flagged.
    """
    log = get_review_log()
    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)
    if log:
        logger.warning(f"{len(log)} row(s) flagged for review: {target_path}")
    else:
        logger.info("No rows flagged for review.")