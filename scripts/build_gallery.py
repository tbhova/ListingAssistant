"""
build_gallery.py - Generate Interactive Resale Batch Inspector & WYSIWYG Editor HTML

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
    
    # Check env var override
    env_path = os.getenv("GALLERY_HTML_PATH")
    if env_path:
        paths.append(Path(env_path))

    # Dynamically find current conversation artifact directory if active
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
        
        # Process photos with EXIF transposition and thumbnail generation
        photo_files = sorted(list(folder.glob("*.jpg")) + list(folder.glob("*.png"))) if folder.exists() else []
        b64_photos = []
        
        for pf in photo_files:
            try:
                with Image.open(pf) as raw_img:
                    img = ImageOps.exif_transpose(raw_img)
                    img.thumbnail((640, 640), Image.Resampling.LANCZOS)
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=62, optimize=True)
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
            badge_style = "bg-emerald-500/15 text-emerald-500 border-emerald-500/30"
        elif route == "EBAY_ONLY":
            badge_label = "eBay Only"
            badge_style = "bg-blue-500/15 text-blue-500 border-blue-500/30"
        elif route == "LOCAL_ONLY":
            badge_label = "Local Only (FBMP + CL)"
            badge_style = "bg-amber-500/15 text-amber-500 border-amber-500/30"
        else:
            badge_label = "Donate / Lot"
            badge_style = "bg-purple-500/15 text-purple-500 border-purple-500/30"

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
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Resale Batch Master Inspector & WYSIWYG Editor</title>
  <script src="https://www.gstatic.com/antigravity/web/dev/tailwindcss.min.js"></script>
</head>
<body class="bg-transparent text-[var(--foreground)] antialiased p-3 font-sans">
  <div class="bg-[var(--card)] border border-[var(--border)] rounded-2xl p-4 sm:p-5 shadow-lg max-w-6xl mx-auto flex flex-col gap-4">
    
    <!-- Top Header -->
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-[var(--border)]">
      <div>
        <div class="flex items-center gap-2 flex-wrap">
          <h2 class="text-xl font-bold text-[var(--foreground)] tracking-tight">Resale Inventory Inspector & WYSIWYG Editor</h2>
          <span class="text-xs font-semibold bg-emerald-500/15 text-emerald-500 px-2.5 py-0.5 rounded-full border border-emerald-500/25">{len(processed_inventory)} Items Active</span>
          <span class="text-xs font-semibold bg-blue-500/15 text-blue-500 px-2.5 py-0.5 rounded-full border border-blue-500/25">{sum(len(x['photos']) for x in processed_inventory)} Photos</span>
        </div>
        <p class="text-xs text-[var(--muted-foreground)] mt-1">Live visual editor: modify titles, prices, conditions, and copy directly next to photos.</p>
      </div>
      
      <!-- Connection Status & Shortcuts -->
      <div class="flex items-center gap-2 text-xs self-start sm:self-auto flex-wrap">
        <div id="server-status-pill" class="flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-medium transition-colors">
          <span id="server-dot" class="inline-block w-2 h-2 rounded-full bg-slate-400"></span>
          <span id="server-status-text">Checking server...</span>
        </div>
        <div class="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-[var(--background)] border border-[var(--border)] text-[var(--muted-foreground)] text-xs font-mono">
          <kbd class="px-1.5 py-0.5 rounded bg-[var(--card)] border border-[var(--border)] text-[10px]">Ctrl+S</kbd> Save
        </div>
      </div>
    </div>

    <!-- Item Selector Tabs -->
    <div class="flex gap-1.5 overflow-x-auto pb-2 scrollbar-thin" id="item-tabs"></div>

    <!-- Active Item Header Card with Inline Editable Title & Price -->
    <div class="bg-[var(--background)]/60 border border-[var(--border)] rounded-xl p-4 flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4">
      <div class="flex-1 w-full">
        <div class="flex items-center gap-2 flex-wrap mb-1.5">
          <span id="item-num-badge" class="text-xs font-bold px-2 py-0.5 rounded bg-[var(--primary)] text-[var(--primary-foreground)] font-mono">Item 01</span>
          <span id="item-sku" class="text-xs font-mono text-[var(--muted-foreground)]">ITEM-20261110-01</span>
          <span id="item-route-badge" class="text-xs px-2.5 py-0.5 rounded-full border font-semibold"></span>
        </div>
        
        <!-- Editable Title Field -->
        <div class="relative w-full">
          <input id="edit-title" type="text" class="w-full text-base font-bold text-[var(--foreground)] bg-transparent border-b border-transparent hover:border-[var(--border)] focus:border-[var(--primary)] focus:bg-[var(--card)] focus:ring-1 focus:ring-[var(--primary)] rounded px-1.5 py-1 transition-all outline-none" placeholder="Item Title..." />
        </div>
        <p id="item-route-reason" class="text-xs text-[var(--muted-foreground)] mt-1 px-1.5 italic"></p>
      </div>
      
      <!-- Economic Quick Stats -->
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-2.5 w-full lg:w-auto shrink-0 border-t lg:border-t-0 pt-3 lg:pt-0 border-[var(--border)]">
        <div class="bg-[var(--card)] border border-[var(--border)] p-2 rounded-lg text-center">
          <div class="text-[10px] uppercase tracking-wider text-[var(--muted-foreground)]">Target Comp ($)</div>
          <input id="edit-comp-price" type="number" step="0.5" class="w-20 text-sm font-bold text-center text-[var(--foreground)] bg-transparent border-b border-transparent hover:border-[var(--border)] focus:border-[var(--primary)] outline-none" />
        </div>
        <div class="bg-[var(--card)] border border-[var(--border)] p-2 rounded-lg text-center">
          <div class="text-[10px] uppercase tracking-wider text-[var(--muted-foreground)]">FBMP Ask</div>
          <div id="stat-fbmp" class="text-sm font-bold text-amber-500"></div>
        </div>
        <div class="bg-[var(--card)] border border-[var(--border)] p-2 rounded-lg text-center">
          <div class="text-[10px] uppercase tracking-wider text-[var(--muted-foreground)]">Zone 8 Ship</div>
          <div id="stat-ship" class="text-sm font-medium text-[var(--muted-foreground)]"></div>
        </div>
        <div class="bg-[var(--card)] border border-[var(--border)] p-2 rounded-lg text-center">
          <div class="text-[10px] uppercase tracking-wider text-[var(--muted-foreground)]">Est. Net Profit</div>
          <div id="stat-net" class="text-sm font-bold text-emerald-500"></div>
        </div>
      </div>
    </div>

    <!-- Main Workspace: Photo Stage + WYSIWYG Inspector Sidebar -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-4 items-start">
      
      <!-- Photo Stage (Left 7 Cols) -->
      <div class="lg:col-span-7 flex flex-col gap-2">
        <div class="relative bg-black/90 rounded-xl overflow-hidden border border-[var(--border)] flex items-center justify-center min-h-[380px] max-h-[500px] group shadow-inner">
          <img id="main-photo" src="" alt="Main Photo" class="w-full h-auto max-h-[500px] object-contain transition-transform duration-200" />
          
          <!-- Prev / Next Overlays -->
          <button id="prev-btn" class="absolute left-2.5 top-1/2 -translate-y-1/2 bg-black/60 hover:bg-black/90 text-white rounded-full p-2.5 border border-white/20 transition-all opacity-85 hover:opacity-100">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"></path></svg>
          </button>
          <button id="next-btn" class="absolute right-2.5 top-1/2 -translate-y-1/2 bg-black/60 hover:bg-black/90 text-white rounded-full p-2.5 border border-white/20 transition-all opacity-85 hover:opacity-100">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"></path></svg>
          </button>

          <!-- Top-right Quick Rotate Controls -->
          <div class="absolute top-2.5 right-2.5 flex gap-1.5 opacity-85 hover:opacity-100 transition-opacity">
            <button id="rotate-left-btn" title="Rotate Left 90°" class="bg-black/75 hover:bg-black text-white px-2 py-1 rounded-lg border border-white/20 text-xs flex items-center gap-1 backdrop-blur shadow">
              <span>↺</span>
              <span class="text-[11px] font-mono">90°</span>
            </button>
            <button id="rotate-right-btn" title="Rotate Right 90° (Shortcut: R)" class="bg-black/75 hover:bg-black text-white px-2 py-1 rounded-lg border border-white/20 text-xs flex items-center gap-1 backdrop-blur shadow">
              <span class="text-[11px] font-mono">90°</span>
              <span>↻</span>
            </button>
          </div>
          
          <!-- Bottom filename badge -->
          <div class="absolute bottom-2.5 left-2.5 right-2.5 flex justify-between items-center px-3 py-1.5 bg-black/80 backdrop-blur rounded-lg text-white text-xs">
            <span id="photo-filename" class="truncate font-mono opacity-90">photo.jpg</span>
            <span id="photo-counter" class="shrink-0 font-medium ml-2 font-mono">1 of 1</span>
          </div>
        </div>

        <!-- Thumbnail Strip below hero photo -->
        <div class="flex gap-2 overflow-x-auto p-1.5 border border-[var(--border)] rounded-xl bg-[var(--background)]/40 scrollbar-thin" id="thumbs-strip"></div>
      </div>

      <!-- Right 5 Cols: Spec & WYSIWYG Editor Panel -->
      <div class="lg:col-span-5 flex flex-col gap-3">
        
        <!-- Specs & Condition Card (Editable Condition & Notes) -->
        <div class="border border-[var(--border)] rounded-xl p-3.5 bg-[var(--card)] flex flex-col gap-2.5 text-xs">
          <div class="font-bold text-sm text-[var(--foreground)] border-b border-[var(--border)] pb-1.5 flex justify-between items-center">
            <span>Item Specifics</span>
            <span id="spec-category" class="text-[11px] font-normal text-[var(--muted-foreground)] truncate max-w-[180px]"></span>
          </div>

          <div class="grid grid-cols-2 gap-2 pt-1">
            <div>
              <span class="text-[var(--muted-foreground)] block mb-0.5">Brand / Model:</span>
              <strong id="spec-brand-model" class="text-[var(--foreground)]"></strong>
            </div>
            <div>
              <span class="text-[var(--muted-foreground)] block mb-0.5">Condition:</span>
              <select id="edit-condition" class="w-full bg-[var(--background)] text-[var(--foreground)] border border-[var(--border)] rounded px-1.5 py-1 text-xs font-semibold outline-none focus:border-[var(--primary)]">
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

          <div class="pt-1.5 border-t border-[var(--border)]">
            <span class="text-[var(--muted-foreground)] block mb-1">Condition Notes:</span>
            <input id="edit-notes" type="text" class="w-full bg-[var(--background)] text-[var(--foreground)] border border-[var(--border)] rounded px-2 py-1 text-xs outline-none focus:border-[var(--primary)]" />
          </div>

          <div class="pt-1.5 border-t border-[var(--border)]">
            <span class="text-[var(--muted-foreground)] block mb-0.5">Included Accessories:</span>
            <ul id="spec-accessories" class="list-disc list-inside text-[var(--foreground)] space-y-0.5"></ul>
          </div>
        </div>

        <!-- Description & WYSIWYG Editor Card -->
        <div class="border border-[var(--border)] rounded-xl p-3.5 bg-[var(--card)] flex flex-col gap-2 text-xs">
          <div class="font-bold text-sm text-[var(--foreground)] border-b border-[var(--border)] pb-2 flex justify-between items-center">
            <span>Listing Copy & Description</span>
            <div class="flex items-center gap-1.5">
              <button id="copy-desc-btn" class="text-[11px] px-2.5 py-1 rounded border border-[var(--border)] bg-[var(--background)] hover:bg-[var(--card)] transition-colors">
                Copy
              </button>
              <button id="save-btn" class="text-[11px] font-semibold px-3 py-1 rounded bg-[var(--primary)] text-[var(--primary-foreground)] hover:opacity-90 transition-opacity flex items-center gap-1 shadow-sm">
                <span>💾 Save</span>
              </button>
            </div>
          </div>

          <!-- Toast notification -->
          <div id="save-toast" class="hidden px-2.5 py-1 rounded text-xs font-medium text-emerald-500 bg-emerald-500/10 border border-emerald-500/25 transition-all"></div>

          <!-- Editable Description Textarea -->
          <textarea id="edit-description" rows="8" class="w-full bg-[var(--background)]/70 border border-[var(--border)] rounded-lg p-2.5 font-sans leading-relaxed text-[var(--foreground)] text-xs resize-y outline-none focus:border-[var(--primary)] focus:ring-1 focus:ring-[var(--primary)] transition-all" placeholder="Enter listing description..."></textarea>
          
          <div class="text-[10px] text-[var(--muted-foreground)] flex justify-between items-center pt-1">
            <span>Tip: Press <kbd class="px-1 rounded bg-[var(--background)] border border-[var(--border)]">Ctrl+S</kbd> to save.</span>
            <span id="char-counter">0 chars</span>
          </div>
        </div>

      </div>

    </div>

  </div>

  <script>
    const inventory = {json_str};
    let currentItemIdx = 10; // Aeron + Atlas bundle by default
    let currentPhotoIdx = 0;
    let currentRotation = 0;
    let isLiveServer = false;
    let serverBaseUrl = '';

    // Check server status
    const statusPill = document.getElementById('server-status-pill');
    const statusDot = document.getElementById('server-dot');
    const statusText = document.getElementById('server-status-text');

    async function checkServer() {{
      const candidateUrls = [window.location.origin, 'http://localhost:8000', 'http://127.0.0.1:8000'];
      for (const url of candidateUrls) {{
        try {{
          const res = await fetch(`${{url}}/api/status`, {{ method: 'GET', signal: AbortSignal.timeout(1000) }});
          if (res.ok) {{
            const data = await res.json();
            if (data.status === 'ok') {{
              isLiveServer = true;
              serverBaseUrl = url;
              statusPill.className = 'flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-medium bg-emerald-500/10 border-emerald-500/30 text-emerald-500';
              statusDot.className = 'inline-block w-2 h-2 rounded-full bg-emerald-500 animate-pulse';
              statusText.textContent = 'Live Server Connected';
              return;
            }}
          }}
        }} catch (e) {{}}
      }}
      // Offline fallback
      statusPill.className = 'flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-medium bg-blue-500/10 border-blue-500/30 text-blue-500';
      statusDot.className = 'inline-block w-2 h-2 rounded-full bg-blue-500';
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
    const editDescription = document.getElementById('edit-description');
    const charCounter = document.getElementById('char-counter');
    const copyDescBtn = document.getElementById('copy-desc-btn');
    const saveBtn = document.getElementById('save-btn');
    const saveToast = document.getElementById('save-toast');

    function renderTabs() {{
      tabsContainer.innerHTML = '';
      inventory.forEach((item, idx) => {{
        const btn = document.createElement('button');
        const isActive = idx === currentItemIdx;
        btn.className = `px-2.5 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap transition-all border ${{
          isActive 
            ? 'bg-[var(--primary)] text-[var(--primary-foreground)] border-[var(--primary)] shadow-sm' 
            : 'bg-[var(--card)] hover:bg-[var(--background)] text-[var(--foreground)] border-[var(--border)]'
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

      // Description
      editDescription.value = item.description_detailed;
      updateCharCounter();
      copyDescBtn.textContent = 'Copy';
      saveToast.className = 'hidden';

      // Thumbnails
      thumbsStrip.innerHTML = '';
      item.photos.forEach((ph, pIdx) => {{
        const t = document.createElement('div');
        const isSelected = pIdx === currentPhotoIdx;
        t.className = `w-16 h-16 shrink-0 rounded-lg overflow-hidden border-2 cursor-pointer transition-all hover:opacity-90 ${{
          isSelected ? 'border-[var(--primary)] ring-2 ring-[var(--primary)]/30' : 'border-[var(--border)] opacity-70'
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
          child.className = 'w-16 h-16 shrink-0 rounded-lg overflow-hidden border-2 cursor-pointer transition-all hover:opacity-90 border-[var(--primary)] ring-2 ring-[var(--primary)]/30';
        }} else {{
          child.className = 'w-16 h-16 shrink-0 rounded-lg overflow-hidden border-2 cursor-pointer transition-all hover:opacity-90 border-[var(--border)] opacity-70';
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
      }}
    }}

    function updateCharCounter() {{
      charCounter.textContent = `${{editDescription.value.length}} chars`;
    }}

    editDescription.addEventListener('input', updateCharCounter);

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
            saveToast.className = 'px-2.5 py-1 rounded text-xs font-medium text-emerald-500 bg-emerald-500/10 border border-emerald-500/25';
            saveToast.textContent = '✓ Saved to disk & synced to LISTINGS_EDIT.md!';
          }} else {{
            throw new Error(data.message || 'Server returned error');
          }}
        }} catch (err) {{
          saveToast.className = 'px-2.5 py-1 rounded text-xs font-medium text-rose-500 bg-rose-500/10 border border-rose-500/25';
          saveToast.textContent = `Save failed: ${{err.message}}`;
        }}
      }} else {{
        // Offline mode
        saveToast.className = 'px-2.5 py-1 rounded text-xs font-medium text-blue-500 bg-blue-500/10 border border-blue-500/25';
        saveToast.textContent = 'Saved in memory. (Run .venv/Scripts/python.exe scripts/editor_server.py to auto-save to disk, or edit items/LISTINGS_EDIT.md)';
      }}

      saveBtn.disabled = false;
      saveBtn.innerHTML = '<span>💾 Save</span>';
      renderTabs();
    }}

    saveBtn.onclick = saveCurrentItem;

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

    copyDescBtn.onclick = () => {{
      navigator.clipboard.writeText(editDescription.value).then(() => {{
        copyDescBtn.textContent = 'Copied!';
        setTimeout(() => {{ copyDescBtn.textContent = 'Copy'; }}, 2000);
      }});
    }};

    window.addEventListener('keydown', (e) => {{
      if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) {{
        e.preventDefault();
        saveCurrentItem();
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
