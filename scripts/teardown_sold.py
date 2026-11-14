#!/usr/bin/env python3
"""
Stage 5 Helper: Sold SKU Cross-Post Teardown (scripts/teardown_sold.py)

Usage:
  .venv\\Scripts\\python.exe scripts/teardown_sold.py <SKU_OR_TITLE> [--sold-on ebay|fbmp|craigslist|amazon] [--dry-run]

Looks up `<SKU_OR_TITLE>` (matches SKU, item_XX, or partial title) in `items/master_inventory.csv` /
`items/batch_manifest.json`, connects to Chrome CDP (`http://localhost:9222`), and removes the live
listings via each platform's seller dashboard with post-reload verification:
  - Craigslist: `https://accounts.craigslist.org/login/home` -> row-scoped `delete` -> verify `Deleted`
  - Facebook Marketplace: `https://www.facebook.com/marketplace/you/selling` -> `More options` -> `Delete listing` -> confirm `Delete` -> verify gone
  - eBay: `https://www.ebay.com/sh/lst/active` -> row `Actions` -> visible `End listing` -> confirm `End listing` -> verify gone

Finally marks the SKU status as `SOLD` in `items/batch_manifest.json`, `items/master_inventory.csv`,
and Google Sheets (if configured).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def lookup_sku_record(
    query: str,
    csv_path: Path,
    manifest_path: Path,
) -> Optional[Dict[str, str]]:
    """Find SKU record by SKU, item_id, or partial title."""
    target = query.strip().lower()
    target_norm = _norm(query)

    # 1. Check batch_manifest.json first (has SKU, item_id, and title)
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            items = manifest.get("items", [])
            # Exact SKU or item_id match
            for item in items:
                if (
                    str(item.get("sku", "")).lower() == target
                    or str(item.get("item_id", "")).lower() == target
                ):
                    return {
                        "SKU": str(item.get("sku", "")),
                        "Item Title": str(item.get("title", "")),
                        "Routing Bucket": str(item.get("routing_bucket", "")),
                        "Est Net Profit": f"${float(item.get('estimated_net_profit_usd', 0.0)):.2f}",
                        "Status": str(item.get("status", "")),
                        "eBay Draft/Item URL": str(item.get("ebay_draft_url", "")),
                        "FBMP Listing URL": str(item.get("fbmp_listing_url", "")),
                        "Craigslist Edit URL": str(item.get("craigslist_edit_url", "")),
                        "Local Folder Path": str(item.get("folder_path", "")),
                    }
            # Partial title match fallback
            if target_norm:
                for item in items:
                    if target_norm in _norm(str(item.get("title", ""))):
                        return {
                            "SKU": str(item.get("sku", "")),
                            "Item Title": str(item.get("title", "")),
                            "Routing Bucket": str(item.get("routing_bucket", "")),
                            "Est Net Profit": f"${float(item.get('estimated_net_profit_usd', 0.0)):.2f}",
                            "Status": str(item.get("status", "")),
                            "eBay Draft/Item URL": str(item.get("ebay_draft_url", "")),
                            "FBMP Listing URL": str(item.get("fbmp_listing_url", "")),
                            "Craigslist Edit URL": str(item.get("craigslist_edit_url", "")),
                            "Local Folder Path": str(item.get("folder_path", "")),
                        }
        except Exception:
            pass

    # 2. Check master_inventory.csv
    if csv_path.exists():
        try:
            with csv_path.open("r", encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))
                for row in rows:
                    if (row.get("SKU") or "").strip().lower() == target:
                        return dict(row)
                if target_norm:
                    for row in rows:
                        if target_norm in _norm(row.get("Item Title") or ""):
                            return dict(row)
        except Exception:
            pass

    return None


async def _teardown_craigslist(ctx, title: str, cl_url: str) -> str:
    """Delete active Craigslist posting matching title or manage URL, then verify."""
    page = await ctx.new_page()
    try:
        await page.goto("https://accounts.craigslist.org/login/home", wait_until="domcontentloaded")
        await page.wait_for_timeout(1800)

        # Match by manage ID if present, otherwise by title
        manage_match = re.search(r"/manage/([A-Za-z0-9]+)", cl_url or "")
        rows = page.locator("table.accthp_postings tr")
        count = await rows.count()
        target_row = None

        for idx in range(count):
            row = rows.nth(idx)
            txt = (await row.inner_text()).replace("\n", " ").strip()
            if not txt.startswith("Active"):
                continue
            if manage_match:
                has_id = await row.locator(f"form[action*='{manage_match.group(1)}']").count() > 0
                if has_id:
                    target_row = row
                    break
            if _norm(title) and _norm(title)[:35] in _norm(txt):
                target_row = row
                break

        if target_row is None:
            return "ALREADY_GONE"

        del_btn = target_row.locator("input[type='submit'][value='delete']").first
        if await del_btn.count() == 0:
            return "FAILED (no delete button in row)"

        await del_btn.click()
        await page.wait_for_timeout(2500)

        # Reload and verify
        await page.goto("https://accounts.craigslist.org/login/home", wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        still_active = False
        for idx in range(await page.locator("table.accthp_postings tr").count()):
            txt = (await page.locator("table.accthp_postings tr").nth(idx).inner_text()).replace("\n", " ").strip()
            if txt.startswith("Active") and _norm(title)[:35] in _norm(txt):
                still_active = True
                break
        return "FAILED (still Active)" if still_active else "DELETED"
    finally:
        await page.close()


async def _teardown_fbmp(ctx, title: str) -> str:
    """Delete active FBMP listing via More options -> Delete listing -> Delete, then verify."""
    page = await ctx.new_page()
    try:
        await page.goto("https://www.facebook.com/marketplace/you/selling", wait_until="domcontentloaded")
        await page.wait_for_timeout(3500)

        # Exact aria-label match first, then normalized partial fallback
        more_btn = page.locator(f'[aria-label="More options for {title}"]').first
        if await more_btn.count() == 0:
            all_more = page.locator('[aria-label^="More options for "]')
            for idx in range(await all_more.count()):
                lbl = (await all_more.nth(idx).get_attribute("aria-label") or "").replace("More options for ", "")
                if _norm(title)[:35] in _norm(lbl):
                    more_btn = all_more.nth(idx)
                    title = lbl
                    break

        if await more_btn.count() == 0:
            return "ALREADY_GONE"

        await more_btn.click()
        await page.wait_for_timeout(1500)

        del_item = page.locator('[role="menuitem"]:has-text("Delete listing"), span:has-text("Delete listing")').first
        if await del_item.count() == 0 or not await del_item.is_visible():
            return "FAILED (Delete listing menu item not visible)"

        await del_item.click()
        await page.wait_for_timeout(1500)

        confirm = page.locator('div[role="dialog"] div[role="button"]:has-text("Delete"), div[role="dialog"] button:has-text("Delete")').last
        if await confirm.count() > 0 and await confirm.is_visible():
            await confirm.click()
            await page.wait_for_timeout(3000)

        # Reload and verify
        await page.goto("https://www.facebook.com/marketplace/you/selling", wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        still_there = await page.locator(f'[aria-label="More options for {title}"]').count()
        return "FAILED (still visible)" if still_there > 0 else "DELETED"
    finally:
        await page.close()


async def _teardown_ebay(ctx, title: str, ebay_url: str) -> str:
    """End active eBay listing via Seller Hub Actions -> End listing, then verify."""
    page = await ctx.new_page()
    try:
        await page.goto("https://www.ebay.com/sh/lst/active", wait_until="domcontentloaded")
        await page.wait_for_timeout(3500)

        itm_match = re.search(r"/itm/(\d+)", ebay_url or "")
        row = page.locator("tr", has_text=itm_match.group(1)).first if itm_match else None
        if row is None or await row.count() == 0:
            # Fallback to title match
            rows = page.locator("table tr")
            for idx in range(await rows.count()):
                txt = (await rows.nth(idx).inner_text()).replace("\n", " ")
                if _norm(title)[:35] in _norm(txt):
                    row = rows.nth(idx)
                    break

        if row is None or await row.count() == 0:
            return "ALREADY_GONE"

        actions_btn = row.locator("button[aria-label*='Actions' i], button:has-text('Actions'), button[aria-expanded]").first
        if await actions_btn.count() == 0:
            return "FAILED (Actions button not found)"

        await actions_btn.scroll_into_view_if_needed()
        await actions_btn.click()
        await page.wait_for_timeout(1200)

        end_opt = row.locator("a:has-text('End listing'), button:has-text('End listing')").first
        if await end_opt.count() == 0:
            end_opt = page.locator("a:has-text('End listing'):visible, button:has-text('End listing'):visible").first
        if await end_opt.count() == 0 or not await end_opt.is_visible():
            return "FAILED (End listing option not visible)"

        await end_opt.click()
        await page.wait_for_timeout(2500)

        confirm = page.locator("button:has-text('End listing'):visible, input[value*='End listing' i]:visible").last
        if await confirm.count() > 0:
            await confirm.click()
            await page.wait_for_timeout(3000)

        # Reload and verify
        await page.goto("https://www.ebay.com/sh/lst/active", wait_until="domcontentloaded")
        await page.wait_for_timeout(3500)
        if itm_match:
            still = await page.locator("tr", has_text=itm_match.group(1)).count()
        else:
            still = await page.locator("tr", has_text=title[:35]).count()
        return "FAILED (still active)" if still > 0 else "DELETED"
    finally:
        await page.close()


async def teardown_listings_in_chrome(
    record: Dict[str, str],
    sold_on: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, str]:
    """Check & delete active listings across Craigslist, FBMP, and eBay via Chrome CDP."""
    sku = record.get("SKU", "")
    title = record.get("Item Title", "")
    ebay_url = (record.get("eBay Draft/Item URL") or "").strip()
    cl_url = (record.get("Craigslist Edit URL") or "").strip()

    platforms = []
    if sold_on != "craigslist":
        platforms.append("Craigslist")
    if sold_on != "fbmp":
        platforms.append("FB Marketplace")
    if sold_on != "ebay":
        platforms.append("eBay")

    print(f"[TEARDOWN] Checking {', '.join(platforms)} for {sku} ('{title}'):")
    if dry_run:
        print("[DRY-RUN] Skipping browser tab teardown.")
        return {p: "DRY_RUN" for p in platforms}

    cdp_url = os.getenv("CHROME_CDP_URL", "http://localhost:9222")
    from playwright.async_api import async_playwright

    results: Dict[str, str] = {}
    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(cdp_url)
        except Exception as exc:
            print(
                f"[WARN] Could not connect to Chrome CDP at {cdp_url}: {exc}\n"
                f"       Run `python scripts/launch_chrome.py --check-or-launch` first."
            )
            return {}

        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        if "Craigslist" in platforms:
            results["Craigslist"] = await _teardown_craigslist(ctx, title, cl_url)
        if "FB Marketplace" in platforms:
            results["FB Marketplace"] = await _teardown_fbmp(ctx, title)
        if "eBay" in platforms:
            results["eBay"] = await _teardown_ebay(ctx, title, ebay_url)

    for plat, res in results.items():
        print(f"  - {plat:15s}: {res}")
    return results


def mark_sku_sold(
    sku: str,
    sold_on: Optional[str],
    csv_path: Path,
    manifest_path: Path,
) -> None:
    """Update status to SOLD in batch_manifest.json, local CSV mirror, and Google Sheets."""
    new_status = f"SOLD ({sold_on.upper()})" if sold_on else "SOLD"

    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for item in manifest.get("items", []):
                if (
                    str(item.get("sku", "")).lower() == sku.lower()
                    or str(item.get("item_id", "")).lower() == sku.lower()
                ):
                    item["status"] = new_status
                    sku = str(item.get("sku", sku))
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        except Exception:
            pass

    if csv_path.exists():
        try:
            with csv_path.open("r", encoding="utf-8", newline="") as f:
                rows = list(csv.reader(f))
            if rows:
                header = rows[0]
                status_idx = header.index("Status") if "Status" in header else 4
                for r in rows[1:]:
                    if r and r[0].strip().lower() == sku.lower():
                        while len(r) <= status_idx:
                            r.append("")
                        r[status_idx] = new_status
                with csv_path.open("w", encoding="utf-8", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerows(rows)
                print(f"[OK] Updated {sku} status -> '{new_status}' in '{csv_path.as_posix()}'.")
        except Exception as exc:
            print(f"[WARN] Could not update local CSV status: {exc}")

    creds_path = Path(os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "./credentials/service_account.json"))
    sheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
    worksheet_name = os.getenv("GOOGLE_WORKSHEET_NAME", "Listings").strip() or "Listings"
    if creds_path.exists() and sheet_id and sheet_id != "YOUR_GOOGLE_SHEET_ID_HERE":
        try:
            import gspread

            gc = gspread.service_account(filename=str(creds_path))
            sh = gc.open_by_key(sheet_id)
            ws = sh.worksheet(worksheet_name)
            cell = ws.find(sku, in_column=1)
            if cell:
                ws.update_cell(cell.row, 5, new_status)
                print(f"[OK] Updated {sku} status -> '{new_status}' in Google Sheet '{sh.title}'.")
        except Exception as exc:
            print(f"[WARN] Google Sheet status update note: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage 5 Helper: Tear down cross-posted listings for a sold SKU."
    )
    parser.add_argument("sku", type=str, help="The sold SKU, item_XX ID, or partial title to tear down")
    parser.add_argument(
        "--sold-on",
        choices=["ebay", "fbmp", "craigslist", "amazon"],
        default=None,
        help="Optional platform where the item sold (leaves that platform alone and tears down the others)",
    )
    parser.add_argument(
        "--csv-mirror",
        type=Path,
        default=Path("items/master_inventory.csv"),
        help="Path to local CSV mirror (default: items/master_inventory.csv)",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("items/batch_manifest.json"),
        help="Path to batch_manifest.json (default: items/batch_manifest.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview teardown without opening browser tabs",
    )

    args = parser.parse_args()
    record = lookup_sku_record(args.sku, args.csv_mirror, args.manifest)
    if not record:
        print(f"[ERROR] SKU '{args.sku}' not found in '{args.csv_mirror.as_posix()}' or '{args.manifest.as_posix()}'.")
        return 1

    asyncio.run(
        teardown_listings_in_chrome(
            record=record,
            sold_on=args.sold_on,
            dry_run=args.dry_run,
        )
    )
    if not args.dry_run:
        mark_sku_sold(
            sku=record.get("SKU", args.sku),
            sold_on=args.sold_on,
            csv_path=args.csv_mirror,
            manifest_path=args.manifest,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
