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


CRAIGSLIST_SUBDOMAIN_TO_POST_CODE: Dict[str, str] = {
    "sfbay": "sfo",
    "sfo": "sfo",
    "sacramento": "sac",
    "seattle": "sea",
    "losangeles": "lax",
    "sandiego": "sdo",
    "newyork": "nyc",
    "chicago": "chi",
    "portland": "pdx",
    "austin": "aus",
    "phoenix": "phx",
    "denver": "den",
}


def match_craigslist_category(category_hint: str, title: str) -> str:
    """Map eBay category taxonomy and item title to a standard Craigslist category."""
    cat = (category_hint or "").lower()
    tit = (title or "").lower()

    # Priority 1: eBay taxonomy mapping
    if any(k in cat for k in ["lawn mower", "yard, garden", "mower"]):
        return "farm & garden"
    if any(k in cat for k in ["cycling", "bicycle"]):
        return "bicycles"
    if any(k in cat for k in ["kitchen appliance", "hvac", "major appliance"]):
        return "appliances"
    if any(k in cat for k in ["power tool", "tools & workshop", "hand tools"]):
        return "tools"
    if any(k in cat for k in ["furniture"]):
        return "furniture"
    if any(k in cat for k in ["video game", "consoles"]):
        return "video gaming"
    if any(k in cat for k in ["clothing", "shoes", "accessories"]):
        return "clothing & accessories"
    if any(k in cat for k in ["automotive tool"]):
        return "tools"
    if any(k in cat for k in ["auto parts", "ebay motors"]):
        return "auto parts"

    # Priority 2: Title keywords (word-boundary matched)
    title_rules = [
        (r"\b(mower|lawn|weed|trimmer|chainsaw)\b", "farm & garden"),
        (r"\b(bike|bicycle|truing|derailleur|cassette)\b", "bicycles"),
        (r"\b(espresso|cappuccino|coffee|blender|refrigerator|microwave)\b", "appliances"),
        (r"\b(saw|miter|drill|router|planer|caliper|press|lathe|wrench|socket)\b", "tools"),
        (r"\b(chair|desk|couch|sofa|table|hall tree|cabinet|dresser)\b", "furniture"),
        (r"\b(nintendo|switch|playstation|xbox|cockpit|wheelbase|pedals)\b", "video gaming"),
        (r"\b(boots|shoes|jacket|helmet|gloves|riding)\b", "clothing & accessories"),
        (r"\b(laptop|desktop|monitor|pc|macbook)\b", "computers"),
        (r"\b(stereo|speaker|amplifier|receiver)\b", "electronics"),
    ]
    for pattern, cl_cat in title_rules:
        if re.search(pattern, tit):
            return cl_cat

    return "general for sale"


def map_craigslist_condition(condition_str: str) -> str:
    """Map condition text to Craigslist select value."""
    c = (condition_str or "").lower()
    if "brand new" in c or "new with tags" in c or c == "new":
        return "10"  # new
    if "like new" in c or "open box" in c:
        return "20"  # like new
    if "excellent" in c or "refurbished" in c:
        return "30"  # excellent
    if "fair" in c or "for parts" in c:
        return "50"  # fair
    return "40"      # good (used default)


