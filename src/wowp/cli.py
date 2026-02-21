#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, NamedTuple

import yaml
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class Color(Enum):
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def colorize(text: str, *styles: Color) -> str:
    """Apply color/style to text using ANSI codes."""
    if not sys.stdout.isatty():  # Don't colorize if output is redirected
        return text

    codes = [style.value for style in styles]
    return f"{''.join(codes)}{text}{Color.RESET.value}" if codes else text


# Semantic color helpers
def error(text: str) -> str:
    return colorize(text, Color.RED)


def print_error(text: str) -> None:
    print(error(text), file=sys.stderr)


def success(text: str) -> str:
    return colorize(text, Color.GREEN, Color.BOLD)


def status(verb: str, detail: str = "", **kwargs) -> None:
    print(f"{success(verb.rjust(12))} {detail}", **kwargs)


def checkmark() -> str:
    return colorize("✓", Color.GREEN)


# VCS and package metadata types
class VcsType(Enum):
    GIT = "git"
    SVN = "svn"


@dataclass
class External:
    """Represents a single external dependency."""

    dest_path: str  # e.g., "Addon/libs/LibStub"
    url: str  # e.g., "https://repos.wowace.com/wow/libstub/tags/1.0"
    vcs_type: VcsType
    tag: str | None = None
    branch: str | None = None
    commit: str | None = None


@dataclass
class PkgMeta:
    """Parsed .pkgmeta configuration."""

    package_as: str = ""
    move_folders: dict[str, str] = field(default_factory=dict)
    externals: list[External] = field(default_factory=list)
    ignore: list[str] = field(default_factory=list)


# Cache configuration
CACHE_DIR = Path.home() / ".cache" / "wowp"
EXTERNALS_CACHE_DIR = CACHE_DIR / "externals"
CACHE_MAX_AGE_HOURS = 24


def _detect_vcs_type(url: str) -> VcsType:
    """Determine if URL is Git or SVN based on patterns."""
    if url.endswith(".git"):
        return VcsType.GIT
    if "github.com" in url or "gitlab.com" in url:
        return VcsType.GIT
    # Check for SVN markers: /trunk, /tags/, /branches/ (with or without trailing slash)
    if re.search(r"/(trunk|tags|branches)(/|$)", url):
        return VcsType.SVN
    if "repos.wowace.com" in url or "repos.curseforge.com" in url:
        return VcsType.GIT
    return VcsType.GIT


def _parse_external(dest_path: str, value: Any) -> External:
    """Parse an external entry (simple string URL or expanded dict)."""
    if isinstance(value, str):
        return External(
            dest_path=dest_path, url=value, vcs_type=_detect_vcs_type(value)
        )

    # Expanded format with url, tag, branch, commit, type
    url = value.get("url", "")
    if not url or not url.strip():
        raise ValueError(
            f"External '{dest_path}': 'url' field is required and cannot be empty"
        )

    vcs_type_str = value.get("type")
    if vcs_type_str:
        vcs_type = VcsType.GIT if vcs_type_str == "git" else VcsType.SVN
    else:
        vcs_type = _detect_vcs_type(url)

    # Convert numeric values to strings (YAML parses 1.0 as float)
    tag = value.get("tag")
    if tag is not None:
        tag = str(tag)

    branch = value.get("branch")
    if branch is not None:
        branch = str(branch)

    commit = value.get("commit")
    if commit is not None:
        commit = str(commit)

    # Validate that only one version specifier is provided
    version_specifiers = [tag, branch, commit]
    if sum(1 for v in version_specifiers if v is not None) > 1:
        raise ValueError(
            f"External '{dest_path}': Cannot specify multiple of tag/branch/commit. "
            f"Only one version specifier should be provided."
        )

    return External(
        dest_path=dest_path,
        url=url,
        vcs_type=vcs_type,
        tag=tag,
        branch=branch,
        commit=commit,
    )


def parse_pkgmeta(pkgmeta_path: Path) -> PkgMeta:
    """Parse a .pkgmeta YAML file and return structured data."""
    data = yaml.safe_load(pkgmeta_path.read_text(encoding="utf-8")) or {}

    externals = []
    for dest_path, value in (data.get("externals") or {}).items():
        externals.append(_parse_external(dest_path, value))

    return PkgMeta(
        package_as=data.get("package-as", ""),
        move_folders=data.get("move-folders") or {},
        externals=externals,
        ignore=data.get("ignore") or [],
    )


