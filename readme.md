# wowp

A Python script that automates building and deploying World of Warcraft addons using the [BigWigs packager](https://github.com/BigWigsMods/packager). It builds your addon once and deploys it to multiple WoW client directories simultaneously.

## Requirements

- Python 3.6+ (uses f-strings and pathlib)
- `WOW_HOME` environment variable set to your World of Warcraft installation directory

## Usage

```bash
python3 wowp.py [options]
```

### Options

| Option | Description |
| ------ | ----------- |
| `--flavor mainline classic` | Target WoW flavors (default: both) |
| `--channel live ptr beta alpha` | Target release channels (default: live) |
| `--retail`, `--mainline` | Deploy to mainline/retail |
| `--classic` | Deploy to classic |
| `--live` | Deploy to live servers |
| `--ptr` | Deploy to PTR servers |  
| `--beta` | Deploy to beta servers |
| `--alpha` | Deploy to alpha servers |

### Examples

```bash
# Deploy to all default targets (mainline + classic, live only)
python3 wowp.py

# Deploy only to retail PTR
python3 wowp.py --retail --ptr

# Deploy to all classic channels
python3 wowp.py --classic --live --ptr --beta
```

## How it works

1. Downloads and verifies the BigWigs packager script (v2.4.2)
2. Builds your addon in `/tmp/wowp` 
3. Deploys to WoW client directories using Python file operations
4. Shows colored progress output
