#!/usr/bin/env python3
"""
Live Multi-Platform Comp Fetcher (scripts/fetch_comps.py)

Queries live listings directly through the persistent Chrome CDP session (localhost:9222):
  1. eBay Sold   (LH_Complete=1&LH_Sold=1&_sop=13 — most recent sold)
  2. eBay Active (_sop=15 — lowest price + shipping first)
  3. Facebook Marketplace local/shipped search
  4. Craigslist SF Bay Area (or CRAIGSLIST_SUBDOMAIN) search

Outputs structured results to `items/comps_report.json` and `items/comps_report.md`.
"""

import argparse
import asyncio
import json
import os
import re
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from playwright.async_api import async_playwright

MANIFEST_PATH = Path("items/batch_manifest.json")
OUT_JSON = Path("items/comps_report.json")
OUT_MD = Path("items/comps_report.md")

STOPWORDS = {
    "with", "and", "for", "the", "new", "used", "good", "very", "like", "open",
    "box", "parts", "repair", "only", "combo", "model", "size", "inch", "in",
    "pack", "set", "kit", "authentic", "oem", "black", "silver", "tan", "clear",
    "men", "womens", "portable", "folding", "plus", "w/", "or",
}


def build_search_query(item: Dict[str, Any]) -> str:
    """Build a concise, high-signal search query from brand, model_mpn, or title."""
    override = item.get("comp_query")
    if override:
        return str(override).strip()

    brand = str(item.get("brand") or "").strip()
    model = str(item.get("model_mpn") or "").strip()
    title = str(item.get("title") or "").strip()

    # Use up to 5 meaningful tokens from title, prioritizing brand + model
    tokens = []
    for raw in f"{brand} {model} {title}".split():
        clean = re.sub(r"[^A-Za-z0-9\-\.]", "", raw)
        low = clean.lower()
        if not clean or len(clean) < 2 or low in STOPWORDS or low in ("unknown", "tbd", "unbranded"):
            continue
        if low not in {t.lower() for t in tokens}:
            tokens.append(clean)
        if len(tokens) >= 5:
            break
    return " ".join(tokens) if tokens else title[:50]


def _relevance_tokens(query: str) -> List[str]:
    return [
        t.lower()
        for t in re.split(r"\s+", query.strip())
        if len(t) >= 2 and t.lower() not in STOPWORDS
    ]


def _is_relevant(title: str, req_tokens: List[str]) -> bool:
    if not req_tokens:
        return True
    tl = title.lower()
    hits = sum(1 for tok in req_tokens if tok in tl)
    needed = 1 if len(req_tokens) == 1 else min(2, len(req_tokens))
    return hits >= needed


def _parse_ebay_card_text(txt: str, own_title: str = "") -> Optional[Dict[str, Any]]:
    if not txt or "Shop on eBay" in txt or "See all similar items" in txt:
        return None
    parts = [p.strip() for p in txt.split("|") if p.strip()]
    if not parts:
        return None

    sold_date = None
    idx = 0
    if parts[0].lower().startswith("sold "):
        sold_date = parts[0]
        idx = 1
    if idx >= len(parts):
        return None

    title = parts[idx]
    if own_title and title.lower()[:35] == own_title.lower()[:35]:
        return None

    # Skip "Parts Only" unless caller wants it
    is_parts_only = any(p.lower() == "parts only" for p in parts)

    # Find first dollar amount (item price) and shipping
    prices = re.findall(r"\$([0-9,]+\.\d{2})", txt)
    if not prices:
        return None
    item_price = float(prices[0].replace(",", ""))

    ship_price: Optional[float] = None
    if re.search(r"free\s+(delivery|shipping)", txt, re.IGNORECASE):
        ship_price = 0.0
    else:
        m_ship = re.search(r"\+\$([0-9,]+\.\d{2})\s+(?:delivery|shipping)", txt, re.IGNORECASE)
        if m_ship:
            ship_price = float(m_ship.group(1).replace(",", ""))

    total_price = round(item_price + (ship_price if ship_price is not None else 0.0), 2)
    return {
        "title": title,
        "sold_date": sold_date,
        "item_price": item_price,
        "shipping_price": ship_price,
        "total_price": total_price,
        "is_parts_only": is_parts_only,
    }


