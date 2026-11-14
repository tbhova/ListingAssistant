#!/usr/bin/env python3
"""
Stage 2A: Amazon Product Page Matcher (scripts/match_amazon.py)

Matches items in `/items/item_XX/` to Amazon.com catalog pages by ISBN, UPC,
Brand + Model/MPN, or Title. Extracts:
  - ASIN & Amazon product URL
  - Amazon catalog title & whether it is a Media item (Books/DVD/Music/Games)
  - Best Sellers Rank (BSR)
  - Whether Amazon.com itself is a seller ("Sold by Amazon.com")
  - Competitive New / Used offer prices

Writes the match results back into `/items/item_XX/vision_input.json` so
`scripts/analyze_items.py --calculate` can compute Amazon FBA & FBM economics.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


ASIN_RE = re.compile(r"/dp/([A-Z0-9]{10})")
PRICE_RE = re.compile(r"\$(\d{1,4}(?:,\d{3})*(?:\.\d{2})?)")
BSR_RE = re.compile(r"#([\d,]+)\s+in\s+([^\(\n]+)", re.IGNORECASE)


def build_search_query(item_data: Dict[str, Any]) -> str:
    """Build the highest-precision Amazon search query from vision metadata."""
    isbn_upc = str(item_data.get("isbn_upc") or "").strip()
    if isbn_upc and isbn_upc.upper() not in ("TBD", "NONE", "N/A"):
        return isbn_upc

    brand = str(item_data.get("brand") or "").strip()
    model = str(item_data.get("model_mpn") or "").strip()
    if brand not in ("", "TBD", "Unknown", "Unbranded") and model not in ("", "TBD", "Unknown"):
        return f"{brand} {model}"

    title = str(item_data.get("title") or "").strip()
    return title[:80]


def parse_price(text: str) -> Optional[float]:
    """Extract first USD price float from text."""
    if not text:
        return None
    m = PRICE_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


async def fetch_amazon_match_via_playwright(
    page: Any,
    query: str,
) -> Optional[Dict[str, Any]]:
    """Search Amazon.com and inspect the top matching product page."""
    encoded = urllib.parse.quote_plus(query)
    search_url = f"https://www.amazon.com/s?k={encoded}"
    await page.goto(search_url, wait_until="domcontentloaded")
    await page.wait_for_timeout(1800)

    # Find first non-sponsored search result with data-asin
    results = page.locator("div[data-component-type='s-search-result'][data-asin]")
    count = await results.count()
    asin: Optional[str] = None
    search_title = ""
    search_price: Optional[float] = None

    for idx in range(min(count, 6)):
        card = results.nth(idx)
        candidate_asin = (await card.get_attribute("data-asin") or "").strip()
        if not candidate_asin or len(candidate_asin) != 10:
            continue
        card_text = (await card.inner_text() or "")
        if "Sponsored" in card_text[:60]:
            continue
        asin = candidate_asin
        try:
            h2_loc = card.locator("h2").first
            if await h2_loc.count() > 0:
                search_title = (await h2_loc.inner_text() or "").strip()
        except Exception:
            pass
        search_price = parse_price(card_text)
        break

    if not asin:
        return None

    dp_url = f"https://www.amazon.com/dp/{asin}"
    await page.goto(dp_url, wait_until="domcontentloaded")
    await page.wait_for_timeout(1800)

    body_text = ""
    try:
        body_text = await page.locator("body").inner_text()
    except Exception:
        pass

    product_title = search_title
    try:
        t_loc = page.locator("#productTitle").first
        if await t_loc.count() > 0:
            product_title = (await t_loc.inner_text() or "").strip()
    except Exception:
        pass

    # Detect Buy Box / Main Price
    new_price = search_price
    for sel in [
        "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
        "#corePrice_feature_div .a-price .a-offscreen",
        "#price_inside_buybox",
        "#priceblock_ourprice",
    ]:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                p_val = parse_price(await loc.inner_text() or "")
                if p_val:
                    new_price = p_val
                    break
        except Exception:
            continue

    # Detect Used Price ("Used: $XX.XX" or "Other sellers on Amazon")
    used_price: Optional[float] = None
    used_match = re.search(r"(?:Used|Pre-owned|Buy used)[:\s]*\$(\d{1,4}(?:,\d{3})*(?:\.\d{2})?)", body_text, re.IGNORECASE)
    if used_match:
        try:
            used_price = float(used_match.group(1).replace(",", ""))
        except ValueError:
            pass

    # Detect if Amazon.com itself is selling on this listing
    amazon_is_seller = bool(
        re.search(r"Ships from\s+Amazon\.com[\s\S]{0,80}Sold by\s+Amazon\.com", body_text, re.IGNORECASE)
        or re.search(r"Sold by\s+Amazon\.com", body_text, re.IGNORECASE)
    )

    # Extract Best Sellers Rank (BSR)
    bsr_rank: Optional[int] = None
    bsr_category: str = ""
    bsr_match = BSR_RE.search(body_text)
    if bsr_match:
        try:
            bsr_rank = int(bsr_match.group(1).replace(",", ""))
            bsr_category = bsr_match.group(2).strip().rstrip("(").strip()
        except ValueError:
            pass

    # Media detection (Books, Kindle, Movies, Music, Video Games)
    is_media = any(
        kw in (bsr_category.lower() + " " + product_title.lower())
        for kw in ("books", "paperback", "hardcover", "dvd", "blu-ray", "cd", "video games")
    )

    return {
        "amazon_asin": asin,
        "amazon_url": dp_url,
        "amazon_title": product_title,
        "amazon_is_media": is_media,
        "amazon_bsr_rank": bsr_rank,
        "amazon_bsr_category": bsr_category,
        "amazon_is_seller": amazon_is_seller,
        "amazon_new_price": new_price,
        "amazon_used_price": used_price,
        "amazon_match_query": query,
    }


async def run_amazon_matcher(
    items_dir: Path,
    sku_filter: Optional[str] = None,
    use_cdp: bool = True,
) -> int:
    """Match eligible items in /items/item_XX/ against Amazon.com."""
    item_dirs = sorted(
        [d for d in items_dir.iterdir() if d.is_dir() and d.name.startswith("item_")]
    )
    if not item_dirs:
        print(f"[INFO] No item_XX folders found in '{items_dir.as_posix()}'.")
        return 0

    from playwright.async_api import async_playwright

    cdp_url = os.getenv("CHROME_CDP_URL", "http://localhost:9222")
    p_cm = await async_playwright().start()
    browser = None
    own_browser = False

    try:
        if use_cdp:
            try:
                browser = await p_cm.chromium.connect_over_cdp(cdp_url)
                print(f"[OK] Connected to Chrome CDP at {cdp_url} for Amazon matching.")
            except Exception:
                browser = None

        if browser is None:
            browser = await p_cm.chromium.launch(headless=True)
            own_browser = True
            print("[INFO] Launched headless Chromium for Amazon product matching.")

        context = browser.contexts[0] if browser.contexts else await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        matched_count = 0
        for folder in item_dirs:
            vision_file = folder / "vision_input.json"
            if not vision_file.exists():
                continue

            item_data = json.loads(vision_file.read_text(encoding="utf-8"))
            if sku_filter and sku_filter.lower() not in (
                str(item_data.get("sku", "")).lower(),
                folder.name.lower(),
            ):
                continue

            query = build_search_query(item_data)
            if not query or "Populate Vision Metadata" in query:
                print(f"[SKIP] {folder.name}: Vision metadata not populated yet.")
                continue

            print(f"[MATCH] {folder.name} -> Searching Amazon for: '{query}'")
            try:
                match = await fetch_amazon_match_via_playwright(page, query)
            except Exception as exc:
                print(f"  [WARN] Amazon lookup error for {folder.name}: {exc}")
                match = None

            if match:
                item_data.update(match)
                vision_file.write_text(json.dumps(item_data, indent=2), encoding="utf-8")
                matched_count += 1
                print(
                    f"  [OK] ASIN={match['amazon_asin']} | Used=${match['amazon_used_price']} | "
                    f"New=${match['amazon_new_price']} | BSR=#{match['amazon_bsr_rank']} | "
                    f"AmzSeller={match['amazon_is_seller']}"
                )
            else:
                print(f"  [MISS] No matching Amazon product found for '{query}'.")

        print(f"\n[DONE] Matched {matched_count} item(s) to Amazon. Run `analyze_items.py --calculate` next.")
        return 0
    finally:
        if own_browser and browser is not None:
            await browser.close()
        await p_cm.stop()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage 2A: Match /items/item_XX/ items to Amazon.com ASIN, BSR, and Prices."
    )
    parser.add_argument(
        "--items-dir",
        type=Path,
        default=Path("items"),
        help="Path to /items directory (default: items)",
    )
    parser.add_argument(
        "--sku",
        type=str,
        default=None,
        help="Optional single SKU or item_XX ID to match",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Force headless Chromium instead of connecting to Chrome CDP on port 9222",
    )

    args = parser.parse_args()
    return asyncio.run(
        run_amazon_matcher(
            items_dir=args.items_dir,
            sku_filter=args.sku,
            use_cdp=not args.headless,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
