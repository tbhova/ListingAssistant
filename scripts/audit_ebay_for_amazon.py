#!/usr/bin/env python3
"""
eBay Active Inventory -> Amazon FBA/FBM Migration & Box Builder (scripts/audit_ebay_for_amazon.py)

Workflow:
  1. Connects to Chrome CDP (http://localhost:9222) and scrapes active listings from
     eBay Seller Hub (`https://www.ebay.com/sh/lst/active`).
     (Also supports `--from-json <path>` if you want to test or re-run offline.)
  2. Matches each active eBay listing on Amazon.com via `match_amazon.fetch_amazon_match_via_playwright`
     to retrieve ASIN, BSR, New/Used prices, Media status, and Sold-by-Amazon competition.
  3. Runs `analyze_items.calculate_amazon_economics` + eBay net profit math to classify each item:
       - MOVE_TO_FBA_BOX: Good FBA profit (>= MIN_PROFIT_AMAZON_FBA) and/or saves individual
         packing/shipping effort on lower-dollar items.
       - CROSS_POST_FBM: Better kept at home and cross-posted to Amazon FBM alongside eBay.
       - KEEP_EBAY_ONLY: Keep on eBay only (not on Amazon, Amazon-dominated, or negative FBA margin).
  4. Evaluates whether the `MOVE_TO_FBA_BOX` items form a viable FBA box (target: 20 items or 25 lbs)
     and writes `items/ebay_to_amazon_audit.md` + `items/ebay_active_audit.json`.

CRITICAL: This script is read-only on your live eBay listings — it NEVER ends or unlists
any eBay item automatically. Ending eBay listings happens only after your explicit approval.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Allow importing sibling scripts
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from analyze_items import calculate_amazon_economics, calculate_zone8_shipping
from match_amazon import fetch_amazon_match_via_playwright, parse_price


EBAY_ITEM_ID_RE = re.compile(r"/itm/(?:[^/]+/)?(\d{9,14})")


async def scrape_ebay_active_listings(page: Any, max_pages: int = 5) -> List[Dict[str, Any]]:
    """Scrape active listings from eBay Seller Hub (https://www.ebay.com/sh/lst/active)."""
    listings: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()

    url = "https://www.ebay.com/sh/lst/active"
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(3500)

    if "signin.ebay.com" in page.url.lower():
        raise RuntimeError(
            "eBay Seller Hub redirected to sign-in. Log into eBay in your dedicated Chrome "
            "profile first (`python scripts/launch_chrome.py --login-setup`)."
        )

    for page_num in range(1, max_pages + 1):
        # Seller Hub active listings table rows
        rows = page.locator("tr.grid-row, table tbody tr")
        row_count = await rows.count()
        print(f"[eBay] Page {page_num}: found {row_count} table row(s)...", flush=True)

        for idx in range(row_count):
            row = rows.nth(idx)
            try:
                row_text = (await row.inner_text() or "").strip()
                if not row_text or len(row_text) < 10:
                    continue

                # Find item link (/itm/...)
                link_loc = row.locator("a[href*='/itm/']").first
                href = ""
                title = ""
                if await link_loc.count() > 0:
                    href = (await link_loc.get_attribute("href") or "").strip()
                    title = (await link_loc.inner_text() or "").strip()
                else:
                    # Fallback: first title link in row
                    any_link = row.locator("a").first
                    if await any_link.count() > 0:
                        href = (await any_link.get_attribute("href") or "").strip()
                        title = (await any_link.inner_text() or "").strip()

                if not title:
                    continue

                m_id = EBAY_ITEM_ID_RE.search(href)
                item_id = m_id.group(1) if m_id else f"EBAY-{page_num}-{idx}"
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)

                # Extract price from row text
                price = parse_price(row_text) or 0.0
                if price <= 0:
                    continue

                # Heuristic: estimate book/media weight if title suggests book, else 1.5 lbs default
                lower_t = title.lower()
                is_book_hint = any(
                    kw in lower_t
                    for kw in ("book", "hardcover", "paperback", "edition", "isbn", "guide", "manual", "textbook")
                )
                est_weight = 1.5 if is_book_hint else 2.0

                listings.append(
                    {
                        "ebay_item_id": item_id,
                        "sku": f"EBAY-{item_id[-6:]}",
                        "title": title[:120],
                        "ebay_url": href if href.startswith("http") else f"https://www.ebay.com/itm/{item_id}",
                        "comp_price": price,
                        "condition": "Used - Good",
                        "category": "Books" if is_book_hint else "General",
                        "weight_lbs": est_weight,
                        "dimensions_in": [10.0, 8.0, 2.0] if is_book_hint else [12.0, 9.0, 5.0],
                    }
                )
            except Exception:
                continue

        # Check for Next page button in Seller Hub pagination
        next_btn = page.locator("button[aria-label*='Next page'], a[aria-label*='Next page']").first
        if await next_btn.count() > 0 and await next_btn.is_enabled():
            await next_btn.click()
            await page.wait_for_timeout(2500)
        else:
            break

    return listings


