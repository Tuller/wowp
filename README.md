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

```bash
git clone https://github.com/Tuller/wowp.git
cd wowp
pipx install .
```

### Development

```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Requirements

- Python 3.11+
- Git (for Git externals)
- SVN (for SVN externals)
- `WOW_HOME` environment variable set to your WoW installation directory

## Usage

Run from your addon's directory (where `.pkgmeta` is located):

```bash
# Build and deploy to all flavors (retail + classic), live channel
wowp

# Watch mode - rebuild on file changes
wowp --watch               # or -w

# Deploy to specific flavors/channels
wowp --retail              # Retail only
wowp --classic             # Classic only
wowp --retail --ptr        # Retail PTR
wowp --classic --beta      # Classic Beta

# Combine watch with flavor/channel selection
wowp --watch --retail      # Watch and deploy to retail only

# Cache management
wowp --cache-info          # Show cache statistics
wowp --refresh-externals   # Force re-download all externals
wowp --clear-cache         # Clear the cache
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