class ExternalsCache:
    """Manages cached external dependencies."""

    def __init__(self, cache_dir: Path = EXTERNALS_CACHE_DIR):
        self.cache_dir = cache_dir

    def get_cache_key(self, external: External) -> str:
        """Generate unique cache key for an external."""
        url_hash = hashlib.sha256(external.url.encode()).hexdigest()[:12]
        dest_name = Path(external.dest_path).name

        # Include version specifier in cache key to prevent collisions
        # when same URL is used with different tags/branches/commits
        version_spec = ""
        if external.tag:
            version_spec = f"_tag-{external.tag}"
        elif external.branch:
            version_spec = f"_branch-{external.branch}"
        elif external.commit:
            version_spec = f"_commit-{external.commit}"

        return f"{url_hash}_{dest_name}{version_spec}"

    def get_cache_path(self, external: External) -> Path:
        """Return cache path for an external."""
        return self.cache_dir / self.get_cache_key(external)

    def get_meta_path(self, external: External) -> Path:
        """Return metadata file path for an external."""
        return self.get_cache_path(external) / ".wowp_meta.json"

    def is_cached(self, external: External) -> bool:
        """Check if external exists in cache."""
        cache_path = self.get_cache_path(external)
        meta_path = self.get_meta_path(external)
        return cache_path.exists() and meta_path.exists()

    def is_stale(self, external: External) -> bool:
        """Check if cached external is stale and needs refresh."""
        if not self.is_cached(external):
            return True

        # Tagged or commit-pinned externals never go stale
        if external.tag or external.commit:
            return False

        # Check age for trunk/branch externals
        try:
            meta = json.loads(self.get_meta_path(external).read_text())
            age_hours = (time.time() - meta.get("fetched_at", 0)) / 3600
            return age_hours > CACHE_MAX_AGE_HOURS
        except (OSError, json.JSONDecodeError):
            return True

    def get(self, external: External) -> Path | None:
        """Get path to cached external if valid, else None."""
        if self.is_cached(external) and not self.is_stale(external):
            return self.get_cache_path(external)
        return None

    def put(self, external: External, source_path: Path) -> Path:
        """Copy fetched external to cache, return cache path."""
        cache_path = self.get_cache_path(external)

        # Remove old cache if exists
        if cache_path.exists():
            shutil.rmtree(cache_path)

        # Copy to cache
        shutil.copytree(source_path, cache_path)

        # Write metadata
        meta = {
            "url": external.url,
            "fetched_at": time.time(),
            "tag": external.tag,
            "branch": external.branch,
            "commit": external.commit,
            "vcs_type": external.vcs_type.value,
        }
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.get_meta_path(external).write_text(json.dumps(meta, indent=2))

        return cache_path

    def invalidate(self, external: External) -> None:
        """Remove external from cache."""
        cache_path = self.get_cache_path(external)
        if cache_path.exists():
            shutil.rmtree(cache_path)

    def clear_all(self) -> None:
        """Clear entire cache."""
        if self.cache_dir.exists():
            shutil.rmtree(self.cache_dir)

    def get_cache_info(self) -> dict[str, Any]:
        """Return cache statistics."""
        if not self.cache_dir.exists():
            return {"total_size": 0, "entry_count": 0, "entries": []}

        entries = []
        total_size = 0

        for entry_dir in self.cache_dir.iterdir():
            if not entry_dir.is_dir():
                continue

            meta_path = entry_dir / ".wowp_meta.json"
            entry_size = sum(
                f.stat().st_size for f in entry_dir.rglob("*") if f.is_file()
            )
            total_size += entry_size

            entry_info = {"name": entry_dir.name, "size": entry_size}
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text())
                    entry_info["url"] = meta.get("url", "unknown")
                    entry_info["fetched_at"] = meta.get("fetched_at", 0)
                except (OSError, json.JSONDecodeError):
                    pass

            entries.append(entry_info)

        return {
            "total_size": total_size,
            "entry_count": len(entries),
            "entries": entries,
        }


