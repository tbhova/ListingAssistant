"""
editor_server.py - Zero-Dependency Local HTTP Server for WYSIWYG Listing Editing
Supports desktop and mobile phone editing over local Wi-Fi.

Run:
    .venv\\Scripts\\python.exe scripts/editor_server.py
"""

from __future__ import annotations

import http.server
import json
import os
import socket
import socketserver
import subprocess
import sys
from pathlib import Path
from typing import Optional
from PIL import Image

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
ITEMS_DIR = WORKSPACE_ROOT / "items"
SCRIPTS_DIR = WORKSPACE_ROOT / "scripts"
PORT = 8000


def get_local_lan_ip() -> str:
    """Detect the local Wi-Fi/Ethernet IP address of this machine."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Doesn't need to be reachable, triggers OS socket route table lookup
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except Exception:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def get_gallery_html_path() -> Optional[Path]:
    """Find batch_gallery.html dynamically without hardcoded personal usernames."""
    env_path = os.getenv("GALLERY_HTML_PATH")
    if env_path and Path(env_path).exists():
        return Path(env_path)

    local_candidates = [
        ITEMS_DIR / "batch_gallery.html",
        WORKSPACE_ROOT / "batch_gallery.html",
    ]
    for c in local_candidates:
        if c.exists():
            return c

    home = Path.home()
    brain_base = home / ".gemini" / "antigravity" / "brain"
    if brain_base.exists():
        matches = list(brain_base.rglob("batch_gallery.html"))
        if matches:
            matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return matches[0]

    return None


class ListingEditorHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WORKSPACE_ROOT), **kwargs)

    def do_GET(self):
        gallery_file = get_gallery_html_path()

        if self.path in ("/", "/index.html", "/gallery"):
            if gallery_file and gallery_file.exists():
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(gallery_file.read_bytes())
                return

        if self.path.startswith("/batch_gallery.html"):
            if gallery_file and gallery_file.exists():
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(gallery_file.read_bytes())
                return

        if self.path == "/api/status":
            lan_ip = get_local_lan_ip()
            port = self.server.server_address[1]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "ok",
                "mode": "live_server",
                "lan_ip": lan_ip,
                "port": port,
                "phone_url": f"http://{lan_ip}:{port}"
            }).encode("utf-8"))
            return

        super().do_GET()

    def do_POST(self):
        if self.path == "/api/save-listing":
            self._handle_save_listing()
            return
        elif self.path == "/api/save-rotation":
            self._handle_save_rotation()
            return

        self.send_response(404)
        self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _handle_save_listing(self):
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_len)
            payload = json.loads(post_data.decode("utf-8"))

            item_id = payload.get("item_id")
            if not item_id:
                raise ValueError("Missing item_id in payload")

            v_file = ITEMS_DIR / item_id / "vision_input.json"
            if not v_file.exists():
                raise FileNotFoundError(f"Cannot find {v_file}")

            v_data = json.loads(v_file.read_text(encoding="utf-8"))

            # Update fields
            if "title" in payload:
                v_data["title"] = str(payload["title"]).strip()
            if "comp_price" in payload:
                v_data["comp_price"] = float(payload["comp_price"])
            if "condition" in payload:
                v_data["condition"] = str(payload["condition"]).strip()
            if "condition_notes" in payload:
                v_data["condition_notes"] = str(payload["condition_notes"]).strip()
            if "description_detailed" in payload:
                v_data["description_detailed"] = str(payload["description_detailed"]).strip()
            if "accessories" in payload and isinstance(payload["accessories"], list):
                v_data["accessories"] = payload["accessories"]

            v_file.write_text(json.dumps(v_data, indent=2), encoding="utf-8")
            print(f"[SERVER] Updated {v_file.name} for {item_id}")

            # Re-calculate economics & refresh manifest
            calc_script = SCRIPTS_DIR / "analyze_items.py"
            if calc_script.exists():
                subprocess.run([sys.executable, str(calc_script), "--calculate"], check=True)

            # Export to LISTINGS_EDIT.md
            sync_script = SCRIPTS_DIR / "sync_listings.py"
            if sync_script.exists():
                subprocess.run([sys.executable, str(sync_script), "--export"], check=True)

            # Rebuild gallery HTML
            builder_script = SCRIPTS_DIR / "build_gallery.py"
            if builder_script.exists():
                subprocess.run([sys.executable, str(builder_script)], check=True)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "ok",
                "message": f"Successfully saved {item_id} to disk and synced manifest & markdown!"
            }).encode("utf-8"))

        except Exception as exc:
            print(f"[SERVER ERROR] Failed to save listing: {exc}")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "error", "message": str(exc)}).encode("utf-8"))

    def _handle_save_rotation(self):
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_len)
            payload = json.loads(post_data.decode("utf-8"))

            item_id = payload.get("item_id")
            filename = payload.get("filename")
            angle = int(payload.get("angle", 0))

            if not item_id or not filename or angle == 0:
                raise ValueError("Invalid parameters for rotation")

            target_file = ITEMS_DIR / item_id / filename
            if not target_file.exists():
                raise FileNotFoundError(f"File not found: {target_file}")

            with Image.open(target_file) as img:
                rotated = img.rotate(360 - angle, expand=True)
                rotated.save(target_file, quality=95, optimize=True)

            print(f"[SERVER] Permanently rotated {target_file.name} by {angle} deg clockwise on disk")

            builder_script = SCRIPTS_DIR / "build_gallery.py"
            if builder_script.exists():
                subprocess.run([sys.executable, str(builder_script)], check=True)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "ok",
                "message": f"Rotated and saved {filename} to disk!"
            }).encode("utf-8"))

        except Exception as exc:
            print(f"[SERVER ERROR] Failed to rotate image: {exc}")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "error", "message": str(exc)}).encode("utf-8"))


def run_server():
    port = PORT
    lan_ip = get_local_lan_ip()
    gallery_file = get_gallery_html_path()

    for attempt in range(5):
        try:
            # Bind to 0.0.0.0 (all interfaces) so phone on same Wi-Fi can connect
            with socketserver.TCPServer(("", port), ListingEditorHandler) as httpd:
                print("=" * 68, flush=True)
                print("  RESALE LISTING WYSIWYG EDITOR SERVER ACTIVE", flush=True)
                print(f"  PC Browser:    http://localhost:{port}", flush=True)
                print(f"  Phone / Wi-Fi: http://{lan_ip}:{port}", flush=True)
                print(f"  Gallery file:  {gallery_file}", flush=True)
                print("  Press Ctrl+C in terminal to stop.", flush=True)
                print("=" * 68, flush=True)
                httpd.serve_forever()
        except OSError:
            print(f"[WARN] Port {port} is in use, trying {port + 1}...")
            port += 1


if __name__ == "__main__":
    run_server()