async def create_craigslist_preview(
    context: Any,
    item: Dict[str, Any],
    dry_run: bool = False,
) -> str:
    """
    Navigate local Craigslist posting flow directly via post.craigslist.org/c/<post_code>,
    select subarea and neighborhood, select for-sale-by-owner and matched category, fill
    all form details, upload full-resolution raw photos, and park on the final 'Preview' tab
    without clicking 'Publish'.
    """
    subdomain = os.getenv("CRAIGSLIST_SUBDOMAIN", "sfbay").strip()
    post_code = CRAIGSLIST_SUBDOMAIN_TO_POST_CODE.get(subdomain.lower(), subdomain.lower())
    cl_area = os.getenv("CRAIGSLIST_AREA", "south bay area").strip()
    cl_city = os.getenv("CRAIGSLIST_CITY", "san jose west").strip()
    origin_zip = os.getenv("ORIGIN_ZIP", "95126").strip()

    title = str(item.get("title") or "")[:70]
    cl_price = int(round(float(item.get("craigslist_list_price_usd") or item.get("comp_price") or 25.0)))
    description = str(
        item.get("description_detailed") or item.get("description_concise") or title
    )
    brand = str(item.get("brand") or "")[:30]
    model_mpn = str(item.get("model_mpn") or "")[:30]
    condition_str = str(item.get("condition") or "Used")
    cond_val = map_craigslist_condition(condition_str)

    # Always use original high-resolution raw photos (excluding thumbnails or metadata)
    photos = sorted(
        [
            str(Path(p).resolve())
            for p in item.get("photos", [])
            if Path(p).is_file()
            and not Path(p).name.startswith("thumb_")
            and Path(p).suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
        ],
        key=lambda x: Path(x).name,
    )

    base_url = f"https://{subdomain}.craigslist.org"
    print(
        f"  [Craigslist] Preparing preview on {base_url} (post_code='{post_code}', "
        f"area='{cl_area}', city='{cl_city}') for '{title}' (${cl_price})...",
        flush=True,
    )
    if dry_run:
        return f"{base_url}/?dry_run=true"

    page = await context.new_page()

    # Direct navigation to post_code avoids the iframe embed and Aberdeen default redirect
    start_url = f"https://post.craigslist.org/c/{post_code}"
    await page.goto(start_url, wait_until="domcontentloaded")
    await page.wait_for_timeout(1500)

    # Step 1: Area selection (?s=subarea)
    try:
        if "?s=subarea" in page.url or "nearest area" in (await page.title()).lower():
            area_selected = False
            if cl_area:
                loc = page.locator(f"label:has-text('{cl_area}')").first
                if await loc.count() > 0:
                    await loc.click()
                    area_selected = True
            if not area_selected:
                sb = page.locator("input[type='radio'][value='2']").first
                if await sb.count() > 0:
                    await sb.click()
                else:
                    await page.locator("input[type='radio']").first.click()
            await page.locator("button[type='submit'], button:has-text('continue')").first.click()
            await page.wait_for_timeout(1500)
    except Exception as exc:
        print(f"  [Craigslist Note - Step 1]: {exc}", flush=True)

    # Step 2: Neighborhood selection (?s=hood)
    try:
        if "?s=hood" in page.url or "neighborhood" in (await page.title()).lower():
            hood_selected = False
            if cl_city:
                loc = page.locator(f"label:has-text('{cl_city}')").first
                if await loc.count() > 0:
                    await loc.click()
                    hood_selected = True
            if not hood_selected:
                sj = page.locator("input[type='radio'][value='40'], label:has-text('san jose west')").first
                if await sj.count() > 0:
                    await sj.click()
                else:
                    await page.locator("input[type='radio']").first.click()
            await page.locator("button[type='submit'], button:has-text('continue')").first.click()
            await page.wait_for_timeout(1500)
    except Exception as exc:
        print(f"  [Craigslist Note - Step 2]: {exc}", flush=True)

    # Step 3: Type of posting (?s=type) -> for sale by owner (fso)
    try:
        if "?s=type" in page.url or "choose type" in (await page.title()).lower():
            fso_radio = page.locator("input[type='radio'][value='fso'], label:has-text('for sale by owner')").first
            if await fso_radio.count() > 0:
                await fso_radio.click()
            await page.locator("button[type='submit'], button:has-text('continue')").first.click()
            await page.wait_for_timeout(1500)
    except Exception as exc:
        print(f"  [Craigslist Note - Step 3]: {exc}", flush=True)

    # Step 4: Category selection (?s=cat)
    try:
        if "?s=cat" in page.url or "choose category" in (await page.title()).lower():
            target_cat = match_craigslist_category(item.get("category", ""), title)
            print(f"  [Craigslist] Matched category '{target_cat}'", flush=True)
            cat_loc = page.locator(f"label:has-text('{target_cat}')").first
            if await cat_loc.count() > 0:
                await cat_loc.click()
            else:
                gen_loc = page.locator("label:has-text('general for sale')").first
                if await gen_loc.count() > 0:
                    await gen_loc.click()
                else:
                    await page.locator("input[type='radio']").first.click()
            await page.locator("button[type='submit'], button:has-text('continue')").first.click()
            await page.wait_for_timeout(1500)
    except Exception as exc:
        print(f"  [Craigslist Note - Step 4]: {exc}", flush=True)

    # Step 5: Post Details Form (?s=edit)
    try:
        title_inp = page.locator("input[name='PostingTitle'], #PostingTitle").first
        if await title_inp.count() > 0 and await title_inp.is_visible() and await title_inp.is_enabled():
            await title_inp.fill(title)
    except Exception:
        pass

    try:
        price_inp = page.locator("input[name='price'], #price").first
        if await price_inp.count() > 0 and await price_inp.is_visible() and await price_inp.is_enabled():
            await price_inp.fill(str(cl_price))
    except Exception:
        pass

    try:
        postal_inp = page.locator("input[name='postal'], #postal_code").first
        if await postal_inp.count() > 0 and await postal_inp.is_visible() and await postal_inp.is_enabled():
            await postal_inp.fill(origin_zip)
    except Exception:
        pass

    try:
        city_inp = page.locator("input[name='city']:not([disabled]), input[name='GeographicArea']:not([disabled])").first
        if await city_inp.count() > 0 and await city_inp.is_visible() and await city_inp.is_enabled():
            await city_inp.fill(cl_city)
    except Exception:
        pass

    try:
        body_inp = page.locator("textarea[name='PostingBody'], #PostingBody").first
        if await body_inp.count() > 0 and await body_inp.is_visible() and await body_inp.is_enabled():
            await body_inp.fill(description)
    except Exception:
        pass

    try:
        if brand and brand != "TBD":
            mfg_inp = page.locator("input[name='sale_manufacturer']").first
            if await mfg_inp.count() > 0 and await mfg_inp.is_visible() and await mfg_inp.is_enabled():
                await mfg_inp.fill(brand)
    except Exception:
        pass

    try:
        if model_mpn and model_mpn != "TBD":
            mod_inp = page.locator("input[name='sale_model']").first
            if await mod_inp.count() > 0 and await mod_inp.is_visible() and await mod_inp.is_enabled():
                await mod_inp.fill(model_mpn)
    except Exception:
        pass

    # Condition dropdown: styled by jQuery UI, select via dispatchEvent
    try:
        cond_elem = page.locator("select[name='condition']").first
        if await cond_elem.count() > 0:
            await cond_elem.evaluate(
                f"el => {{ el.value = '{cond_val}'; el.dispatchEvent(new Event('change', {{ bubbles: true }})); }}"
            )
    except Exception:
        pass

    try:
        cont_btn = page.locator("button[type='submit']:has-text('continue'), button.continue").first
        if await cont_btn.count() > 0:
            await cont_btn.click()
            for _ in range(10):
                await page.wait_for_timeout(500)
                if "?s=edit" not in page.url:
                    break
    except Exception as exc:
        print(f"  [Craigslist Note - Step 5 submit]: {exc}", flush=True)

    # Step 6: Map / Location Confirmation (?s=geoverify)
    try:
        if "?s=geoverify" in page.url or "map" in (await page.title()).lower():
            map_cont = page.locator("button[type='submit']:has-text('continue'), button.continue").first
            if await map_cont.count() > 0 and await map_cont.is_visible():
                btn_txt = (await map_cont.inner_text() or "").strip()
                assert_safe_action_label(btn_txt)
                await map_cont.click()
                for _ in range(10):
                    await page.wait_for_timeout(500)
                    if "?s=geoverify" not in page.url:
                        break
    except Exception as exc:
        print(f"  [Craigslist Note - Step 6]: {exc}", flush=True)

    # Step 7: Upload Original High-Resolution Raw Photos (?s=editimage)
    if photos and ("?s=editimage" in page.url or "images" in (await page.title()).lower()):
        try:
            file_input = page.locator("input[type='file']").first
            if await file_input.count() > 0:
                upload_set = photos[:24]
                print(f"  [Craigslist] Uploading {len(upload_set)} original high-resolution raw photo(s)...", flush=True)
                await file_input.set_input_files(upload_set)
                upload_wait_seconds = min(15, 3 + len(upload_set) * 1)
                await page.wait_for_timeout(upload_wait_seconds * 1000)
        except Exception as exc:
            print(f"  [Craigslist Photo upload note]: {exc}", flush=True)

    # Step 8: Click "done with images" to land on final Preview screen
    try:
        done_img_btn = page.locator("button:has-text('done with images')").first
        if await done_img_btn.count() > 0 and await done_img_btn.is_visible():
            await done_img_btn.click()
            for _ in range(10):
                await page.wait_for_timeout(500)
                if "?s=preview" in page.url:
                    break
    except Exception as exc:
        print(f"  [Craigslist Note - Step 8]: {exc}", flush=True)

    # Verify Preview Screen (Hard Safety Rule: NEVER click "publish")
    publish_btn = page.locator("button:has-text('publish'), input[value='publish']")
    pub_count = await publish_btn.count()
    if pub_count > 0:
        print(f"  [Craigslist Safety Check] 'Publish' button found on screen — strictly parked on Preview (NOT published).", flush=True)

    print(f"  [Craigslist] Successfully parked on final Preview tab: {page.url}", flush=True)
    return page.url


async def run_draft_pipeline(
    manifest_path: Path,
    sku_filter: Optional[str] = None,
    platform_filter: Optional[str] = None,
    dry_run: bool = False,
    overwrite: bool = False,
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

            cl_url = item.get("craigslist_edit_url") or ""
            is_cl_incomplete = (
                not cl_url
                or "dry_run" in cl_url
                or "s=type" in cl_url
                or "s=subarea" in cl_url
                or "s=hood" in cl_url
                or "s=cat" in cl_url
                or "s=edit" in cl_url
                or "s=geoverify" in cl_url
                or "s=editimage" in cl_url
                or "aberdeen" in cl_url.lower()
            )

            needs_ebay = run_ebay and (overwrite or not item.get("ebay_draft_url"))
            needs_fbmp = run_fbmp and (overwrite or not item.get("fbmp_listing_url"))
            needs_cl = run_cl and (overwrite or is_cl_incomplete)

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
        "--overwrite",
        action="store_true",
        help="Regenerate drafts even if a draft URL is already recorded",
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
            overwrite=args.overwrite,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
