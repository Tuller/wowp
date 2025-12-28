#!/usr/bin/env python3
"""
Unit tests for wowp.py

Tests for pkgmeta parsing, SVN URL building, and related helper functions.
"""

import pytest
import tempfile
import yaml
from pathlib import Path
from wowp import (
    parse_pkgmeta,
    _parse_external,
    _detect_vcs_type,
    External,
    VcsType,
    ExternalFetcher,
    ExternalsCache
)


class TestPkgMetaParser:
    """Test the pkgmeta parsing functionality."""

    def test_parse_simple_string_external(self):
        """Test parsing a simple string URL external."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkgmeta_path = Path(tmpdir) / ".pkgmeta"
            pkgmeta_content = """
externals:
  Libs/LibStub: https://repos.wowace.com/wow/libstub/trunk
"""
            pkgmeta_path.write_text(pkgmeta_content)

            result = parse_pkgmeta(pkgmeta_path)

            assert len(result.externals) == 1
            ext = result.externals[0]
            assert ext.dest_path == "Libs/LibStub"
            assert ext.url == "https://repos.wowace.com/wow/libstub/trunk"
            assert ext.vcs_type == VcsType.SVN  # URLs with /trunk are SVN
            assert ext.tag is None
            assert ext.branch is None
            assert ext.commit is None

    def test_parse_expanded_format_with_tag(self):
        """Test parsing expanded format with tag field."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkgmeta_path = Path(tmpdir) / ".pkgmeta"
            pkgmeta_content = """
externals:
  Libs/LibStub:
    url: https://repos.wowace.com/wow/libstub
    tag: 1.0
"""
            pkgmeta_path.write_text(pkgmeta_content)

            result = parse_pkgmeta(pkgmeta_path)

            assert len(result.externals) == 1
            ext = result.externals[0]
            assert ext.dest_path == "Libs/LibStub"
            assert ext.url == "https://repos.wowace.com/wow/libstub"
            assert ext.tag == "1.0"  # Now properly converted from YAML number
            assert ext.branch is None
            assert ext.commit is None

    def test_parse_expanded_format_with_branch(self):
        """Test parsing expanded format with branch field."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkgmeta_path = Path(tmpdir) / ".pkgmeta"
            pkgmeta_content = """
externals:
  Libs/LibStub:
    url: https://repos.wowace.com/wow/libstub
    branch: develop
"""
            pkgmeta_path.write_text(pkgmeta_content)

            result = parse_pkgmeta(pkgmeta_path)

            assert len(result.externals) == 1
            ext = result.externals[0]
            assert ext.dest_path == "Libs/LibStub"
            assert ext.url == "https://repos.wowace.com/wow/libstub"
            assert ext.tag is None
            assert ext.branch == "develop"
            assert ext.commit is None

    def test_parse_expanded_format_with_commit(self):
        """Test parsing expanded format with commit field."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkgmeta_path = Path(tmpdir) / ".pkgmeta"
            pkgmeta_content = """
externals:
  Libs/LibStub:
    url: https://repos.wowace.com/wow/libstub/tags/1.0
    commit: 12345
"""
            pkgmeta_path.write_text(pkgmeta_content)

            result = parse_pkgmeta(pkgmeta_path)

            assert len(result.externals) == 1
            ext = result.externals[0]
            assert ext.dest_path == "Libs/LibStub"
            assert ext.url == "https://repos.wowace.com/wow/libstub/tags/1.0"
            assert ext.tag is None
            assert ext.branch is None
            assert ext.commit == "12345"  # Now properly converted from YAML number

    def test_parse_explicit_vcs_type_svn(self):
        """Test parsing with explicit VCS type declaration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkgmeta_path = Path(tmpdir) / ".pkgmeta"
            pkgmeta_content = """
externals:
  Libs/LibStub:
    url: https://example.com/svn-repo
    type: svn
    tag: 1.0
