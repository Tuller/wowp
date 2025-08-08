#!/usr/bin/env python3
import argparse
import os
from pathlib import Path
import shutil
import urllib.request
import hashlib
import stat
import subprocess
import tempfile
from typing import List, Union

# ANSI color codes and utilities
from enum import Enum
import sys

class Color(Enum):
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    RESET = '\033[0m'

def colorize(text: str, *styles: Union[Color, str]) -> str:
    """Apply color/style to text using ANSI codes."""
    if not sys.stdout.isatty():  # Don't colorize if output is redirected
        return text
    
    codes = [style.value if isinstance(style, Color) else style for style in styles]
    return f"{''.join(codes)}{text}{Color.RESET.value}" if codes else text

# Semantic color helpers
def error(text: str) -> str: return colorize(text, Color.RED)
def success(text: str) -> str: return colorize(text, Color.GREEN, Color.BOLD)
def header(text: str) -> str: return colorize(text, Color.BLUE, Color.BOLD)
def counter(text: str) -> str: return colorize(text, Color.CYAN)
def checkmark() -> str: return colorize("✓", Color.GREEN)

PACKAGER_VERSION = "v2.4.2"

# curl https://raw.githubusercontent.com/BigWigsMods/packager/refs/tags/v2.4.2/release.sh | sha256sum
PACKAGER_SHA256 = "37c259ef699fc1cd816d5d1839a4c4773a6418f627f7bf27cd5cfefe1b682e2c"

# Mapping of (flavor, channel) combinations to target directory names
TARGET_MAP = {
    ('mainline', 'live'): ['retail'],
    ('mainline', 'ptr'): ['ptr', 'xptr'],
    ('mainline', 'beta'): ['beta'],
    ('mainline', 'alpha'): ['alpha'],
    ('classic', 'live'): ['classic', 'classic_era'],
    ('classic', 'ptr'): ['classic_ptr', 'classic_era_ptr'],
    ('classic', 'beta'): ['classic_beta', 'classic_era_beta'],
    ('classic', 'alpha'): ['classic_alpha', 'classic_era_alpha'],
}

def get_sha256(path: Path) -> str:
    h = hashlib.sha256()

    with open(path, 'rb') as fh:
        while True:
            data = fh.read(4096)
            if len(data) == 0:
                break
            else:
                h.update(data)

    return h.hexdigest()

def download_packager(working_dir: Path) -> Path:
    src = f"https://raw.githubusercontent.com/BigWigsMods/packager/{PACKAGER_VERSION}/release.sh"
    script_path  = working_dir.joinpath("release.sh")

    with urllib.request.urlopen(src) as response, script_path.open('wb') as out_file:
        shutil.copyfileobj(response, out_file)

    # verify hash
    digest = get_sha256(script_path)
    if digest != PACKAGER_SHA256:
        raise Exception("Packager digest does not match expected value")

    # make the packager executable
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)

    return script_path

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WoW Publisher")

    parser.add_argument('--flavor', choices=['mainline', 'classic'], nargs='+',
                       help='Target WoW flavors (default: mainline classic)')
    parser.add_argument('--channel', choices=['live', 'ptr', 'beta', 'alpha'], nargs='+',
                       help='Target release channels (default: live)')
    
    # Convenience flags
    parser.add_argument('--retail', '--mainline', action='append_const', const='mainline', dest='flavor')
    parser.add_argument('--classic', action='append_const', const='classic', dest='flavor')
    parser.add_argument('--live', action='append_const', const='live', dest='channel')
    parser.add_argument('--ptr', action='append_const', const='ptr', dest='channel')
    parser.add_argument('--beta', action='append_const', const='beta', dest='channel')
    parser.add_argument('--alpha', action='append_const', const='alpha', dest='channel')

    args = parser.parse_args()
    
    # Apply defaults if no flags were used
    if not args.flavor:
        args.flavor = ['mainline', 'classic']
    if not args.channel:
        args.channel = ['live']
    
    # Remove duplicates while preserving order
    args.flavor = list(dict.fromkeys(args.flavor))
    args.channel = list(dict.fromkeys(args.channel))
    
    return args

def get_target_dirs(wow_home: Path, flavors: List[str], channels: List[str]) -> List[Path]:
    """Get target directories based on flavors and channels."""
    targets = set()
    
    for flavor in flavors:
        for channel in channels:
            if (flavor, channel) in TARGET_MAP:
                targets.update(TARGET_MAP[(flavor, channel)])
    
    found_targets = []
    for target in targets:
        target_dir = wow_home.joinpath(f"_{target}_", "Interface", "AddOns")
        if target_dir.exists():
            found_targets.append(target_dir)
    return found_targets

def run_packager(working_dir: Path) -> List[Path]:
    """Downloads and runs the packager."""
    packager_path = download_packager(working_dir)
    release_dir = working_dir.joinpath("release")

    proc = subprocess.run([
        str(packager_path),
        "-dlzS",
        "-t",
        str(Path.cwd()),
        "-r",
        str(release_dir),
    ])

    if proc.returncode != 0:
        # The packager script already prints errors to stderr, so we just need to exit.
        raise RuntimeError(f"Packager execution failed with return code {proc.returncode}")
    
    return [f for f in release_dir.iterdir() if f.is_dir()]

def deploy_addons(addon_dirs: List[Path], target_dirs: List[Path]) -> None:
    """Deploys addons to the target directories."""
    print(colorize("Deploying addons...", Color.BOLD) + "\n")

    for i, target_dir in enumerate(target_dirs, 1):
        target_name = target_dir.parent.parent.name.strip('_')
        print(f"{counter(f'[{i}/{len(target_dirs)}]')} {header(target_name)}:")

        for j, addon_dir in enumerate(addon_dirs, 1):
            dest_addon_dir = target_dir.joinpath(addon_dir.name)
            print(f"  {counter(f'[{j}/{len(addon_dirs)}]')} {addon_dir.name}...", end=" ")

            if dest_addon_dir.exists():
                shutil.rmtree(dest_addon_dir)
            
            shutil.copytree(addon_dir, dest_addon_dir)

            print(checkmark())
        
        print()

def main() -> int:
    args = parse_args()

    # Validate WOW_HOME environment
    wow_home_str = os.environ.get("WOW_HOME")
    if not wow_home_str:
        print(error("WOW_HOME environment variable not set. Please set it to your World of Warcraft installation directory."))
        return 1

    wow_home = Path(wow_home_str)
    if not wow_home.exists():
        print(error(f'World of Warcraft directory "{wow_home.absolute()}" not found.'))
        return 1

    target_dirs = get_target_dirs(wow_home, args.flavor, args.channel)
    if not target_dirs:
        print(error("No WoW installations found for the specified flavor/channel combination."))
        return 1

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            addon_dirs = run_packager(Path(temp_dir))
            deploy_addons(addon_dirs, target_dirs)
    except RuntimeError as e:
        print(error(str(e)))
        return 1

    print(success("Deployment complete!"))


if __name__ == "__main__":
    main()
