"""Tests for llm-fragments-folder plugin."""

import pathlib
import textwrap

import pytest

from llm_fragments_folder import (
    _is_dotfile,
    _is_text_file,
    _parse_argument,
    _should_skip_dir,
    _walk_folder,
    folder_loader,
    project_loader,
)


@pytest.fixture
def sample_folder(tmp_path):
    """Create a sample folder structure for testing."""
    # Text files
    (tmp_path / "README.md").write_text("# My Project\nHello world")
    (tmp_path / "main.py").write_text("print('hello')")
    (tmp_path / "config.yaml").write_text("key: value")

    # Subdirectory
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("# Guide\nSome guide content")
    (docs / "api.txt").write_text("API docs here")

    # Dotfiles
    (tmp_path / ".bashrc").write_text("export PATH=$PATH:/usr/local/bin")
    (tmp_path / ".gitconfig").write_text("[user]\n  name = Test")
    (tmp_path / ".vimrc").write_text("set number")

    # Binary-like file (no text extension)
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n")

    # Directories that should be skipped
    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    (node_modules / "package.json").write_text("{}")

    pycache = tmp_path / "__pycache__"
    pycache.mkdir()
    (pycache / "main.cpython-312.pyc").write_bytes(b"\x00\x00")

    return tmp_path


@pytest.fixture
def git_project(tmp_path):
    """Create a sample project with .gitignore."""
    (tmp_path / "README.md").write_text("# Project")
    (tmp_path / "app.py").write_text("import flask")
    (tmp_path / "secret.env").write_text("API_KEY=xxx")

    (tmp_path / ".gitignore").write_text(
        textwrap.dedent("""\
        *.env
        __pycache__/
        dist/
        """)
    )

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "bundle.js").write_text("minified code")

    return tmp_path


