#!/usr/bin/env python3
import argparse
import os
from pathlib import Path
import shutil
import urllib.request
import hashlib
import stat
import subprocess

# ANSI color codes
class Colors:
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    RESET = '\033[0m'

def colored(text, *styles):
    """Apply color/style to text using ANSI codes."""
    prefix = ''.join(styles)
    return f"{prefix}{text}{Colors.RESET}" if prefix else text

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

def parse_args():
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

def get_target_dirs(wow_home, flavors, channels):
    """Get target directories based on flavors and channels."""
    targets = set()
    
    for flavor in flavors:
        for channel in channels:
            if (flavor, channel) in TARGET_MAP:
                targets.update(TARGET_MAP[(flavor, channel)])
    
    for target in targets:
        target_dir = wow_home.joinpath(f"_{target}_", "Interface", "AddOns")
        if target_dir.exists():
            yield target_dir

def main():
    args = parse_args()

    # Validate WOW_HOME environment
    wow_home_str = os.environ.get("WOW_HOME")
    if not wow_home_str:
        print(colored("Error: WOW_HOME environment variable not set. Please set it to your World of Warcraft installation directory.", Colors.RED))
        return 1

    wow_home = Path(wow_home_str)
    if not wow_home.exists():
        print(colored(f'Error: World of Warcraft directory "{wow_home.absolute()}" not found.', Colors.RED))
        return 1

    # setup the working directory
    working_dir = Path("/tmp/wowp")
    if working_dir.exists():
        shutil.rmtree("/tmp/wowp")

    working_dir.mkdir()

    # grab the packager script
    packager = download_packager(working_dir)

    # setup the release directory
    release_dir = working_dir.joinpath("release")

    # run the packager
    packager_result = subprocess.run([
        packager,
        "-dlzS",
        "-t",
        os.getcwd(),
        "-r",
        release_dir
    ])

    if packager_result.returncode != 0:
        print(colored(f"Error: Packager execution failed with return code {packager_result.returncode}", Colors.RED))
        return 1

    # deploy addons to the output directories
    print(colored("Deploying addons...", Colors.BOLD) + "\n")

    target_dirs = list(get_target_dirs(wow_home, args.flavor, args.channel))
    addon_dirs = [f for f in os.scandir(release_dir) if f.is_dir()]
    
    for i, target_dir in enumerate(target_dirs, 1):
        # Extract just the target name from the path (e.g., "retail" from "/_retail_/Interface/AddOns")
        target_name = target_dir.parent.parent.name.strip('_')
        counter = colored(f"[{i}/{len(target_dirs)}]", Colors.CYAN)
        target_header = colored(target_name, Colors.BLUE, Colors.BOLD)
        print(f"{counter} {target_header}:")
        
        for j, d in enumerate(addon_dirs, 1):
            addon_counter = colored(f"[{j}/{len(addon_dirs)}]", Colors.CYAN)
            print(f"  {addon_counter} {d.name}...", end=" ")

            dest_addon_dir = target_dir / d.name
            
            # Remove existing addon directory if it exists (equivalent to rsync --delete)
            if dest_addon_dir.exists():
                shutil.rmtree(dest_addon_dir)
            
            # Copy the addon directory
            shutil.copytree(d.path, dest_addon_dir)

            print(colored("✓", Colors.GREEN))
        
        print()

    print(colored("Deployment complete!", Colors.GREEN, Colors.BOLD))

if __name__ == "__main__":
    main()
