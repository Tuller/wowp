# wowp

This is a Python script I use to call the [BigWigs packager](https://github.com/BigWigsMods/packager)
in order to build and deploy an addon I'm working on to multiple World of
Warcraft addon directories at once.

# Requirements

- Python 3
- rsync
- A WOW_HOME environment variable set to the World of Warcraft installation directory

# Usage

```bash wowp.py [options] ```

| Option | Description |
| ------ | ----------- |
| --flavor \[mainline, classic\] | Sets the target versions. Defaults to mainline and classic |
| --channel \[live, ptr, beta, alpha\] | Sets the target release channels. Defaults to live |
| --retail --mainline | Adds mainline to the flavor list |
| --classic | Adds classic to the flavor list |
| --live | Adds live to the channel list |
| --ptr | Adds ptr to the channel list |
| --beta | Adds beta to the channel list |
| --alpha | Adds alpha to the channel list |
