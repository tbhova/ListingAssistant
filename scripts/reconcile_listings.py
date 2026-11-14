#!/usr/bin/env python3
"""
Stage 5 Helper: Live Listing Reconciliation (scripts/reconcile_listings.py)

Connects to Chrome CDP (`http://localhost:9222`), scans your live seller dashboards on:
  - eBay Seller Hub Active (`https://www.ebay.com/sh/lst/active`)
  - Facebook Marketplace Your Selling (`https://www.facebook.com/marketplace/you/selling`)
  - Craigslist Account Home (`https://accounts.craigslist.org/login/home`)

Matches live listings against `items/batch_manifest.json`, replaces stale draft/create URLs
with live published/manage URLs (where exposed), updates per-platform `LIVE` / `ENDED`
statuses, and syncs `items/master_inventory.csv` (and Google Sheets if configured).

Usage:
  .venv\\Scripts\\python.exe scripts/reconcile_listings.py [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from sync_sheets import item_to_row, sync_google_sheet, sync_local_csv


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _match_listing(item_title: str, candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Match a manifest item title against scraped platform listings."""
    target = _norm(item_title)
    if not target:
        return None

    # 1. Exact or prefix match (FBMP/CL sometimes truncate very long titles)
    for c in candidates:
        cand = _norm(c.get("title", ""))
        if not cand:
            continue
        if cand == target or target.startswith(cand) or cand.startswith(target):
            return c

    # 2. High token overlap fallback (>= 80% of shorter token set, min 3 tokens)
    t_tokens = set(target.split())
    best: Optional[Dict[str, Any]] = None
    best_score = 0.0
    for c in candidates:
        c_tokens = set(_norm(c.get("title", "")).split())
        if len(c_tokens) < 3 or len(t_tokens) < 3:
            continue
        overlap = len(t_tokens & c_tokens) / min(len(t_tokens), len(c_tokens))
        if overlap >= 0.8 and overlap > best_score:
            best_score = overlap
            best = c
    return best


async def scrape_ebay_active(page) -> List[Dict[str, str]]:
    await page.goto("https://www.ebay.com/sh/lst/active", wait_until="domcontentloaded")
    await page.wait_for_timeout(3500)
    rows = await page.evaluate("""() => {
        const out = [];
        for (const tr of document.querySelectorAll('table tr')) {
            const itmLink = tr.querySelector('a[href*="/itm/"]');
            if (!itmLink) continue;
            const href = itmLink.getAttribute('href') || '';
            const m = href.match(/\\/itm\\/(\\d+)/);
            if (!m) continue;
            const itemId = m[1];
            const title = (itmLink.innerText || '').trim() || (tr.innerText || '').split('\\n')[0].trim();
            out.push({
                item_id: itemId,
                title: title,
                url: 'https://www.ebay.com/itm/' + itemId,
            });
        }
        return out;
    }""")
    return rows


async def scrape_fbmp_selling(page) -> List[Dict[str, str]]:
    await page.goto("https://www.facebook.com/marketplace/you/selling", wait_until="domcontentloaded")
    await page.wait_for_timeout(3500)
    items = await page.evaluate("""() => {
        const out = [];
        const seen = new Set();
        for (const el of document.querySelectorAll('[aria-label^="More options for "]')) {
            const label = (el.getAttribute('aria-label') || '').replace('More options for ', '').trim();
            if (!label || seen.has(label)) continue;
            // Exclude Messenger chat rows in the left/overlay widget if present
            const card = el.closest('[role="article"], div');
            const cardText = card ? (card.innerText || '') : '';
            if (!cardText.includes('$') && !document.body.innerText.includes(label)) continue;
            seen.add(label);
            out.push({ title: label });
        }
        return out;
    }""")
    return items


async def scrape_craigslist_home(page) -> List[Dict[str, str]]:
    await page.goto("https://accounts.craigslist.org/login/home", wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)
    rows = await page.evaluate("""() => {
        const out = [];
        for (const tr of document.querySelectorAll('table.accthp_postings tr')) {
            const txt = (tr.innerText || '').trim();
            if (!txt.startsWith('Active')) continue;
            const viewLink = tr.querySelector('a[href*="craigslist.org/"]');
            const manageForm = tr.querySelector('form[action*="/manage/"]');
            const titleRaw = viewLink ? (viewLink.innerText || '').trim() : '';
            const title = titleRaw.replace(/\\s+-\\s+\\$\\d[\\d,.]*$/, '').trim();
            out.push({
                title: title,
                view_url: viewLink ? (viewLink.getAttribute('href') || '') : '',
                manage_url: manageForm ? (manageForm.getAttribute('action') || '') : '',
            });
        }
        return out;
    }""")
    return rows


