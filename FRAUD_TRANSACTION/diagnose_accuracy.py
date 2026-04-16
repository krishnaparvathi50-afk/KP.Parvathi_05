import os
import re
from typing import Dict, List, Tuple


def extract_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}:
        try:
            import pytesseract  # type: ignore
            from PIL import Image  # type: ignore

            return pytesseract.image_to_string(Image.open(path)).lower()
        except Exception:
            pass
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8", errors="ignore").lower()
    except Exception:
        return ""


def scan_file_metadata(path: str) -> Tuple[bool, List[str]]:
    markers_found: List[str] = []
    try:
        with open(path, "rb") as f:
            chunk = f.read(16384).decode("utf-8", errors="ignore").lower()
            forgery_markers = [
                "photoshop",
                "adobe imageready",
                "canva",
                "gimp",
                "picsart",
                "lightshot",
                "snipping tool",
                "screen capture",
                "edited",
                "manipulated",
                "whatsapp",
            ]
            for marker in forgery_markers:
                if marker in chunk:
                    markers_found.append(marker)
    except Exception:
        pass
    return len(markers_found) > 0, markers_found


def parse_amount(v: str) -> float:
    return float(v.replace(",", "").strip())


def detect_statement_type(text: str, filename: str) -> str:
    digital_markers = [
        "upi",
        "gpay",
        "google pay",
        "phonepe",
        "paytm",
        "bhim",
        "paid to",
        "transaction successful",
        "utr",
    ]
    bank_markers = ["ifsc", "account number", "statement period", "opening balance", "closing balance", "bank"]
    t = f"{filename.lower()} {text}"
    digital_score = sum(1 for m in digital_markers if m in t)
    bank_score = sum(1 for m in bank_markers if m in t)
    return "Digital" if digital_score > bank_score else "Bank"


def calc_check_bank(text: str) -> Tuple[str, str]:
    open_m = re.search(r"opening\s*balance[^0-9]*([0-9][0-9,]*\.?[0-9]{0,2})", text)
    close_m = re.search(r"closing\s*balance[^0-9]*([0-9][0-9,]*\.?[0-9]{0,2})", text)
    credit_m = re.search(r"(?:total\s*credits?|credits?)[^0-9]*([0-9][0-9,]*\.?[0-9]{0,2})", text)
    debit_m = re.search(r"(?:total\s*debits?|debits?)[^0-9]*([0-9][0-9,]*\.?[0-9]{0,2})", text)

    if open_m and close_m:
        opening = parse_amount(open_m.group(1))
        closing = parse_amount(close_m.group(1))
        credits = parse_amount(credit_m.group(1)) if credit_m else 0.0
        debits = parse_amount(debit_m.group(1)) if debit_m else 0.0
        expected = round(opening + credits - debits, 2)
        actual = round(closing, 2)
        if abs(expected - actual) > 0.01:
            return "Incorrect", f"Opening({opening}) + Credits({credits}) - Debits({debits}) = {expected}, but Closing is {actual}."
        return "Correct", f"Opening({opening}) + Credits({credits}) - Debits({debits}) matches Closing({actual})."

    # Could not prove mismatch; keep as correct and let detail checks decide SUSPICIOUS/FRAUD.
    return "Correct", "Balance math could not be fully verified from OCR text."


def calc_check_digital(text: str) -> Tuple[str, str]:
    amounts = re.findall(r"(?:₹|rs\.?|inr)\s*([0-9][0-9,]*\.?[0-9]{0,2})", text, flags=re.IGNORECASE)
    normalized = {round(parse_amount(a), 2) for a in amounts}
    if len(normalized) > 1:
        return "Incorrect", f"Conflicting amount values detected: {sorted(normalized)}."
    return "Correct", "No arithmetic contradiction detected in payment values."