class ExternalFetcher:
    """Fetches external dependencies from Git and SVN."""

    def __init__(self, cache: ExternalsCache):
        self.cache = cache

    def _build_svn_url(
        self, base_url: str, tag: str | None = None, branch: str | None = None
    ) -> str:
        """
        Build a proper SVN URL by replacing or adding trunk/tags/branches markers.

        Handles standard SVN repository layouts where repositories have:
        - /trunk/ for main development
        - /tags/VERSION/ for tagged releases
        - /branches/NAME/ for branches

        Args:
            base_url: The base SVN URL (may or may not contain markers)
            tag: Optional tag name to use (e.g., "1.0")
            branch: Optional branch name to use (e.g., "feature-x")

        Returns:
            Properly formatted SVN URL with markers replaced/added as needed

        Examples:
            _build_svn_url("https://repos.com/lib/trunk", tag="1.0")
            -> "https://repos.com/lib/tags/1.0"

            _build_svn_url("https://repos.com/lib/trunk/Sub", tag="1.0")
            -> "https://repos.com/lib/tags/1.0/Sub"

            _build_svn_url("https://repos.com/lib", tag="1.0")
            -> "https://repos.com/lib/tags/1.0"
        """
        # If neither tag nor branch specified, return URL unchanged
        if not tag and not branch:
            return base_url

        # Determine which marker to use
        if tag:
            new_marker = f"tags/{tag}"
        else:
            new_marker = f"branches/{branch}"

        # Pattern to match trunk, tags/*, or branches/* with optional subdirectories
        # Groups: (1) everything before marker, (2) optional subdirectory after marker
        pattern = r"(.*?/)(?:trunk|tags/[^/]+|branches/[^/]+)((?:/.*)?)"

        match = re.match(pattern, base_url)

        if match:
            # Found an existing marker - replace it
            prefix = match.group(1)  # Everything before marker
            suffix = match.group(2)  # Everything after marker (subdirs)

            # Remove leading slash from suffix if present for clean joining
            suffix = suffix.lstrip("/")

            # Reconstruct URL
            if suffix:
                return f"{prefix}{new_marker}/{suffix}"
            else:
                return f"{prefix}{new_marker}"
        else:
            # No marker found - append new marker
            base_url = base_url.rstrip("/")
            return f"{base_url}/{new_marker}"

    def _has_svn_marker(self, url: str) -> bool:
        """
        Check if URL contains an SVN marker (trunk/tags/branches).

        Args:
            url: SVN URL to check

        Returns:
            True if URL contains a marker, False otherwise
        """
        return bool(re.search(r"/(?:trunk|tags/[^/]+|branches/[^/]+)(?:/|$)", url))

    def fetch_all(
        self, externals: list[External], staging_dir: Path, force_refresh: bool = False
    ) -> None:
        """Fetch all externals, optimizing for shared parent repos."""
        # Group SVN externals by base repo for optimization
        svn_groups: dict[str, list[External]] = {}
        other_externals: list[External] = []

        for ext in externals:
            if ext.vcs_type == VcsType.SVN:
                base_url = self._get_svn_base_url(ext.url)
                if base_url:
                    svn_groups.setdefault(base_url, []).append(ext)
                else:
                    other_externals.append(ext)
            else:
                other_externals.append(ext)

        # Fetch grouped SVN repos (fetch parent once, copy subdirs)
        for base_url, group in svn_groups.items():
            if len(group) > 1:
                self._fetch_svn_group(base_url, group, staging_dir, force_refresh)
            else:
                other_externals.extend(group)

        # Fetch remaining externals individually
        for ext in other_externals:
            dest_path = staging_dir / ext.dest_path
            self.fetch(ext, dest_path, force_refresh)

    def _get_svn_base_url(self, url: str) -> str | None:
        """
        Extract base SVN repo URL for grouping optimization.

        Grouping optimization: Multiple externals from the same /trunk can be fetched
        together by checking out /trunk once and copying subdirectories.

        However, tags/branches with different versions should NOT be grouped together
        (e.g., tags/1.0 and tags/2.0 are different and can't share a checkout).

        Strategy: Only enable grouping for /trunk URLs. Disable for tags/branches.

        Args:
            url: Full SVN URL

        Returns:
            Base URL for trunk externals, None for tags/branches (disables grouping)

        Examples:
            _get_svn_base_url("https://repos.com/lib/trunk/SubDir")
            -> "https://repos.com/lib/trunk"

            _get_svn_base_url("https://repos.com/lib/tags/1.0/SubDir")
            -> None  (grouping disabled for tags)
        """
        # Only group /trunk URLs - tags/branches should be fetched individually
        pattern = r"(.*?/trunk)(?:/|$)"
        match = re.match(pattern, url)

        if match:
            return match.group(1)

        return None

    def _fetch_svn_group(
        self,
        base_url: str,
        externals: list[External],
        staging_dir: Path,
        force_refresh: bool,
    ) -> None:
        """Fetch a group of externals from the same SVN base."""
        # Check if all are cached
        all_cached = all(
            self.cache.get(ext) is not None and not force_refresh for ext in externals
        )

        if all_cached:
            # Just copy from cache
            for ext in externals:
                dest_path = staging_dir / ext.dest_path
                self._copy_from_cache(self.cache.get_cache_path(ext), dest_path)
            return

        # Fetch the base repo once
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir) / "base"
            cmd = [
                "svn",
                "checkout",
                "-q",
                "--non-interactive",
                base_url,
                str(base_path),
            ]
            self._run_with_retry(
                cmd, f"svn checkout {base_url}", max_attempts=5, cleanup_dir=base_path
            )

            # Clean up .svn directories
            for svn_dir in base_path.rglob(".svn"):
                shutil.rmtree(svn_dir, ignore_errors=True)

            # Copy each subdir and cache it
            for ext in externals:
                subdir = self._get_svn_subdir(ext.url)
                src_path = base_path / subdir

                if src_path.exists():
                    # Cache this external
                    self.cache.put(ext, src_path)
                    # Copy to destination
                    dest_path = staging_dir / ext.dest_path
                    self._copy_from_cache(self.cache.get_cache_path(ext), dest_path)

    def _get_svn_subdir(self, url: str) -> str:
        """
        Get subdirectory path from SVN URL (path after the marker).

        For grouping optimization, this extracts everything after the base marker
        (trunk/tags/branches), including the version number for tags/branches.

        Args:
            url: Full SVN URL

        Returns:
            Subdirectory path (empty string if none)

        Examples:
            _get_svn_subdir("https://repos.com/lib/trunk/LibStub/Core")
            -> "LibStub/Core"

            _get_svn_subdir("https://repos.com/lib/tags/1.0")
            -> "1.0"

            _get_svn_subdir("https://repos.com/lib/tags/1.0/LibStub")
            -> "1.0/LibStub"

            _get_svn_subdir("https://repos.com/lib/branches/dev/Sub")
            -> "dev/Sub"
        """
        # Pattern to extract everything after base marker (trunk/tags/branches)
        # For tags/branches, this includes the version/name as part of the subdir
        pattern = r".*?/(?:trunk|tags|branches)(?:/(.+))?"
        match = re.match(pattern, url)

        if match and match.group(1):
            return match.group(1)

        return ""

    def fetch(
        self, external: External, dest_dir: Path, force_refresh: bool = False
    ) -> None:
        """Fetch external to destination, using cache if available."""
        # Check cache first
        if not force_refresh:
            cached_path = self.cache.get(external)
            if cached_path:
                self._copy_from_cache(cached_path, dest_dir)
                return

        # Fetch to temp directory, then cache
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / "fetch"
            temp_path.mkdir()

            if external.vcs_type == VcsType.GIT:
                self._fetch_git(external, temp_path)
            else:
                self._fetch_svn(external, temp_path)

            # Cache the fetched content
            self.cache.put(external, temp_path)

            # Copy to destination
            self._copy_from_cache(self.cache.get_cache_path(external), dest_dir)

    def _copy_from_cache(self, cache_path: Path, dest_dir: Path) -> None:
        """Copy cached external to destination."""
        dest_dir.parent.mkdir(parents=True, exist_ok=True)
        if dest_dir.exists():
            shutil.rmtree(dest_dir)

        # Copy all files except .wowp_meta.json
        shutil.copytree(
            cache_path, dest_dir, ignore=shutil.ignore_patterns(".wowp_meta.json")
        )

    def _fetch_git(self, external: External, dest_dir: Path) -> None:
        """Clone Git repository."""
        cmd = ["git", "clone", "-q"]

        # Use shallow clone when possible
        if external.tag:
            cmd.extend(["--depth", "1", "--branch", external.tag])
        elif external.branch:
            cmd.extend(["--depth", "1", "--branch", external.branch])
        elif not external.commit:
            # No specific ref, shallow clone default branch
            cmd.extend(["--depth", "1"])

        cmd.extend([external.url, str(dest_dir)])

        self._run_with_retry(cmd, f"git clone {external.url}")

        # For specific commit, need to fetch then checkout
        if external.commit:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(dest_dir),
                    "fetch",
                    "--depth",
                    "1",
                    "origin",
                    external.commit,
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(dest_dir), "checkout", "-q", external.commit],
                check=True,
                capture_output=True,
            )

        # Clean up .git directory to save space
        git_dir = dest_dir / ".git"
        if git_dir.exists():
            shutil.rmtree(git_dir)

    def _fetch_svn(self, external: External, dest_dir: Path) -> None:
        """Checkout SVN repository."""
        # Build proper SVN URL with tag/branch replacement
        url = self._build_svn_url(external.url, external.tag, external.branch)

        # For repos without trunk/tags/branches, try with /trunk first, then without
        urls_to_try = [url]
        if not self._has_svn_marker(url):
            urls_to_try = [url.rstrip("/") + "/trunk", url]

        last_error = None
        for try_url in urls_to_try:
            cmd = ["svn", "checkout", "-q", "--non-interactive", try_url, str(dest_dir)]

            if external.commit:
                # Validate SVN revision is numeric
                try:
                    int(external.commit)
                except ValueError:
                    raise ValueError(
                        f"SVN revision must be numeric, got: '{external.commit}' "
                        f"for external '{external.dest_path}'"
                    )
                cmd.insert(3, f"-r{external.commit}")

            try:
                self._run_with_retry(
                    cmd, f"svn checkout {try_url}", max_attempts=3, cleanup_dir=dest_dir
                )
                # Clean up .svn directories
                for svn_dir in dest_dir.rglob(".svn"):
                    shutil.rmtree(svn_dir, ignore_errors=True)
                return
            except RuntimeError as e:
                last_error = e
                # Clean up partial checkout before trying next URL
                if dest_dir.exists():
                    shutil.rmtree(dest_dir)

        raise last_error or RuntimeError(f"Failed to checkout {url}")

    def _run_with_retry(
        self,
        cmd: list[str],
        description: str,
        max_attempts: int = 5,
        cleanup_dir: Path | None = None,
    ) -> None:
        """Run command with retry logic. Optionally cleans up directory before retry."""
        last_error = None
        for attempt in range(max_attempts):
            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True)
                return
            except subprocess.CalledProcessError as e:
                last_error = e
                if attempt < max_attempts - 1:
                    # Clean up directory before retry to prevent SVN locked state
                    if cleanup_dir and cleanup_dir.exists():
                        shutil.rmtree(cleanup_dir, ignore_errors=True)
                        cleanup_dir.mkdir(parents=True, exist_ok=True)
                    wait_time = (
                        2**attempt
                    )  # Exponential backoff: 1, 2, 4, 8, 16 seconds
                    time.sleep(wait_time)

        raise RuntimeError(
            f"Failed to {description} after {max_attempts} attempts: {last_error.stderr if last_error else 'unknown error'}"
        )


