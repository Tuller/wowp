# wowp

A fast World of Warcraft addon builder and deployer for local development.
This iteration was mostly vibe coded, but works OK for my purposes.

## Features

- Parses `.pkgmeta` files
- Caches external dependencies for fast rebuilds
- Supports Git and SVN externals
- Deploys to multiple WoW clients (retail, classic, PTR, beta)
- **Watch mode** - automatically rebuilds on file changes
- Colored terminal output with progress indicators

## Installation

### From Source (development)

```bash
git clone https://github.com/Tuller/wowp.git
cd wowp
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Build Standalone Binary

```bash
source .venv/bin/activate
pip install pyinstaller
pyinstaller --onefile --name wowp wowp.py

# Copy to PATH
cp dist/wowp ~/.local/bin/
```

## Requirements

- Python 3.8+
- Git (for Git externals)
- SVN (for SVN externals)
- `WOW_HOME` environment variable set to your WoW installation directory

## Usage

Run from your addon's directory (where `.pkgmeta` is located):

```bash
# Build and deploy to all flavors (retail + classic), live channel
python3 /path/to/wowp.py

# Watch mode - rebuild on file changes
python3 wowp.py --watch               # or -w

# Deploy to specific flavors/channels
python3 wowp.py --retail              # Retail only
python3 wowp.py --classic             # Classic only
python3 wowp.py --retail --ptr        # Retail PTR
python3 wowp.py --classic --beta      # Classic Beta

# Combine watch with flavor/channel selection
python3 wowp.py --watch --retail      # Watch and deploy to retail only

# Cache management
python3 wowp.py --cache-info          # Show cache statistics
python3 wowp.py --refresh-externals   # Force re-download all externals
python3 wowp.py --clear-cache         # Clear the cache
```

## Supported .pkgmeta Features

- `package-as` - Package name
- `externals` - Git/SVN dependencies (simple and expanded format)
- `move-folders` - Directory restructuring for multi-addon packages
- `ignore` - File patterns to exclude
- `@project-version@` keyword replacement in TOC files

## Caching

External dependencies are cached at `~/.cache/wowp/externals/`:

- Tagged or commit-pinned externals are cached forever
- Trunk/branch externals expire after 24 hours
- SVN repositories from the same base URL are fetched once and shared
