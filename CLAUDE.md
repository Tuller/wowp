# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`wowp.py` is a Python script that automates World of Warcraft addon building and deployment. It downloads the BigWigs packager, builds addons, and deploys them to multiple WoW client directories with colored progress output.

## Architecture

**Core Components:**
- **Packager Management** (`download_packager`): Downloads and SHA256-verifies BigWigs packager v2.4.2
- **Argument Processing** (`parse_args`): Handles flavor/channel selection with convenience flags  
- **Target Resolution** (`get_target_dirs`): Maps flavors+channels to WoW directory paths using `TARGET_MAP`
- **Deployment** (`main`): Copies built addons with colored progress display

**Key Data Structure:**
```python
TARGET_MAP = {
    ('mainline', 'live'): ['retail'],
    ('classic', 'ptr'): ['classic_ptr', 'classic_era_ptr'],
    # etc.
}
```

## Environment & Usage

**Requirements:**
- Python 3 (no external dependencies)
- `WOW_HOME` environment variable pointing to WoW installation

**Common Commands:**
```bash
python3 wowp.py                    # Default: all flavors, live only
python3 wowp.py --retail --ptr     # Retail PTR only  
python3 wowp.py --classic --live --ptr --beta  # All classic channels
```

## Implementation Details

**Build Process:**
1. Creates `/tmp/wowp` working directory
2. Downloads packager to working directory with verification
3. Runs packager with `-dlzS` flags on current directory
4. Copies results from `release/` to target addon directories

**Color Output:**
- Uses ANSI escape codes for cross-platform terminal colors
- Progress counters (cyan), target names (bold blue), success marks (green)
- `colored(text, *styles)` helper function for consistent formatting

**File Operations:**
- Uses `shutil.copytree/rmtree` instead of rsync for cross-platform compatibility
- Equivalent to `rsync -a --delete` behavior