class _IgnoreRule(NamedTuple):
    is_negation: bool
    regex: re.Pattern[str]
    dir_only: bool


class IgnoreMatcher:
    """Simplified gitignore-style pattern matcher."""

    def __init__(self, patterns: list[str], base_dir: Path):
        self.base_dir = base_dir
        self.rules: list[_IgnoreRule] = []

        for pattern in patterns:
            pattern = pattern.strip()
            if not pattern or pattern.startswith("#"):
                continue
            self._add_pattern(pattern)

    @classmethod
    def from_gitignore(cls, gitignore_path: Path) -> "IgnoreMatcher":
        """Load patterns from .gitignore file."""
        if gitignore_path.exists():
            patterns = gitignore_path.read_text(encoding="utf-8").splitlines()
        else:
            patterns = []
        return cls(patterns, gitignore_path.parent)

    def _add_pattern(self, pattern: str) -> None:
        """Parse and add a pattern rule."""
        is_negation = pattern.startswith("!")
        if is_negation:
            pattern = pattern[1:]

        dir_only = pattern.endswith("/")
        if dir_only:
            pattern = pattern[:-1]

        # Check if pattern is anchored (starts with / or contains /)
        anchored = pattern.startswith("/") or "/" in pattern.rstrip("/")
        if pattern.startswith("/"):
            pattern = pattern[1:]

        regex = self._glob_to_regex(pattern, anchored)
        self.rules.append(_IgnoreRule(is_negation, re.compile(regex), dir_only))

    def _glob_to_regex(self, pattern: str, anchored: bool) -> str:
        """Convert gitignore glob pattern to regex."""
        result = []
        i = 0
        n = len(pattern)

        while i < n:
            c = pattern[i]
            if c == "*":
                # Check for **
                if i + 1 < n and pattern[i + 1] == "*":
                    # ** matches everything including path separators
                    if i + 2 < n and pattern[i + 2] == "/":
                        # **/ matches zero or more directories
                        result.append("(?:.*/)?")
                        i += 3
                        continue
                    else:
                        result.append(".*")
                        i += 2
                        continue
                else:
                    # * matches anything except /
                    result.append("[^/]*")
            elif c == "?":
                result.append("[^/]")
            elif c == "[":
                # Character class - find closing ]
                j = i + 1
                if j < n and pattern[j] == "!":
                    j += 1
                if j < n and pattern[j] == "]":
                    j += 1
                while j < n and pattern[j] != "]":
                    j += 1
                if j < n:
                    char_class = pattern[i : j + 1].replace("!", "^", 1)
                    result.append(char_class)
                    i = j
                else:
                    result.append(re.escape(c))
            elif c in ".^$+{}|()\\":
                result.append("\\" + c)
            else:
                result.append(c)
            i += 1

        regex_pattern = "".join(result)

        if anchored:
            return "^" + regex_pattern + "$"
        else:
            # Match at any path level
            return "(?:^|/)" + regex_pattern + "$"

    def is_ignored(self, path: Path, is_dir: bool = False) -> bool:
        """Check if path should be ignored."""
        try:
            rel_path = path.relative_to(self.base_dir)
        except ValueError:
            return False

        # Always ignore hidden files (starting with .)
        if any(part.startswith(".") for part in rel_path.parts):
            return True

        path_str = str(rel_path).replace("\\", "/")  # Normalize for Windows
        ignored = False

        for rule in self.rules:
            if rule.dir_only and not is_dir:
                continue
            if rule.regex.search(path_str):
                ignored = not rule.is_negation

        return ignored

    def filter_paths(self, paths: list[Path]) -> list[Path]:
        """Return paths that are NOT ignored."""
        return [p for p in paths if not self.is_ignored(p, p.is_dir())]