async def fetch_ebay_comps(
    page: Any,
    query: str,
    own_title: str = "",
    allow_parts: bool = False,
    sold: bool = True,
    limit: int = 8,
) -> List[Dict[str, Any]]:
    url = (
        f"https://www.ebay.com/sch/i.html?_nkw={quote_plus(query)}&LH_Complete=1&LH_Sold=1&_sop=13"
        if sold
        else f"https://www.ebay.com/sch/i.html?_nkw={quote_plus(query)}&_sop=15"
    )
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(2800)

    req_tokens = _relevance_tokens(query)
    cards = await page.locator("li.s-card, li.s-item").all()
    results: List[Dict[str, Any]] = []
    for c in cards[:35]:
        txt = (await c.inner_text() or "").strip().replace("\n", " | ")
        parsed = _parse_ebay_card_text(txt, own_title=own_title)
        if not parsed:
            continue
        if parsed["is_parts_only"] and not allow_parts:
            continue
        if not _is_relevant(parsed["title"], req_tokens):
            continue
        results.append(parsed)
        if len(results) >= limit:
            break
    return results


async def fetch_fbmp_comps(
    page: Any,
    query: str,
    own_title: str = "",
    limit: int = 6,
) -> List[Dict[str, Any]]:
    url = f"https://www.facebook.com/marketplace/search/?query={quote_plus(query)}"
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    req_tokens = _relevance_tokens(query)
    links = await page.locator("a[href*='/marketplace/item/']").all()
    results: List[Dict[str, Any]] = []
    seen = set()
    for a in links[:35]:
        txt = (await a.inner_text() or "").strip().replace("\n", " | ")
        if not txt:
            continue
        m_p = re.search(r"\$([0-9,]+)", txt)
        if not m_p:
            continue
        price = float(m_p.group(1).replace(",", ""))
        parts = [p.strip() for p in txt.split("|") if p.strip() and not p.strip().startswith("$")]
        if not parts:
            continue
        title = parts[0]
        if own_title and title.lower()[:30] == own_title.lower()[:30]:
            continue
        if not _is_relevant(title, req_tokens):
            continue
        key = (title.lower()[:40], price)
        if key in seen:
            continue
        seen.add(key)
        loc = parts[1] if len(parts) > 1 else ""
        results.append({"title": title, "price": price, "location": loc})
        if len(results) >= limit:
            break
    return results


async def fetch_cl_comps(
    page: Any,
    query: str,
    own_title: str = "",
    subdomain: str = "sfbay",
    limit: int = 6,
) -> List[Dict[str, Any]]:
    url = f"https://{subdomain}.craigslist.org/search/sss?query={quote_plus(query)}"
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(2800)

    req_tokens = _relevance_tokens(query)
    rows = await page.locator(".cl-search-result").all()
    results: List[Dict[str, Any]] = []
    for r in rows[:30]:
        txt = (await r.inner_text() or "").strip().replace("\n", " | ")
        if not txt:
            continue
        m_p = re.search(r"\$([0-9,]+)", txt)
        if not m_p:
            continue
        price = float(m_p.group(1).replace(",", ""))
        title_el = r.locator("a.posting-title, .titlestring").first
        title = (await title_el.inner_text() or "").strip() if await title_el.count() > 0 else txt[:60]
        if own_title and title.lower()[:30] == own_title.lower()[:30]:
            continue
        if not _is_relevant(title, req_tokens):
            continue
        results.append({"title": title, "price": price})
        if len(results) >= limit:
            break
    return results


def _summarize_prices(vals: List[float]) -> Dict[str, Any]:
    if not vals:
        return {"count": 0, "min": None, "median": None, "max": None}
    return {
        "count": len(vals),
        "min": round(min(vals), 2),
        "median": round(statistics.median(vals), 2),
        "max": round(max(vals), 2),
    }


