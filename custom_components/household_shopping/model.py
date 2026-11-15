"""Pure receipt and item helpers, independent of Home Assistant."""
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
import re

STORES = ("Albert Heijn", "Vomar", "Action", "DekaMarkt", "Hema", "Kruidvat")
ALIASES = {s.casefold(): s for s in STORES}
ALIASES.update(dict(zip(("ah", "vm", "ac", "dk", "hm", "kv"), STORES)))
ALIASES["deka"] = "DekaMarkt"
TAGS = dict(zip(("AH-Tag", "VM-Tag", "AC-Tag", "DK-Tag", "HM-Tag", "KV-Tag"), STORES))
CARDS = {"Albert Heijn": "input_text.bonuskaart_ah", "Vomar": "input_text.bonuskaart_vm", "Action": "input_text.bonuskaart_ac"}

def store_name(value, custom_stores=()):
    key = str(value).strip().casefold()
    result = next((name for name in custom_stores if name.casefold() == key), None) or ALIASES.get(key)
    if result is None:
        raise ValueError("Choose a supported shop: " + ", ".join(STORES))
    return result

def parse_item(item, custom_stores=()):
    """Keep the original UID and summary for precise completion and rollback."""
    summary = item.get("summary", "")
    match = re.fullmatch(r"\[([^\]]+)\]\s*(?:(\d+)x\s+)?(.+?)(?:\s+\(([^()]*)\))?", summary)
    if not match:
        return None
    try:
        store = store_name(match[1], custom_stores)
    except ValueError:
        return None
    return {"uid": item["uid"], "summary": summary, "store": store,
            "quantity": int(match[2] or 1), "item": match[3].strip(),
            "requester": match[4] or "Household"}

def receipt_lines(items):
    grouped = OrderedDict()
    for item in items:
        group = grouped.setdefault(item["item"].casefold(), {"name": item["item"], "quantity": 0, "people": OrderedDict()})
        group["quantity"] += item["quantity"]
        person = item["requester"]
        group["people"][person] = group["people"].get(person, 0) + item["quantity"]
    lines = []
    for group in grouped.values():
        people = group["people"]
        if len(people) == 1:
            requesters = next(iter(people))
        else:
            requesters = ", ".join(f"{quantity} {person}" for person, quantity in people.items())
        lines.append(f"[ ] {group['quantity']}x {group['name']} ({requesters})")
    return "\n".join(lines)


def cents(value):
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value).replace(",", "."))
        if not number.is_finite() or number < 0 or number > 100000 or number != number.quantize(Decimal("0.01")):
            raise ValueError("Enter a non-negative amount with at most two decimals.")
        return int(number * 100)
    except InvalidOperation as err:
        raise ValueError("Enter a valid amount.") from err

def loyalty_block(value):
    value = str(value).strip()
    if value in ("", "None", "none", "unknown", "unavailable"):
        return None
    if value.startswith("image:"):
        return {"type": "image", "image_path": value[6:].strip(), "image_width_mm": 50, "alignment": "center"}
    if value.startswith("_") or value.startswith("qr:"):
        return {"type": "qr", "data": value[1:] if value.startswith("_") else value[3:], "alignment": "center", "qr_size": 6}
    kind = "ean13" if value.isascii() and value.isdigit() and len(value) in (12, 13) else "code128"
    return {"type": "barcode", "data": value, "code_type": kind, "alignment": "center", "barcode_height": 90}