def get_project_version(project_dir: Path) -> str:
    """Get version string from git tags/commits."""
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always", "--abbrev=7"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return "dev"


class AddonBuilder:
    """Builds addon package from source."""

    def __init__(self, source_dir: Path, output_dir: Path, force_refresh: bool = False):
        self.source_dir = source_dir
        self.output_dir = output_dir
        self.force_refresh = force_refresh
        self.cache = ExternalsCache()
        self.fetcher = ExternalFetcher(self.cache)

    def build(self) -> list[Path]:
        """Build addon and return list of output directories."""
        # Parse .pkgmeta
        pkgmeta_path = self.source_dir / ".pkgmeta"
        if pkgmeta_path.exists():
            pkgmeta = parse_pkgmeta(pkgmeta_path)
        else:
            pkgmeta = PkgMeta(package_as=self.source_dir.name)

        package_name = pkgmeta.package_as or self.source_dir.name
        version = get_project_version(self.source_dir)
        status("Building", f"{package_name} {colorize(version, Color.CYAN)}")

        # When we have move-folders, the source files go into a package subdirectory
        # and move-folders paths are relative to the staging root
        staging_dir = self.output_dir
        package_dir = staging_dir / package_name
        package_dir.mkdir(parents=True, exist_ok=True)

        # Fetch externals to the package directory
        if pkgmeta.externals:
            self._fetch_externals(pkgmeta, package_dir)

        # Copy, move, and replace keywords
        self._copy_addon_files(pkgmeta, package_dir)
        addon_dirs = self._apply_move_folders(pkgmeta, staging_dir, package_name)
        self._replace_keywords(addon_dirs, version)

        return addon_dirs

    def _fetch_externals(self, pkgmeta: PkgMeta, staging_dir: Path) -> None:
        """Fetch all external dependencies."""
        status("Fetching", f"{len(pkgmeta.externals)} externals", flush=True)

        # Group externals by SVN base URL for optimization
        svn_groups: dict[str, list[External]] = {}
        other_externals: list[External] = []

        for ext in pkgmeta.externals:
            if ext.vcs_type == VcsType.SVN:
                base_url = self.fetcher._get_svn_base_url(ext.url)
                if base_url:
                    svn_groups.setdefault(base_url, []).append(ext)
                else:
                    other_externals.append(ext)
            else:
                other_externals.append(ext)

        # Fetch SVN groups (one checkout per base repo)
        for base_url, group in svn_groups.items():
            if len(group) > 1:
                # Check cache status
                all_cached = all(
                    self.cache.get(ext) is not None and not self.force_refresh
                    for ext in group
                )
                group_names = ", ".join(Path(e.dest_path).name for e in group[:3])
                if len(group) > 3:
                    group_names += f", ... ({len(group)} total)"
                print(
                    f"{'':>13}{group_names}",
                    end=" ", flush=True,
                )

                try:
                    self.fetcher._fetch_svn_group(
                        base_url, group, staging_dir, self.force_refresh
                    )
                    print(checkmark())
                except Exception as e:
                    print(error("✗"))
                    raise RuntimeError(f"Failed to fetch SVN group {base_url}: {e}")
            else:
                other_externals.extend(group)

        # Fetch remaining externals individually
        for external in other_externals:
            lib_name = Path(external.dest_path).name
            print(f"{'':>13}{lib_name}", end=" ", flush=True)

            try:
                dest_path = staging_dir / external.dest_path
                self.fetcher.fetch(external, dest_path, self.force_refresh)
                print(checkmark())
            except Exception as e:
                print(error("✗"))
                raise RuntimeError(f"Failed to fetch {lib_name}: {e}")

    def _copy_addon_files(self, pkgmeta: PkgMeta, staging_dir: Path) -> None:
        """Copy addon source files, respecting ignore patterns."""

        # Build ignore matcher from .gitignore and .pkgmeta ignore
        gitignore_path = self.source_dir / ".gitignore"
        if gitignore_path.exists():
            ignore_matcher = IgnoreMatcher.from_gitignore(gitignore_path)
        else:
            ignore_matcher = IgnoreMatcher(pkgmeta.ignore, self.source_dir)

        # Walk source directory and copy non-ignored files
        for src_path in self.source_dir.rglob("*"):
            if not src_path.is_file():
                continue

            # Check if ignored
            if ignore_matcher.is_ignored(src_path, is_dir=False):
                continue

            # Compute destination path
            rel_path = src_path.relative_to(self.source_dir)
            dest_path = staging_dir / rel_path

            # Skip if file already exists (e.g., from externals)
            if dest_path.exists():
                continue

            # Copy file
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dest_path)

    def _apply_move_folders(
        self, pkgmeta: PkgMeta, staging_dir: Path, package_name: str
    ) -> list[Path]:
        """Apply move-folders mappings and return final addon directories."""
        package_dir = staging_dir / package_name

        if not pkgmeta.move_folders:
            # No move-folders, package_dir is the only addon
            return [package_dir]


        # First pass: move all sources to temp locations to avoid conflicts
        temp_moves: list[tuple] = []  # (temp_path, dest_path)
        addon_dirs = []

        for src_rel, dest_name in pkgmeta.move_folders.items():
            src_path = staging_dir / src_rel
            dest_path = self.output_dir / dest_name

            if not src_path.exists():
                continue

            # Move to temp location first
            temp_path = staging_dir / f".temp_{dest_name}"
            if temp_path.exists():
                shutil.rmtree(temp_path)
            shutil.move(str(src_path), str(temp_path))
            temp_moves.append((temp_path, dest_path))

        # Second pass: move from temp to final destinations
        for temp_path, dest_path in temp_moves:
            if dest_path.exists():
                shutil.rmtree(dest_path)
            shutil.move(str(temp_path), str(dest_path))
            addon_dirs.append(dest_path)

        # Clean up empty directories in the package dir
        self._cleanup_empty_dirs(package_dir)

        # Remove package dir if empty
        if package_dir.exists() and not any(package_dir.iterdir()):
            package_dir.rmdir()

        return addon_dirs

    def _cleanup_empty_dirs(self, root: Path) -> None:
        """Remove empty directories recursively."""
        if not root.exists():
            return

        for dirpath in sorted(root.rglob("*"), reverse=True):
            if dirpath.is_dir() and not any(dirpath.iterdir()):
                dirpath.rmdir()

    def _replace_keywords(self, addon_dirs: list[Path], version: str) -> None:
        """Replace @keyword@ patterns in TOC files."""
        replacements = {
            "project-version": version,
        }

        for addon_dir in addon_dirs:
            for toc_file in addon_dir.rglob("*.toc"):
                self._replace_in_file(toc_file, replacements)

    def _replace_in_file(self, file_path: Path, replacements: dict[str, str]) -> None:
        """Replace @keyword@ patterns in a file."""
        try:
            content = file_path.read_text(encoding="utf-8-sig")  # Handle BOM
        except UnicodeDecodeError:
            content = file_path.read_text(encoding="latin-1")

        modified = False
        for keyword, value in replacements.items():
            pattern = f"@{keyword}@"
            if pattern in content:
                content = content.replace(pattern, value)
                modified = True

        if modified:
            file_path.write_text(content, encoding="utf-8")


