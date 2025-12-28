# AGENTS.md

## Project Overview

wowp is a fast World of Warcraft addon builder and deployer for local development. It's a single-file Python application (`wowp.py`) that:
- Parses `.pkgmeta` files (YAML format used by WoW addon developers)
- Manages external dependencies from Git and SVN repositories
- Builds and deploys addons to multiple WoW installations (retail, classic, PTR, beta)
- Provides watch mode for automatic rebuilds on file changes

## Development Commands

### Setup
```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Running the tool
```bash
# Basic build and deploy (from an addon directory with .pkgmeta)
python3 wowp.py

# Watch mode for development
python3 wowp.py --watch

# Deploy to specific flavors/channels
python3 wowp.py --retail --ptr

# Cache management
python3 wowp.py --cache-info
python3 wowp.py --refresh-externals
python3 wowp.py --clear-cache
```

### Building standalone binary
```bash
source .venv/bin/activate
pip install pyinstaller
pyinstaller --onefile --name wowp wowp.py
# Binary will be in dist/wowp
```

## Architecture

### Core Components (all in wowp.py)

**PkgMeta & External parsing** (lines 52-128)
- `parse_pkgmeta()` reads `.pkgmeta` YAML files
- `External` dataclass represents a single external dependency (Git/SVN)
- Supports both simple URL format and expanded format with tags/branches/commits
- `_detect_vcs_type()` auto-detects Git vs SVN from URL patterns

**ExternalsCache** (lines 131-252)
- Caches external dependencies at `~/.cache/wowp/externals/`
- Uses SHA256 hash of URL + destination name as cache key
- Tagged/commit-pinned externals cached forever; trunk/branch expire after 24h
- Stores metadata in `.wowp_meta.json` alongside cached content

**ExternalFetcher** (lines 255-471)
- Fetches dependencies from Git and SVN with retry logic
- **Key optimization**: Groups SVN externals by base URL to fetch parent repo once
- Uses shallow clones for Git when possible
- Implements exponential backoff retry for network failures
- Removes `.git` and `.svn` directories after checkout to save space

**IgnoreMatcher** (lines 474-594)
- Gitignore-style pattern matcher for filtering files during build
- Supports glob patterns: `*`, `**`, `?`, `[...]`, negation (`!`), directory-only (`/`)
- Always ignores hidden files (starting with `.`)
- Can load patterns from `.gitignore` or `.pkgmeta` ignore list

**AddonBuilder** (lines 612-841)
- Main build orchestrator using temporary staging directory
- Build process:
  1. Parse `.pkgmeta` to get package name, externals, move-folders, ignore patterns
  2. Fetch all externals to staging directory
  3. Copy addon source files (respecting ignore patterns)
  4. Apply `move-folders` mappings for multi-addon packages
  5. Replace `@project-version@` keyword in `.toc` files with git describe output
- Returns list of addon directories ready for deployment

**Watch mode** (lines 944-1087)
- `AddonChangeHandler` uses watchdog library for file system monitoring
- Implements 1-second debounce to avoid rapid rebuilds
- Respects ignore patterns to avoid triggering on cache/build files
- Rebuilds automatically on changes, shows timestamp for each rebuild

**Deployment** (lines 898-933)
- Target directories mapped by flavor (mainline/classic) and channel (live/ptr/beta/alpha)
- WoW addon directories have format: `WOW_HOME/_<target>_/Interface/AddOns`
- Example targets: `retail`, `ptr`, `classic`, `classic_era`, `classic_ptr`, etc.
- Deploys all addons from build to all matching target directories

## Important Patterns

### Move-folders behavior
When `.pkgmeta` has `move-folders`, the source files go into a package subdirectory first, then move-folders paths are relative to the staging root. This allows creating multi-addon packages from a single repository.

### SVN optimization for WowAce/CurseForge
Many WoW addon libraries are hosted on repos.wowace.com or repos.curseforge.com. When multiple externals come from the same base SVN URL (e.g., `https://repos.wowace.com/wow/libstub/trunk/`), the fetcher checks out the parent once and copies subdirectories to save bandwidth.

### Git version detection
`get_project_version()` uses `git describe --tags --always --abbrev=7` to generate version strings, which gets replaced in TOC files during build.

## Environment Requirements

- **WOW_HOME**: Must be set to WoW installation directory for deployment
- Python 3.8+
- Git and SVN executables in PATH (for external dependencies)
- Dependencies: pyyaml>=6.0, watchdog>=4.0

## Testing wowp

To test wowp, you need:
1. A WoW addon directory with `.pkgmeta` file
2. WOW_HOME environment variable pointing to WoW installation
3. At least one WoW flavor installed (retail or classic)

The tool is designed to be run from within an addon's source directory.