def evaluate_migration_candidate(item: Dict[str, Any]) -> Dict[str, Any]:
    """Compare current eBay net profit vs. Amazon FBA & FBM net profit and recommend action."""
    origin_zip = os.getenv("ORIGIN_ZIP", "95126")
    ebay_fee_pct = float(os.getenv("EBAY_FEE_PERCENT", "0.135"))
    ebay_fixed_fee = float(os.getenv("EBAY_FIXED_FEE", "0.40"))
    min_profit_fba = float(os.getenv("MIN_PROFIT_AMAZON_FBA", "5.00"))
    min_profit_fbm = float(os.getenv("MIN_PROFIT_AMAZON_FBM", "10.00"))

    ebay_price = float(item.get("comp_price") or 0.0)
    weight_lbs = float(item.get("weight_lbs") or 1.5)
    dims = item.get("dimensions_in") or [10.0, 8.0, 4.0]

    shipping_info = calculate_zone8_shipping(weight_lbs, dims, origin_zip=origin_zip)
    zone8_ship = shipping_info["estimated_shipping_usd"]
    ebay_fees = round((ebay_price * ebay_fee_pct) + ebay_fixed_fee, 2) if ebay_price > 0 else 0.0
    ebay_net = round(ebay_price - zone8_ship - ebay_fees, 2)

    amz = calculate_amazon_economics(item, shipping_info)
    fba_net = amz.get("fba_net_profit_usd")
    fbm_net = amz.get("fbm_net_profit_usd")
    amz_blocks = amz["amazon_is_seller"] and not amz["is_media"]

    recommendation = "KEEP_EBAY_ONLY"
    reason = "No viable Amazon match or better margin on eBay."

    if amz["matched"] and not amz_blocks:
        # Favor FBA for low-dollar / individual-shipping-drag items if FBA net >= min_profit_fba
        # and FBA beats eBay OR eBay net is < $12 while FBA net >= $5 (saves packing/shipping time!)
        if fba_net is not None and fba_net >= min_profit_fba and (fba_net >= ebay_net or ebay_net < 12.0):
            recommendation = "MOVE_TO_FBA_BOX"
            reason = (
                f"FBA nets ${fba_net:.2f} (vs eBay ${ebay_net:.2f}); bundling into an FBA box "
                f"eliminates individual packing/shipping."
            )
        elif fbm_net is not None and fbm_net >= min_profit_fbm and fbm_net >= ebay_net:
            recommendation = "CROSS_POST_FBM"
            reason = f"Amazon FBM nets ${fbm_net:.2f} (>= eBay ${ebay_net:.2f}); keep at home & cross-post."
        elif fba_net is not None and fba_net > 0:
            reason = f"FBA nets ${fba_net:.2f}, below ${min_profit_fba:.2f} min or trails eBay (${ebay_net:.2f})."
    elif amz["matched"] and amz_blocks:
        reason = "Amazon.com sells directly on this non-media listing (Buy Box risk)."

    enriched = dict(item)
    enriched.update(
        {
            "shipping": shipping_info,
            "ebay_fees_usd": ebay_fees,
            "ebay_net_profit_usd": ebay_net,
            "amazon": amz,
            "recommendation": recommendation,
            "recommendation_reason": reason,
        }
    )
    return enriched