def _update_platform_status(status_str: str, platform: str, state: str) -> str:
    """Update a single platform token in 'eBay:LIVE | FBMP:DRAFT | CL:N/A'."""
    if str(status_str).upper().startswith("SOLD"):
        return status_str
    parts: Dict[str, str] = {}
    for seg in (status_str or "").split("|"):
        seg = seg.strip()
        if ":" in seg:
            k, v = seg.split(":", 1)
            parts[k.strip()] = v.strip()
    if not parts:
        parts = {"eBay": "N/A", "FBMP": "N/A", "CL": "N/A"}
    parts[platform] = state
    order = ["eBay", "FBMP", "CL"]
    ordered = [f"{k}:{parts.get(k, 'N/A')}" for k in order]
    for k, v in parts.items():
        if k not in order:
            ordered.append(f"{k}:{v}")
    return " | ".join(ordered)


async def reconcile(manifest_path: Path, csv_path: Path, dry_run: bool = False) -> int:
    if not manifest_path.exists():
        print(f"[ERROR] Manifest not found: {manifest_path}")
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = manifest.get("items", [])

    cdp_url = os.getenv("CHROME_CDP_URL", "http://localhost:9222")
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(cdp_url)
        except Exception as exc:
            print(f"[ERROR] Could not connect to Chrome CDP at {cdp_url}: {exc}")
            return 1

        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await ctx.new_page()
        try:
            ebay_active = await scrape_ebay_active(page)
            fbmp_active = await scrape_fbmp_selling(page)
            cl_active = await scrape_craigslist_home(page)
        finally:
            await page.close()

    print(
        f"[SCAN] Live dashboard counts -> eBay: {len(ebay_active)}, "
        f"FBMP: {len(fbmp_active)}, Craigslist: {len(cl_active)}"
    )

    updated_count = 0
    for item in items:
        if item.get("routing_bucket") == "DONATE_OR_LOT":
            continue
        if str(item.get("status", "")).upper().startswith("SOLD"):
            continue

        title = str(item.get("title") or "")
        sku = str(item.get("sku") or item.get("item_id") or "")
        status = str(item.get("status") or "")
        changed = False

        eb_match = _match_listing(title, ebay_active)
        if eb_match:
            if item.get("ebay_draft_url") != eb_match["url"]:
                item["ebay_draft_url"] = eb_match["url"]
                changed = True
            new_status = _update_platform_status(status, "eBay", "LIVE")
            if new_status != status:
                status = new_status
                changed = True

        fb_match = _match_listing(title, fbmp_active)
        if fb_match:
            new_status = _update_platform_status(status, "FBMP", "LIVE")
            if new_status != status:
                status = new_status
                changed = True

        cl_match = _match_listing(title, cl_active)
        if cl_match:
            target_url = cl_match.get("manage_url") or cl_match.get("view_url") or ""
            if target_url and item.get("craigslist_edit_url") != target_url:
                item["craigslist_edit_url"] = target_url
                changed = True
            new_status = _update_platform_status(status, "CL", "LIVE")
            if new_status != status:
                status = new_status
                changed = True

        if changed:
            item["status"] = status
            updated_count += 1
            print(f"  [UPDATED] {sku:18s} | {status:34s} | {title[:45]}")

    if dry_run:
        print(f"[DRY-RUN] {updated_count} item(s) would be updated; skipping file/sheet writes.")
        return 0

    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    rows = [item_to_row(i) for i in items if i.get("routing_bucket") != "DONATE_OR_LOT"]
    sync_local_csv(csv_path, rows)
    print(f"[OK] Reconciled {updated_count} item(s) -> saved '{manifest_path.as_posix()}' and '{csv_path.as_posix()}'.")

    creds_path = Path(os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "./credentials/service_account.json"))
    sheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
    worksheet_name = os.getenv("GOOGLE_WORKSHEET_NAME", "Listings").strip() or "Listings"
    if creds_path.exists() and sheet_id and sheet_id != "YOUR_GOOGLE_SHEET_ID_HERE":
        sync_google_sheet(creds_path, sheet_id, worksheet_name, rows)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile live eBay, FBMP, and CL URLs into inventory trackers.")
    parser.add_argument("--manifest", type=Path, default=Path("items/batch_manifest.json"))
    parser.add_argument("--csv-mirror", type=Path, default=Path("items/master_inventory.csv"))
    parser.add_argument("--dry-run", action="store_true", help="Scan dashboards without writing files")
    args = parser.parse_args()
    return asyncio.run(reconcile(args.manifest, args.csv_mirror, dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
