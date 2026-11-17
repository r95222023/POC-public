# SPDX-License-Identifier: GPL-3.0-only
"""Download openly licensed drone orbit videos from Wikimedia Commons for 3D scene tests.

Videos are not redistributed with the repository. Commons refuses scripted downloads
of original files, so the published VP9 transcodes (at most 1080p) are fetched instead;
licence, author, source URL and the local SHA-256 are recorded in sources.json.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent / "data" / "scene-samples"
API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "POC-photogrammetry-samples/1.0 (local research script)"}
MAX_HEIGHT = 1080

SAMPLES = {
    "oubiels": {
        "title": "File:Notre Dame des Oubiels drone.webm",
        "label": "小教堂 360° 環繞（480p · 20 秒）",
        "description": "CC0。畫質低但檔案小，幾分鐘內可跑完整個流程，適合先確認環境。",
        "frames": 40, "default": True,
    },
    "seacroft": {
        "title": "File:Drone footage of St James' Church, Seacroft.webm",
        "label": "St James' 教堂環繞（1080p · 94 秒）",
        "description": "CC0。以興趣點（POI）環繞拍攝教堂，畫質足以產生較完整的貼圖模型。",
        "frames": 70, "default": True,
    },
    "okulice": {
        "title": "File:Okulice - kościół (360 stopni).webm",
        "label": "Okulice 教堂 360°（1080p · 104 秒）",
        "description": "CC BY 4.0，使用或發布成果時須標示作者。",
        "frames": 70, "default": False,
    },
}


def api(**params):
    query = urlencode({"action": "query", "format": "json", **params})
    with urlopen(Request(f"{API}?{query}", headers=HEADERS), timeout=30) as response:
        return next(iter(json.load(response)["query"]["pages"].values()))


def metadata(title):
    page = api(titles=title, prop="imageinfo|videoinfo", iiprop="url|size|extmetadata",
               iiextmetadatafilter="LicenseShortName|LicenseUrl|Artist", viprop="derivatives")
    info = page["imageinfo"][0]
    extra = info.get("extmetadata", {})
    plain = lambda key: re.sub(r"<[^>]+>", "", extra.get(key, {}).get("value", "")).strip()
    transcodes = [d for d in page.get("videoinfo", [{}])[0].get("derivatives", [])
                  if d.get("transcodekey", "").endswith(".vp9.webm") and (d.get("height") or 0) <= MAX_HEIGHT]
    if not transcodes:
        raise RuntimeError(f"No VP9 transcode up to {MAX_HEIGHT}p is published for {title}")
    best = max(transcodes, key=lambda d: d.get("height") or 0)
    return {"commons_title": title, "page": info["descriptionurl"], "original_size": [info.get("width"), info.get("height")],
            "duration_seconds": info.get("duration"), "license": plain("LicenseShortName"),
            "license_url": plain("LicenseUrl"), "author": plain("Artist"),
            "download": best["src"], "transcode": best["transcodekey"], "size": [best.get("width"), best.get("height")]}


def fetch(url, target):
    curl = shutil.which("curl")
    if curl:
        # upload.wikimedia.org throttles Python's urllib client but serves curl normally.
        subprocess.run([curl, "--fail", "--location", "--silent", "--show-error", "--retry", "2",
                        "--user-agent", HEADERS["User-Agent"], "--output", str(target), url], check=True)
    else:
        with urlopen(Request(url, headers=HEADERS), timeout=120) as response, target.open("wb") as stream:
            shutil.copyfileobj(response, stream, 1 << 20)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def download(key, spec, manifest):
    target = ROOT / f"{key}.webm"
    known = manifest.get(key, {})
    if target.is_file() and known.get("sha256") == sha256(target):
        print(f"{key}: already present", flush=True)
        return known
    info = metadata(spec["title"])
    print(f"{key}: downloading {info['transcode']} from {info['page']}", flush=True)
    temporary = target.with_suffix(".webm.part")
    try:
        fetch(info["download"], temporary)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    temporary.replace(target)
    return {**info, "file": target.name, "bytes": target.stat().st_size, "sha256": sha256(target)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="*", help=f"Samples to fetch: {', '.join(SAMPLES)} (default: small CC0 set)")
    parser.add_argument("--all", action="store_true", help="Also fetch the CC BY sample")
    args = parser.parse_args()
    unknown = sorted(set(args.names) - set(SAMPLES))
    if unknown:
        parser.error(f"Unknown sample(s): {', '.join(unknown)}")
    names = args.names or [k for k, v in SAMPLES.items() if v["default"] or args.all]
    ROOT.mkdir(parents=True, exist_ok=True)
    manifest_path = ROOT / "sources.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    for key in names:
        manifest[key] = download(key, SAMPLES[key], manifest)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Samples ready in {ROOT}", flush=True)


if __name__ == "__main__":
    main()