def build_audit_markdown(results: List[Dict[str, Any]]) -> str:
    """Build interactive Markdown report for eBay -> Amazon FBA/FBM audit."""
    fba_items = [r for r in results if r["recommendation"] == "MOVE_TO_FBA_BOX"]
    fbm_items = [r for r in results if r["recommendation"] == "CROSS_POST_FBM"]
    keep_items = [r for r in results if r["recommendation"] == "KEEP_EBAY_ONLY"]

    target_items = int(os.getenv("FBA_BIN_TARGET_ITEMS", "20"))
    target_lbs = float(os.getenv("FBA_BIN_TARGET_LBS", "25.0"))
    fba_total_lbs = round(sum(float(r.get("weight_lbs") or 1.5) for r in fba_items), 2)
    fba_total_profit = round(sum(float((r.get("amazon") or {}).get("fba_net_profit_usd") or 0.0) for r in fba_items), 2)
    inbound_per_lb = float(os.getenv("AMAZON_FBA_INBOUND_PER_LB", "0.50"))
    est_box_inbound_cost = round(fba_total_lbs * inbound_per_lb, 2)

    lines: List[str] = []
    lines.append("# eBay Active Inventory -> Amazon FBA / FBM Audit")
    lines.append("")
    lines.append(
        f"**Scanned:** `{len(results)}` eBay listing(s) | "
        f"**Move to FBA Box:** `{len(fba_items)}` | "
        f"**Cross-Post FBM:** `{len(fbm_items)}` | "
        f"**Keep eBay Only:** `{len(keep_items)}`"
    )
    lines.append("")
    lines.append("## 1. Proposed FBA Box Summary")
    lines.append(
        f"- **Box Volume:** `{len(fba_items)}` / `{target_items}` items | "
        f"`{fba_total_lbs:.1f}` / `{target_lbs:.1f} lbs`"
    )
    lines.append(f"- **Est. Inbound UPS Box Cost:** `~${est_box_inbound_cost:.2f}` (`${inbound_per_lb:.2f}/lb` already factored into FBA Net)")
    lines.append(f"- **Total Projected FBA Net Profit:** **`${fba_total_profit:.2f}`**")
    if len(fba_items) >= target_items or fba_total_lbs >= target_lbs:
        lines.append("- **Box Status:** **READY TO SHIP!** You have enough items/weight for an economical FBA box.")
    else:
        lines.append("- **Box Status:** **ACCUMULATE.** Good FBA candidates found — stage them in your FBA bin until you hit ~20 items or ~25 lbs.")
    lines.append("")

    if fba_items:
        lines.append("## 2. Recommended to Unlist from eBay & Move to FBA Box")
        lines.append("")
        lines.append("| SKU (`eBay ID`) | Title | ASIN | BSR | eBay Net | **FBA Net** | Wt (lbs) |")
        lines.append("| :--- | :--- | :---: | :---: | :---: | :---: | :---: |")
        for r in sorted(fba_items, key=lambda x: float((x["amazon"] or {}).get("fba_net_profit_usd") or 0), reverse=True):
            amz = r["amazon"]
            bsr = f"#{amz['bsr_rank']:,}" if amz.get("bsr_rank") else "—"
            lines.append(
                f"| `{r['sku']}` | {r['title'][:55]} | `{amz['asin']}` | {bsr} | "
                f"${r['ebay_net_profit_usd']:.2f} | **${amz['fba_net_profit_usd']:.2f}** | {r['weight_lbs']} |"
            )
        lines.append("")

    if fbm_items:
        lines.append("## 3. Recommended to Cross-Post on Amazon FBM (Keep at Home)")
        lines.append("")
        lines.append("| SKU | Title | ASIN | eBay Net | **AMZ FBM Net** |")
        lines.append("| :--- | :--- | :---: | :---: | :---: |")
        for r in fbm_items:
            amz = r["amazon"]
            lines.append(
                f"| `{r['sku']}` | {r['title'][:55]} | `{amz['asin']}` | "
                f"${r['ebay_net_profit_usd']:.2f} | **${amz['fbm_net_profit_usd']:.2f}** |"
            )
        lines.append("")

    if keep_items:
        lines.append("## 4. Keep on eBay Only")
        lines.append("")
        for r in keep_items[:25]:
            lines.append(f"- `{r['sku']}` ({r['title'][:50]}): {r['recommendation_reason']}")
        lines.append("")

    lines.append("---")
    lines.append(
        "> **NEXT STEP:** Review the table above. Tell me which SKUs you approve to **Move to FBA Box** "
        "(and unlist from eBay) or **Cross-Post to Amazon FBM**."
    )
    return "\n".join(lines)


