# SPDX-License-Identifier: GPL-3.0-only
"""Install optional photogrammetry dependencies into project-local caches."""

import argparse
import hashlib
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
from urllib.request import urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent
OPENMVS_URL = "https://github.com/cdcseacave/openMVS/releases/download/v2.4.0/OpenMVS_Windows_x64.zip"
OPENMVS_SHA256 = "0c31660c15c9ebc4c106873cf67564d9570d404aef7a6403451da1b6178b2167"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--surface", action="store_true", help="Also download the official Windows CPU OpenMVS release")
    args = parser.parse_args()
    target = ROOT / ".cache/photogrammetry"
    subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "--only-binary=:all:", "--no-deps",
                    "--target", str(target), "-r", str(ROOT / "requirements-photogrammetry.txt")], check=True)
    if not args.surface:
        return
    if sys.platform != "win32":
        parser.error("Automatic OpenMVS download supports Windows only; install OpenMVS separately on other platforms")
    destination = ROOT / ".cache/openmvs-2.4.0"
    binary = destination / "vc17/x64/Release/DensifyPointCloud.exe"
    archive = ROOT / ".cache/OpenMVS-2.4.0-Windows-x64.zip"
    if not archive.is_file():
        print("Downloading official OpenMVS 2.4.0 CPU archive...", flush=True)
        with urlopen(OPENMVS_URL, timeout=120) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != OPENMVS_SHA256:
        raise RuntimeError("OpenMVS checksum mismatch; inspect the cached archive before retrying")
    if not binary.is_file():
        with ZipFile(archive) as package:
            for entry in package.infolist():
                name = PurePosixPath(entry.filename.replace("\\", "/"))
                if name.is_absolute() or ".." in name.parts or ":" in str(name):
                    raise ValueError("Unsafe OpenMVS archive path")
            package.extractall(destination)
    print(f"OpenMVS ready: {binary.parent}")


if __name__ == "__main__":
    main()