"""
            pkgmeta_path.write_text(pkgmeta_content)

            result = parse_pkgmeta(pkgmeta_path)

            assert len(result.externals) == 1
            ext = result.externals[0]
            assert ext.dest_path == "Libs/LibStub"
            assert ext.url == "https://example.com/svn-repo"
            assert ext.vcs_type == VcsType.SVN
            assert ext.tag == "1.0"  # Now properly converted from YAML number

    def test_parse_explicit_vcs_type_git(self):
        """Test parsing with explicit Git type."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkgmeta_path = Path(tmpdir) / ".pkgmeta"
            pkgmeta_content = """
externals:
  Libs/LibStub:
    url: https://example.com/git-repo
    type: git
    tag: v1.0
"""
            pkgmeta_path.write_text(pkgmeta_content)

            result = parse_pkgmeta(pkgmeta_path)

            assert len(result.externals) == 1
            ext = result.externals[0]
            assert ext.vcs_type == VcsType.GIT

    def test_vcs_type_detection_git(self):
        """Test automatic Git detection."""
        assert _detect_vcs_type("https://github.com/user/repo") == VcsType.GIT
        assert _detect_vcs_type("https://gitlab.com/user/repo") == VcsType.GIT
        assert _detect_vcs_type("https://example.com/repo.git") == VcsType.GIT
        assert _detect_vcs_type("https://repos.wowace.com/wow/lib") == VcsType.GIT
        assert _detect_vcs_type("https://repos.curseforge.com/wow/lib") == VcsType.GIT

    def test_vcs_type_detection_svn(self):
        """Test automatic SVN detection."""
        assert _detect_vcs_type("https://example.com/repo/trunk/") == VcsType.SVN
        assert _detect_vcs_type("https://example.com/repo/trunk") == VcsType.SVN  # Without trailing slash
        assert _detect_vcs_type("https://repos.curseforge.com/wow/libstub/trunk") == VcsType.SVN
        assert _detect_vcs_type("https://example.com/repo/tags/1.0") == VcsType.SVN
        assert _detect_vcs_type("https://example.com/repo/branches/dev") == VcsType.SVN


class TestSVNURLBuilding:
    """Test the SVN URL building functionality."""

    @pytest.fixture
    def fetcher(self):
        """Create an ExternalFetcher instance for testing."""
        cache = ExternalsCache()
        return ExternalFetcher(cache)

    def test_build_svn_url_trunk_to_tag(self, fetcher):
        """Test converting /trunk/ to /tags/{tag}."""
        result = fetcher._build_svn_url("https://repos.com/lib/trunk", tag="1.0")
        assert result == "https://repos.com/lib/tags/1.0"

    def test_build_svn_url_trunk_with_trailing_slash(self, fetcher):
        """Test converting /trunk/ with trailing slash."""
        result = fetcher._build_svn_url("https://repos.com/lib/trunk/", tag="1.0")
        assert result == "https://repos.com/lib/tags/1.0"

    def test_build_svn_url_trunk_with_subdirectory(self, fetcher):
        """Test converting /trunk/SubDir to /tags/{tag}/SubDir."""
        result = fetcher._build_svn_url("https://repos.com/lib/trunk/Sub", tag="1.0")
        assert result == "https://repos.com/lib/tags/1.0/Sub"

    def test_build_svn_url_tag_replacement(self, fetcher):
        """Test replacing one tag with another."""
        result = fetcher._build_svn_url("https://repos.com/lib/tags/old", tag="new")
        assert result == "https://repos.com/lib/tags/new"

    def test_build_svn_url_branch_to_tag(self, fetcher):
        """Test converting /branches/{branch} to /tags/{tag}."""
        result = fetcher._build_svn_url("https://repos.com/lib/branches/feat", tag="1.0")
        assert result == "https://repos.com/lib/tags/1.0"

    def test_build_svn_url_base_url_to_tag(self, fetcher):
        """Test appending /tags/{tag} to base URL."""
        result = fetcher._build_svn_url("https://repos.com/lib", tag="1.0")
        assert result == "https://repos.com/lib/tags/1.0"

    def test_build_svn_url_trunk_to_branch(self, fetcher):
        """Test converting /trunk to /branches/{branch}."""
        result = fetcher._build_svn_url("https://repos.com/lib/trunk", branch="dev")
        assert result == "https://repos.com/lib/branches/dev"

    def test_build_svn_url_no_tag_no_branch(self, fetcher):
        """Test that URL is unchanged when no tag/branch specified."""
        url = "https://repos.com/lib/tags/1.0"
        result = fetcher._build_svn_url(url)
        assert result == url

    def test_build_svn_url_deep_subdirectory(self, fetcher):
        """Test preserving deep subdirectory paths."""
        result = fetcher._build_svn_url("https://repos.com/lib/trunk/a/b/c", tag="1.0")
        assert result == "https://repos.com/lib/tags/1.0/a/b/c"

    def test_build_svn_url_tag_with_subdirectory(self, fetcher):
        """Test tag replacement with subdirectory."""
        result = fetcher._build_svn_url("https://repos.com/lib/tags/old/Sub", tag="new")
        assert result == "https://repos.com/lib/tags/new/Sub"

    def test_build_svn_url_branches_with_subdirectory(self, fetcher):
        """Test branch to tag conversion with subdirectory."""
        result = fetcher._build_svn_url("https://repos.com/lib/branches/feat/Sub", tag="1.0")
        assert result == "https://repos.com/lib/tags/1.0/Sub"