def check_required_bank(text: str) -> Tuple[List[str], List[str]]:
    present, missing = [], []

    checks: Dict[str, bool] = {
        "Bank name": bool(re.search(r"\b\w+\s+bank\b|\bbank\b", text)),
        "IFSC code": bool(re.search(r"\b[a-z]{4}0[a-z0-9]{6}\b", text)),
        "Account number": bool(re.search(r"account\s*(?:number|no|#)?\s*[:\-]?\s*[x*\d]{6,18}", text)),
        "Statement period": bool(
            re.search(r"statement\s*period|from\s+\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\s+to\s+\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", text)
        ),
    }

    table_header_terms = ["date", "description", "debit", "credit", "balance"]
    header_count = sum(1 for term in table_header_terms if term in text)
    date_rows = len(re.findall(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", text))
    checks["Proper table format"] = header_count >= 3 and date_rows >= 2

    txn_id_hits = re.findall(r"(?:transaction|txn|utr|ref)\s*(?:id|no|number)?\s*[:\-]?\s*([a-z0-9\-]{6,})", text)
    checks["Transaction ID for each entry"] = len(txn_id_hits) >= 1

    for label, ok in checks.items():
        (present if ok else missing).append(label)
    return present, missing


def check_required_digital(text: str) -> Tuple[List[str], List[str]]:
    present, missing = [], []

    checks: Dict[str, bool] = {
        "App name": bool(re.search(r"\bgpay\b|\bgoogle pay\b|\bphonepe\b|\bpaytm\b|\bbhim\b", text)),
        "Sender/Receiver name": bool(re.search(r"paid to|from|to\s+[a-z]{2,}", text)),
        "UPI ID or phone number": bool(
            re.search(r"\b[a-z0-9._-]{2,}@[a-z]{2,}\b", text) or re.search(r"\b\d{10}\b", text)
        ),
        "Transaction ID": bool(re.search(r"\b(?:utr|txn|transaction|ref)\s*(?:id|no|number)?\s*[:\-]?\s*[a-z0-9\-]{6,}\b", text)),
        "Date & time": bool(
            re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", text)
            and re.search(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?\b", text)
        ),
        "Status": bool(re.search(r"\bsuccess(?:ful)?\b|\bfailed\b|\bfailure\b", text)),
    }

    for label, ok in checks.items():
        (present if ok else missing).append(label)
    return present, missing


def classify(path: str) -> str:
    filename = os.path.basename(path)
    text = extract_text(path)
    statement_type = detect_statement_type(text, filename)

    manipulated, markers = scan_file_metadata(path)
    lower_name = filename.lower()
    screenshot_or_crop = any(token in lower_name for token in ["whatsapp", "screenshot", "crop", "cropped"])

    if statement_type == "Bank":
        calc_status, calc_reason = calc_check_bank(text)
        present, missing = check_required_bank(text)
    else:
        calc_status, calc_reason = calc_check_digital(text)
        present, missing = check_required_digital(text)

    if calc_status == "Incorrect":
        final_result = "FRAUD"
        reason = f"Calculation mismatch detected. {calc_reason}"
    else:
        missing_count = len(missing)
        critical_missing = missing_count >= 2
        visual_tampering = manipulated

        if visual_tampering or critical_missing:
            final_result = "FRAUD"
            reason = "Multiple critical details missing and/or tampering indicators detected."
        elif missing_count > 0 or screenshot_or_crop:
            final_result = "SUSPICIOUS"
            reason = "Calculation is acceptable, but required details/metadata are missing."
        else:
            final_result = "GENUINE"
            reason = "All mandatory details are present and no contradiction was found."

        if "Transaction ID" in missing:
            if final_result == "GENUINE":
                final_result = "SUSPICIOUS"
            reason = "Transaction ID is missing, so it cannot be marked GENUINE."

        if screenshot_or_crop and final_result == "GENUINE":
            final_result = "SUSPICIOUS"
            reason = "Screenshot/cropped evidence requires manual scrutiny."

    details_lines = [
        f"Present: {', '.join(present) if present else 'None'}",
        f"Missing: {', '.join(missing) if missing else 'None'}",
    ]

    if screenshot_or_crop:
        details_lines.append("Flagged: Screenshot/WhatsApp/cropped source detected.")
    if manipulated:
        details_lines.append(f"Flagged: Metadata tampering markers found ({', '.join(markers)}).")

    return (
        "----------------------------------------\n\n"
        f"Statement Type: {statement_type}\n\n"
        "Calculation Check:\n"
        f"{calc_status}\n\n"
        "Details Status:\n"
        f"{chr(10).join(details_lines)}\n\n"
        "Final Result:\n"
        f"{final_result}\n\n"
        "Reason:\n"
        f"{reason}\n\n"
        "----------------------------------------"
    )


def run_diagnostics():
    upload_dir = r"c:\Users\KRISHNA PARVATHI\Downloads\FRAUD_TRANSACTION (1)\FRAUD_TRANSACTION\web1\static\uploads"
    files = [f for f in os.listdir(upload_dir) if os.path.isfile(os.path.join(upload_dir, f))]
    print(f"\n[DIAGNOSTICS] Scanning {len(files)} uploaded files...\n" + "-" * 60)

    for filename in files:
        path = os.path.join(upload_dir, filename)
        print(f"\nFILE: {filename}")
        print(classify(path))


if __name__ == "__main__":
    run_diagnostics()