async def run_audit(
    output_dir: Path,
    from_json: Optional[Path] = None,
    max_pages: int = 5,
    limit: Optional[int] = None,
) -> int:
    """Scan eBay active listings (or load from JSON), match on Amazon, and generate FBA Box report."""
    output_dir.mkdir(parents=True, exist_ok=True)

    from playwright.async_api import async_playwright

    cdp_url = os.getenv("CHROME_CDP_URL", "http://localhost:9222")
    p_cm = await async_playwright().start()
    browser = None
    own_browser = False

    try:
        try:
            browser = await p_cm.chromium.connect_over_cdp(cdp_url)
            print(f"[OK] Connected to Chrome CDP at {cdp_url}.", flush=True)
        except Exception:
            if from_json is None:
                await p_cm.stop()
                print(
                    f"[ERROR] Chrome CDP is not running on {cdp_url}.\n"
                    f"        Run: .venv\\Scripts\\python.exe scripts/launch_chrome.py --check-or-launch"
                )
                return 1
            browser = await p_cm.chromium.launch(headless=True)
            own_browser = True

        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await context.new_page()

        if from_json and from_json.exists():
            raw_listings = json.loads(from_json.read_text(encoding="utf-8"))
        else:
            raw_listings = await scrape_ebay_active_listings(page, max_pages=max_pages)

        if limit:
            raw_listings = raw_listings[:limit]

        print(f"[AUDIT] Matching {len(raw_listings)} eBay listing(s) against Amazon.com...", flush=True)
        evaluated: List[Dict[str, Any]] = []
        for idx, item in enumerate(raw_listings, start=1):
            print(f"  ({idx}/{len(raw_listings)}) {item['title'][:60]}...", flush=True)
            try:
                match = await fetch_amazon_match_via_playwright(page, item["title"][:80])
                if match:
                    item.update(match)
            except Exception as exc:
                print(f"    [WARN] Amazon lookup error: {exc}", flush=True)
            evaluated.append(evaluate_migration_candidate(item))

        audit_json_path = output_dir / "ebay_active_audit.json"
        audit_json_path.write_text(json.dumps(evaluated, indent=2), encoding="utf-8")

        audit_md = build_audit_markdown(evaluated)
        audit_md_path = output_dir / "ebay_to_amazon_audit.md"
        audit_md_path.write_text(audit_md, encoding="utf-8")

        print("=" * 72)
        print(f"AUDIT COMPLETE: {len(evaluated)} eBay listing(s) evaluated.")
        print(f"Report: {audit_md_path.as_posix()} | JSON: {audit_json_path.as_posix()}")
        print("=" * 72)
        return 0
    finally:
        if own_browser and browser is not None:
            await browser.close()
        await p_cm.stop()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan active eBay listings, match on Amazon, and build an optimal FBA Box recommendation."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("items"),
        help="Directory to write ebay_to_amazon_audit.md and ebay_active_audit.json (default: items)",
    )
    parser.add_argument(
        "--from-json",
        type=Path,
        default=None,
        help="Optional JSON file of eBay listings to audit instead of scraping Seller Hub",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=5,
        help="Max Seller Hub pages to scan (default: 5)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap on number of listings to match on Amazon",
    )

    args = parser.parse_args()
    return asyncio.run(
        run_audit(
            output_dir=args.output_dir,
            from_json=args.from_json,
            max_pages=args.max_pages,
            limit=args.limit,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
