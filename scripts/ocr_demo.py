"""Dev-only OCR pipeline demo: synthesizes a fake price sheet, runs the full
preprocess -> OCR -> extract -> export pipeline, prints the results.

Usage (from the repo root):
    python scripts/ocr_demo.py

Requires the heavy OCR engine (paddlepaddle + paddleocr + opencv) - not
installed on slim free-Render builds.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np

from ocr.ocr import (
    logger,
    preprocess_image,
    run_ocr,
    extract_text,
    extract_products,
    extract_table,
    export_csv,
    export_excel,
    export_json,
)


def main():
    logger.info("Starting execution demonstration pipeline loop...")
    mock_img_path = "test_market_sheet.png"

    canvas = np.ones((600, 1100, 3), dtype=np.uint8) * 255
    cv2.putText(canvas, "Market Price Updates for 30 June 2026", (150, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (50, 50, 50), 2)
    cv2.putText(canvas, "1   India   Sea   Onion New Crop (18 Kg)   1.0   Sold by Weight   2.70", (50, 150), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
    cv2.putText(canvas, "2   India   Sea   Onion Old Crop (18 Kg)   1.0   Sold by Weight   NA", (50, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
    cv2.putText(canvas, "3   India   Sea   Elephant Yam (Suran)     9.0   Jute Bag         16.00", (50, 290), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
    cv2.putText(canvas, "30  Pakistan Sea  Onion NS                 --    By Weight        NA", (50, 360), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
    cv2.putText(canvas, "32  Pakistan Sea  Mango Sindri             6.0   Carton           18.00", (50, 430), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

    cv2.line(canvas, (30, 100), (1070, 100), (100, 100, 100), 2)
    cv2.line(canvas, (30, 500), (1070, 500), (100, 100, 100), 2)

    cv2.imwrite(mock_img_path, canvas)
    logger.info(f"Synthesized simulated invoice sheet artifact image at: {mock_img_path}")

    try:
        processed_matrix = preprocess_image(mock_img_path)
        ocr_tokens = run_ocr(processed_matrix, confidence_threshold=0.60)

        extracted_plain_text = extract_text(ocr_tokens)
        extracted_unique_products = extract_products(ocr_tokens)
        final_structured_table = extract_table(ocr_tokens)

        export_csv(final_structured_table, "market_prices.csv")
        export_excel(final_structured_table, "market_prices.xlsx")
        json_representation = export_json(final_structured_table)

        print("\n" + "=" * 50)
        print("EXTRACTED RAW TEXT VIEW:")
        print("=" * 50)
        print(extracted_plain_text)

        print("\n" + "=" * 50)
        print("ISOLATED VALID PRODUCTS IDENTIFIED:")
        print("=" * 50)
        print(extracted_unique_products)

        print("\n" + "=" * 50)
        print("RECONSTRUCTED STRUCTURED TABLE DATA (JSON):")
        print("=" * 50)
        print(json_representation)
        print("=" * 50 + "\n")

        logger.info("OCR execution module process loop completed successfully.")

    except Exception as err:
        logger.exception(f"Fatal system termination error discovered inside processing loop: {str(err)}")
        sys.exit(1)
    finally:
        if os.path.exists(mock_img_path):
            os.remove(mock_img_path)


if __name__ == "__main__":
    main()