class TestExternalsCache:
    """Test caching behavior."""

    @pytest.fixture
    def cache(self):
        """Create an ExternalsCache instance for testing."""
        return ExternalsCache()

    def test_cache_key_includes_tag(self, cache):
        """Test that cache key includes tag to prevent collisions."""
        ext1 = External(
            dest_path="Libs/LibStub",
            url="https://repos.com/lib",
            vcs_type=VcsType.GIT,
            tag="1.0"
        )
        ext2 = External(
            dest_path="Libs/LibStub",
            url="https://repos.com/lib",
            vcs_type=VcsType.GIT,
            tag="2.0"
        )

        key1 = cache.get_cache_key(ext1)
        key2 = cache.get_cache_key(ext2)

        # Different tags should produce different cache keys
        assert key1 != key2
        assert "_tag-1.0" in key1
        assert "_tag-2.0" in key2

    def test_cache_key_includes_branch(self, cache):
        """Test that cache key includes branch."""
        ext1 = External(
            dest_path="Libs/LibStub",
            url="https://repos.com/lib",
            vcs_type=VcsType.GIT,
            branch="main"
        )
        ext2 = External(
            dest_path="Libs/LibStub",
            url="https://repos.com/lib",
            vcs_type=VcsType.GIT,
            branch="develop"
        )

        key1 = cache.get_cache_key(ext1)
        key2 = cache.get_cache_key(ext2)

        # Different branches should produce different cache keys
        assert key1 != key2
        assert "_branch-main" in key1
        assert "_branch-develop" in key2

    def test_cache_key_includes_commit(self, cache):
        """Test that cache key includes commit."""
        ext1 = External(
            dest_path="Libs/LibStub",
            url="https://repos.com/lib",
            vcs_type=VcsType.GIT,
            commit="abc123"
        )
        ext2 = External(
            dest_path="Libs/LibStub",
            url="https://repos.com/lib",
            vcs_type=VcsType.GIT,
            commit="def456"
        )

        key1 = cache.get_cache_key(ext1)
        key2 = cache.get_cache_key(ext2)

        # Different commits should produce different cache keys
        assert key1 != key2
        assert "_commit-abc123" in key1
        assert "_commit-def456" in key2

    def test_cache_key_no_version_specifier(self, cache):
        """Test cache key for external without tag/branch/commit."""
        ext = External(
            dest_path="Libs/LibStub",
            url="https://repos.com/lib/trunk",
            vcs_type=VcsType.SVN
        )

        key = cache.get_cache_key(ext)

        # No version specifier should not have any suffix
        assert "_tag-" not in key
        assert "_branch-" not in key
        assert "_commit-" not in key


