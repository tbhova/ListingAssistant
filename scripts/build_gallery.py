"""
build_gallery.py - Generate Interactive Resale Batch Inspector & WYSIWYG Editor HTML
Optimized for desktop and mobile phone browsers over local Wi-Fi with Dark Mode by default.

Outputs:
  - items/batch_gallery.html
  - ~/.gemini/antigravity/brain/<conv_id>/batch_gallery.html (if detected)
"""

from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
from typing import Any, Dict, List
from PIL import Image, ImageOps

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
ITEMS_DIR = WORKSPACE_ROOT / "items"
MANIFEST_PATH = ITEMS_DIR / "batch_manifest.json"


def get_output_paths() -> List[Path]:
    """Determine output paths dynamically without hardcoded personal usernames."""
    paths = [ITEMS_DIR / "batch_gallery.html"]
    
    env_path = os.getenv("GALLERY_HTML_PATH")
    if env_path:
        paths.append(Path(env_path))

    home = Path.home()
    brain_base = home / ".gemini" / "antigravity" / "brain"
    if brain_base.exists():
        recent_dirs = sorted(
            [d for d in brain_base.iterdir() if d.is_dir() and not d.name.startswith(".")],
            key=lambda d: d.stat().st_mtime,
            reverse=True
        )
        if recent_dirs:
            paths.append(recent_dirs[0] / "batch_gallery.html")

    return list(dict.fromkeys(paths))


def build_gallery() -> None:
    if not MANIFEST_PATH.exists():
        print(f"[ERROR] Manifest not found: {MANIFEST_PATH}")
        return

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    raw_items = manifest.get("items", [])
    sorted_items = sorted(raw_items, key=lambda x: x.get("item_id", ""))

    processed_inventory: List[Dict[str, Any]] = []

    for item in sorted_items:
        item_id = item.get("item_id", "item_XX")
        folder = ITEMS_DIR / item_id
        num_str = item_id.split("_")[1] if "_" in item_id else item_id
        
        photo_files = sorted(list(folder.glob("*.jpg")) + list(folder.glob("*.png"))) if folder.exists() else []
        b64_photos = []
        
        for pf in photo_files:
            try:
                with Image.open(pf) as raw_img:
                    img = ImageOps.exif_transpose(raw_img)
                    img.thumbnail((720, 720), Image.Resampling.LANCZOS)
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=68, optimize=True)
                    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                    b64_photos.append({
                        "filename": pf.name,
                        "data": f"data:image/jpeg;base64,{b64}"
                    })
            except Exception as e:
                print(f"[WARN] Error encoding photo {pf}: {e}")
                
        route = item.get("routing_bucket", "LOCAL_ONLY")
        if route == "ALL_THREE":
            badge_label = "eBay + FBMP + Craigslist"
            badge_style = "bg-emerald-500/15 text-emerald-400 border-emerald-500/30"
        elif route == "EBAY_ONLY":
            badge_label = "eBay Only"
            badge_style = "bg-blue-500/15 text-blue-400 border-blue-500/30"
        elif route == "LOCAL_ONLY":
            badge_label = "Local Only (FBMP + CL)"
            badge_style = "bg-amber-500/15 text-amber-400 border-amber-500/30"
        else:
            badge_label = "Donate / Lot"
            badge_style = "bg-purple-500/15 text-purple-400 border-purple-500/30"

        ship_info = item.get("shipping", {})
        zone8_cost = ship_info.get("zone8_cost_usd", 0.0)
        billable_wt = ship_info.get("billable_weight_lbs", item.get("weight_lbs", 0.0))
        carrier = ship_info.get("service_selected", "USPS Ground Advantage")
        
        processed_inventory.append({
            "id": item_id,
            "sku": item.get("sku", f"ITEM-{num_str}"),
            "num": num_str,
            "title": item.get("title", ""),
            "brand": item.get("brand", ""),
            "model_mpn": item.get("model_mpn", ""),
            "category": item.get("category", ""),
            "condition": item.get("condition", "Used - Good"),
            "condition_notes": item.get("condition_notes", ""),
            "accessories": item.get("accessories", []),
            "comp_price": float(item.get("comp_price", 0.0)),
            "ebay_price": f"${item.get('ebay_list_price_usd', item.get('comp_price', 0.0)):.2f}",
            "fbmp_price": f"${item.get('fbmp_list_price_usd', item.get('comp_price', 0.0)):.2f}",
            "cl_price": f"${item.get('craigslist_list_price_usd', item.get('comp_price', 0.0)):.2f}",
            "net_profit": f"${item.get('estimated_net_profit_usd', 0.0):.2f}",
            "zone8_ship": f"${zone8_cost:.2f}",
            "billable_wt": f"{billable_wt:.1f} lbs",
            "carrier": carrier,
            "ebay_fees": f"${item.get('ebay_fees_usd', 0.0):.2f}",
            "route_bucket": route,
            "badge_label": badge_label,
            "badge_style": badge_style,
            "routing_reason": item.get("routing_reason", ""),
            "description_detailed": item.get("description_detailed", ""),
            "description_concise": item.get("description_concise", ""),
            "photos": b64_photos
        })

    json_str = json.dumps(processed_inventory)

    html_template = f"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover" />
  <title>Resale Inventory Master Inspector & Mobile Editor</title>
  <script src="https://www.gstatic.com/antigravity/web/dev/tailwindcss.min.js"></script>
  <style>
    :root {{
      --background: #0b0f19;
      --card: #111827;
      --card-muted: #162032;
      --foreground: #f3f4f6;
      --muted-foreground: #9ca3af;
      --border: #1f2937;
      --border-focus: #3b82f6;
      --primary: #2563eb;
      --primary-hover: #1d4ed8;
      --primary-foreground: #ffffff;
      --input-bg: #090d16;
      color-scheme: dark;
    }}

    body {{
      background-color: var(--background);
      color: var(--foreground);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      color-scheme: dark;
    }}

    /* Smooth touch scrolling */
    .touch-scroll {{ -webkit-overflow-scrolling: touch; }}

    /* Dark Mode Scrollbars */
    ::-webkit-scrollbar {{
      width: 6px;
      height: 6px;
    }}
    ::-webkit-scrollbar-track {{
      background: #0b0f19;
    }}
    ::-webkit-scrollbar-thumb {{
      background: #1f2937;
      border-radius: 4px;
    }}
    ::-webkit-scrollbar-thumb:hover {{
      background: #374151;
    }}
  </style>
