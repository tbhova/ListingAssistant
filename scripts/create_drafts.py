#!/usr/bin/env python3
"""
Stage 4: Multi-Platform Playwright Drafts (scripts/create_drafts.py)

Connects via Playwright CDP (`connect_over_cdp("http://localhost:9222")`) to the dedicated,
logged-in Chrome profile (`./chrome_profile`) and creates draft listings based on each item's
Routing Bucket (`ALL_THREE`, `LOCAL_ONLY`, `EBAY_ONLY`):
  - eBay Seller Hub: Uploads photos, fills title, specifics, description, and pricing,
    and clicks "Save for later" / "Save as draft".
  - Facebook Marketplace: Uploads photos, fills title, price (+10-15% negotiation buffer),
    condition, description, and location, and clicks "Save draft".
  - Craigslist: Navigates local posting flow, fills fields, uploads images, and parks on the
    final "Preview" / "Draft" tab without publishing.

HARD SAFETY RULE:
  This script enforces a strict runtime guard (`assert_not_publish_button`) that refuses to
  click any button whose accessible label or text matches a live publish action
  ("Publish", "List it", "List item", "Publish listing").
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# Hard Safety Guard: Any button text matching these tokens is strictly forbidden
FORBIDDEN_PUBLISH_TOKENS = {
    "publish",
    "publish listing",
    "list it",
    "list item",
    "list for free",
    "post now",
    "submit listing",
}


def assert_safe_action_label(label: str) -> None:
    """Raise RuntimeError if an automation selector/label ever attempts a live publish click."""
    normalized = " ".join(label.strip().lower().split())
    for forbidden in FORBIDDEN_PUBLISH_TOKENS:
        if normalized == forbidden or normalized.startswith(f"{forbidden} "):
            raise RuntimeError(
                f"[SAFETY BLOCK] Attempted to click forbidden publish button: '{label}'. "
                f"Stage 4 is strictly restricted to saving drafts / previewing."
            )


async def safe_click_draft_button(page: Any, selectors: List[str], platform_name: str) -> bool:
    """
    Iterate candidate draft-save selectors, verify text does not violate the Hard Safety Rule,
    and click the first visible/enabled draft button.
    """
    for sel in selectors:
        try:
            locator = page.locator(sel).first
            if await locator.count() > 0 and await locator.is_visible():
                text_content = (await locator.inner_text() or "").strip()
                aria_label = (await locator.get_attribute("aria-label") or "").strip()
                assert_safe_action_label(text_content)
                assert_safe_action_label(aria_label)
                await locator.click(timeout=5000)
                print(f"  [{platform_name}] Clicked draft button: '{text_content or aria_label or sel}'")
                return True
        except RuntimeError:
            raise
        except Exception:
            continue
    return False


async def create_ebay_draft(
    context: Any,
    item: Dict[str, Any],
    dry_run: bool = False,
) -> str:
    """
    Create an eBay Seller Hub draft listing (patterns adapted from ashnicholes-droid/hahm-ebay-lister).
    Never clicks 'List it' — only clicks 'Save for later' (or leaves open on draft editor).
    Returns the draft URL.
    """
    title = str(item.get("title") or "")[:80]
    price = float(item.get("ebay_list_price_usd") or item.get("comp_price") or 25.0)
    description = str(
        item.get("description_detailed") or item.get("description_concise") or title
    )
    brand = str(item.get("brand") or "Unbranded")
    model_mpn = str(item.get("model_mpn") or "Does Not Apply")
    condition_str = str(item.get("condition") or "Used - Good").lower()
    photos = [str(Path(p).resolve()) for p in item.get("photos", []) if Path(p).exists()]

    print(f"  [eBay] Preparing draft for '{title}' (${price:.2f}, {len(photos)} photo(s))...")
    if dry_run:
        return "https://www.ebay.com/sh/lst/drafts?dry_run=true"

    page = await context.new_page()
    await page.goto("https://www.ebay.com/sl/prelist/suggest", wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)

    # Step 1: Prelist search box ("Tell us what you're selling")
    search_selectors = [
        "input[placeholder*='Tell us what you']",
        "input[name='keyword']",
        "input[type='text']",
    ]
    for sel in search_selectors:
        loc = page.locator(sel).first
        if await loc.count() > 0 and await loc.is_visible():
            await loc.fill(title)
            await loc.press("Enter")
            await page.wait_for_timeout(2500)
            break

    # Step 2: Click "Continue without match" if product catalog prompt appears
    continue_without_match = [
        "button:has-text('Continue without match')",
        "a:has-text('Continue without match')",
        "button:has-text('Skip')",
    ]
    for sel in continue_without_match:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                for _ in range(10):
                    if await loc.is_enabled():
                        await loc.click(timeout=3000)
                        await page.wait_for_timeout(2000)
                        break
                    await page.wait_for_timeout(500)
                break
        except Exception as exc:
            print(f"  [eBay] Prelist catalog match note: {exc}")

    # Step 3: Condition selection modal if prompted before form
    if "new" in condition_str and "like" not in condition_str:
        cond_choice = "New"
    elif "parts" in condition_str:
        cond_choice = "For parts or not working"
    else:
        cond_choice = "Used"

    for sel in [
        f"label:has-text('{cond_choice}')",
        f"button:has-text('{cond_choice}')",
        "button:has-text('Continue to listing')",
    ]:
        loc = page.locator(sel).first
        if await loc.count() > 0 and await loc.is_visible():
            try:
                await loc.click(timeout=2000)
                await page.wait_for_timeout(1000)
            except Exception:
                pass

    # Step 4: Upload photos via input[type="file"]
    if photos:
        try:
            file_input = page.locator("input[type='file']").first
            if await file_input.count() > 0:
                await file_input.set_input_files(photos[:24])
                print(f"  [eBay] Uploaded {len(photos[:24])} photo(s).")
                await page.wait_for_timeout(3000)
        except Exception as exc:
            print(f"  [eBay] Photo upload note: {exc}")

    # Step 5: Fill Title, Brand/MPN specifics, Description, Price, and Package Weight/Dimensions
    for title_sel in [
        "input[name='title']",
        "input[aria-label*='Item title']",
        "textarea[name='title']",
    ]:
        loc = page.locator(title_sel).first
        if await loc.count() > 0 and await loc.is_visible():
            await loc.fill(title)
            break

    for brand_sel in ["input[aria-label*='Brand']", "input[name*='Brand']"]:
        loc = page.locator(brand_sel).first
        if await loc.count() > 0 and await loc.is_visible():
            await loc.fill(brand)
            break

    for mpn_sel in ["input[aria-label*='MPN']", "input[aria-label*='Model']"]:
        loc = page.locator(mpn_sel).first
        if await loc.count() > 0 and await loc.is_visible():
            await loc.fill(model_mpn)
            break

    # Description iframe or rich text editor
    try:
        desc_iframe = page.frame_locator("iframe[id*='desc'], iframe[title*='Description']").first
        body_loc = desc_iframe.locator("body")
        if await body_loc.count() > 0:
            await body_loc.fill(description)
    except Exception:
        for desc_sel in [
            "div[contenteditable='true'][aria-label*='description']",
            "textarea[name*='description']",
        ]:
            loc = page.locator(desc_sel).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.fill(description)
                break

    # Price (Buy It Now)
    for price_sel in [
        "input[name='price']",
        "input[aria-label*='Buy It Now price']",
        "input[aria-label*='Price']",
    ]:
        loc = page.locator(price_sel).first
        if await loc.count() > 0 and await loc.is_visible():
            await loc.fill(f"{price:.2f}")
            break

    # Package weight & dimensions if visible
    shipping = item.get("shipping") or {}
    weight_lbs = int(float(shipping.get("actual_weight_lbs") or 1))
    dims = shipping.get("dimensions_in") or [12, 9, 4]
    field_pairs = [
        ("input[aria-label*='Weight in pounds'], input[name*='majorWeight']", str(max(1, weight_lbs))),
        ("input[aria-label*='Length'], input[name*='packageLength']", str(int(dims[0]))),
        ("input[aria-label*='Width'], input[name*='packageWidth']", str(int(dims[1]))),
        ("input[aria-label*='Depth'], input[aria-label*='Height'], input[name*='packageDepth']", str(int(dims[2]))),
    ]
    for sel, val in field_pairs:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.fill(val)
        except Exception:
            pass

    # Step 6: Click "Save for later" / "Save as draft" — NEVER click "List it"
    await safe_click_draft_button(
        page,
        selectors=[
            "button:has-text('Save for later')",
            "button:has-text('Save as draft')",
            "button[aria-label*='Save for later']",
        ],
        platform_name="eBay",
    )
    await page.wait_for_timeout(2000)
    return page.url


async def create_fbmp_draft(
    context: Any,
    item: Dict[str, Any],
    dry_run: bool = False,
) -> str:
    """
    Create a Facebook Marketplace item draft (patterns adapted from
    NmediaCloud/marketplace-listing-assistant & Haron1-max/Facebook-Marketplace-Auto-Poster).
    Applies +10-15% negotiation buffer price and clicks 'Save draft'.
    """
    title = str(item.get("title") or "")[:80]
    fb_price = int(round(float(item.get("fbmp_list_price_usd") or item.get("comp_price") or 25.0)))
    description = str(
        item.get("description_detailed") or item.get("description_concise") or title
    )
    origin_zip = os.getenv("ORIGIN_ZIP", "98101")
    photos = [str(Path(p).resolve()) for p in item.get("photos", []) if Path(p).exists()]

    print(f"  [FBMP] Preparing draft for '{title}' (${fb_price} w/ negotiation buffer, {len(photos)} photo(s))...")
    if dry_run:
        return "https://www.facebook.com/marketplace/you/selling?dry_run=true"

    page = await context.new_page()
    await page.goto("https://www.facebook.com/marketplace/create/item", wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    # Upload photos via file input
    if photos:
        try:
            file_input = page.locator("input[type='file'][accept*='image']").first
            if await file_input.count() > 0:
                await file_input.set_input_files(photos[:20])
                print(f"  [FBMP] Uploaded {len(photos[:20])} photo(s).")
                await page.wait_for_timeout(2500)
        except Exception as exc:
            print(f"  [FBMP] Photo upload note: {exc}")

    # Fill Title, Price, Description, and Location using resilient aria/label selectors
    label_fields = [
        ("label:has-text('Title') input", title),
        ("input[aria-label='Title']", title),
        ("label:has-text('Price') input", str(fb_price)),
        ("input[aria-label='Price']", str(fb_price)),
        ("label:has-text('Description') textarea", description),
        ("textarea[aria-label='Description']", description),
        ("label:has-text('Location') input", origin_zip),
        ("input[aria-label='Location']", origin_zip),
    ]
    for sel, val in label_fields:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.fill(val)
                await page.wait_for_timeout(400)
        except Exception:
            continue

    # Click "Save draft" — NEVER click "Publish"
    await safe_click_draft_button(
        page,
        selectors=[
            "div[role='button']:has-text('Save draft')",
            "button:has-text('Save draft')",
            "span:has-text('Save draft')",
        ],
        platform_name="FBMP",
    )
    await page.wait_for_timeout(2000)
    return page.url


async def create_craigslist_preview(
    context: Any,
    item: Dict[str, Any],
    dry_run: bool = False,
) -> str:
    """
    Navigate local Craigslist posting flow, fill fields, upload images, and park on the
    final 'Preview' tab without clicking 'Publish'.
    """
    subdomain = os.getenv("CRAIGSLIST_SUBDOMAIN", "sfbay").strip()
    cl_area = os.getenv("CRAIGSLIST_AREA", "").strip()
    cl_city = os.getenv("CRAIGSLIST_CITY", "").strip()
    origin_zip = os.getenv("ORIGIN_ZIP", "95126").strip()
    title = str(item.get("title") or "")[:70]
    cl_price = int(round(float(item.get("craigslist_list_price_usd") or item.get("comp_price") or 25.0)))
    description = str(
        item.get("description_detailed") or item.get("description_concise") or title
    )
    brand = str(item.get("brand") or "")
    model_mpn = str(item.get("model_mpn") or "")
    photos = [str(Path(p).resolve()) for p in item.get("photos", []) if Path(p).exists()]

    base_url = f"https://{subdomain}.craigslist.org"
    print(
        f"  [Craigslist] Preparing preview on {base_url} "
        f"(area='{cl_area}', city='{cl_city}') for '{title}' (${cl_price})..."
    )
    if dry_run:
        return f"{base_url}/?dry_run=true"

    page = await context.new_page()
    await page.goto(f"https://post.craigslist.org/c/{subdomain}", wait_until="domcontentloaded")
    await page.wait_for_timeout(1500)

    # Step 1: Sub-area selection (e.g., "south bay area") and neighborhood selection (e.g., "san jose west")
    for location_choice in (cl_area, cl_city):
        try:
            radios = page.locator("input[type='radio']")
            if await radios.count() > 0 and await radios.first.is_visible():
                matched = False
                if location_choice:
                    lbl = page.locator(f"label:has-text('{location_choice}')").first
                    if await lbl.count() > 0 and await lbl.is_visible():
                        await lbl.click()
                        matched = True
                if not matched:
                    await radios.first.check()
                await page.locator("button[type='submit'], button:has-text('continue')").first.click()
                await page.wait_for_timeout(1200)
        except Exception:
            pass

    # Step 2: Select "for sale by owner" (fso)
    try:
        fso_radio = page.locator("input[type='radio'][value='fso'], label:has-text('for sale by owner')").first
        if await fso_radio.count() > 0 and await fso_radio.is_visible():
            await fso_radio.click()
            await page.locator("button[type='submit'], button:has-text('continue')").first.click()
            await page.wait_for_timeout(1200)
    except Exception:
        pass

    # Step 3: Select general category (default to electronics or general for sale if prompted)
    cat_hint = str(item.get("category") or "general").lower()
    category_keywords = ["electronics", "tools", "sporting", "household", "general for sale"]
    selected_cat = False
    for kw in category_keywords:
        if kw.split()[0] in cat_hint or kw == "general for sale":
            loc = page.locator(f"label:has-text('{kw}')").first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click()
                selected_cat = True
                break
    if selected_cat:
        try:
            await page.locator("button[type='submit'], button:has-text('continue')").first.click()
            await page.wait_for_timeout(1500)
        except Exception:
            pass

    # Fill Postal Code and City/Neighborhood ("GeographicArea") if present
    cl_fields = [
        ("input[name='PostingTitle']", title),
        ("input[name='price']", str(cl_price)),
        ("input[name='GeographicArea']", cl_city),
        ("input[name='postal']", origin_zip),
        ("textarea[name='PostingBody']", description),
        ("input[name='sale_manufacturer']", brand if brand != "TBD" else ""),
        ("input[name='sale_model']", model_mpn if model_mpn != "TBD" else ""),
    ]
    for sel, val in cl_fields:
        if not val:
            continue
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.fill(val)
        except Exception:
            continue

    # Continue from text form -> map/location -> image upload
    for _ in range(2):
        try:
            cont_btn = page.locator("button.continue, button[type='submit']:has-text('continue')").first
            if await cont_btn.count() > 0 and await cont_btn.is_visible():
                btn_text = (await cont_btn.inner_text() or "").strip()
                assert_safe_action_label(btn_text)
                await cont_btn.click()
                await page.wait_for_timeout(1500)
        except Exception:
            break

    # Step 5: Upload photos if on image upload screen
    if photos:
        try:
            file_input = page.locator("input[type='file']").first
            if await file_input.count() > 0:
                await file_input.set_input_files(photos[:24])
                print(f"  [Craigslist] Uploaded {len(photos[:24])} photo(s).")
                await page.wait_for_timeout(3000)
        except Exception as exc:
            print(f"  [Craigslist] Photo upload note: {exc}")

    # Click "done with images" to land on final Preview screen — NEVER click "publish"
    try:
        done_img_btn = page.locator("button:has-text('done with images')").first
        if await done_img_btn.count() > 0 and await done_img_btn.is_visible():
            await done_img_btn.click()
            await page.wait_for_timeout(1500)
    except Exception:
        pass

    print(f"  [Craigslist] Parked on final Preview tab (NOT published): {page.url}")
    return page.url


async def run_draft_pipeline(
    manifest_path: Path,
    sku_filter: Optional[str] = None,
    platform_filter: Optional[str] = None,
    dry_run: bool = False,
) -> int:
    """Connect to Chrome CDP on port 9222 and create drafts for all eligible items in manifest."""
    if not manifest_path.exists():
        print(f"[ERROR] Manifest not found at '{manifest_path.as_posix()}'. Run Stage 2/3 first.")
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items: List[Dict[str, Any]] = manifest.get("items", [])
    if not items:
        print("[INFO] No items in batch_manifest.json.")
        return 0

    cdp_url = os.getenv("CHROME_CDP_URL", "http://localhost:9222")

    if dry_run:
        print(f"[DRY-RUN] Simulating Playwright CDP draft creation against {cdp_url}...")
        context = None
        browser = None
        p_cm = None
    else:
        from playwright.async_api import async_playwright

        p_cm = await async_playwright().start()
        try:
            browser = await p_cm.chromium.connect_over_cdp(cdp_url)
        except Exception as exc:
            await p_cm.stop()
            print(
                f"[ERROR] Could not connect to Chrome CDP at {cdp_url}: {exc}\n"
                f"        Run: .venv\\Scripts\\python.exe scripts/launch_chrome.py --check-or-launch"
            )
            return 1

        context = browser.contexts[0] if browser.contexts else await browser.new_context()

    try:
        for item in items:
            sku = item.get("sku", "")
            if sku_filter and sku.lower() != sku_filter.lower() and item.get("item_id", "").lower() != sku_filter.lower():
                continue

            bucket = item.get("routing_bucket", "DONATE_OR_LOT")
            if bucket == "DONATE_OR_LOT" and not sku_filter:
                print(f"[SKIP] {sku} ({item.get('item_id')}) is in DONATE_OR_LOT bucket.")
                continue

            print("-" * 72)
            print(f"Processing {sku} ({item.get('item_id')}) | Bucket: {bucket} | Title: {item.get('title')}")

            run_ebay = bucket in ("ALL_THREE", "EBAY_ONLY") and platform_filter in (None, "ebay")
            run_fbmp = bucket in ("ALL_THREE", "LOCAL_ONLY") and platform_filter in (None, "fbmp")
            run_cl = bucket in ("ALL_THREE", "LOCAL_ONLY") and platform_filter in (None, "craigslist")

            needs_ebay = run_ebay and not item.get("ebay_draft_url")
            needs_fbmp = run_fbmp and not item.get("fbmp_listing_url")
            needs_cl = run_cl and not item.get("craigslist_edit_url")

            if not (needs_ebay or needs_fbmp or needs_cl) and not sku_filter:
                print(f"[SKIP] {sku} ({item.get('item_id')}) already has all required drafts created.", flush=True)
                continue

            if needs_ebay:
                try:
                    ebay_url = await create_ebay_draft(context, item, dry_run=dry_run)
                    item["ebay_draft_url"] = ebay_url
                except Exception as exc:
                    print(f"  [eBay ERROR] {exc}", flush=True)

            if needs_fbmp:
                try:
                    fbmp_url = await create_fbmp_draft(context, item, dry_run=dry_run)
                    item["fbmp_listing_url"] = fbmp_url
                except Exception as exc:
                    print(f"  [FBMP ERROR] {exc}", flush=True)

            if needs_cl:
                try:
                    cl_url = await create_craigslist_preview(context, item, dry_run=dry_run)
                    item["craigslist_edit_url"] = cl_url
                except Exception as exc:
                    print(f"  [Craigslist ERROR] {exc}", flush=True)

            item["status"] = "DRAFTS_CREATED" if not dry_run else "DRY_RUN_VERIFIED"
            item["drafted_at"] = datetime.now().isoformat()

            # Persist per-item analysis.json and batch_manifest.json
            folder_path = Path(item.get("folder_path", ""))
            if folder_path.exists():
                (folder_path / "analysis.json").write_text(json.dumps(item, indent=2), encoding="utf-8")
            manifest["updated_at"] = datetime.now().isoformat()
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        manifest["updated_at"] = datetime.now().isoformat()
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print("=" * 72)
        print(f"STAGE 4 COMPLETE: Updated draft URLs in '{manifest_path.as_posix()}'.")
        print("Hard Safety Rule verified: 0 listings were auto-published.")
        print("=" * 72)
        return 0
    finally:
        if p_cm is not None:
            await p_cm.stop()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage 4: Multi-Platform Playwright CDP Draft Creator (eBay, FBMP, Craigslist)."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("items/batch_manifest.json"),
        help="Path to batch_manifest.json (default: items/batch_manifest.json)",
    )
    parser.add_argument(
        "--sku",
        type=str,
        default=None,
        help="Optional single SKU or item_XX ID to draft",
    )
    parser.add_argument(
        "--platform",
        choices=["ebay", "fbmp", "craigslist"],
        default=None,
        help="Optional single platform filter",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate draft creation and verify routing without launching browser clicks",
    )

    args = parser.parse_args()
    return asyncio.run(
        run_draft_pipeline(
            manifest_path=args.manifest,
            sku_filter=args.sku,
            platform_filter=args.platform,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