class TestSVNHelperFunctions:
    """Test SVN helper functions."""

    @pytest.fixture
    def fetcher(self):
        """Create an ExternalFetcher instance for testing."""
        cache = ExternalsCache()
        return ExternalFetcher(cache)

    def test_has_svn_marker_trunk(self, fetcher):
        """Test detecting /trunk marker."""
        assert fetcher._has_svn_marker("https://repos.com/lib/trunk") is True
        assert fetcher._has_svn_marker("https://repos.com/lib/trunk/") is True
        assert fetcher._has_svn_marker("https://repos.com/lib/trunk/Sub") is True

    def test_has_svn_marker_tags(self, fetcher):
        """Test detecting /tags/ marker."""
        assert fetcher._has_svn_marker("https://repos.com/lib/tags/1.0") is True
        assert fetcher._has_svn_marker("https://repos.com/lib/tags/v2.0/Sub") is True

    def test_has_svn_marker_branches(self, fetcher):
        """Test detecting /branches/ marker."""
        assert fetcher._has_svn_marker("https://repos.com/lib/branches/dev") is True
        assert fetcher._has_svn_marker("https://repos.com/lib/branches/feature/Sub") is True

    def test_has_svn_marker_none(self, fetcher):
        """Test detecting no marker."""
        assert fetcher._has_svn_marker("https://repos.com/lib") is False
        assert fetcher._has_svn_marker("https://repos.com/lib/") is False

    def test_get_svn_base_url_trunk(self, fetcher):
        """Test extracting base URL with /trunk."""
        result = fetcher._get_svn_base_url("https://repos.com/lib/trunk")
        assert result == "https://repos.com/lib/trunk"

        result = fetcher._get_svn_base_url("https://repos.com/lib/trunk/Sub")
        assert result == "https://repos.com/lib/trunk"

    def test_get_svn_base_url_tags(self, fetcher):
        """Test that tags return None (grouping disabled)."""
        # Grouping optimization disabled for tags to prevent version conflicts
        result = fetcher._get_svn_base_url("https://repos.com/lib/tags/1.0")
        assert result is None

        result = fetcher._get_svn_base_url("https://repos.com/lib/tags/1.0/Sub")
        assert result is None

    def test_get_svn_base_url_branches(self, fetcher):
        """Test that branches return None (grouping disabled)."""
        # Grouping optimization disabled for branches to prevent version conflicts
        result = fetcher._get_svn_base_url("https://repos.com/lib/branches/dev")
        assert result is None

        result = fetcher._get_svn_base_url("https://repos.com/lib/branches/feat/Sub")
        assert result is None

    def test_get_svn_base_url_none(self, fetcher):
        """Test extracting base URL with no marker."""
        result = fetcher._get_svn_base_url("https://repos.com/lib")
        assert result is None

    def test_get_svn_subdir_no_subdir(self, fetcher):
        """Test extracting subdirectory when none exists."""
        result = fetcher._get_svn_subdir("https://repos.com/lib/trunk")
        assert result == ""

        # For tags/branches, the version is part of the subdirectory for grouping optimization
        result = fetcher._get_svn_subdir("https://repos.com/lib/tags/1.0")
        assert result == "1.0"

    def test_get_svn_subdir_with_subdir(self, fetcher):
        """Test extracting subdirectory."""
        result = fetcher._get_svn_subdir("https://repos.com/lib/trunk/LibStub")
        assert result == "LibStub"

        result = fetcher._get_svn_subdir("https://repos.com/lib/tags/1.0/LibStub/Core")
        assert result == "1.0/LibStub/Core"

        result = fetcher._get_svn_subdir("https://repos.com/lib/branches/dev/Sub/Deep")
        assert result == "dev/Sub/Deep"


class TestGitExternals:
    """Test Git repository handling."""

    def test_parse_git_external_with_tag(self):
        """Test parsing Git external with tag."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkgmeta_path = Path(tmpdir) / ".pkgmeta"
            pkgmeta_content = """
externals:
  Libs/LibStub:
    url: https://github.com/user/lib
    tag: v1.0.0