</head>
<body class="bg-[#0b0f19] text-[#f3f4f6] antialiased p-2 sm:p-4 font-sans pb-24 lg:pb-6 min-h-screen">
  <div class="bg-[#111827] border border-[#1f2937] rounded-2xl p-3.5 sm:p-5 shadow-2xl max-w-6xl mx-auto flex flex-col gap-3.5 sm:gap-4">
    
    <!-- Top Header -->
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 pb-3 border-b border-[#1f2937]">
      <div>
        <div class="flex items-center gap-2 flex-wrap">
          <h2 class="text-lg sm:text-xl font-bold text-white tracking-tight">Resale Inventory Master Inspector</h2>
          <span class="text-xs font-semibold bg-emerald-500/15 text-emerald-400 px-2.5 py-0.5 rounded-full border border-emerald-500/30">{len(processed_inventory)} Items</span>
          <span class="text-xs font-semibold bg-blue-500/15 text-blue-400 px-2.5 py-0.5 rounded-full border border-blue-500/30">{sum(len(x['photos']) for x in processed_inventory)} Photos</span>
          <span class="text-[11px] font-medium bg-slate-800 text-slate-300 px-2 py-0.5 rounded-full border border-slate-700">Dark Mode</span>
        </div>
        <p class="text-xs text-slate-400 mt-0.5">Edit titles, prices, conditions, and copy live on desktop or your phone.</p>
      </div>
      
      <!-- Connection Status & Phone Info -->
      <div class="flex items-center gap-2 text-xs self-start sm:self-auto flex-wrap">
        <div id="server-status-pill" class="flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-medium transition-colors bg-slate-800/80 border-slate-700 text-slate-300">
          <span id="server-dot" class="inline-block w-2 h-2 rounded-full bg-slate-400"></span>
          <span id="server-status-text">Checking server...</span>
        </div>
        <div id="phone-pill" class="hidden items-center gap-1.5 px-2.5 py-1 rounded-lg bg-[#090d16] border border-[#1f2937] text-xs text-slate-200 font-mono">
          <svg class="w-3.5 h-3.5 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 18h.01M8 21h8a2 2 0 002-2V5a2 2 0 00-2-2H8a2 2 0 00-2 2v14a2 2 0 002 2z"></path></svg>
          <span id="phone-url-text" class="truncate max-w-[170px]"></span>
          <button id="copy-phone-url-btn" class="hover:text-emerald-400 transition-colors text-[11px] p-0.5" title="Copy phone URL">
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"></path></svg>
          </button>
        </div>
      </div>
    </div>

    <!-- Item Selector Tabs -->
    <div class="flex gap-1.5 overflow-x-auto pb-1.5 scrollbar-thin touch-scroll" id="item-tabs"></div>

    <!-- Active Item Header Card with Inline Editable Title & Price -->
    <div class="bg-[#0b0f19]/80 border border-[#1f2937] rounded-xl p-3 sm:p-4 flex flex-col lg:flex-row justify-between items-start lg:items-center gap-3 sm:gap-4 shadow-sm">
      <div class="flex-1 w-full">
        <div class="flex items-center gap-2 flex-wrap mb-1">
          <span id="item-num-badge" class="text-xs font-bold px-2 py-0.5 rounded bg-blue-600 text-white font-mono">Item 01</span>
          <span id="item-sku" class="text-xs font-mono text-slate-400">ITEM-20261110-01</span>
          <span id="item-route-badge" class="text-xs px-2.5 py-0.5 rounded-full border font-semibold"></span>
        </div>
        
        <!-- Editable Title Field -->
        <div class="relative w-full">
          <input id="edit-title" type="text" class="w-full text-sm sm:text-base font-bold text-white bg-transparent border-b border-transparent hover:border-slate-700 focus:border-blue-500 focus:bg-[#111827] focus:ring-1 focus:ring-blue-500 rounded px-1.5 py-1 transition-all outline-none" placeholder="Item Title..." />
        </div>
        <p id="item-route-reason" class="text-[11px] sm:text-xs text-slate-400 mt-0.5 px-1.5 italic"></p>
      </div>
      
      <!-- Economic Quick Stats -->
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 w-full lg:w-auto shrink-0 border-t lg:border-t-0 pt-2.5 lg:pt-0 border-[#1f2937]">
        <div class="bg-[#111827] border border-[#1f2937] p-1.5 sm:p-2 rounded-lg text-center shadow-inner">
          <div class="text-[9px] sm:text-[10px] uppercase tracking-wider text-slate-400 font-medium">Target Comp ($)</div>
          <input id="edit-comp-price" type="number" step="0.5" class="w-20 text-sm font-bold text-center text-white bg-transparent border-b border-transparent hover:border-slate-700 focus:border-blue-500 outline-none" />
        </div>
        <div class="bg-[#111827] border border-[#1f2937] p-1.5 sm:p-2 rounded-lg text-center shadow-inner">
          <div class="text-[9px] sm:text-[10px] uppercase tracking-wider text-slate-400 font-medium">FBMP Ask</div>
          <div id="stat-fbmp" class="text-sm font-bold text-amber-400"></div>
        </div>
        <div class="bg-[#111827] border border-[#1f2937] p-1.5 sm:p-2 rounded-lg text-center shadow-inner">
          <div class="text-[9px] sm:text-[10px] uppercase tracking-wider text-slate-400 font-medium">Zone 8 Ship</div>
          <div id="stat-ship" class="text-xs sm:text-sm font-medium text-slate-300"></div>
        </div>
        <div class="bg-[#111827] border border-[#1f2937] p-1.5 sm:p-2 rounded-lg text-center shadow-inner">
          <div class="text-[9px] sm:text-[10px] uppercase tracking-wider text-slate-400 font-medium">Est. Net Profit</div>
          <div id="stat-net" class="text-sm font-bold text-emerald-400"></div>
        </div>
      </div>
    </div>

    <!-- Main Workspace: Photo Stage + WYSIWYG Inspector Sidebar -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-3.5 sm:gap-4 items-start">
      
      <!-- Photo Stage (Left 7 Cols) -->
      <div class="lg:col-span-7 flex flex-col gap-2">
        <div class="relative bg-black rounded-xl overflow-hidden border border-[#1f2937] flex items-center justify-center min-h-[300px] sm:min-h-[380px] max-h-[480px] group shadow-inner">
          <img id="main-photo" src="" alt="Main Photo" class="w-full h-auto max-h-[480px] object-contain transition-transform duration-200 cursor-zoom-in" title="Click to view full screen" />
          
          <!-- Prev / Next Overlays -->
          <button id="prev-btn" class="absolute left-2 top-1/2 -translate-y-1/2 bg-black/60 hover:bg-black/90 text-white rounded-full p-2.5 sm:p-3 border border-white/20 transition-all opacity-85 hover:opacity-100 active:scale-95" title="Previous photo">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"></path></svg>
          </button>
          <button id="next-btn" class="absolute right-2 top-1/2 -translate-y-1/2 bg-black/60 hover:bg-black/90 text-white rounded-full p-2.5 sm:p-3 border border-white/20 transition-all opacity-85 hover:opacity-100 active:scale-95" title="Next photo">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"></path></svg>
          </button>

          <!-- Top-right Quick Rotate Controls -->
          <div class="absolute top-2.5 right-2.5 flex gap-1.5 opacity-90 hover:opacity-100 transition-opacity">
            <button id="rotate-left-btn" title="Rotate Left 90°" class="bg-black/80 hover:bg-black text-white px-2.5 py-1.5 rounded-lg border border-white/20 text-xs flex items-center gap-1 backdrop-blur shadow active:scale-95">
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 10h10a5 5 0 015 5v2m0 0l-3-3m3 3l3-3M3 10l3-3m-3 3l3 3"></path></svg>
              <span>↺</span>
            </button>
            <button id="rotate-right-btn" title="Rotate Right 90° (Shortcut: R)" class="bg-black/80 hover:bg-black text-white px-2.5 py-1.5 rounded-lg border border-white/20 text-xs flex items-center gap-1 backdrop-blur shadow active:scale-95">
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 10H11a5 5 0 00-5 5v2m0 0l3-3m-3 3l-3-3m5-6l-3-3m3 3l-3 3"></path></svg>
              <span>↻</span>
            </button>
          </div>
          
          <!-- Bottom filename badge -->
          <div class="absolute bottom-2.5 left-2.5 right-2.5 flex justify-between items-center px-3 py-1 bg-black/80 backdrop-blur rounded-lg text-white text-[11px] sm:text-xs">
            <span id="photo-filename" class="truncate font-mono opacity-90">photo.jpg</span>
            <span id="photo-counter" class="shrink-0 font-medium ml-2 font-mono">1 of 1</span>
          </div>
        </div>

        <!-- Thumbnail Strip below hero photo -->
        <div class="flex gap-2 overflow-x-auto p-1.5 border border-[#1f2937] rounded-xl bg-[#090d16]/70 scrollbar-thin touch-scroll" id="thumbs-strip"></div>
      </div>

      <!-- Right 5 Cols: Spec & WYSIWYG Editor Panel -->
      <div class="lg:col-span-5 flex flex-col gap-3">
        
        <!-- Specs & Condition Card (Editable Condition & Notes) -->
        <div class="border border-[#1f2937] rounded-xl p-3 sm:p-3.5 bg-[#111827] flex flex-col gap-2 text-xs shadow-sm">
          <div class="font-bold text-sm text-white border-b border-[#1f2937] pb-1.5 flex justify-between items-center">
            <span>Item Specifics</span>
            <span id="spec-category" class="text-[11px] font-normal text-slate-400 truncate max-w-[180px]"></span>
          </div>

          <div class="grid grid-cols-2 gap-2 pt-0.5">
            <div>
              <span class="text-slate-400 block mb-0.5 font-medium">Brand / Model:</span>
              <strong id="spec-brand-model" class="text-white truncate block"></strong>
            </div>
            <div>
              <span class="text-slate-400 block mb-0.5 font-medium">Condition:</span>
              <select id="edit-condition" class="w-full bg-[#090d16] text-slate-200 border border-[#1f2937] rounded px-1.5 py-1 text-xs font-semibold outline-none focus:border-blue-500">
                <option value="New">New</option>
                <option value="Open Box">Open Box</option>
                <option value="Used - Like New">Used - Like New</option>
                <option value="Used - Very Good">Used - Very Good</option>
                <option value="Used - Good">Used - Good</option>
                <option value="Used - Fair">Used - Fair</option>
                <option value="For Parts or Not Working">For Parts or Not Working</option>
              </select>
            </div>
          </div>

          <div class="pt-1.5 border-t border-[#1f2937]">
            <span class="text-slate-400 block mb-1 font-medium">Condition Notes:</span>
            <input id="edit-notes" type="text" class="w-full bg-[#090d16] text-slate-200 border border-[#1f2937] rounded px-2 py-1 text-xs outline-none focus:border-blue-500" />
          </div>

          <div class="pt-1.5 border-t border-[#1f2937]">
            <span class="text-slate-400 block mb-0.5 font-medium">Included Accessories:</span>
            <ul id="spec-accessories" class="list-disc list-inside text-slate-300 space-y-0.5 max-h-[85px] overflow-y-auto"></ul>
          </div>
        </div>

        <!-- Description & WYSIWYG Editor Card with Edit & Preview Modes -->
        <div class="border border-[#1f2937] rounded-xl p-3 sm:p-3.5 bg-[#111827] flex flex-col gap-2 text-xs shadow-sm">
          <div class="font-bold text-sm text-white border-b border-[#1f2937] pb-1.5 flex justify-between items-center flex-wrap gap-2">
            
            <!-- Edit Mode vs Formatted Preview Toggle -->
            <div class="flex items-center rounded-lg bg-[#090d16] p-0.5 border border-[#1f2937]">
              <button id="mode-edit-btn" class="px-2.5 py-1 text-xs font-semibold rounded-md bg-blue-600 text-white transition-all">
                Edit Mode
              </button>
              <button id="mode-preview-btn" class="px-2.5 py-1 text-xs font-medium text-slate-400 hover:text-white rounded-md transition-all">
                Preview Mode
              </button>
            </div>

            <div class="flex items-center gap-1.5">
              <button id="copy-desc-btn" class="text-[11px] px-2.5 py-1 rounded border border-[#1f2937] bg-[#090d16] text-slate-300 hover:bg-slate-800 transition-colors active:scale-95 flex items-center gap-1">
                <svg class="w-3 h-3 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"></path></svg>
                <span>Copy</span>
              </button>
              <button id="save-btn" class="text-[11px] font-semibold px-3 py-1 rounded bg-blue-600 text-white hover:bg-blue-500 transition-colors flex items-center gap-1 shadow-sm active:scale-95">
                <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7H5a2 2 0 00-2 2v9a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-3m-1 4l-3 3m0 0l-3-3m3 3V4"></path></svg>
                <span>Save</span>
              </button>
            </div>
          </div>

          <!-- Toast notification -->
          <div id="save-toast" class="hidden px-2.5 py-1 rounded text-xs font-medium text-emerald-400 bg-emerald-500/10 border border-emerald-500/30 transition-all"></div>

          <!-- Edit Mode: Textarea -->
          <div id="edit-pane" class="w-full">
            <textarea id="edit-description" rows="8" class="w-full bg-[#090d16] border border-[#1f2937] rounded-lg p-2.5 font-sans leading-relaxed text-slate-200 text-xs resize-y outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-all" placeholder="Enter listing description..."></textarea>
          </div>

          <!-- Preview Mode: Formatted Render Pane -->
          <div id="preview-pane" class="hidden w-full bg-[#090d16] border border-[#1f2937] rounded-lg p-3 font-sans text-slate-200 text-xs max-h-[220px] overflow-y-auto leading-relaxed space-y-2 select-text">
            <div id="preview-rendered" class="whitespace-pre-line text-slate-300"></div>
          </div>
          
          <div class="text-[10px] text-slate-400 flex justify-between items-center pt-0.5">
            <span>Desktop: <kbd class="px-1 py-0.5 rounded bg-[#090d16] border border-[#1f2937] text-slate-300">Ctrl+S</kbd></span>
            <span id="char-counter" class="font-mono">0 chars</span>
          </div>
        </div>

      </div>

    </div>

  </div>

  <!-- Mobile Sticky Bottom Save Bar (Visible on mobile screens) -->
  <div class="lg:hidden fixed bottom-0 left-0 right-0 p-3 bg-[#111827]/95 backdrop-blur border-t border-[#1f2937] shadow-2xl flex items-center justify-between gap-3 z-50">
    <div class="flex-1 min-w-0">
      <div id="mobile-item-title" class="text-xs font-bold text-white truncate">Item</div>
      <div id="mobile-item-price" class="text-[11px] font-semibold text-emerald-400 font-mono">$0.00</div>
    </div>
    <div class="flex items-center gap-2">
      <button id="mobile-copy-btn" class="px-3 py-2 text-xs font-medium rounded-lg border border-[#1f2937] bg-[#090d16] text-slate-300 active:scale-95">
        Copy
      </button>
      <button id="mobile-save-btn" class="px-4 py-2 text-xs font-bold rounded-lg bg-blue-600 text-white shadow-md flex items-center gap-1 active:scale-95 hover:bg-blue-500">
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7H5a2 2 0 00-2 2v9a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-3m-1 4l-3 3m0 0l-3-3m3 3V4"></path></svg>
        <span>Save</span>
      </button>
    </div>
  </div>

  <!-- Fullscreen Photo Modal -->
  <div id="photo-modal" class="hidden fixed inset-0 z-50 bg-black/95 backdrop-blur-md flex flex-col justify-between p-3 sm:p-6">
    <div class="flex justify-between items-center text-white pb-2">
      <span id="modal-filename" class="font-mono text-xs sm:text-sm text-slate-300">photo.jpg</span>
      <button id="close-modal-btn" class="p-2 rounded-full bg-slate-800 hover:bg-slate-700 text-white transition-colors">
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
      </button>
    </div>
    <div class="flex-1 flex items-center justify-center overflow-hidden">
      <img id="modal-photo" src="" alt="Full Screen" class="max-w-full max-h-[82vh] object-contain transition-transform duration-200" />
    </div>
    <div class="flex justify-center gap-4 pt-2">
      <button id="modal-prev-btn" class="px-4 py-2 bg-slate-800 text-white rounded-lg text-xs font-semibold hover:bg-slate-700">Previous</button>
      <button id="modal-next-btn" class="px-4 py-2 bg-slate-800 text-white rounded-lg text-xs font-semibold hover:bg-slate-700">Next</button>
    </div>
  </div>

  <script>
    const inventory = {json_str};
    let currentItemIdx = 10; // Aeron + Atlas bundle by default
    let currentPhotoIdx = 0;
    let currentRotation = 0;
    let isLiveServer = false;
    let serverBaseUrl = '';
    let currentMode = 'edit'; // 'edit' or 'preview'

    const statusPill = document.getElementById('server-status-pill');
    const statusDot = document.getElementById('server-dot');
    const statusText = document.getElementById('server-status-text');
    const phonePill = document.getElementById('phone-pill');
    const phoneUrlText = document.getElementById('phone-url-text');
    const copyPhoneBtn = document.getElementById('copy-phone-url-btn');

    async function checkServer() {{
      const candidateUrls = [window.location.origin, 'http://localhost:8000', 'http://127.0.0.1:8000'];
      for (const url of candidateUrls) {{
        try {{
          const res = await fetch(`${{url}}/api/status`, {{ method: 'GET', signal: AbortSignal.timeout(1200) }});
          if (res.ok) {{
            const data = await res.json();
            if (data.status === 'ok') {{
              isLiveServer = true;
              serverBaseUrl = url;
              statusPill.className = 'flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-medium bg-emerald-500/10 border-emerald-500/30 text-emerald-400';
              statusDot.className = 'inline-block w-2 h-2 rounded-full bg-emerald-400 animate-pulse';
              statusText.textContent = 'Live Server Connected';
              
              if (data.phone_url) {{
                phonePill.classList.remove('hidden');
                phonePill.classList.add('flex');
                phoneUrlText.textContent = data.phone_url;
                copyPhoneBtn.onclick = () => {{
                  navigator.clipboard.writeText(data.phone_url).then(() => {{
                    copyPhoneBtn.innerHTML = '<span class="text-emerald-400 font-bold">✓</span>';
                    setTimeout(() => {{ 
                      copyPhoneBtn.innerHTML = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"></path></svg>';
                    }}, 2000);
                  }});
                }};
              }}
              return;
            }}
          }}
        }} catch (e) {{}}
      }}
      statusPill.className = 'flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-medium bg-blue-500/10 border-blue-500/30 text-blue-400';
      statusDot.className = 'inline-block w-2 h-2 rounded-full bg-blue-400';
      statusText.textContent = 'Offline File View';
    }}

    checkServer();

    const tabsContainer = document.getElementById('item-tabs');
    const mainPhoto = document.getElementById('main-photo');
    const photoFilename = document.getElementById('photo-filename');
    const photoCounter = document.getElementById('photo-counter');
    const thumbsStrip = document.getElementById('thumbs-strip');
    
    // Header & Stats
    const itemNumBadge = document.getElementById('item-num-badge');
    const itemSku = document.getElementById('item-sku');
    const itemRouteBadge = document.getElementById('item-route-badge');
    const itemRouteReason = document.getElementById('item-route-reason');
    const editTitle = document.getElementById('edit-title');
    const editCompPrice = document.getElementById('edit-comp-price');
    const statFbmp = document.getElementById('stat-fbmp');
    const statShip = document.getElementById('stat-ship');
    const statNet = document.getElementById('stat-net');

    // Specs
    const specCategory = document.getElementById('spec-category');
    const specBrandModel = document.getElementById('spec-brand-model');
    const editCondition = document.getElementById('edit-condition');
    const editNotes = document.getElementById('edit-notes');
    const specAccessories = document.getElementById('spec-accessories');
    
    // Editor & Preview Modes
    const modeEditBtn = document.getElementById('mode-edit-btn');
    const modePreviewBtn = document.getElementById('mode-preview-btn');
    const editPane = document.getElementById('edit-pane');
    const previewPane = document.getElementById('preview-pane');
    const previewRendered = document.getElementById('preview-rendered');
    const editDescription = document.getElementById('edit-description');
    const charCounter = document.getElementById('char-counter');
    const copyDescBtn = document.getElementById('copy-desc-btn');
    const saveBtn = document.getElementById('save-btn');
    const saveToast = document.getElementById('save-toast');

    // Mobile sticky bar elements
    const mobileItemTitle = document.getElementById('mobile-item-title');
    const mobileItemPrice = document.getElementById('mobile-item-price');
    const mobileSaveBtn = document.getElementById('mobile-save-btn');
    const mobileCopyBtn = document.getElementById('mobile-copy-btn');

    // Modal elements
    const photoModal = document.getElementById('photo-modal');
    const modalPhoto = document.getElementById('modal-photo');
    const modalFilename = document.getElementById('modal-filename');
    const closeModalBtn = document.getElementById('close-modal-btn');
    const modalPrevBtn = document.getElementById('modal-prev-btn');
    const modalNextBtn = document.getElementById('modal-next-btn');

    function renderTabs() {{
      tabsContainer.innerHTML = '';
      inventory.forEach((item, idx) => {{
        const btn = document.createElement('button');
        const isActive = idx === currentItemIdx;
        btn.className = `px-2.5 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap transition-all border ${{
          isActive 
            ? 'bg-blue-600 text-white border-blue-500 shadow-sm' 
            : 'bg-[#111827] hover:bg-[#1f2937] text-slate-300 border-[#1f2937]'
        }}`;
        btn.innerHTML = `<span class="opacity-80 font-mono">#${{item.num}}</span> ${{item.brand || item.title.split(' ')[0]}} <span class="text-[10px] opacity-70 ml-0.5">(${{item.photos.length}})</span>`;
        btn.onclick = () => selectItem(idx);
        tabsContainer.appendChild(btn);
      }});
    }}

    function selectItem(idx) {{
      currentItemIdx = idx;
      currentPhotoIdx = 0;
      currentRotation = 0;
      renderTabs();
      updateItemView();
    }}

    function updateItemView() {{
      const item = inventory[currentItemIdx];
      itemNumBadge.textContent = `Item ${{item.num}} / ${{inventory.length}}`;
      itemSku.textContent = item.sku;
      editTitle.value = item.title;
      editCompPrice.value = item.comp_price;
      itemRouteBadge.textContent = item.badge_label;
      itemRouteBadge.className = `text-xs px-2.5 py-0.5 rounded-full border font-semibold ${{item.badge_style}}`;
      itemRouteReason.textContent = item.routing_reason;

      // Stats
      statFbmp.textContent = item.fbmp_price;
      statShip.textContent = `${{item.zone8_ship}} (${{item.billable_wt}})`;
      statNet.textContent = item.net_profit;

      // Mobile Bar
      mobileItemTitle.textContent = `#${{item.num}} ${{item.title}}`;
      mobileItemPrice.textContent = `Comp: $${{item.comp_price.toFixed(2)}} | FBMP: ${{item.fbmp_price}}`;

      // Specs
      specCategory.textContent = item.category;
      specBrandModel.textContent = `${{item.brand}} - ${{item.model_mpn}}`;
      editCondition.value = item.condition || 'Used - Good';
      editNotes.value = item.condition_notes || '';

      // Accessories
      specAccessories.innerHTML = '';
      (item.accessories || []).forEach(acc => {{
        const li = document.createElement('li');
        li.textContent = acc;
        specAccessories.appendChild(li);
      }});

      // Description & Preview
      editDescription.value = item.description_detailed;
      updatePreviewPane();
      updateCharCounter();
      copyDescBtn.innerHTML = '<span>Copy</span>';
      saveToast.className = 'hidden';

      // Thumbnails
      thumbsStrip.innerHTML = '';
      item.photos.forEach((ph, pIdx) => {{
        const t = document.createElement('div');
        const isSelected = pIdx === currentPhotoIdx;
        t.className = `w-14 h-14 sm:w-16 sm:h-16 shrink-0 rounded-lg overflow-hidden border-2 cursor-pointer transition-all hover:opacity-90 active:scale-95 ${{
          isSelected ? 'border-blue-500 ring-2 ring-blue-500/40 opacity-100' : 'border-[#1f2937] opacity-60 hover:opacity-100'
        }}`;
        t.innerHTML = `<img src="${{ph.data}}" alt="${{ph.filename}}" class="w-full h-full object-cover" />`;
        t.onclick = () => selectPhoto(pIdx);
        thumbsStrip.appendChild(t);
      }});

      updatePhotoView();
    }}

    function selectPhoto(pIdx) {{
      currentPhotoIdx = pIdx;
      currentRotation = 0;
      Array.from(thumbsStrip.children).forEach((child, idx) => {{
        if (idx === currentPhotoIdx) {{
          child.className = 'w-14 h-14 sm:w-16 sm:h-16 shrink-0 rounded-lg overflow-hidden border-2 cursor-pointer transition-all border-blue-500 ring-2 ring-blue-500/40 opacity-100';
        }} else {{
          child.className = 'w-14 h-14 sm:w-16 sm:h-16 shrink-0 rounded-lg overflow-hidden border-2 cursor-pointer transition-all border-[#1f2937] opacity-60 hover:opacity-100';
        }}
      }});
      updatePhotoView();
    }}

    function updatePhotoView() {{
      const item = inventory[currentItemIdx];
      const ph = item.photos[currentPhotoIdx];
      if (ph) {{
        mainPhoto.src = ph.data;
        mainPhoto.style.transform = `rotate(${{currentRotation}}deg)`;
        photoFilename.textContent = ph.filename;
        photoCounter.textContent = `${{currentPhotoIdx + 1}} of ${{item.photos.length}}`;
        
        if (!photoModal.classList.contains('hidden')) {{
          modalPhoto.src = ph.data;
          modalPhoto.style.transform = `rotate(${{currentRotation}}deg)`;
          modalFilename.textContent = ph.filename;
        }}
      }}
    }}

    function updateCharCounter() {{
      charCounter.textContent = `${{editDescription.value.length}} chars`;
    }}

    function updatePreviewPane() {{
      const rawText = editDescription.value || '';
      previewRendered.textContent = rawText;
    }}

    editDescription.addEventListener('input', () => {{
      updateCharCounter();
      updatePreviewPane();
    }});

    // Mode Toggle
    modeEditBtn.onclick = () => {{
      currentMode = 'edit';
      modeEditBtn.className = 'px-2.5 py-1 text-xs font-semibold rounded-md bg-blue-600 text-white transition-all';
      modePreviewBtn.className = 'px-2.5 py-1 text-xs font-medium text-slate-400 hover:text-white rounded-md transition-all';
      editPane.classList.remove('hidden');
      previewPane.classList.add('hidden');
    }};

    modePreviewBtn.onclick = () => {{
      currentMode = 'preview';
      modePreviewBtn.className = 'px-2.5 py-1 text-xs font-semibold rounded-md bg-blue-600 text-white transition-all';
      modeEditBtn.className = 'px-2.5 py-1 text-xs font-medium text-slate-400 hover:text-white rounded-md transition-all';
      updatePreviewPane();
      editPane.classList.add('hidden');
      previewPane.classList.remove('hidden');
    }};

    // Save action
    async function saveCurrentItem() {{
      const item = inventory[currentItemIdx];
      item.title = editTitle.value.trim();
      item.comp_price = parseFloat(editCompPrice.value) || item.comp_price;
      item.condition = editCondition.value;
      item.condition_notes = editNotes.value.trim();
      item.description_detailed = editDescription.value.trim();

      saveBtn.disabled = true;
      saveBtn.innerHTML = '<span>Saving...</span>';
      mobileSaveBtn.disabled = true;
      mobileSaveBtn.innerHTML = '<span>Saving...</span>';

      if (isLiveServer) {{
        try {{
          const res = await fetch(`${{serverBaseUrl}}/api/save-listing`, {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{
              item_id: item.id,
              title: item.title,
              comp_price: item.comp_price,
              condition: item.condition,
              condition_notes: item.condition_notes,
              description_detailed: item.description_detailed
            }})
          }});

          const data = await res.json();
          if (res.ok && data.status === 'ok') {{
            saveToast.className = 'px-2.5 py-1 rounded text-xs font-medium text-emerald-400 bg-emerald-500/10 border border-emerald-500/30';
            saveToast.textContent = '✓ Saved to disk & synced to LISTINGS_EDIT.md!';
            mobileSaveBtn.innerHTML = '<span>✓ Saved</span>';
            setTimeout(() => {{ 
              mobileSaveBtn.innerHTML = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7H5a2 2 0 00-2 2v9a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-3m-1 4l-3 3m0 0l-3-3m3 3V4"></path></svg><span>Save</span>'; 
            }}, 2000);
          }} else {{
            throw new Error(data.message || 'Server returned error');
          }}
        }} catch (err) {{
          saveToast.className = 'px-2.5 py-1 rounded text-xs font-medium text-rose-400 bg-rose-500/10 border border-rose-500/30';
          saveToast.textContent = `Save failed: ${{err.message}}`;
        }}
      }} else {{
        saveToast.className = 'px-2.5 py-1 rounded text-xs font-medium text-blue-400 bg-blue-500/10 border border-blue-500/30';
        saveToast.textContent = 'Saved in memory. (Start editor server to persist changes to disk)';
      }}

      saveBtn.disabled = false;
      saveBtn.innerHTML = '<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7H5a2 2 0 00-2 2v9a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-3m-1 4l-3 3m0 0l-3-3m3 3V4"></path></svg><span>Save</span>';
      mobileSaveBtn.disabled = false;
      renderTabs();
    }}

    saveBtn.onclick = saveCurrentItem;
    mobileSaveBtn.onclick = saveCurrentItem;

    document.getElementById('rotate-left-btn').onclick = () => {{
      currentRotation = (currentRotation - 90) % 360;
      mainPhoto.style.transform = `rotate(${{currentRotation}}deg)`;
    }};

    document.getElementById('rotate-right-btn').onclick = () => {{
      currentRotation = (currentRotation + 90) % 360;
      mainPhoto.style.transform = `rotate(${{currentRotation}}deg)`;
    }};

    document.getElementById('prev-btn').onclick = () => {{
      const item = inventory[currentItemIdx];
      currentPhotoIdx = (currentPhotoIdx - 1 + item.photos.length) % item.photos.length;
      selectPhoto(currentPhotoIdx);
    }};

    document.getElementById('next-btn').onclick = () => {{
      const item = inventory[currentItemIdx];
      currentPhotoIdx = (currentPhotoIdx + 1) % item.photos.length;
      selectPhoto(currentPhotoIdx);
    }};

    // Full screen modal interaction
    mainPhoto.onclick = () => {{
      const item = inventory[currentItemIdx];
      const ph = item.photos[currentPhotoIdx];
      if (ph) {{
        modalPhoto.src = ph.data;
        modalPhoto.style.transform = `rotate(${{currentRotation}}deg)`;
        modalFilename.textContent = ph.filename;
        photoModal.classList.remove('hidden');
      }}
    }};

    closeModalBtn.onclick = () => {{
      photoModal.classList.add('hidden');
    }};

    modalPrevBtn.onclick = () => {{
      document.getElementById('prev-btn').click();
    }};

    modalNextBtn.onclick = () => {{
      document.getElementById('next-btn').click();
    }};

    function copyDescription() {{
      navigator.clipboard.writeText(editDescription.value).then(() => {{
        copyDescBtn.innerHTML = '<span class="text-emerald-400 font-bold">Copied!</span>';
        mobileCopyBtn.textContent = 'Copied!';
        setTimeout(() => {{ 
          copyDescBtn.innerHTML = '<svg class="w-3 h-3 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"></path></svg><span>Copy</span>'; 
          mobileCopyBtn.textContent = 'Copy';
        }}, 2000);
      }});
    }}

    copyDescBtn.onclick = copyDescription;
    mobileCopyBtn.onclick = copyDescription;

    window.addEventListener('keydown', (e) => {{
      if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) {{
        e.preventDefault();
        saveCurrentItem();
      }}
      if (e.key === 'Escape' && !photoModal.classList.contains('hidden')) {{
        photoModal.classList.add('hidden');
      }}
      const activeTag = document.activeElement ? document.activeElement.tagName.toLowerCase() : '';
      if (activeTag !== 'input' && activeTag !== 'textarea') {{
        if (e.key === 'ArrowLeft') document.getElementById('prev-btn').click();
        if (e.key === 'ArrowRight') document.getElementById('next-btn').click();
        if (e.key === 'r' || e.key === 'R') document.getElementById('rotate-right-btn').click();
      }}
    }});

    renderTabs();
    updateItemView();
  </script>
</body>
</html>
"""

    for out_path in get_output_paths():
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html_template, encoding="utf-8")
        print(f"[OK] Gallery written to {out_path} ({len(html_template)} bytes)")


if __name__ == "__main__":
    build_gallery()
