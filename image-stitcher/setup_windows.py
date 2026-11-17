"""Windows Python 3.12 x64 installer; keeps dependencies inside the project."""
import argparse
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sift-only', action='store_true', help='Skip PyTorch and CNN weights')
    parser.add_argument('--scene', action='store_true',
                        help='Also install COLMAP/OpenMVS and sample drone videos for the 3D scene page')
    args = parser.parse_args()
    if os.name != 'nt' or sys.version_info[:2] != (3, 12) or struct.calcsize('P') != 8:
        raise SystemExit('Run setup.cmd with Windows Python 3.12 x64.')
    python = ROOT / '.venv' / 'Scripts' / 'python.exe'
    if (ROOT / '.venv').exists() and not python.exists():
        raise SystemExit('Existing .venv is incomplete or not a Windows environment. Rename it before setup; no files were removed.')
    if not args.sift_only and not shutil.which('git'):
        raise SystemExit('Install Git for Windows first, or use setup.cmd --sift-only.')
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(ROOT / '.venv')

    def run(*arguments):
        subprocess.run([str(python), *arguments], cwd=ROOT, check=True)

    run('-m', 'pip', 'install', '-r', 'requirements.txt')
    if not args.sift_only:
        run('-m', 'pip', 'install', 'torch==2.15.0+cpu', 'torchvision==0.29.2+cpu',
            '--index-url', 'https://download.pytorch.org/whl/cpu')
        run('-m', 'pip', 'install', '-r', 'requirements-learned.txt')
        run('learned.py')
    run('download_samples.py')
    if args.scene:
        run('setup_photogrammetry.py', '--surface')
        run('download_scene_samples.py')
    run('-m', 'pip', 'check')
    run('-m', 'pytest', '-q')


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode)