"""
            pkgmeta_path.write_text(pkgmeta_content)

            result = parse_pkgmeta(pkgmeta_path)

            assert len(result.externals) == 1
            ext = result.externals[0]
            assert ext.vcs_type == VcsType.GIT
            assert ext.tag == "v1.0.0"
            assert ext.branch is None
            assert ext.commit is None

    def test_parse_git_external_with_branch(self):
        """Test parsing Git external with branch."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkgmeta_path = Path(tmpdir) / ".pkgmeta"
            pkgmeta_content = """
externals:
  Libs/LibStub:
    url: https://github.com/user/lib
    branch: develop
"""
            pkgmeta_path.write_text(pkgmeta_content)

            result = parse_pkgmeta(pkgmeta_path)

            assert len(result.externals) == 1
            ext = result.externals[0]
            assert ext.vcs_type == VcsType.GIT
            assert ext.tag is None
            assert ext.branch == "develop"
            assert ext.commit is None

    def test_parse_git_external_with_commit(self):
        """Test parsing Git external with commit hash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkgmeta_path = Path(tmpdir) / ".pkgmeta"
            pkgmeta_content = """
externals:
  Libs/LibStub:
    url: https://github.com/user/lib
    commit: abc123def456
"""
            pkgmeta_path.write_text(pkgmeta_content)

            result = parse_pkgmeta(pkgmeta_path)

            assert len(result.externals) == 1
            ext = result.externals[0]
            assert ext.vcs_type == VcsType.GIT
            assert ext.tag is None
            assert ext.branch is None
            assert ext.commit == "abc123def456"

    def test_parse_git_url_detection(self):
        """Test automatic Git VCS detection from URL patterns."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkgmeta_path = Path(tmpdir) / ".pkgmeta"
            pkgmeta_content = """
externals:
  Libs/GitHub: https://github.com/user/repo
  Libs/GitLab: https://gitlab.com/user/repo
  Libs/DotGit: https://example.com/repo.git
  Libs/WowAce: https://repos.wowace.com/wow/lib
  Libs/CurseForge: https://repos.curseforge.com/wow/lib
"""
            pkgmeta_path.write_text(pkgmeta_content)

            result = parse_pkgmeta(pkgmeta_path)

            assert len(result.externals) == 5
            for ext in result.externals:
                assert ext.vcs_type == VcsType.GIT

    def test_git_commit_allows_alphanumeric(self):
        """Test that Git commit hashes can be alphanumeric."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkgmeta_path = Path(tmpdir) / ".pkgmeta"
            pkgmeta_content = """
externals:
  Libs/LibStub:
    url: https://github.com/user/lib
    type: git
    commit: a1b2c3d4e5f6
"""
            pkgmeta_path.write_text(pkgmeta_content)

            result = parse_pkgmeta(pkgmeta_path)

            assert len(result.externals) == 1
            ext = result.externals[0]
            assert ext.vcs_type == VcsType.GIT
            assert ext.commit == "a1b2c3d4e5f6"

    def test_git_tag_vs_branch_different_cache_keys(self):
        """Test that same repo with different refs gets different cache keys."""
        cache = ExternalsCache()

        ext_tag = External(
            dest_path="Libs/LibStub",
            url="https://github.com/user/lib",
            vcs_type=VcsType.GIT,
            tag="v1.0"
        )

        ext_branch = External(
            dest_path="Libs/LibStub",
            url="https://github.com/user/lib",
            vcs_type=VcsType.GIT,
            branch="v1.0"  # Same name as tag but different type
        )

        key_tag = cache.get_cache_key(ext_tag)
        key_branch = cache.get_cache_key(ext_branch)

        # Even though the version name is the same, tag vs branch should be different
        assert key_tag != key_branch
        assert "_tag-v1.0" in key_tag
        assert "_branch-v1.0" in key_branch


class TestRealWorldScenarios:
    """Test real-world .pkgmeta scenarios."""

    def test_curseforge_libstub_with_tag(self):
        """Test the exact scenario from LibEditMode .pkgmeta."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkgmeta_path = Path(tmpdir) / ".pkgmeta"
            pkgmeta_content = """
externals:
  LibStub:
    url: https://repos.curseforge.com/wow/libstub/trunk
    tag: 1.0
"""
            pkgmeta_path.write_text(pkgmeta_content)

            result = parse_pkgmeta(pkgmeta_path)

            assert len(result.externals) == 1
            ext = result.externals[0]
            assert ext.dest_path == "LibStub"
            assert ext.url == "https://repos.curseforge.com/wow/libstub/trunk"
            assert ext.vcs_type == VcsType.SVN  # Should be detected as SVN due to /trunk
            assert ext.tag == "1.0"

            # Verify that _build_svn_url will correctly transform this URL
            cache = ExternalsCache()
            fetcher = ExternalFetcher(cache)
            built_url = fetcher._build_svn_url(ext.url, ext.tag, ext.branch)
            assert built_url == "https://repos.curseforge.com/wow/libstub/tags/1.0"