# Mapping of (flavor, channel) combinations to target directory names
TARGET_MAP = {
    ("mainline", "live"): ["retail"],
    ("mainline", "ptr"): ["ptr", "xptr"],
    ("mainline", "beta"): ["beta"],
    ("mainline", "alpha"): ["alpha"],
    ("classic", "live"): ["classic", "classic_era", "anniversary"],
    ("classic", "ptr"): ["classic_ptr", "classic_era_ptr", "anniversary_ptr"],
    ("classic", "beta"): ["classic_beta", "classic_era_beta", "anniversary_beta"],
    ("classic", "alpha"): ["classic_alpha", "classic_era_alpha", "anniversary_alpha"],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WoW Publisher")

    parser.add_argument(
        "--flavor",
        choices=["mainline", "classic"],
        nargs="+",
        help="Target WoW flavors (default: mainline classic)",
    )
    parser.add_argument(
        "--channel",
        choices=["live", "ptr", "beta", "alpha"],
        nargs="+",
        help="Target release channels (default: live)",
    )

    # Convenience flags
    parser.add_argument(
        "--retail", "--mainline", action="append_const", const="mainline", dest="flavor"
    )
    parser.add_argument(
        "--classic", action="append_const", const="classic", dest="flavor"
    )
    parser.add_argument("--live", action="append_const", const="live", dest="channel")
    parser.add_argument("--ptr", action="append_const", const="ptr", dest="channel")
    parser.add_argument("--beta", action="append_const", const="beta", dest="channel")
    parser.add_argument("--alpha", action="append_const", const="alpha", dest="channel")

    # Cache management flags
    parser.add_argument(
        "--refresh-externals",
        action="store_true",
        help="Force re-download all external dependencies",
    )
    parser.add_argument(
        "--clear-cache", action="store_true", help="Clear the externals cache and exit"
    )
    parser.add_argument(
        "--cache-info", action="store_true", help="Show cache statistics and exit"
    )

    # Watch mode
    parser.add_argument(
        "--watch",
        "-w",
        action="store_true",
        help="Watch for file changes and rebuild automatically",
    )

    args = parser.parse_args()

    # Apply defaults if no flags were used
    if not args.flavor:
        args.flavor = ["mainline", "classic"]
    if not args.channel:
        args.channel = ["live"]

    # Remove duplicates while preserving order
    args.flavor = list(dict.fromkeys(args.flavor))
    args.channel = list(dict.fromkeys(args.channel))

    return args


def get_target_dirs(
    wow_home: Path, flavors: list[str], channels: list[str]
) -> list[Path]:
    """Get target directories based on flavors and channels."""
    targets = set()

    for flavor in flavors:
        for channel in channels:
            if (flavor, channel) in TARGET_MAP:
                targets.update(TARGET_MAP[(flavor, channel)])

    return [
        target_dir
        for target in targets
        if (target_dir := wow_home / f"_{target}_" / "Interface" / "AddOns").exists()
    ]


def deploy_addons(addon_dirs: list[Path], target_dirs: list[Path]) -> None:
    """Deploys addons to the target directories."""
    addon_names = ", ".join(d.name for d in addon_dirs)
    status("Deploying", f"{addon_names} to {len(target_dirs)} targets", flush=True)

    for target_dir in target_dirs:
        target_name = target_dir.parent.parent.name.strip("_")
        print(f"{'':>13}{target_name}", end=" ", flush=True)

        for addon_dir in addon_dirs:
            dest_addon_dir = target_dir / addon_dir.name
            if dest_addon_dir.exists():
                shutil.rmtree(dest_addon_dir)
            shutil.copytree(addon_dir, dest_addon_dir)

        print(checkmark())


def format_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


class AddonChangeHandler(FileSystemEventHandler):
    """Handles file system events and triggers rebuilds with debouncing."""

    DEBOUNCE_SECONDS = 1.0

    def __init__(
        self, source_dir: Path, ignore_matcher: IgnoreMatcher | None = None
    ):
        super().__init__()
        self.source_dir = source_dir
        self.ignore_matcher = ignore_matcher
        self.last_change_time: float | None = None
        self.pending_rebuild = False
        self._lock = threading.Lock()

    def _should_ignore(self, path: str) -> bool:
        """Check if path should be ignored."""
        path_obj = Path(path)

        # Always ignore hidden files and directories
        if any(part.startswith(".") for part in path_obj.parts):
            return True

        # Check against ignore matcher
        if self.ignore_matcher:
            try:
                return self.ignore_matcher.is_ignored(path_obj, path_obj.is_dir())
            except (OSError, ValueError):
                return False

        return False

    def on_any_event(self, event):
        """Handle any file system event."""
        # Ignore directory events
        if event.is_directory:
            return

        # Check if path should be ignored
        if self._should_ignore(event.src_path):
            return

        # Also check dest_path for move events
        if hasattr(event, "dest_path") and event.dest_path:
            if self._should_ignore(event.dest_path):
                return

        with self._lock:
            self.last_change_time = time.time()
            self.pending_rebuild = True

    def should_rebuild(self) -> bool:
        """Check if enough time has passed since last change to trigger rebuild."""
        with self._lock:
            if not self.pending_rebuild:
                return False

            if self.last_change_time is None:
                return False

            elapsed = time.time() - self.last_change_time
            if elapsed >= self.DEBOUNCE_SECONDS:
                self.pending_rebuild = False
                return True

            return False

    def reset(self):
        """Reset the handler state after a rebuild."""
        with self._lock:
            self.pending_rebuild = False
            self.last_change_time = None


def build_and_deploy(
    source_dir: Path, target_dirs: list[Path], force_refresh: bool = False
) -> bool:
    """Build and deploy addon. Returns True on success."""
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            builder = AddonBuilder(
                source_dir=source_dir,
                output_dir=Path(temp_dir),
                force_refresh=force_refresh,
            )
            addon_dirs = builder.build()
            deploy_addons(addon_dirs, target_dirs)
        return True
    except RuntimeError as e:
        print_error(str(e))
        return False


def watch_and_build(
    source_dir: Path, target_dirs: list[Path], force_refresh: bool = False
) -> int:
    """Watch for changes and rebuild automatically."""
    # Load ignore patterns
    gitignore_path = source_dir / ".gitignore"
    if gitignore_path.exists():
        ignore_matcher = IgnoreMatcher.from_gitignore(gitignore_path)
    else:
        pkgmeta_path = source_dir / ".pkgmeta"
        if pkgmeta_path.exists():
            pkgmeta = parse_pkgmeta(pkgmeta_path)
            ignore_matcher = IgnoreMatcher(pkgmeta.ignore, source_dir)
        else:
            ignore_matcher = IgnoreMatcher([], source_dir)

    # Initial build
    if not build_and_deploy(source_dir, target_dirs, force_refresh):
        print_error("Initial build failed. Watching for changes...")

    # Set up file watcher
    event_handler = AddonChangeHandler(source_dir, ignore_matcher)
    observer = Observer()
    observer.schedule(event_handler, str(source_dir), recursive=True)
    observer.start()

    status("Watching", "for changes... (Ctrl+C to stop)")

    try:
        while True:
            time.sleep(0.1)  # Check every 100ms

            if event_handler.should_rebuild():
                print()
                build_and_deploy(source_dir, target_dirs, force_refresh=False)
                event_handler.reset()
                status("Watching", "for changes... (Ctrl+C to stop)")

    except KeyboardInterrupt:
        print()
        status("Stopped", "")
        observer.stop()

    observer.join()
    return 0


def main() -> int:
    args = parse_args()
    cache = ExternalsCache()

    # Handle cache management commands
    if args.clear_cache:
        cache.clear_all()
        status("Cleared", "cache")
        return 0

    if args.cache_info:
        info = cache.get_cache_info()
        print(f"Cache location: {EXTERNALS_CACHE_DIR}")
        print(f"Total size: {format_size(info['total_size'])}")
        print(f"Entries: {info['entry_count']}")
        if info["entries"]:
            print("\nCached externals:")
            for entry in sorted(info["entries"], key=lambda e: e.get("url", "")):
                url = entry.get("url", "unknown")
                size = format_size(entry["size"])
                print(f"  {entry['name']}: {size}")
                print(f"    {url}")
        return 0

    # Validate WOW_HOME environment
    wow_home_str = os.environ.get("WOW_HOME")
    if not wow_home_str:
        print_error(
            "WOW_HOME environment variable not set. Please set it to your World of Warcraft installation directory."
        )
        return 1

    wow_home = Path(wow_home_str)
    if not wow_home.exists():
        print_error(f'World of Warcraft directory "{wow_home.absolute()}" not found.')
        return 1

    target_dirs = get_target_dirs(wow_home, args.flavor, args.channel)
    if not target_dirs:
        print_error(
            "No WoW installations found for the specified flavor/channel combination."
        )
        return 1

    # Watch mode
    if args.watch:
        return watch_and_build(Path.cwd(), target_dirs, args.refresh_externals)

    # Single build mode
    if build_and_deploy(Path.cwd(), target_dirs, args.refresh_externals):
        return 0
    return 1


def cli() -> None:
    sys.exit(main())


if __name__ == "__main__":
    cli()
