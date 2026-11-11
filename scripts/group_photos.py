#!/usr/bin/env python3
"""
Stage 1: Photo Slicing & Visual Audit (scripts/group_photos.py)

Scans /raw_photos/, extracts EXIF DateTimeOriginal timestamps (supporting JPEG, PNG,
WebP, and iPhone HEIC/HEIF via pillow-heif), groups sequential photos using a
configurable time-gap threshold (default 45 seconds), stages them into /items/item_XX/,
and flags 1-2 photo outlier folders so the vision model can verify if they belong
to an earlier item group.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from PIL import Image, ExifTags
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass
except ImportError:
    Image = None  # type: ignore

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}

# Standard EXIF tag IDs for capture timestamps
EXIF_DATETIME_ORIGINAL = 36867
EXIF_DATETIME_DIGITIZED = 36868
EXIF_DATETIME = 306


def parse_exif_datetime(raw_val: Any) -> Optional[datetime]:
    """Parse common EXIF datetime strings into a Python datetime object."""
    if not raw_val:
        return None
    if isinstance(raw_val, bytes):
        raw_val = raw_val.decode("utf-8", errors="ignore")
    text = str(raw_val).strip().rstrip("\x00")
    # Common formats: "YYYY:MM:DD HH:MM:SS" or ISO-like "YYYY-MM-DD HH:MM:SS"
    for fmt in (
        "%Y:%m:%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y:%m:%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def extract_photo_timestamp(photo_path: Path) -> Tuple[datetime, str]:
    """
    Extract capture timestamp from a photo.
    Returns (datetime_obj, timestamp_source) where source is 'EXIF:DateTimeOriginal',
    'EXIF:DateTimeDigitized', 'EXIF:DateTime', or 'Filesystem:mtime'.
    """
    if Image is not None:
        try:
            with Image.open(photo_path) as img:
                exif = img.getexif()
                if exif:
                    # Check IFD Exif sub-dictionary first (where DateTimeOriginal often lives in modern Pillow)
                    exif_ifd = {}
                    try:
                        exif_ifd = exif.get_ifd(0x8769) or {}
                    except Exception:
                        pass

                    for tag_id, label in (
                        (EXIF_DATETIME_ORIGINAL, "EXIF:DateTimeOriginal"),
                        (EXIF_DATETIME_DIGITIZED, "EXIF:DateTimeDigitized"),
                        (EXIF_DATETIME, "EXIF:DateTime"),
                    ):
                        val = exif_ifd.get(tag_id) or exif.get(tag_id)
                        dt = parse_exif_datetime(val)
                        if dt is not None:
                            return dt, label

                # Fallback for older Pillow _getexif() on JPEGs
                if hasattr(img, "_getexif") and callable(img._getexif):
                    legacy_exif = img._getexif() or {}
                    for tag_id, label in (
                        (EXIF_DATETIME_ORIGINAL, "EXIF:DateTimeOriginal"),
                        (EXIF_DATETIME_DIGITIZED, "EXIF:DateTimeDigitized"),
                        (EXIF_DATETIME, "EXIF:DateTime"),
                    ):
                        dt = parse_exif_datetime(legacy_exif.get(tag_id))
                        if dt is not None:
                            return dt, label
        except Exception:
            pass

    # Fallback to file modification time if EXIF is stripped
    stat = photo_path.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime)
    return mtime, "Filesystem:mtime"


def build_grouping_report(
    items_dir: Path,
    threshold_seconds: float,
    outlier_max_photos: int,
    photo_metadata_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Inspect all /items/item_XX/ folders and build /items/grouping_report.json,
    including the secondary outlier detection pass for 1-2 photo folders.
    """
    item_dirs = sorted(
        [d for d in items_dir.iterdir() if d.is_dir() and d.name.startswith("item_")]
    )

    groups_report: List[Dict[str, Any]] = []
    outlier_Reviews: List[Dict[str, Any]] = []

    for idx, folder in enumerate(item_dirs):
        photos = sorted(
            [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS]
        )
        if not photos:
            continue

        photo_records = []
        for p in photos:
            meta = (photo_metadata_map or {}).get(p.name)
            if meta:
                dt_str = meta["timestamp"]
                source = meta["source"]
                gap_sec = meta.get("gap_from_prev_seconds")
            else:
                dt, source = extract_photo_timestamp(p)
                dt_str = dt.isoformat()
                gap_sec = None

            photo_records.append(
                {
                    "filename": p.name,
                    "path": str(p.as_posix()),
                    "timestamp": dt_str,
                    "timestamp_source": source,
                    "gap_from_prev_seconds": gap_sec,
                }
            )

        is_outlier = len(photos) <= outlier_max_photos and idx > 0
        prev_folder = item_dirs[idx - 1] if idx > 0 else None
        prev_photos = (
            sorted([p for p in prev_folder.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS])
            if prev_folder
            else []
        )

        # Compute gap from the end of the previous folder
        gap_from_prev_group_sec: Optional[float] = None
        if prev_photos and photos:
            prev_dt, _ = extract_photo_timestamp(prev_photos[-1])
            curr_dt, _ = extract_photo_timestamp(photos[0])
            gap_from_prev_group_sec = round((curr_dt - prev_dt).total_seconds(), 2)

        group_entry: Dict[str, Any] = {
            "item_id": folder.name,
            "sku": f"SKU-{folder.name.upper().replace('_', '-')}",
            "folder_path": str(folder.as_posix()),
            "photo_count": len(photos),
            "start_timestamp": photo_records[0]["timestamp"],
            "end_timestamp": photo_records[-1]["timestamp"],
            "gap_from_previous_group_seconds": gap_from_prev_group_sec,
            "outlier_candidate": is_outlier,
            "photos": photo_records,
        }

        if is_outlier and prev_folder and prev_photos:
            comparison_info = {
                "outlier_item_id": folder.name,
                "previous_item_id": prev_folder.name,
                "photo_count": len(photos),
                "gap_seconds": gap_from_prev_group_sec,
                "previous_group_last_photo": str(prev_photos[-1].as_posix()),
                "outlier_photos": [str(p.as_posix()) for p in photos],
                "merge_command": f"python scripts/group_photos.py --merge {folder.name} {prev_folder.name}",
                "audit_prompt": (
                    f"Folder '{folder.name}' only has {len(photos)} photo(s), taken "
                    f"{gap_from_prev_group_sec}s after '{prev_folder.name}'. Inspect "
                    f"'{prev_photos[-1].as_posix()}' and '{photos[0].as_posix()}' to verify "
                    f"whether '{folder.name}' is a continuation shot (e.g., serial tag, accessory, "
                    f"or close-up) of '{prev_folder.name}'."
                ),
            }
            group_entry["outlier_audit"] = comparison_info
            outlier_Reviews.append(comparison_info)

        groups_report.append(group_entry)

    report = {
        "generated_at": datetime.now().isoformat(),
        "time_gap_threshold_seconds": threshold_seconds,
        "outlier_max_photos": outlier_max_photos,
        "total_items": len(groups_report),
        "total_photos": sum(g["photo_count"] for g in groups_report),
        "outlier_count": len(outlier_Reviews),
        "outlier_candidates": outlier_Reviews,
        "groups": groups_report,
    }

    report_path = items_dir / "grouping_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def reindex_item_folders(items_dir: Path) -> None:
    """Ensure item folders are sequentially numbered item_01, item_02, ... without gaps."""
    existing = sorted(
        [d for d in items_dir.iterdir() if d.is_dir() and d.name.startswith("item_")]
    )
    # First move to temporary names to avoid collisions
    temp_dirs: List[Path] = []
    for idx, folder in enumerate(existing, start=1):
        # Skip empty folders
        has_files = any(folder.iterdir())
        if not has_files:
            shutil.rmtree(folder, ignore_errors=True)
            continue
        tmp_path = items_dir / f"__tmp_item_{idx:02d}"
        folder.rename(tmp_path)
        temp_dirs.append(tmp_path)

    for idx, tmp_folder in enumerate(temp_dirs, start=1):
        final_path = items_dir / f"item_{idx:02d}"
        tmp_folder.rename(final_path)