class TestValidation:
    """Test validation and error handling."""

    def test_conflicting_tag_and_branch_raises_error(self):
        """Test that specifying both tag and branch raises an error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkgmeta_path = Path(tmpdir) / ".pkgmeta"
            pkgmeta_content = """
externals:
  Libs/LibStub:
    url: https://repos.com/lib
    tag: 1.0
    branch: develop
"""
            pkgmeta_path.write_text(pkgmeta_content)

            with pytest.raises(ValueError, match="Cannot specify multiple of tag/branch/commit"):
                parse_pkgmeta(pkgmeta_path)

    def test_conflicting_tag_and_commit_raises_error(self):
        """Test that specifying both tag and commit raises an error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkgmeta_path = Path(tmpdir) / ".pkgmeta"
            pkgmeta_content = """
externals:
  Libs/LibStub:
    url: https://repos.com/lib
    tag: 1.0
    commit: abc123
"""
            pkgmeta_path.write_text(pkgmeta_content)

            with pytest.raises(ValueError, match="Cannot specify multiple of tag/branch/commit"):
                parse_pkgmeta(pkgmeta_path)

    def test_conflicting_branch_and_commit_raises_error(self):
        """Test that specifying both branch and commit raises an error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkgmeta_path = Path(tmpdir) / ".pkgmeta"
            pkgmeta_content = """
externals:
  Libs/LibStub:
    url: https://repos.com/lib
    branch: develop
    commit: abc123
"""
            pkgmeta_path.write_text(pkgmeta_content)

            with pytest.raises(ValueError, match="Cannot specify multiple of tag/branch/commit"):
                parse_pkgmeta(pkgmeta_path)

    def test_empty_url_raises_error(self):
        """Test that an empty URL raises an error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkgmeta_path = Path(tmpdir) / ".pkgmeta"
            pkgmeta_content = """
externals:
  Libs/LibStub:
    url: ""
    tag: 1.0
"""
            pkgmeta_path.write_text(pkgmeta_content)

            with pytest.raises(ValueError, match="'url' field is required and cannot be empty"):
                parse_pkgmeta(pkgmeta_path)

    def test_missing_url_raises_error(self):
        """Test that a missing URL raises an error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkgmeta_path = Path(tmpdir) / ".pkgmeta"
            pkgmeta_content = """
externals:
  Libs/LibStub:
    tag: 1.0
"""
            pkgmeta_path.write_text(pkgmeta_content)

            with pytest.raises(ValueError, match="'url' field is required and cannot be empty"):
                parse_pkgmeta(pkgmeta_path)

    def test_whitespace_only_url_raises_error(self):
        """Test that a whitespace-only URL raises an error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pkgmeta_path = Path(tmpdir) / ".pkgmeta"
            pkgmeta_content = """
externals:
  Libs/LibStub:
    url: "   "
    tag: 1.0
"""
            pkgmeta_path.write_text(pkgmeta_content)

            with pytest.raises(ValueError, match="'url' field is required and cannot be empty"):
                parse_pkgmeta(pkgmeta_path)

    def test_svn_non_numeric_revision_raises_error(self):
        """Test that a non-numeric SVN revision raises an error."""
        cache = ExternalsCache()
        fetcher = ExternalFetcher(cache)

        external = External(
            dest_path="Libs/LibStub",
            url="https://repos.com/lib/trunk",
            vcs_type=VcsType.SVN,
            commit="not-a-number"
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            dest_dir = Path(tmpdir) / "LibStub"
            with pytest.raises(ValueError, match="SVN revision must be numeric"):
                fetcher._fetch_svn(external, dest_dir)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
