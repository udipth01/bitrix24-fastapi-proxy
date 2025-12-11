def parse_custom_extractions(raw):
    """
    raw can be:
    - None
    - JSON string: '{"RM_meeting_time": "...", "Webinar_attended": "..."}'
    - dict
    Returns a dict or {}.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            # If parsing fails, log-like behavior but just return {}
            print("⚠️ Failed to parse custom_extractions JSON:", raw)
            return {}
    return {}

def parse_budget_to_number(budget_str: str | None) -> int | None:
    """
    Parse all Indian-style budget inputs:
    - "60,00,000"
    - "₹60,00,000"
    - "60 lakh", "60 lakhs", "60 lac"
    - "over 10 Lakh"
    - "10-20 Lakh" (→ take max = 20 lakh)
    - "1.5 crore"
    - "sixty lakh" (convert words to numbers)
    """

    if not budget_str:
        return None

    s = budget_str.lower().strip()

    # Remove ₹, rs, whitespace
    s = re.sub(r"[₹, ]", "", s)
    s = s.replace("rs", "").replace("rs.", "").strip()

    # If pure digit now → direct
    if s.isdigit():
        return int(s)

    # Restore original with commas for unit detection
    orig = budget_str.lower()

    # Extract digits (handles "10-20 lakh")
    nums = re.findall(r"\d+(?:\.\d+)?", orig)
    if nums:
        # choose maximum number if ranges
        val = float(max(nums))
    else:
        # WORD BASED numbers (simple mapping)
        word_map = {
            "ten": 10, "twenty": 20, "thirty": 30, "forty": 40,
            "fifty": 50, "sixty": 60, "seventy": 70,
            "eighty": 80, "ninety": 90, "hundred": 100
        }
        words = orig.split()
        vals = [word_map[w] for w in words if w in word_map]
        if vals:
            val = max(vals)
        else:
            return None

    # Detect lakh / crore
    if "crore" in orig or "cr" in orig:
        multiplier = 10_000_000
    elif "lakh" in orig or "lac" in orig:
        multiplier = 100_000
    else:
        # If we see a number like 6000000 (7 digits → lakhs) but no word,
        # assume it is final amount
        return int(val)

    return int(val * multiplier)
