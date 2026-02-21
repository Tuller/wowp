"""Tests for wowp addon builder."""

import json
import tempfile
import time
from pathlib import Path

import pytest

from wowp.cli import (
    AddonBuilder,
    ExternalsCache,
    ExternalFetcher,
    External,
    IgnoreMatcher,
    PkgMeta,
    VcsType,
    _detect_vcs_type,
    get_target_dirs,
    parse_pkgmeta,
)


# --- Helpers ---


def write_pkgmeta(tmpdir: Path, content: str) -> Path:
    path = tmpdir / ".pkgmeta"
    path.write_text(content)
    return path


def make_addon(tmpdir: Path, files: dict[str, str]) -> Path:
    """Create a fake addon directory with given files and content."""
    for rel_path, content in files.items():
        p = tmpdir / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return tmpdir


# --- PkgMeta Parsing ---


class TestParsePkgMeta:
    """Test .pkgmeta parsing per the BigWigsMods packager spec."""

    def test_simple_string_external(self, tmp_path):
        write_pkgmeta(tmp_path, """
externals:
  Libs/LibStub: https://repos.wowace.com/wow/libstub/trunk
""")
        result = parse_pkgmeta(tmp_path / ".pkgmeta")
        assert len(result.externals) == 1
        ext = result.externals[0]
        assert ext.dest_path == "Libs/LibStub"
        assert ext.url == "https://repos.wowace.com/wow/libstub/trunk"
        assert ext.vcs_type == VcsType.SVN
        assert ext.tag is None

    def test_expanded_external_with_tag(self, tmp_path):
        write_pkgmeta(tmp_path, """
externals:
  Libs/LibStub:
    url: https://repos.curseforge.com/wow/libstub/trunk
    tag: 1.0
""")
        result = parse_pkgmeta(tmp_path / ".pkgmeta")
        ext = result.externals[0]
        assert ext.tag == "1.0"  # YAML parses 1.0 as float; must be string
        assert ext.branch is None
        assert ext.commit is None

    def test_expanded_external_with_branch(self, tmp_path):
        write_pkgmeta(tmp_path, """
externals:
  Libs/LibStub:
    url: https://github.com/user/lib
    branch: develop
""")
        ext = parse_pkgmeta(tmp_path / ".pkgmeta").externals[0]
        assert ext.branch == "develop"
        assert ext.vcs_type == VcsType.GIT

    def test_expanded_external_with_commit(self, tmp_path):
        write_pkgmeta(tmp_path, """
externals:
  Libs/LibStub:
    url: https://github.com/user/lib
    commit: abc123def456
""")
        ext = parse_pkgmeta(tmp_path / ".pkgmeta").externals[0]
        assert ext.commit == "abc123def456"

    def test_explicit_type_overrides_detection(self, tmp_path):
        write_pkgmeta(tmp_path, """
externals:
  Libs/LibStub:
    url: https://example.com/repo
    type: svn
""")
        ext = parse_pkgmeta(tmp_path / ".pkgmeta").externals[0]
        assert ext.vcs_type == VcsType.SVN

    def test_multiple_version_specifiers_rejected(self, tmp_path):
        write_pkgmeta(tmp_path, """
externals:
  Libs/LibStub:
    url: https://repos.com/lib
    tag: 1.0
    branch: develop
""")
        with pytest.raises(ValueError, match="Cannot specify multiple"):
            parse_pkgmeta(tmp_path / ".pkgmeta")

    def test_empty_url_rejected(self, tmp_path):
        write_pkgmeta(tmp_path, """
externals:
  Libs/LibStub:
    url: ""
    tag: 1.0
""")
        with pytest.raises(ValueError, match="'url' field is required"):
            parse_pkgmeta(tmp_path / ".pkgmeta")

    def test_missing_url_rejected(self, tmp_path):
        write_pkgmeta(tmp_path, """
externals:
  Libs/LibStub:
    tag: 1.0
""")
        with pytest.raises(ValueError, match="'url' field is required"):
            parse_pkgmeta(tmp_path / ".pkgmeta")

    def test_package_as(self, tmp_path):
        write_pkgmeta(tmp_path, """
package-as: MyAddon
""")
        result = parse_pkgmeta(tmp_path / ".pkgmeta")
        assert result.package_as == "MyAddon"

    def test_move_folders(self, tmp_path):
        write_pkgmeta(tmp_path, """
package-as: MyAddon
move-folders:
  MyAddon/Libs: MyAddon_Libs
  MyAddon/Options: MyAddon_Options
""")
        result = parse_pkgmeta(tmp_path / ".pkgmeta")
        assert result.move_folders == {
            "MyAddon/Libs": "MyAddon_Libs",
            "MyAddon/Options": "MyAddon_Options",
        }

    def test_ignore_list(self, tmp_path):
        write_pkgmeta(tmp_path, """
ignore:
  - CHANGELOG.md
  - tests
  - "*.txt"
""")
        result = parse_pkgmeta(tmp_path / ".pkgmeta")
        assert result.ignore == ["CHANGELOG.md", "tests", "*.txt"]

    def test_empty_pkgmeta(self, tmp_path):
        write_pkgmeta(tmp_path, "")
        result = parse_pkgmeta(tmp_path / ".pkgmeta")
        assert result.externals == []
        assert result.move_folders == {}
        assert result.ignore == []
        assert result.package_as == ""

    def test_multiple_externals(self, tmp_path):
        write_pkgmeta(tmp_path, """
externals:
  Libs/LibStub: https://repos.wowace.com/wow/libstub/trunk
  Libs/CallbackHandler:
    url: https://repos.wowace.com/wow/callbackhandler/trunk
    tag: 1.0
  Libs/AceAddon:
    url: https://github.com/user/ace
    branch: main
""")
        result = parse_pkgmeta(tmp_path / ".pkgmeta")
        assert len(result.externals) == 3


