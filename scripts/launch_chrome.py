#!/usr/bin/env python3
"""
Helper: Dedicated Chrome Automation Profile Launcher (scripts/launch_chrome.py)

Launches Google Chrome with `--user-data-dir=./chrome_profile` and
`--remote-debugging-port=9222` so Playwright can connect via CDP (`http://localhost:9222`)
without conflicting with your main open Chrome browser.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


CANDIDATE_CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]


def find_chrome_executable() -> Optional[str]:
    """Locate Google Chrome executable on Windows."""
    custom_bin = os.getenv("CHROME_EXECUTABLE_PATH", "").strip()
    if custom_bin and Path(custom_bin).exists():
        return custom_bin
    for candidate in CANDIDATE_CHROME_PATHS:
        if Path(candidate).exists():
            return candidate
    return None


def is_cdp_port_open(port: int = 9222) -> bool:
    """Check if Chrome DevTools Protocol is already listening on localhost:<port>."""
    url = f"http://localhost:{port}/json/version"
    try:
        with urllib.request.urlopen(url, timeout=2.0) as resp:
            return resp.status == 200
    except Exception:
        return False


def launch_dedicated_chrome(port: int, profile_dir: Path, open_login_tabs: bool = False) -> int:
    """Launch Chrome with dedicated user-data-dir and remote debugging port."""
    if is_cdp_port_open(port):
        print(f"[OK] Chrome CDP is already listening on http://localhost:{port}")
        return 0

    chrome_exe = find_chrome_executable()
    if not chrome_exe:
        print(
            "[ERROR] Could not find Google Chrome at standard Windows paths. "
            "Set CHROME_EXECUTABLE_PATH in .env."
        )
        return 1

    profile_dir = profile_dir.resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        chrome_exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
    ]

    if open_login_tabs:
        cmd.extend(
            [
                "https://www.ebay.com/sh/ovw",
                "https://www.facebook.com/marketplace/create/item",
                "https://accounts.craigslist.org/login/home",
            ]
        )

    print(f"[INFO] Launching dedicated Chrome profile at '{profile_dir.as_posix()}' on CDP port {port}...")
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0,
    )

    for _ in range(15):
        time.sleep(0.5)
        if is_cdp_port_open(port):
            print(f"[OK] Chrome CDP is ready on http://localhost:{port}")
            return 0

    print(f"[WARN] Chrome launched, but port {port} did not respond within 7.5 seconds.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Launch or verify dedicated Chrome automation profile on CDP port 9222."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("CHROME_CDP_PORT", "9222")),
        help="Remote debugging port (default: 9222)",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=Path(os.getenv("CHROME_PROFILE_DIR", "./chrome_profile")),
        help="Path to dedicated Chrome user-data-dir (default: ./chrome_profile)",
    )
    parser.add_argument(
        "--check-or-launch",
        action="store_true",
        help="Check if port 9222 is active, and launch Chrome if not",
    )
    parser.add_argument(
        "--login-setup",
        action="store_true",
        help="Open eBay, Facebook Marketplace, and Craigslist login tabs for one-time session setup",
    )

    args = parser.parse_args()
    return launch_dedicated_chrome(
        port=args.port,
        profile_dir=args.profile_dir,
        open_login_tabs=args.login_setup,
    )


if __name__ == "__main__":
    sys.exit(main())