def merge_item_folders(
    items_dir: Path,
    source_item: str,
    target_item: str,
    threshold_seconds: float,
    outlier_max_photos: int,
) -> Dict[str, Any]:
    """Merge all photos from source_item into target_item and re-index item folders."""
    src_dir = items_dir / source_item
    dst_dir = items_dir / target_item

    if not src_dir.exists() or not src_dir.is_dir():
        raise FileNotFoundError(f"Source item folder not found: {src_dir}")
    if not dst_dir.exists() or not dst_dir.is_dir():
        raise FileNotFoundError(f"Target item folder not found: {dst_dir}")

    moved_files = []
    for item_file in sorted(src_dir.iterdir()):
        if item_file.is_file():
            dest_file = dst_dir / item_file.name
            if dest_file.exists():
                dest_file = dst_dir / f"{item_file.stem}_merged{item_file.suffix}"
            shutil.move(str(item_file), str(dest_file))
            moved_files.append(dest_file.name)

    shutil.rmtree(src_dir, ignore_errors=True)
    reindex_item_folders(items_dir)
    report = build_grouping_report(items_dir, threshold_seconds, outlier_max_photos)
    print(
        f"[OK] Merged {len(moved_files)} file(s) from '{source_item}' into '{target_item}' "
        f"and re-indexed {report['total_items']} item folder(s)."
    )
    return report