async def run_comps(skus: Optional[List[str]] = None, custom_query: Optional[str] = None) -> None:
    cdp_url = os.getenv("CHROME_CDP_URL", "http://localhost:9222")
    subdomain = os.getenv("CRAIGSLIST_SUBDOMAIN", "sfbay")

    p = await async_playwright().start()
    try:
        browser = await p.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await context.new_page()

        if custom_query:
            sold = await fetch_ebay_comps(page, custom_query, sold=True)
            active = await fetch_ebay_comps(page, custom_query, sold=False)
            fbmp = await fetch_fbmp_comps(page, custom_query)
            cl = await fetch_cl_comps(page, custom_query, subdomain=subdomain)
            print(json.dumps({"query": custom_query, "ebay_sold": sold, "ebay_active": active, "fbmp": fbmp, "cl": cl}, indent=2))
            await page.close()
            return

        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        items = manifest.get("items", [])
        targets = [
            it for it in items
            if it.get("routing_bucket") != "DONATE_OR_LOT"
            and (not skus or it.get("item_id", "").lower() in skus or it.get("sku", "").lower() in skus)
        ]

        report: List[Dict[str, Any]] = []
        for it in targets:
            iid = it["item_id"]
            title = it.get("title", "")
            query = build_search_query(it)
            allow_parts = "parts" in str(it.get("condition", "")).lower()
            need_ebay = it.get("routing_bucket") in ("ALL_THREE", "EBAY_ONLY")

            sold = await fetch_ebay_comps(page, query, own_title=title, allow_parts=allow_parts, sold=True) if need_ebay else []
            active = await fetch_ebay_comps(page, query, own_title=title, allow_parts=allow_parts, sold=False) if need_ebay else []
            fbmp = await fetch_fbmp_comps(page, query, own_title=title)
            cl = await fetch_cl_comps(page, query, own_title=title, subdomain=subdomain)

            sold_sum = _summarize_prices([x["total_price"] for x in sold])
            act_sum = _summarize_prices([x["total_price"] for x in active])
            fb_sum = _summarize_prices([x["price"] for x in fbmp])
            cl_sum = _summarize_prices([x["price"] for x in cl])

            entry = {
                "item_id": iid,
                "title": title,
                "query": query,
                "routing_bucket": it.get("routing_bucket"),
                "our_ebay_price": it.get("ebay_list_price_usd") if need_ebay else None,
                "our_fbmp_price": it.get("fbmp_list_price_usd"),
                "our_cl_price": it.get("craigslist_list_price_usd"),
                "ebay_sold_summary": sold_sum,
                "ebay_active_summary": act_sum,
                "fbmp_summary": fb_sum,
                "cl_summary": cl_sum,
                "ebay_sold": sold,
                "ebay_active": active,
                "fbmp": fbmp,
                "cl": cl,
            }
            report.append(entry)
            print(
                f"{iid:8s} | q='{query[:28]:28s}' | "
                f"eSold={sold_sum['min']}..{sold_sum['max']} (n={sold_sum['count']}) | "
                f"eAct={act_sum['min']} (n={act_sum['count']}) | "
                f"FB={fb_sum['min']}..{fb_sum['max']} (n={fb_sum['count']}) | "
                f"CL={cl_sum['min']}..{cl_sum['max']} (n={cl_sum['count']})",
                flush=True,
            )

        await page.close()
        OUT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")

        md_lines = [
            "# Live Multi-Platform Comps Report",
            "",
            "| SKU | Item | Query | Our Prices (eBay / FB / CL) | eBay Sold (Min–Med) | eBay Active Min | FBMP Local (Min–Med) | CL Local (Min–Med) |",
            "| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: |",
        ]
        for r in report:
            our_e = f"${r['our_ebay_price']:.2f}" if r["our_ebay_price"] else "—"
            our_f = f"${r['our_fbmp_price']:.0f}" if r["our_fbmp_price"] else "—"
            our_c = f"${r['our_cl_price']:.0f}" if r["our_cl_price"] and r["routing_bucket"] != "EBAY_ONLY" else "—"
            es = r["ebay_sold_summary"]
            ea = r["ebay_active_summary"]
            fb = r["fbmp_summary"]
            cl = r["cl_summary"]
            es_s = f"${es['min']:.2f}–${es['median']:.2f} ({es['count']})" if es["count"] else "—"
            ea_s = f"${ea['min']:.2f} ({ea['count']})" if ea["count"] else "—"
            fb_s = f"${fb['min']:.0f}–${fb['median']:.0f} ({fb['count']})" if fb["count"] else "—"
            cl_s = f"${cl['min']:.0f}–${cl['median']:.0f} ({cl['count']})" if cl["count"] else "—"
            md_lines.append(
                f"| `{r['item_id']}` | {r['title'][:42]} | `{r['query']}` | {our_e} / {our_f} / {our_c} | {es_s} | {ea_s} | {fb_s} | {cl_s} |"
            )
        OUT_MD.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
        print(f"\nSaved {OUT_JSON} and {OUT_MD}", flush=True)
    finally:
        await p.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch live eBay Sold/Active, FBMP, and Craigslist comps via Chrome CDP.")
    parser.add_argument("--sku", help="Optional comma-separated item_id(s) or SKU(s)")
    parser.add_argument("--query", help="Optional ad-hoc search query")
    args = parser.parse_args()

    skus = [s.strip().lower() for s in args.sku.split(",") if s.strip()] if args.sku else None
    asyncio.run(run_comps(skus=skus, custom_query=args.query))


if __name__ == "__main__":
    main()
