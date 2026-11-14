#!/usr/bin/env python3
"""
Stage 2 & Stage 3: Vision Extraction, Zone 8 Shipping Math, Routing & Priority Review Queue
(scripts/analyze_items.py)

Supports Dual-Mode Vision Inspection:
  1. Agent-Native Mode (Default, $0 API cost):
     - `--init` creates `/items/item_XX/vision_input.json` and `/items/vision_batch.json`
       so Antigravity / Claude can inspect photos directly and populate item metadata.
     - `--calculate` computes Conservative Zone 8 shipping from ORIGIN_ZIP, eBay fees
       (13.5% + $0.40), Net Profit, Routing Buckets, Priority Scores (0-100), and outputs
       the interactive Markdown "Grill Me" review queue (`/items/review_queue.md` and
       `/items/batch_manifest.json`).
  2. Standalone API Fallback Mode (`--use-api`):
     - Uses `google-genai` (GEMINI_API_KEY) to inspect photos in each `/items/item_XX/`
       folder and automatically run the full calculation and review queue generation.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}

# ==============================================================================
# Conservative Zone 8 Commercial Shipping Rate Tables (USPS Ground Advantage & UPS Ground)
# ==============================================================================
# Worst-case Zone 8 (Cross-Country) commercial rates in USD by billable weight (lbs).
USPS_GROUND_ADVANTAGE_ZONE8_TABLE: List[Tuple[float, float]] = [
    (0.25, 5.45),   # 4 oz
    (0.50, 6.15),   # 8 oz
    (0.75, 6.85),   # 12 oz
    (1.00, 7.65),   # 15.99 oz / 1 lb
    (2.00, 10.40),
    (3.00, 12.60),
    (4.00, 14.35),
    (5.00, 16.10),
    (6.00, 17.85),
    (7.00, 19.45),
    (8.00, 21.10),
    (9.00, 22.75),
    (10.00, 24.40),
    (12.00, 27.90),
    (15.00, 33.15),
    (20.00, 41.80),
    (25.00, 50.50),
    (30.00, 59.20),
    (40.00, 75.80),
    (50.00, 92.40),
    (70.00, 124.00),
]

UPS_GROUND_ZONE8_TABLE: List[Tuple[float, float]] = [
    (1.00, 10.95),
    (2.00, 12.10),
    (3.00, 13.45),
    (4.00, 14.80),
    (5.00, 16.05),
    (6.00, 17.20),
    (7.00, 18.35),
    (8.00, 19.50),
    (9.00, 20.65),
    (10.00, 21.80),
    (12.00, 24.20),
    (15.00, 27.85),
    (20.00, 33.90),
    (25.00, 40.15),
    (30.00, 46.50),
    (35.00, 52.80),
    (40.00, 59.20),
    (45.00, 65.60),
    (50.00, 72.10),
    (60.00, 84.90),
    (70.00, 97.50),
    (90.00, 125.00),
    (150.00, 195.00),
]


def lookup_rate(weight_lbs: float, table: List[Tuple[float, float]], per_lb_over: float = 1.85) -> float:
    """Lookup tiered rate from table, extrapolating linearly if above max table weight."""
    for max_w, rate in table:
        if weight_lbs <= max_w:
            return rate
    highest_w, highest_rate = table[-1]
    extra_lbs = math.ceil(weight_lbs - highest_w)
    return round(highest_rate + (extra_lbs * per_lb_over), 2)


def calculate_zone8_shipping(
    weight_lbs: float,
    dimensions_in: List[float],
    origin_zip: str = "98101",
) -> Dict[str, Any]:
    """
    Calculate conservative worst-case Zone 8 shipping cost from origin_zip,
    comparing USPS Ground Advantage and UPS Ground with dimensional weight rules.
    """
    weight_lbs = max(0.1, float(weight_lbs or 1.0))
    if not dimensions_in or len(dimensions_in) != 3:
        dimensions_in = [10.0, 8.0, 6.0]
    dims = sorted([max(1.0, float(d)) for d in dimensions_in], reverse=True)
    length, width, height = dims[0], dims[1], dims[2]
    cubic_in = length * width * height
    length_plus_girth = length + 2.0 * (width + height)

    # USPS dim divisor is 166 (for packages > 1 cubic foot = 1,728 cu in)
    usps_dim_weight = math.ceil(cubic_in / 166.0) if cubic_in > 1728.0 else weight_lbs
    usps_billable_lbs = max(weight_lbs, usps_dim_weight)
    usps_cost = lookup_rate(usps_billable_lbs, USPS_GROUND_ADVANTAGE_ZONE8_TABLE)
    # USPS nonstandard length fee Surcharges
    if length > 30.0:
        usps_cost += 15.00
    elif length > 22.0:
        usps_cost += 4.00
    if cubic_in > 3456.0:  # > 2 cubic feet
        usps_cost += 18.00

    # UPS Ground dim divisor is 139 (commercial)
    ups_dim_weight = math.ceil(cubic_in / 139.0)
    ups_billable_lbs = max(math.ceil(weight_lbs), ups_dim_weight)
    ups_cost = lookup_rate(ups_billable_lbs, UPS_GROUND_ZONE8_TABLE)
    if length > 48.0 or width > 30.0 or length_plus_girth > 105.0 or ups_billable_lbs > 50.0:
        ups_cost += 24.00  # Additional Handling surcharge

    # Select best carrier (note USPS max 70 lbs & 130" L+G)
    if weight_lbs <= 70.0 and length_plus_girth <= 130.0 and usps_cost <= ups_cost:
        selected_carrier = "USPS Ground Advantage (Zone 8)"
        selected_cost = round(usps_cost, 2)
        billable_lbs = round(usps_billable_lbs, 2)
    else:
        selected_carrier = "UPS Ground (Zone 8)"
        selected_cost = round(ups_cost, 2)
        billable_lbs = round(ups_billable_lbs, 2)

    return {
        "origin_zip": origin_zip,
        "selected_carrier": selected_carrier,
        "estimated_shipping_usd": selected_cost,
        "actual_weight_lbs": round(weight_lbs, 2),
        "billable_weight_lbs": billable_lbs,
        "dimensions_in": [length, width, height],
        "cubic_inches": round(cubic_in, 1),
        "length_plus_girth_in": round(length_plus_girth, 1),
        "usps_zone8_quote_usd": round(usps_cost, 2),
        "ups_zone8_quote_usd": round(ups_cost, 2),
    }


# ==============================================================================
# 2026 Amazon FBA Fulfillment Fee Tiers (Standard-Size + 3.5% Fuel Surcharge)
# ==============================================================================
AMAZON_FBA_STD_TIERS: List[Tuple[float, float]] = [
    (0.25, 3.30),   # Small Standard 4 oz
    (0.50, 3.48),   # Small Standard 8 oz
    (0.75, 3.66),   # Small Standard 12 oz
    (1.00, 3.85),   # Small/Large Standard 16 oz
    (1.50, 4.95),   # Large Standard 1.5 lb
    (2.00, 5.45),   # Large Standard 2 lb
    (3.00, 6.20),   # Large Standard 3 lb
    (5.00, 7.15),   # Large Standard 5 lb
    (10.00, 8.65),  # Large Standard 10 lb
    (20.00, 11.50), # Large Standard 20 lb
]


def calculate_amazon_economics(
    item_data: Dict[str, Any],
    shipping_info: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Calculate Amazon FBA and Amazon FBM fees and net profit for items with an ASIN match
    (or media/book items). Includes Individual plan $0.99 fee, 15% referral fee,
    $1.80 media closing fee, 2026 FBA fulfillment + 3.5% surcharge, inbound placement,
    inbound UPS box shipping, and estimated 2-month warehouse storage.
    """
    asin = str(item_data.get("amazon_asin") or "").strip()
    is_new = "new" in str(item_data.get("condition") or "").lower() and "like" not in str(item_data.get("condition") or "").lower()
    amz_used = item_data.get("amazon_used_price")
    amz_new = item_data.get("amazon_new_price")

    # Determine target Amazon selling price
    if is_new and amz_new:
        amz_price = float(amz_new)
    elif amz_used:
        amz_price = float(amz_used)
    elif amz_new:
        # Conservative Used discount off New Buy Box if Used price wasn't parsed
        amz_price = round(float(amz_new) * 0.70, 2)
    else:
        amz_price = float(item_data.get("comp_price") or 0.0) if asin else 0.0

    is_media = bool(item_data.get("amazon_is_media")) or any(
        kw in str(item_data.get("category") or "").lower()
        for kw in ("book", "media", "dvd", "cd", "music", "video game")
    )

    plan = os.getenv("AMAZON_PLAN", "individual").strip().lower()
    plan_fee = 0.99 if plan == "individual" else 0.00
    referral_pct = float(os.getenv("AMAZON_REFERRAL_PCT", "0.15"))
    closing_fee = float(os.getenv("AMAZON_MEDIA_CLOSING_FEE", "1.80")) if is_media else 0.00
    inbound_per_lb = float(os.getenv("AMAZON_FBA_INBOUND_PER_LB", "0.50"))
    placement_fee = float(os.getenv("AMAZON_FBA_PLACEMENT_FEE", "0.35"))
    storage_months = float(os.getenv("AMAZON_FBA_STORAGE_MONTHS", "2"))

    weight_lbs = max(0.1, float(shipping_info.get("actual_weight_lbs") or 1.0))
    cubic_in = float(shipping_info.get("cubic_inches") or 400.0)
    cubic_ft = cubic_in / 1728.0

    # FBA fulfillment fee (billable weight + 3.5% fuel surcharge; -$0.77 Low-Price FBA if < $10)
    base_fba_fee = lookup_rate(weight_lbs, AMAZON_FBA_STD_TIERS, per_lb_over=0.45)
    if 0 < amz_price < 10.0:
        base_fba_fee = max(2.20, base_fba_fee - 0.77)
    fba_fulfillment_fee = round(base_fba_fee * 1.035, 2)

    inbound_shipping = round(weight_lbs * inbound_per_lb, 2)
    est_storage = round(cubic_ft * 0.87 * storage_months, 2)

    if amz_price > 0 and asin:
        referral_fee = round(max(0.30, amz_price * referral_pct), 2)
        total_fba_fees = round(
            referral_fee + closing_fee + plan_fee + fba_fulfillment_fee + placement_fee + inbound_shipping + est_storage,
            2,
        )
        fba_net = round(amz_price - total_fba_fees, 2)

        zone8_ship = float(shipping_info.get("estimated_shipping_usd") or 0.0)
        total_fbm_fees = round(referral_fee + closing_fee + plan_fee + zone8_ship, 2)
        fbm_net = round(amz_price - total_fbm_fees, 2)
    else:
        referral_fee = 0.0
        total_fba_fees = 0.0
        fba_net = None
        fbm_net = None

    return {
        "matched": bool(asin),
        "asin": asin or None,
        "amazon_url": item_data.get("amazon_url") or (f"https://www.amazon.com/dp/{asin}" if asin else None),
        "is_media": is_media,
        "amazon_is_seller": bool(item_data.get("amazon_is_seller")),
        "bsr_rank": item_data.get("amazon_bsr_rank"),
        "bsr_category": item_data.get("amazon_bsr_category"),
        "target_price_usd": round(amz_price, 2) if amz_price > 0 else 0.0,
        "referral_fee_usd": referral_fee,
        "media_closing_fee_usd": closing_fee,
        "plan_fee_usd": plan_fee,
        "fba_fulfillment_fee_usd": fba_fulfillment_fee,
        "inbound_placement_fee_usd": placement_fee,
        "inbound_shipping_usd": inbound_shipping,
        "est_storage_usd": est_storage,
        "total_fba_fees_usd": total_fba_fees,
        "fba_net_profit_usd": fba_net,
        "fbm_net_profit_usd": fbm_net,
    }