# --- VCS Type Detection ---


class TestVcsTypeDetection:
    def test_git_urls(self):
        assert _detect_vcs_type("https://github.com/user/repo") == VcsType.GIT
        assert _detect_vcs_type("https://gitlab.com/user/repo") == VcsType.GIT
        assert _detect_vcs_type("https://example.com/repo.git") == VcsType.GIT
        assert _detect_vcs_type("https://repos.wowace.com/wow/lib") == VcsType.GIT
        assert _detect_vcs_type("https://repos.curseforge.com/wow/lib") == VcsType.GIT

    def test_svn_urls(self):
        assert _detect_vcs_type("https://example.com/repo/trunk") == VcsType.SVN
        assert _detect_vcs_type("https://example.com/repo/trunk/") == VcsType.SVN
        assert _detect_vcs_type("https://example.com/repo/tags/1.0") == VcsType.SVN
        assert _detect_vcs_type("https://example.com/repo/branches/dev") == VcsType.SVN

    def test_unknown_defaults_to_git(self):
        assert _detect_vcs_type("https://example.com/something") == VcsType.GIT


# --- SVN URL Building ---


class TestSvnUrlBuilding:
    @pytest.fixture
    def fetcher(self):
        return ExternalFetcher(ExternalsCache())

    def test_trunk_to_tag(self, fetcher):
        assert fetcher._build_svn_url("https://repos.com/lib/trunk", tag="1.0") == \
            "https://repos.com/lib/tags/1.0"

    def test_trunk_to_branch(self, fetcher):
        assert fetcher._build_svn_url("https://repos.com/lib/trunk", branch="dev") == \
            "https://repos.com/lib/branches/dev"

    def test_preserves_subdirectory(self, fetcher):
        assert fetcher._build_svn_url("https://repos.com/lib/trunk/Sub", tag="1.0") == \
            "https://repos.com/lib/tags/1.0/Sub"

    def test_replaces_existing_tag(self, fetcher):
        assert fetcher._build_svn_url("https://repos.com/lib/tags/old", tag="new") == \
            "https://repos.com/lib/tags/new"

    def test_no_marker_appends(self, fetcher):
        assert fetcher._build_svn_url("https://repos.com/lib", tag="1.0") == \
            "https://repos.com/lib/tags/1.0"

    def test_unchanged_without_tag_or_branch(self, fetcher):
        url = "https://repos.com/lib/tags/1.0"
        assert fetcher._build_svn_url(url) == url

    def test_svn_non_numeric_revision_rejected(self, fetcher):
        ext = External(
            dest_path="Libs/LibStub",
            url="https://repos.com/lib/trunk",
            vcs_type=VcsType.SVN,
            commit="not-a-number",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(ValueError, match="SVN revision must be numeric"):
                fetcher._fetch_svn(ext, Path(tmpdir) / "out")


# --- Cache Key Generation ---


class TestCacheKeys:
    def test_different_tags_different_keys(self):
        cache = ExternalsCache()
        ext1 = External("Libs/X", "https://repos.com/lib", VcsType.GIT, tag="1.0")
        ext2 = External("Libs/X", "https://repos.com/lib", VcsType.GIT, tag="2.0")
        assert cache.get_cache_key(ext1) != cache.get_cache_key(ext2)

    def test_tag_vs_branch_different_keys(self):
        cache = ExternalsCache()
        ext_tag = External("Libs/X", "https://repos.com/lib", VcsType.GIT, tag="v1")
        ext_branch = External("Libs/X", "https://repos.com/lib", VcsType.GIT, branch="v1")
        assert cache.get_cache_key(ext_tag) != cache.get_cache_key(ext_branch)

    def test_no_version_specifier_has_no_suffix(self):
        cache = ExternalsCache()
        ext = External("Libs/X", "https://repos.com/lib/trunk", VcsType.SVN)
        key = cache.get_cache_key(ext)
        assert "_tag-" not in key
        assert "_branch-" not in key
        assert "_commit-" not in key


# --- Cache Lifecycle ---


class TestCacheLifecycle:
    def test_put_and_get(self, tmp_path):
        cache = ExternalsCache(cache_dir=tmp_path / "cache")
        ext = External("Libs/X", "https://repos.com/lib", VcsType.GIT, tag="1.0")

        # Create source content
        src = tmp_path / "src"
        src.mkdir()
        (src / "init.lua").write_text("-- hello")

        cache.put(ext, src)
        result = cache.get(ext)
        assert result is not None
        assert (result / "init.lua").read_text() == "-- hello"

    def test_tagged_external_never_stale(self, tmp_path):
        cache = ExternalsCache(cache_dir=tmp_path / "cache")
        ext = External("Libs/X", "https://repos.com/lib", VcsType.GIT, tag="1.0")

        src = tmp_path / "src"
        src.mkdir()
        (src / "file.lua").write_text("x")
        cache.put(ext, src)

        # Backdate the metadata
        meta_path = cache.get_meta_path(ext)
        meta = json.loads(meta_path.read_text())
        meta["fetched_at"] = time.time() - 100 * 3600  # 100 hours ago
        meta_path.write_text(json.dumps(meta))

        assert not cache.is_stale(ext)

    def test_trunk_external_goes_stale(self, tmp_path):
        cache = ExternalsCache(cache_dir=tmp_path / "cache")
        ext = External("Libs/X", "https://repos.com/lib/trunk", VcsType.SVN)

        src = tmp_path / "src"
        src.mkdir()
        (src / "file.lua").write_text("x")
        cache.put(ext, src)

        # Backdate the metadata
        meta_path = cache.get_meta_path(ext)
        meta = json.loads(meta_path.read_text())
        meta["fetched_at"] = time.time() - 100 * 3600
        meta_path.write_text(json.dumps(meta))

        assert cache.is_stale(ext)

    def test_invalidate(self, tmp_path):
        cache = ExternalsCache(cache_dir=tmp_path / "cache")
        ext = External("Libs/X", "https://repos.com/lib", VcsType.GIT, tag="1.0")

        src = tmp_path / "src"
        src.mkdir()
        (src / "file.lua").write_text("x")
        cache.put(ext, src)

        cache.invalidate(ext)
        assert cache.get(ext) is None

    def test_clear_all(self, tmp_path):
        cache = ExternalsCache(cache_dir=tmp_path / "cache")
        ext = External("Libs/X", "https://repos.com/lib", VcsType.GIT, tag="1.0")

        src = tmp_path / "src"
        src.mkdir()
        (src / "file.lua").write_text("x")
        cache.put(ext, src)

        cache.clear_all()
        assert not (tmp_path / "cache").exists()


# --- IgnoreMatcher ---


class TestIgnoreMatcher:
    def test_hidden_files_always_ignored(self, tmp_path):
        matcher = IgnoreMatcher([], tmp_path)
        assert matcher.is_ignored(tmp_path / ".git" / "config")
        assert matcher.is_ignored(tmp_path / ".gitignore")
        assert not matcher.is_ignored(tmp_path / "MyAddon.lua")

    def test_simple_filename_pattern(self, tmp_path):
        matcher = IgnoreMatcher(["*.txt"], tmp_path)
        assert matcher.is_ignored(tmp_path / "README.txt")
        assert matcher.is_ignored(tmp_path / "sub" / "notes.txt")
        assert not matcher.is_ignored(tmp_path / "MyAddon.lua")

    def test_directory_only_pattern(self, tmp_path):
        matcher = IgnoreMatcher(["build/"], tmp_path)
        assert matcher.is_ignored(tmp_path / "build", is_dir=True)
        assert not matcher.is_ignored(tmp_path / "build", is_dir=False)

    def test_anchored_pattern(self, tmp_path):
        matcher = IgnoreMatcher(["/README.md"], tmp_path)
        assert matcher.is_ignored(tmp_path / "README.md")
        assert not matcher.is_ignored(tmp_path / "sub" / "README.md")

    def test_unanchored_pattern_matches_any_depth(self, tmp_path):
        matcher = IgnoreMatcher(["*.log"], tmp_path)
        assert matcher.is_ignored(tmp_path / "error.log")
        assert matcher.is_ignored(tmp_path / "deep" / "nested" / "error.log")

    def test_negation_pattern(self, tmp_path):
        matcher = IgnoreMatcher(["*.txt", "!important.txt"], tmp_path)
        assert matcher.is_ignored(tmp_path / "junk.txt")
        assert not matcher.is_ignored(tmp_path / "important.txt")

    def test_doublestar_pattern(self, tmp_path):
        matcher = IgnoreMatcher(["**/test_*.py"], tmp_path)
        assert matcher.is_ignored(tmp_path / "test_foo.py")
        assert matcher.is_ignored(tmp_path / "tests" / "test_foo.py")
        assert not matcher.is_ignored(tmp_path / "foo.py")

    def test_path_outside_base_not_ignored(self, tmp_path):
        matcher = IgnoreMatcher(["*"], tmp_path / "sub")
        assert not matcher.is_ignored(tmp_path / "outside.txt")

    def test_from_gitignore(self, tmp_path):
        (tmp_path / ".gitignore").write_text("*.pyc\n__pycache__/\n")
        matcher = IgnoreMatcher.from_gitignore(tmp_path / ".gitignore")
        assert matcher.is_ignored(tmp_path / "module.pyc")
        assert matcher.is_ignored(tmp_path / "__pycache__", is_dir=True)
        assert not matcher.is_ignored(tmp_path / "module.py")

    def test_filter_paths(self, tmp_path):
        matcher = IgnoreMatcher(["*.bak"], tmp_path)
        paths = [tmp_path / "a.lua", tmp_path / "b.bak", tmp_path / "c.toc"]
        result = matcher.filter_paths(paths)
        assert [p.name for p in result] == ["a.lua", "c.toc"]

    def test_comment_and_blank_lines_skipped(self, tmp_path):
        matcher = IgnoreMatcher(["# comment", "", "  ", "*.log"], tmp_path)
        assert len(matcher.rules) == 1
        assert matcher.is_ignored(tmp_path / "debug.log")


# --- AddonBuilder ---


class TestAddonBuilder:
    def test_copies_files_to_package_dir(self, tmp_path):
        source = tmp_path / "source"
        output = tmp_path / "output"
        make_addon(source, {
            "MyAddon.lua": "print('hello')",
            "MyAddon.toc": "## Title: MyAddon",
            ".pkgmeta": "package-as: MyAddon",
        })

        builder = AddonBuilder(source, output)
        addon_dirs = builder.build()

        assert len(addon_dirs) == 1
        assert addon_dirs[0].name == "MyAddon"
        assert (addon_dirs[0] / "MyAddon.lua").read_text() == "print('hello')"
        assert (addon_dirs[0] / "MyAddon.toc").exists()

    def test_respects_gitignore(self, tmp_path):
        source = tmp_path / "source"
        output = tmp_path / "output"
        make_addon(source, {
            "MyAddon.lua": "print('hello')",
            ".gitignore": "*.bak\n",
            ".pkgmeta": "package-as: MyAddon",
            "backup.bak": "junk",
        })

        builder = AddonBuilder(source, output)
        addon_dirs = builder.build()

        assert (addon_dirs[0] / "MyAddon.lua").exists()
        assert not (addon_dirs[0] / "backup.bak").exists()

    def test_keyword_replacement_in_toc(self, tmp_path):
        source = tmp_path / "source"
        output = tmp_path / "output"
        make_addon(source, {
            "MyAddon.toc": "## Version: @project-version@",
            ".pkgmeta": "package-as: MyAddon",
        })

        builder = AddonBuilder(source, output)
        addon_dirs = builder.build()

        content = (addon_dirs[0] / "MyAddon.toc").read_text()
        assert "@project-version@" not in content
        assert "## Version:" in content

    def test_no_pkgmeta_uses_dirname(self, tmp_path):
        source = tmp_path / "MyAddon"
        output = tmp_path / "output"
        make_addon(source, {
            "MyAddon.lua": "print('hello')",
        })

        builder = AddonBuilder(source, output)
        addon_dirs = builder.build()

        assert len(addon_dirs) == 1
        assert addon_dirs[0].name == "MyAddon"

    def test_move_folders(self, tmp_path):
        source = tmp_path / "source"
        output = tmp_path / "output"
        make_addon(source, {
            "Core.lua": "-- core",
            "Options/Options.lua": "-- options",
            ".pkgmeta": """
package-as: MyAddon
move-folders:
  MyAddon/Options: MyAddon_Options
""",
        })

        builder = AddonBuilder(source, output)
        addon_dirs = builder.build()

        names = sorted(d.name for d in addon_dirs)
        assert "MyAddon_Options" in names
        assert (output / "MyAddon_Options" / "Options.lua").read_text() == "-- options"

    def test_hidden_files_excluded(self, tmp_path):
        source = tmp_path / "source"
        output = tmp_path / "output"
        make_addon(source, {
            "MyAddon.lua": "print('hello')",
            ".pkgmeta": "package-as: MyAddon",
            ".secret": "password123",
        })

        builder = AddonBuilder(source, output)
        addon_dirs = builder.build()

        assert not (addon_dirs[0] / ".secret").exists()
        assert not (addon_dirs[0] / ".pkgmeta").exists()

    def test_toc_bom_handling(self, tmp_path):
        source = tmp_path / "source"
        output = tmp_path / "output"
        source.mkdir(parents=True)
        (source / ".pkgmeta").write_text("package-as: MyAddon")
        # Write TOC with BOM
        toc_content = "\ufeff## Version: @project-version@\n"
        (source / "MyAddon.toc").write_bytes(toc_content.encode("utf-8-sig"))

        builder = AddonBuilder(source, output)
        addon_dirs = builder.build()

        content = (addon_dirs[0] / "MyAddon.toc").read_text()
        assert "@project-version@" not in content


# --- Target Directory Resolution ---


class TestTargetDirs:
    def test_finds_existing_dirs(self, tmp_path):
        retail = tmp_path / "_retail_" / "Interface" / "AddOns"
        retail.mkdir(parents=True)
        classic = tmp_path / "_classic_" / "Interface" / "AddOns"
        classic.mkdir(parents=True)

        result = get_target_dirs(tmp_path, ["mainline"], ["live"])
        assert retail in result

    def test_skips_missing_dirs(self, tmp_path):
        # No directories created
        result = get_target_dirs(tmp_path, ["mainline"], ["live"])
        assert result == []

    def test_classic_live_includes_all_variants(self, tmp_path):
        for name in ["classic", "classic_era", "anniversary"]:
            (tmp_path / f"_{name}_" / "Interface" / "AddOns").mkdir(parents=True)

        result = get_target_dirs(tmp_path, ["classic"], ["live"])
        assert len(result) == 3

    def test_multiple_flavors_and_channels(self, tmp_path):
        for name in ["retail", "ptr"]:
            (tmp_path / f"_{name}_" / "Interface" / "AddOns").mkdir(parents=True)

        result = get_target_dirs(tmp_path, ["mainline"], ["live", "ptr"])
        assert len(result) >= 2
