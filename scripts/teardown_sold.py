#!/usr/bin/env python3
"""
Stage 5 Helper: Sold SKU Cross-Post Teardown (scripts/teardown_sold.py)

Usage:
  python scripts/teardown_sold.py <SKU> [--sold-on ebay|fbmp|craigslist] [--dry-run]

Reads the cross-posted URLs for `<SKU>` from Google Sheets (or `items/master_inventory.csv` /
`items/batch_manifest.json`), connects to Chrome CDP (`http://localhost:9222`) to open and
initiate deletion/ending of the remaining cross-posted listings, and marks the SKU status as
`SOLD` in both Google Sheets and the local CSV mirror.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def lookup_sku_record(
    sku: str,
    csv_path: Path,
    manifest_path: Path,
) -> Optional[Dict[str, str]]:
    """Find SKU URLs from local CSV mirror, batch_manifest.json, or Google Sheets."""
    target = sku.strip().lower()

    # 1. Check master_inventory.csv
    if csv_path.exists():
        try:
            with csv_path.open("r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if (row.get("SKU") or "").strip().lower() == target:
                        return dict(row)
        except Exception:
            pass

    # 2. Check batch_manifest.json (matches either SKU or item_XX)
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for item in manifest.get("items", []):
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
        except Exception:
            pass

    # 3. Check Google Sheet if configured
    creds_path = Path(os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "./credentials/service_account.json"))
    sheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
    worksheet_name = os.getenv("GOOGLE_WORKSHEET_NAME", "Listings").strip() or "Listings"
    if creds_path.exists() and sheet_id and sheet_id != "YOUR_GOOGLE_SHEET_ID_HERE":
        try:
            import gspread

            gc = gspread.service_account(filename=str(creds_path))
            sh = gc.open_by_key(sheet_id)
            ws = sh.worksheet(worksheet_name)
            for row in ws.get_all_records():
                if str(row.get("SKU", "")).strip().lower() == target:
                    return {k: str(v) for k, v in row.items()}
        except Exception:
            pass

    return None


async def teardown_listings_in_chrome(
    record: Dict[str, str],
    sold_on: Optional[str] = None,
    dry_run: bool = False,
) -> List[str]:
    """Open remaining cross-posted URLs in Chrome CDP and attempt draft/listing deletion."""
    sku = record.get("SKU", "")
    title = record.get("Item Title", "")
    ebay_url = (record.get("eBay Draft/Item URL") or "").strip()
    fbmp_url = (record.get("FBMP Listing URL") or "").strip()
    cl_url = (record.get("Craigslist Edit URL") or "").strip()

    targets: List[tuple[str, str]] = []
    if ebay_url and sold_on != "ebay":
        targets.append(("eBay", ebay_url))
    if fbmp_url and sold_on != "fbmp":
        targets.append(("FB Marketplace", fbmp_url))
    if cl_url and sold_on != "craigslist":
        targets.append(("Craigslist", cl_url))

    if not targets:
        print(f"[INFO] No cross-post URLs to tear down for {sku} ('{title}').")
        return []

    print(f"[TEARDOWN] Tearing down {len(targets)} cross-posted listing(s) for {sku} ('{title}'):")
    for platform, url in targets:
        print(f"  - {platform}: {url}")

    if dry_run:
        print("[DRY-RUN] Skipping browser tab teardown.")
        return [t[0] for t in targets]

    cdp_url = os.getenv("CHROME_CDP_URL", "http://localhost:9222")
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(cdp_url)
        except Exception as exc:
            print(
                f"[WARN] Could not connect to Chrome CDP at {cdp_url}: {exc}\n"
                f"       Run `python scripts/launch_chrome.py --check-or-launch` first."
            )
            return []

        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        closed_platforms: List[str] = []

        for platform, url in targets:
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(2000)

                if platform == "Craigslist":
                    # Click 'delete this posting' button if on Craigslist manage page
                    for sel in [
                        "input[value*='delete']",
                        "button:has-text('delete')",
                        "a:has-text('delete')",
                    ]:
                        loc = page.locator(sel).first
                        if await loc.count() > 0 and await loc.is_visible():
                            await loc.click()
                            print(f"  [Craigslist] Triggered delete on {url}")
                            break
                elif platform == "FB Marketplace":
                    for sel in [
                        "div[aria-label*='Delete']",
                        "span:has-text('Delete listing')",
                        "span:has-text('Delete draft')",
                    ]:
                        loc = page.locator(sel).first
                        if await loc.count() > 0 and await loc.is_visible():
                            await loc.click()
                            print(f"  [FB Marketplace] Triggered delete action on {url}")
                            break
                elif platform == "eBay":
                    for sel in [
                        "button:has-text('Delete draft')",
                        "a:has-text('End item')",
                    ]:
                        loc = page.locator(sel).first
                        if await loc.count() > 0 and await loc.is_visible():
                            await loc.click()
                            print(f"  [eBay] Triggered delete/end action on {url}")
                            break

                closed_platforms.append(platform)
            except Exception as exc:
                print(f"  [{platform}] Opened tab for manual confirmation ({exc})")

        return closed_platforms


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
                        r[status_idx] = new_status
                with csv_path.open("w", encoding="utf-8", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerows(rows)
                print(f"[OK] Updated {sku} status -> '{new_status}' in '{csv_path.as_posix()}'.")
        except Exception as exc:
            print(f"[WARN] Could not update CSV mirror: {exc}")

    # Sync Google Sheet if configured
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
    parser.add_argument("sku", type=str, help="The sold SKU (or item_XX ID) to tear down")
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
        help="Preview URLs to tear down and update status without opening browser tabs",
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
    mark_sku_sold(
        sku=record.get("SKU", args.sku),
        sold_on=args.sold_on,
        csv_path=args.csv_mirror,
        manifest_path=args.manifest,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