class TestIsTextFile:
    def test_markdown(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("hello")
        assert _is_text_file(f) is True

    def test_python(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("hello")
        assert _is_text_file(f) is True

    def test_png(self, tmp_path):
        f = tmp_path / "test.png"
        f.write_bytes(b"\x89PNG")
        assert _is_text_file(f) is False

    def test_makefile(self, tmp_path):
        f = tmp_path / "Makefile"
        f.write_text("all: build")
        assert _is_text_file(f) is True

    def test_dockerfile(self, tmp_path):
        f = tmp_path / "Dockerfile"
        f.write_text("FROM python:3.12")
        assert _is_text_file(f) is True

    def test_shebang_script(self, tmp_path):
        f = tmp_path / "myscript"
        f.write_text("#!/bin/bash\necho hello")
        assert _is_text_file(f) is True

    def test_bashrc(self, tmp_path):
        f = tmp_path / ".bashrc"
        f.write_text("export PATH=/usr/local/bin")
        assert _is_text_file(f) is True

    def test_gitconfig(self, tmp_path):
        f = tmp_path / ".gitconfig"
        f.write_text("[user]\n  name = Test")
        assert _is_text_file(f) is True


class TestIsDotfile:
    def test_bashrc(self, tmp_path):
        assert _is_dotfile(tmp_path / ".bashrc") is True

    def test_gitconfig(self, tmp_path):
        assert _is_dotfile(tmp_path / ".gitconfig") is True

    def test_not_dotfile(self, tmp_path):
        assert _is_dotfile(tmp_path / "main.py") is False

    def test_dotfile_with_extension(self, tmp_path):
        # .env.example has suffix .example, so it's not a "pure" dotfile
        assert _is_dotfile(tmp_path / ".env.example") is False


class TestShouldSkipDir:
    def test_node_modules(self):
        assert _should_skip_dir("node_modules") is True

    def test_git(self):
        assert _should_skip_dir(".git") is True

    def test_pycache(self):
        assert _should_skip_dir("__pycache__") is True

    def test_normal_dir(self):
        assert _should_skip_dir("src") is False

    def test_docs_dir(self):
        assert _should_skip_dir("docs") is False

    def test_egg_info(self):
        assert _should_skip_dir("mypackage.egg-info") is True


class TestWalkFolder:
    def test_finds_text_files(self, sample_folder):
        files = _walk_folder(sample_folder)
        names = {f.name for f in files}
        assert "README.md" in names
        assert "main.py" in names
        assert "config.yaml" in names

    def test_finds_subdirectory_files(self, sample_folder):
        files = _walk_folder(sample_folder)
        names = {f.name for f in files}
        assert "guide.md" in names
        assert "api.txt" in names

    def test_skips_binary_files(self, sample_folder):
        files = _walk_folder(sample_folder)
        names = {f.name for f in files}
        assert "image.png" not in names

    def test_skips_node_modules(self, sample_folder):
        files = _walk_folder(sample_folder)
        names = {f.name for f in files}
        assert "package.json" not in names

    def test_skips_pycache(self, sample_folder):
        files = _walk_folder(sample_folder)
        paths = {str(f) for f in files}
        assert not any("__pycache__" in p for p in paths)

    def test_max_files_limit(self, sample_folder):
        files = _walk_folder(sample_folder, max_files=2)
        assert len(files) <= 2

    def test_not_a_directory(self, tmp_path):
        fake = tmp_path / "nonexistent"
        with pytest.raises(ValueError, match="Not a directory"):
            _walk_folder(fake)

    def test_ext_filter_only_markdown(self, sample_folder):
        files = _walk_folder(sample_folder, ext_filter={".md"})
        names = {f.name for f in files}
        assert "README.md" in names
        assert "guide.md" in names
        assert "main.py" not in names
        assert "config.yaml" not in names
        assert "api.txt" not in names

    def test_ext_filter_multiple(self, sample_folder):
        files = _walk_folder(sample_folder, ext_filter={".md", ".py"})
        names = {f.name for f in files}
        assert "README.md" in names
        assert "main.py" in names
        assert "config.yaml" not in names

    def test_ext_filter_no_matches(self, sample_folder):
        files = _walk_folder(sample_folder, ext_filter={".xyz"})
        assert files == []

    def test_ext_filter_dotfiles_catchall(self, sample_folder):
        files = _walk_folder(sample_folder, ext_filter={"dotfiles"})
        names = {f.name for f in files}
        assert ".bashrc" in names
        assert ".gitconfig" in names
        assert ".vimrc" in names
        assert "README.md" not in names
        assert "main.py" not in names

    def test_ext_filter_dotfiles_combined(self, sample_folder):
        files = _walk_folder(sample_folder, ext_filter={"dotfiles", ".py"})
        names = {f.name for f in files}
        assert ".bashrc" in names
        assert "main.py" in names
        assert "README.md" not in names

    def test_ext_filter_specific_dotfile(self, sample_folder):
        files = _walk_folder(sample_folder, ext_filter={".bashrc"})
        names = {f.name for f in files}
        assert ".bashrc" in names
        assert ".gitconfig" not in names

    def test_default_includes_known_dotfiles(self, sample_folder):
        files = _walk_folder(sample_folder)
        names = {f.name for f in files}
        assert ".bashrc" in names
        assert ".gitconfig" in names
        assert ".vimrc" in names

    def test_gitignore_respected(self, git_project):
        files = _walk_folder(git_project, respect_gitignore=True)
        names = {f.name for f in files}
        assert "README.md" in names
        assert "app.py" in names
        # .env files should be ignored by .gitignore
        assert "secret.env" not in names
        # dist/ should be ignored
        assert "bundle.js" not in names


class TestParseArgument:
    def test_empty_string(self):
        path, ext = _parse_argument("")
        assert path == pathlib.Path.cwd()
        assert ext is None

    def test_dot(self):
        path, ext = _parse_argument(".")
        assert path == pathlib.Path(".")
        assert ext is None

    def test_relative_path(self):
        path, ext = _parse_argument("./docs")
        assert path == pathlib.Path("./docs")
        assert ext is None

    def test_home_expansion(self):
        path, ext = _parse_argument("~/projects")
        assert "~" not in str(path)
        assert ext is None

    def test_ext_filter(self):
        path, ext = _parse_argument("./docs?ext=md,txt")
        assert path == pathlib.Path("./docs")
        assert ext == {".md", ".txt"}

    def test_ext_filter_with_dots(self):
        path, ext = _parse_argument(".?ext=.py,.js")
        assert path == pathlib.Path(".")
        assert ext == {".py", ".js"}

    def test_ext_filter_no_path(self):
        path, ext = _parse_argument("?ext=md")
        assert path == pathlib.Path(".")
        assert ext == {".md"}

    def test_ext_filter_dotfiles_keyword(self):
        path, ext = _parse_argument(".?ext=dotfiles")
        assert path == pathlib.Path(".")
        assert ext == {"dotfiles"}

    def test_ext_filter_dotfiles_combined(self):
        path, ext = _parse_argument("./src?ext=dotfiles,py,md")
        assert path == pathlib.Path("./src")
        assert ext == {"dotfiles", ".py", ".md"}


class TestFolderLoader:
    def test_loads_fragments(self, sample_folder):
        fragments = folder_loader(str(sample_folder))
        assert len(fragments) >= 3
        # Fragment is a string subclass, so check directly
        assert any("My Project" in str(f) for f in fragments)
        assert any("print('hello')" in str(f) for f in fragments)

    def test_empty_folder(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(ValueError, match="no text files found"):
            folder_loader(str(empty))

    def test_nonexistent_folder(self):
        with pytest.raises(ValueError, match="not a directory"):
            folder_loader("/nonexistent/path/that/doesnt/exist")

    def test_ext_filter(self, sample_folder):
        fragments = folder_loader(f"{sample_folder}?ext=md")
        assert all("---" in str(f) for f in fragments)
        # Should only have .md files
        assert any("My Project" in str(f) for f in fragments)
        assert not any("print('hello')" in str(f) for f in fragments)


class TestProjectLoader:
    def test_includes_file_tree(self, sample_folder):
        fragments = project_loader(str(sample_folder))
        # First fragment should be the file tree
        assert "FILE_TREE" in fragments[0].source
        assert sample_folder.name in str(fragments[0])

    def test_loads_file_contents(self, sample_folder):
        fragments = project_loader(str(sample_folder))
        # Should have tree + file fragments
        assert len(fragments) >= 4  # tree + at least 3 files
        assert any("My Project" in str(f) for f in fragments[1:])