def slice_raw_photos(
    raw_dir: Path,
    items_dir: Path,
    threshold_seconds: float,
    outlier_max_photos: int,
    clean: bool = False,
) -> Dict[str, Any]:
    """Scan raw_photos, sort by EXIF timestamp, slice by time gap, and stage into /items/item_XX/."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    items_dir.mkdir(parents=True, exist_ok=True)

    raw_photos = [
        p
        for p in raw_dir.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    if not raw_photos:
        print(f"[INFO] No photos found in '{raw_dir.as_posix()}'.")
        print(f"       Supported formats: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
        report = build_grouping_report(items_dir, threshold_seconds, outlier_max_photos)
        return report

    if clean:
        for child in items_dir.iterdir():
            if child.is_dir() and child.name.startswith("item_"):
                shutil.rmtree(child, ignore_errors=True)
            elif child.is_file() and child.name != ".gitkeep":
                child.unlink(missing_ok=True)

    # Extract timestamps for all photos
    extracted: List[Tuple[Path, datetime, str]] = []
    for photo_path in raw_photos:
        dt, source = extract_photo_timestamp(photo_path)
        extracted.append((photo_path, dt, source))

    # Sort chronologically by timestamp, breaking ties by filename
    extracted.sort(key=lambda x: (x[1], x[0].name.lower()))

    groups: List[List[Tuple[Path, datetime, str, Optional[float]]]] = []
    current_group: List[Tuple[Path, datetime, str, Optional[float]]] = []

    for photo_path, dt, source in extracted:
        if not current_group:
            current_group.append((photo_path, dt, source, None))
        else:
            prev_dt = current_group[-1][1]
            gap_seconds = round((dt - prev_dt).total_seconds(), 2)
            if gap_seconds <= threshold_seconds:
                current_group.append((photo_path, dt, source, gap_seconds))
            else:
                groups.append(current_group)
                current_group = [(photo_path, dt, source, gap_seconds)]

    if current_group:
        groups.append(current_group)

    # Clear existing item_XX folders before restaging
    for child in items_dir.iterdir():
        if child.is_dir() and child.name.startswith("item_"):
            shutil.rmtree(child, ignore_errors=True)

    photo_metadata_map: Dict[str, Dict[str, Any]] = {}

    for idx, grp in enumerate(groups, start=1):
        folder_name = f"item_{idx:02d}"
        folder_path = items_dir / folder_name
        folder_path.mkdir(parents=True, exist_ok=True)

        for photo_idx, (src_path, dt, source, gap_sec) in enumerate(grp, start=1):
            # Preserve readable ordering while keeping original filename
            staged_name = f"{photo_idx:02d}_{src_path.name}"
            dst_path = folder_path / staged_name
            shutil.copy2(src_path, dst_path)
            photo_metadata_map[staged_name] = {
                "original_filename": src_path.name,
                "timestamp": dt.isoformat(),
                "source": source,
                "gap_from_prev_seconds": gap_sec,
            }

    report = build_grouping_report(
        items_dir=items_dir,
        threshold_seconds=threshold_seconds,
        outlier_max_photos=outlier_max_photos,
        photo_metadata_map=photo_metadata_map,
    )

    print("=" * 72)
    print(f"STAGE 1 COMPLETE: Sliced {report['total_photos']} photo(s) into {report['total_items']} item group(s)")
    print(f"Time-Gap Threshold: {threshold_seconds}s | Report: items/grouping_report.json")
    print("=" * 72)
    for grp_info in report["groups"]:
        flag = " [OUTLIER CANDIDATE - VERIFY VISION MERGE]" if grp_info["outlier_candidate"] else ""
        gap_txt = (
            f" (gap from prev: {grp_info['gap_from_previous_group_seconds']}s)"
            if grp_info["gap_from_previous_group_seconds"] is not None
            else ""
        )
        print(
            f"  - {grp_info['item_id']}: {grp_info['photo_count']} photo(s) "
            f"[{grp_info['start_timestamp']} -> {grp_info['end_timestamp']}]{gap_txt}{flag}"
        )

    if report["outlier_candidates"]:
        print("\n[!] OUTLIER AUDIT REQUIRED:")
        for outlier in report["outlier_candidates"]:
            print(f"  * {outlier['audit_prompt']}")
            print(f"    To merge: {outlier['merge_command']}")

    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage 1: Slice /raw_photos/ by EXIF DateTimeOriginal into /items/item_XX/ folders."
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=Path("raw_photos"),
        help="Source directory containing raw photos (default: raw_photos)",
    )
    parser.add_argument(
        "--items-dir",
        type=Path,
        default=Path("items"),
        help="Destination directory for grouped item folders (default: items)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=float(os.getenv("PHOTO_TIME_GAP_SECONDS", "45")),
        help="Max seconds between sequential photos in the same group (default: 45)",
    )
    parser.add_argument(
        "--outlier-max",
        type=int,
        default=int(os.getenv("OUTLIER_MAX_PHOTOS", "2")),
        help="Max photo count in a group to flag as an outlier candidate (default: 2)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean existing /items/item_XX/ directories before slicing",
    )
    parser.add_argument(
        "--merge",
        nargs=2,
        metavar=("SOURCE_ITEM", "TARGET_ITEM"),
        help="Merge SOURCE_ITEM (e.g. item_03) into TARGET_ITEM (e.g. item_02) and re-index",
    )

    args = parser.parse_args()

    if args.merge:
        source_item, target_item = args.merge
        merge_item_folders(
            items_dir=args.items_dir,
            source_item=source_item,
            target_item=target_item,
            threshold_seconds=args.threshold,
            outlier_max_photos=args.outlier_max,
        )
        return 0

    slice_raw_photos(
        raw_dir=args.raw_dir,
        items_dir=args.items_dir,
        threshold_seconds=args.threshold,
        outlier_max_photos=args.outlier_max,
        clean=args.clean,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