def compute_economics_and_routing(item_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate eBay fees, Net Profit, Routing Bucket, FBMP buffered price,
    and Stage 3 Priority Score (0-100).
    """
    origin_zip = os.getenv("ORIGIN_ZIP", "98101")
    ebay_fee_pct = float(os.getenv("EBAY_FEE_PERCENT", "0.135"))
    ebay_fixed_fee = float(os.getenv("EBAY_FIXED_FEE", "0.40"))
    fbmp_buffer = float(os.getenv("FBMP_NEGOTIATION_BUFFER", "0.12"))

    min_profit_all_three = float(os.getenv("MIN_PROFIT_ALL_THREE", "50.0"))
    min_profit_ebay_only = float(os.getenv("MIN_PROFIT_EBAY_ONLY", "15.0"))
    min_comp_local_only = float(os.getenv("MIN_COMP_LOCAL_ONLY", "20.0"))
    local_max_weight = float(os.getenv("LOCAL_ONLY_MAX_WEIGHT_LBS", "20.0"))
    local_max_side = float(os.getenv("LOCAL_ONLY_MAX_SIDE_IN", "24.0"))
    local_max_ship_ratio = float(os.getenv("LOCAL_ONLY_MAX_SHIPPING_RATIO", "0.40"))
    value_score_cap = float(os.getenv("VALUE_SCORE_CAP_DOLLARS", "200.0"))
    ebay_free_shipping_max_lbs = float(os.getenv("EBAY_FREE_SHIPPING_MAX_LBS", "10.0"))

    comp_price = max(0.0, float(item_data.get("comp_price", 0.0)))
    lowest_comp_with_shipping = max(
        0.0, float(item_data.get("lowest_comp_with_shipping_usd") or comp_price)
    )
    weight_lbs = max(0.1, float(item_data.get("weight_lbs", 1.0)))
    dims = item_data.get("dimensions_in") or [10.0, 8.0, 6.0]
    max_side = max(float(d) for d in dims) if dims else 10.0

    shipping_info = calculate_zone8_shipping(weight_lbs, dims, origin_zip=origin_zip)
    est_shipping = shipping_info["estimated_shipping_usd"]
    ebay_free_shipping = (
        bool(item_data["ebay_free_shipping"])
        if "ebay_free_shipping" in item_data
        else (weight_lbs < ebay_free_shipping_max_lbs)
    )

    if lowest_comp_with_shipping > 0:
        if ebay_free_shipping:
            ebay_list_price = round(max(0.99, lowest_comp_with_shipping - 0.01), 2)
        else:
            ebay_list_price = round(max(0.99, lowest_comp_with_shipping - est_shipping - 0.01), 2)
    else:
        ebay_list_price = 0.0

    landed_ebay_revenue = (ebay_list_price if ebay_free_shipping else ebay_list_price + est_shipping)
    ebay_fees = round((landed_ebay_revenue * ebay_fee_pct) + ebay_fixed_fee, 2) if landed_ebay_revenue > 0 else 0.0
    net_profit = round(landed_ebay_revenue - est_shipping - ebay_fees, 2)

    # Local pickup pricing: FBMP includes +10-15% negotiation buffer; Craigslist uses target price
    raw_fbmp_price = comp_price * (1.0 + fbmp_buffer)
    fbmp_list_price = float(round(raw_fbmp_price)) if raw_fbmp_price < 50 else float(round(raw_fbmp_price / 5.0) * 5)
    craigslist_list_price = float(round(comp_price))

    # Determine if item is heavy/bulky or shipping kills margin
    shipping_ratio = (est_shipping / comp_price) if comp_price > 0 else 1.0
    is_bulky_or_heavy = (
        weight_lbs > local_max_weight
        or max_side > local_max_side
        or shipping_ratio > local_max_ship_ratio
    )

    # Amazon FBA & FBM Economics
    min_profit_fba = float(os.getenv("MIN_PROFIT_AMAZON_FBA", "5.00"))
    min_profit_fbm = float(os.getenv("MIN_PROFIT_AMAZON_FBM", "10.00"))
    amazon_econ = calculate_amazon_economics(item_data, shipping_info)
    fba_net = amazon_econ.get("fba_net_profit_usd")
    fbm_net = amazon_econ.get("fbm_net_profit_usd")

    # Buy Box competition rule: Books/Media allow Used even if Amazon sells New;
    # non-books skip FBA if Amazon itself is a seller on the listing.
    # FBA standard-size eligibility checks physical size/weight (<=20 lbs, <=18" longest side)
    # rather than USPS shipping ratio, since Amazon's fulfillment rates beat USPS on 1-5 lb books.
    amz_blocks_buybox = amazon_econ["amazon_is_seller"] and not amazon_econ["is_media"]
    is_fba_standard_size = weight_lbs <= 20.0 and max_side <= 18.0
    fba_eligible = (
        amazon_econ["matched"]
        and not amz_blocks_buybox
        and is_fba_standard_size
        and fba_net is not None
        and fba_net >= min_profit_fba
    )

    # Assign Routing Bucket
    explicit_bucket = item_data.get("routing_bucket")
    if explicit_bucket and explicit_bucket in ("ALL_THREE", "LOCAL_ONLY", "EBAY_ONLY", "DONATE_OR_LOT", "AMAZON_FBA_STAGE"):
        routing_bucket = explicit_bucket
        routing_reason = f"Explicit override: {explicit_bucket}."
    elif fba_eligible and (fba_net >= net_profit or net_profit < min_profit_ebay_only):
        routing_bucket = "AMAZON_FBA_STAGE"
        routing_reason = (
            f"ASIN {amazon_econ['asin']} ({'Media' if amazon_econ['is_media'] else 'Standard'}) "
            f"nets ${fba_net:.2f} via FBA (vs ${net_profit:.2f} on eBay); stage in FBA Bin."
        )
    elif is_bulky_or_heavy and comp_price >= min_comp_local_only and net_profit < min_profit_all_three:
        routing_bucket = "LOCAL_ONLY"
        routing_reason = (
            f"Heavy/bulky ({weight_lbs} lbs, max side {max_side}\", shipping ${est_shipping:.2f} = "
            f"{shipping_ratio*100:.0f}% of comp) kills eBay margin, strong local comp (${comp_price:.2f})."
        )
    elif net_profit >= min_profit_all_three:
        routing_bucket = "ALL_THREE"
        routing_reason = (
            f"High net profit (${net_profit:.2f} >= ${min_profit_all_three:.2f}) justifies full "
            f"cross-posting on eBay + FB Marketplace + Craigslist."
        )
    elif is_bulky_or_heavy and comp_price >= min_comp_local_only:
        routing_bucket = "LOCAL_ONLY"
        routing_reason = (
            f"Bulky/oversize item ({weight_lbs} lbs, {max_side}\" max side) best suited for local cash pickup."
        )
    elif net_profit >= min_profit_ebay_only:
        routing_bucket = "EBAY_ONLY"
        routing_reason = (
            f"Shippable small/medium item with ${net_profit:.2f} net profit; "
            f"not worth local pickup scheduling friction."
        )
    else:
        routing_bucket = "DONATE_OR_LOT"
        routing_reason = (
            f"eBay Net Profit (${net_profit:.2f}) is below ${min_profit_ebay_only:.2f} minimum threshold; "
            f"recommend bundling into a lot or donating."
        )

    # Optional Amazon FBM cross-post flag (never combined with exclusive FBA staging)
    cross_post_amazon_fbm = bool(
        routing_bucket in ("ALL_THREE", "EBAY_ONLY")
        and amazon_econ["matched"]
        and not amz_blocks_buybox
        and fbm_net is not None
        and fbm_net >= min_profit_fbm
        and fbm_net >= net_profit
    )

    # Stage 3 Priority Score (0-100):
    # Priority = (Uncertainty_Score * 0.6) + (Normalized_Value_Score * 0.4)
    uncertainty_score = max(0.0, min(100.0, float(item_data.get("uncertainty_score", 50.0))))
    normalized_value_score = min(100.0, (comp_price / value_score_cap) * 100.0) if value_score_cap > 0 else 0.0
    priority_score = round((uncertainty_score * 0.6) + (normalized_value_score * 0.4), 1)

    is_high_priority = priority_score >= 50.0 or net_profit >= min_profit_all_three or uncertainty_score >= 60.0

    enriched = dict(item_data)
    enriched.update(
        {
            "shipping": shipping_info,
            "ebay_fees_usd": ebay_fees,
            "estimated_net_profit_usd": net_profit,
            "lowest_comp_with_shipping_usd": round(lowest_comp_with_shipping, 2),
            "ebay_free_shipping": ebay_free_shipping,
            "ebay_list_price_usd": ebay_list_price,
            "fbmp_list_price_usd": fbmp_list_price,
            "craigslist_list_price_usd": craigslist_list_price,
            "routing_bucket": routing_bucket,
            "routing_reason": routing_reason,
            "amazon": amazon_econ,
            "cross_post_amazon_fbm": cross_post_amazon_fbm,
            "fba_bin": item_data.get("fba_bin") or ("FBA_BIN_01" if routing_bucket == "AMAZON_FBA_STAGE" else ""),
            "uncertainty_score": round(uncertainty_score, 1),
            "normalized_value_score": round(normalized_value_score, 1),
            "priority_score": priority_score,
            "review_tier": "HIGH_PRIORITY_GRILL" if is_high_priority else "LOW_PRIORITY_QUICK_APPROVE",
            "status": item_data.get("status", "PENDING_REVIEW"),
        }
    )
    return enriched


def default_vision_template(item_id: str, sku: str, photos: List[str]) -> Dict[str, Any]:
    """Create a default vision_input.json template for an item folder."""
    return {
        "item_id": item_id,
        "sku": sku,
        "photos": photos,
        "brand": "TBD",
        "model_mpn": "TBD",
        "isbn_upc": "",
        "title": f"Draft Item {item_id} - Populate Vision Metadata",
        "category": "General",
        "condition": "Used - Good",
        "condition_notes": "Inspect photos and describe cosmetic condition.",
        "accessories": ["Item only (as pictured)"],
        "weight_lbs": 2.0,
        "dimensions_in": [12.0, 9.0, 4.0],
        "comp_price": 35.00,
        "uncertainty_score": 65,
        "serial_readable": False,
        "untested_areas": ["Power/functional test confirmation needed"],
        "grill_questions": [
            "Is the exact model/serial label legible on the back/bottom?",
            "Has this item been tested for full working functionality?",
            "Are there any hidden flaws or missing accessories not shown in the photos?",
        ],
        "description_detailed": (
            "Condition: Pre-owned in good cosmetic condition. Please review all photos for exact item "
            "condition and included accessories. Ships securely packed with tracking."
        ),
        "description_concise": (
            "Pre-owned in good working and cosmetic condition. Includes everything shown in photos."
        ),
    }


def init_vision_templates(items_dir: Path, force: bool = False) -> List[Dict[str, Any]]:
    """Create or load vision_input.json in each /items/item_XX/ directory and write /items/vision_batch.json."""
    item_dirs = sorted(
        [d for d in items_dir.iterdir() if d.is_dir() and d.name.startswith("item_")]
    )
    batch_list: List[Dict[str, Any]] = []
    date_prefix = datetime.now().strftime("%Y%m%d")

    for idx, folder in enumerate(item_dirs, start=1):
        photos = sorted(
            [
                str(p.as_posix())
                for p in folder.iterdir()
                if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
            ]
        )
        if not photos:
            continue

        sku = f"ITEM-{date_prefix}-{idx:02d}"
        vision_file = folder / "vision_input.json"
        if vision_file.exists() and not force:
            try:
                data = json.loads(vision_file.read_text(encoding="utf-8"))
                data["item_id"] = folder.name
                data["sku"] = data.get("sku") or sku
                data["photos"] = photos
            except Exception:
                data = default_vision_template(folder.name, sku, photos)
        else:
            data = default_vision_template(folder.name, sku, photos)
            vision_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

        batch_list.append(data)

    batch_file = items_dir / "vision_batch.json"
    batch_file.write_text(json.dumps(batch_list, indent=2), encoding="utf-8")
    print(f"[OK] Initialized {len(batch_list)} item vision template(s) in '{batch_file.as_posix()}'.")
    return batch_list


def run_gemini_api_extraction(items_dir: Path) -> None:
    """
    Optional standalone CLI fallback (--use-api): Uses google-genai and GEMINI_API_KEY
    to inspect photos in each /items/item_XX/ folder and write vision_input.json.
    """
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set in .env. Either set GEMINI_API_KEY for standalone --use-api mode "
            "or use the default Agent-Native mode where Antigravity/Claude inspects photos directly."
        )

    from google import genai
    from PIL import Image

    client = genai.Client(api_key=api_key)
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    batch_list = init_vision_templates(items_dir, force=False)
    for entry in batch_list:
        folder = items_dir / entry["item_id"]
        photo_paths = [Path(p) for p in entry["photos"]]
        pil_images = []
        for p in photo_paths[:8]:
            try:
                pil_images.append(Image.open(p))
            except Exception as exc:
                print(f"[WARN] Could not open image {p}: {exc}")

        if not pil_images:
            continue

        prompt = (
            "You are an expert eBay, Facebook Marketplace, and Craigslist resale appraiser.\n"
            "Inspect all attached photos of this item and return ONLY a valid JSON object with these exact keys:\n"
            "{\n"
            '  "brand": "Manufacturer or Brand",\n'
            '  "model_mpn": "Model number / MPN",\n'
            '  "title": "SEO-optimized 80-char max listing title (Brand + Model + Key Specs + Condition)",\n'
            '  "category": "Category name (e.g. Electronics, Audio, Tools, Home, Apparel)",\n'
            '  "condition": "One of: New, Open Box, Used - Like New, Used - Good, Used - Fair, For Parts or Not Working",\n'
            '  "condition_notes": "Detailed cosmetic condition notes observed from photos",\n'
            '  "accessories": ["List of included cables, remotes, accessories visible"],\n'
            '  "weight_lbs": 3.5,\n'
            '  "dimensions_in": [14.0, 10.0, 6.0],\n'
            '  "comp_price": 65.00,\n'
            '  "uncertainty_score": 45,\n'
            '  "serial_readable": true,\n'
            '  "untested_areas": ["Any functionality that cannot be verified from photos alone"],\n'
            '  "grill_questions": ["1-3 specific clarifying questions for the seller about serials, testing, or wear"],\n'
            '  "description_detailed": "Multi-paragraph professional listing description covering features, condition, and included items",\n'
            '  "description_concise": "Clean 2-3 sentence listing description ready for quick approval"\n'
            "}"
        )

        print(f"[API] Analyzing {entry['item_id']} ({len(pil_images)} photo(s)) with {model_name}...")
        response = client.models.generate_content(
            model=model_name,
            contents=[*pil_images, prompt],
            config={"response_mime_type": "application/json"},
        )

        try:
            parsed = json.loads(response.text)
            entry.update(parsed)
            vision_file = folder / "vision_input.json"
            vision_file.write_text(json.dumps(entry, indent=2), encoding="utf-8")
        except Exception as exc:
            print(f"[WARN] Failed to parse Gemini JSON for {entry['item_id']}: {exc}")

    batch_file = items_dir / "vision_batch.json"
    batch_file.write_text(json.dumps(batch_list, indent=2), encoding="utf-8")


def generate_bundle_suggestions(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Suggest logical lots/bundles when multiple DONATE_OR_LOT items exist."""
    low_val_items = [i for i in items if i.get("routing_bucket") == "DONATE_OR_LOT"]
    if len(low_val_items) < 2:
        return []

    by_category: Dict[str, List[Dict[str, Any]]] = {}
    for item in low_val_items:
        cat = str(item.get("category") or "General").strip()
        by_category.setdefault(cat, []).append(item)

    bundles: List[Dict[str, Any]] = []
    for cat, cat_items in by_category.items():
        if len(cat_items) >= 2:
            combined_comp = round(sum(float(i.get("comp_price", 0)) for i in cat_items) * 0.9, 2)
            combined_weight = round(sum(float(i.get("weight_lbs", 1.0)) for i in cat_items) * 0.85, 2)
            bundles.append(
                {
                    "bundle_title": f"Lot of {len(cat_items)} {cat} Items ({', '.join(i['item_id'] for i in cat_items)})",
                    "category": cat,
                    "member_skus": [i["sku"] for i in cat_items],
                    "member_item_ids": [i["item_id"] for i in cat_items],
                    "estimated_bundle_comp_usd": combined_comp,
                    "estimated_bundle_weight_lbs": combined_weight,
                    "recommendation": (
                        f"Combine {', '.join(i['item_id'] for i in cat_items)} into a single '{cat}' lot "
                        f"(Est. bundle price ${combined_comp:.2f}) to save on shipping & per-order fees."
                    ),
                }
            )

    if not bundles and len(low_val_items) >= 2:
        combined_comp = round(sum(float(i.get("comp_price", 0)) for i in low_val_items) * 0.85, 2)
        bundles.append(
            {
                "bundle_title": f"Reseller Mixed Lot ({len(low_val_items)} Items)",
                "category": "Mixed Lot",
                "member_skus": [i["sku"] for i in low_val_items],
                "member_item_ids": [i["item_id"] for i in low_val_items],
                "estimated_bundle_comp_usd": combined_comp,
                "recommendation": (
                    f"Group all {len(low_val_items)} low-margin items ({', '.join(i['item_id'] for i in low_val_items)}) "
                    f"into a single local pickup box lot for ~${combined_comp:.2f} or donate."
                ),
            }
        )

    return bundles


def build_markdown_review_queue(manifest: Dict[str, Any]) -> str:
    """Build Stage 3 interactive Markdown 'Grill Me' review queue sorted by Priority Score."""
    items: List[Dict[str, Any]] = manifest["items"]
    bundles: List[Dict[str, Any]] = manifest.get("bundle_suggestions", [])
    origin_zip = manifest.get("origin_zip", "98101")

    lines: List[str] = []
    lines.append("# Stage 3: Priority Review Queue & \"Grill Me\" Audit")
    lines.append("")
    lines.append(
        f"**Origin ZIP:** `{origin_zip}` (Conservative Zone 8 Shipping) | "
        f"**Total Items:** `{len(items)}` | "
        f"**Formula:** `Priority = (Uncertainty * 0.6) + (Normalized_Value * 0.4)`"
    )
    lines.append("")
    lines.append("## 1. Master Priority Queue (Sorted Highest Priority -> Lowest)")
    lines.append("")
    lines.append(
        "| Priority | SKU (`item_XX`) | Proposed Title | Cond. | Comp ($) | Zone 8 Ship ($) | eBay Net | **AMZ FBA Net** | ASIN | Routing Bucket |"
    )
    lines.append(
        "| :---: | :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |"
    )

    for item in items:
        prio = item["priority_score"]
        sku_label = f"`{item['sku']}` ({item['item_id']})"
        title = item.get("title", "Untitled")
        cond = item.get("condition", "Used")
        comp = item.get("comp_price", 0.0)
        ship = item["shipping"]["estimated_shipping_usd"]
        net = item["estimated_net_profit_usd"]
        amz = item.get("amazon") or {}
        fba_net = amz.get("fba_net_profit_usd")
        fba_str = f"**${fba_net:.2f}**" if fba_net is not None else "—"
        asin_str = f"`{amz['asin']}`" if amz.get("asin") else "—"
        bucket = f"`{item['routing_bucket']}`"
        if item.get("cross_post_amazon_fbm"):
            bucket += " + `AMZ_FBM`"
        lines.append(
            f"| **{prio:.1f}** | {sku_label} | {title} | {cond} | ${comp:.2f} | ${ship:.2f} | ${net:.2f} | {fba_str} | {asin_str} | {bucket} |"
        )

    lines.append("")

    high_items = [i for i in items if i["review_tier"] == "HIGH_PRIORITY_GRILL"]
    low_items = [i for i in items if i["review_tier"] != "HIGH_PRIORITY_GRILL"]

    if high_items:
        lines.append("---")
        lines.append("## 2. High-Priority / High-Value Items — \"Grill Me\" Clarifying Questions")
        lines.append("")
        for item in high_items:
            lines.append(
                f"### [{item['priority_score']:.1f} pts] `{item['sku']}` ({item['item_id']}) — {item.get('title')}"
            )
            lines.append(
                f"- **Brand / Model:** `{item.get('brand', 'Unknown')}` / `{item.get('model_mpn', 'Unknown')}`"
            )
            lines.append(
                f"- **Routing Bucket:** `{item['routing_bucket']}` — *{item['routing_reason']}*"
            )
            lines.append(
                f"- **Pricing Breakdown:** eBay Buy-It-Now: **${item['ebay_list_price_usd']:.2f}** | "
                f"FBMP Local (+Buffer): **${item['fbmp_list_price_usd']:.2f}** | "
                f"Craigslist Local: **${item['craigslist_list_price_usd']:.2f}**"
            )
            lines.append(
                f"- **Shipping Spec:** `{item['shipping']['actual_weight_lbs']} lbs` "
                f"(`{item['shipping']['dimensions_in'][0]}x{item['shipping']['dimensions_in'][1]}x{item['shipping']['dimensions_in'][2]} in`, "
                f"Billable `{item['shipping']['billable_weight_lbs']} lbs` via {item['shipping']['selected_carrier']}: "
                f"**${item['shipping']['estimated_shipping_usd']:.2f}**)"
            )
            lines.append(
                f"- **Included Accessories:** {', '.join(item.get('accessories') or ['None noted'])}"
            )
            lines.append(f"- **Cosmetic Notes:** {item.get('condition_notes', 'N/A')}")
            lines.append("")
            lines.append("#### Clarifying Questions Needed Before Drafting:")
            questions = item.get("grill_questions") or [
                "Please confirm working condition and any flaws not visible in the photos."
            ]
            for q_idx, q_text in enumerate(questions, start=1):
                lines.append(f"{q_idx}. **[?]** {q_text}")
            lines.append("")
            lines.append("#### Draft Detailed Description:")
            lines.append("```text")
            lines.append(str(item.get("description_detailed") or item.get("description_concise") or "").strip())
            lines.append("```")
            lines.append("")

    if low_items:
        lines.append("---")
        lines.append("## 3. Low-Priority / Low-Value Items — Ready for One-Click Approval")
        lines.append("")
        for item in low_items:
            lines.append(
                f"### [{item['priority_score']:.1f} pts] `{item['sku']}` ({item['item_id']}) — {item.get('title')}"
            )
            lines.append(
                f"- **Routing:** `{item['routing_bucket']}` | **Comp:** `${item.get('comp_price', 0):.2f}` | "
                f"**Est. Net Profit:** `${item['estimated_net_profit_usd']:.2f}` | "
                f"**Package:** `{item['shipping']['actual_weight_lbs']} lbs` "
                f"(`{item['shipping']['dimensions_in'][0]}x{item['shipping']['dimensions_in'][1]}x{item['shipping']['dimensions_in'][2]} in`)"
            )
            lines.append("```text")
            lines.append(str(item.get("description_concise") or item.get("description_detailed") or "").strip())
            lines.append("```")
            lines.append("")

    if bundles:
        lines.append("---")
        lines.append("## 4. Suggested Low-Value Bundles (`DONATE_OR_LOT`)")
        lines.append("")
        for b in bundles:
            lines.append(f"- **{b['bundle_title']}**: {b['recommendation']}")
        lines.append("")

    fba_summary = manifest.get("fba_staging_summary") or {}
    if fba_summary.get("item_count", 0) > 0:
        lines.append("---")
        lines.append("## 5. Amazon FBA Staging Bin Status")
        lines.append("")
        lines.append(
            f"- **Current Batch FBA Items:** `{fba_summary['item_count']}` / `{fba_summary['target_items']}` items | "
            f"**Weight:** `{fba_summary['total_weight_lbs']:.1f}` / `{fba_summary['target_weight_lbs']:.1f} lbs`"
        )
        if fba_summary.get("ready_to_ship"):
            lines.append("- **STATUS:** **READY TO SHIP!** Your FBA Staging Bin meets the box threshold — create a *Send to Amazon* plan.")
        else:
            lines.append("- **STATUS:** Accumulate in your physical FBA Staging Bin until you reach 20 items or 25 lbs.")
        lines.append("")

    lines.append("---")
    lines.append(
        "> **ACTION REQUIRED:** Reply with your answers to the clarifying questions above and any "
        "title/price adjustments, or reply **\"Approved\"** to launch Stage 4 Playwright draft creation."
    )
    return "\n".join(lines)


def calculate_all_items(items_dir: Path) -> Dict[str, Any]:
    """
    Read vision metadata from /items/vision_batch.json or individual /items/item_XX/vision_input.json,
    compute shipping/fees/routing/priority scores, and write /items/batch_manifest.json + /items/review_queue.md.
    """
    batch_file = items_dir / "vision_batch.json"
    item_dirs = sorted(
        [d for d in items_dir.iterdir() if d.is_dir() and d.name.startswith("item_")]
    )

    raw_items: List[Dict[str, Any]] = []
    batch_by_id: Dict[str, Dict[str, Any]] = {}
    if batch_file.exists():
        try:
            loaded_batch = json.loads(batch_file.read_text(encoding="utf-8"))
            if isinstance(loaded_batch, list):
                for entry in loaded_batch:
                    if isinstance(entry, dict) and "item_id" in entry:
                        batch_by_id[entry["item_id"]] = entry
        except Exception:
            pass

    date_prefix = datetime.now().strftime("%Y%m%d")
    for idx, folder in enumerate(item_dirs, start=1):
        photos = sorted(
            [
                str(p.as_posix())
                for p in folder.iterdir()
                if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
            ]
        )
        if not photos:
            continue

        vision_file = folder / "vision_input.json"
        item_data: Optional[Dict[str, Any]] = None
        if vision_file.exists():
            try:
                item_data = json.loads(vision_file.read_text(encoding="utf-8"))
            except Exception:
                item_data = None

        # Merge with vision_batch.json if updated there
        if folder.name in batch_by_id:
            if item_data is None:
                item_data = batch_by_id[folder.name]
            else:
                # Prefer non-TBD fields from whichever was updated more recently
                batch_entry = batch_by_id[folder.name]
                if batch_entry.get("brand") not in (None, "TBD"):
                    item_data.update(batch_entry)

        if item_data is None:
            sku = f"ITEM-{date_prefix}-{idx:02d}"
            item_data = default_vision_template(folder.name, sku, photos)

        item_data["item_id"] = folder.name
        item_data["sku"] = item_data.get("sku") or f"ITEM-{date_prefix}-{idx:02d}"
        item_data["folder_path"] = str(folder.as_posix())
        item_data["photos"] = photos

        # Persist synced vision_input.json inside item folder
        vision_file.write_text(json.dumps(item_data, indent=2), encoding="utf-8")

        enriched = compute_economics_and_routing(item_data)
        (folder / "analysis.json").write_text(json.dumps(enriched, indent=2), encoding="utf-8")
        raw_items.append(enriched)

    # Sort from highest Priority Score to lowest (breaking ties by Est Net Profit descending)
    sorted_items = sorted(
        raw_items,
        key=lambda x: (x["priority_score"], x["estimated_net_profit_usd"]),
        reverse=True,
    )

    bundles = generate_bundle_suggestions(sorted_items)

    fba_items = [i for i in sorted_items if i["routing_bucket"] == "AMAZON_FBA_STAGE"]
    fba_target_items = int(os.getenv("FBA_BIN_TARGET_ITEMS", "20"))
    fba_target_lbs = float(os.getenv("FBA_BIN_TARGET_LBS", "25.0"))
    fba_total_lbs = round(sum(float(i["shipping"]["actual_weight_lbs"]) for i in fba_items), 2)

    manifest = {
        "generated_at": datetime.now().isoformat(),
        "origin_zip": os.getenv("ORIGIN_ZIP", "95126"),
        "craigslist_subdomain": os.getenv("CRAIGSLIST_SUBDOMAIN", "sfbay"),
        "craigslist_area": os.getenv("CRAIGSLIST_AREA", ""),
        "craigslist_city": os.getenv("CRAIGSLIST_CITY", ""),
        "total_items": len(sorted_items),
        "bucket_counts": {
            bucket: sum(1 for i in sorted_items if i["routing_bucket"] == bucket)
            for bucket in ("ALL_THREE", "LOCAL_ONLY", "EBAY_ONLY", "AMAZON_FBA_STAGE", "DONATE_OR_LOT")
        },
        "fba_staging_summary": {
            "item_count": len(fba_items),
            "total_weight_lbs": fba_total_lbs,
            "target_items": fba_target_items,
            "target_weight_lbs": fba_target_lbs,
            "ready_to_ship": len(fba_items) >= fba_target_items or fba_total_lbs >= fba_target_lbs,
        },
        "bundle_suggestions": bundles,
        "items": sorted_items,
    }

    manifest_path = items_dir / "batch_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    review_md = build_markdown_review_queue(manifest)
    review_path = items_dir / "review_queue.md"
    review_path.write_text(review_md, encoding="utf-8")

    print("=" * 72)
    print(f"STAGE 2 & 3 COMPLETE: Analyzed {len(sorted_items)} item(s) | Origin ZIP: {manifest['origin_zip']}")
    print(f"Manifest: {manifest_path.as_posix()} | Review Queue: {review_path.as_posix()}")
    print("=" * 72)
    for item in sorted_items:
        print(
            f"  [{item['priority_score']:5.1f}] {item['sku']} ({item['item_id']}) | "
            f"{item['routing_bucket']:13s} | Comp: ${item.get('comp_price', 0):6.2f} | "
            f"Ship: ${item['shipping']['estimated_shipping_usd']:5.2f} | "
            f"Net: ${item['estimated_net_profit_usd']:6.2f} | {item.get('title', '')[:40]}"
        )

    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage 2 & 3: Multimodal Vision Extraction, Zone 8 Shipping Math, Routing & Priority Scoring."
    )
    parser.add_argument(
        "--items-dir",
        type=Path,
        default=Path("items"),
        help="Path to /items directory (default: items)",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Initialize vision_input.json templates in each /items/item_XX/ folder and /items/vision_batch.json",
    )
    parser.add_argument(
        "--force-init",
        action="store_true",
        help="Overwrite existing vision_input.json templates with fresh defaults",
    )
    parser.add_argument(
        "--use-api",
        action="store_true",
        help="Use standalone Gemini Vision API (requires GEMINI_API_KEY in .env) to populate vision_input.json",
    )
    parser.add_argument(
        "--calculate",
        action="store_true",
        help="Calculate Zone 8 shipping, eBay fees, routing buckets, priority scores, and build review_queue.md",
    )

    args = parser.parse_args()
    args.items_dir.mkdir(parents=True, exist_ok=True)

    if args.init or args.force_init:
        init_vision_templates(args.items_dir, force=args.force_init)
        if not args.calculate and not args.use_api:
            return 0

    if args.use_api:
        run_gemini_api_extraction(args.items_dir)
        calculate_all_items(args.items_dir)
        return 0

    # Default action if --calculate or no flags passed: ensure templates exist and calculate
    init_vision_templates(args.items_dir, force=False)
    calculate_all_items(args.items_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
