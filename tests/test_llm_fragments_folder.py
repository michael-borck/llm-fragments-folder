"""Tests for llm-fragments-folder plugin."""

import pathlib
import textwrap

import pytest

from llm_fragments_folder import (
    _compile_glob_filter,
    _is_text_file,
    _parse_argument,
    _read_file_safe,
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


class TestReadFileSafe:
    def test_reads_text(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        assert _read_file_safe(f) == "hello world"

    def test_skips_binary_with_null_bytes(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello\x00world")
        assert _read_file_safe(f) is None

    def test_skips_pdf(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4\x00some binary content")
        assert _read_file_safe(f) is None

    def test_skips_large_files(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_text("x" * 100)
        assert _read_file_safe(f, max_size=50) is None


class TestCompileGlobFilter:
    def test_single_pattern(self):
        spec = _compile_glob_filter("*.md")
        assert spec.match_file("README.md")
        assert not spec.match_file("main.py")

    def test_multiple_patterns(self):
        spec = _compile_glob_filter("*.md,*.py")
        assert spec.match_file("README.md")
        assert spec.match_file("main.py")
        assert not spec.match_file("config.yaml")

    def test_negation(self):
        spec = _compile_glob_filter("*.py,!*_test.py")
        assert spec.match_file("main.py")
        assert not spec.match_file("main_test.py")

    def test_dotfile_pattern(self):
        spec = _compile_glob_filter(".*")
        assert spec.match_file(".bashrc")
        assert spec.match_file(".gitconfig")
        assert not spec.match_file("main.py")

    def test_directory_negation(self):
        spec = _compile_glob_filter("*.py,!tests/**")
        assert spec.match_file("main.py")
        assert spec.match_file("src/app.py")
        assert not spec.match_file("tests/test_main.py")

    def test_empty_returns_none(self):
        assert _compile_glob_filter("") is None
        assert _compile_glob_filter("  ,  ") is None

    def test_wildcard_substring(self):
        spec = _compile_glob_filter("*finance*")
        assert spec.match_file("finance_report.md")
        assert spec.match_file("q1_finance.txt")
        assert not spec.match_file("readme.md")


class TestParseArgument:
    def test_empty_string(self):
        path, gf = _parse_argument("")
        assert path == pathlib.Path.cwd()
        assert gf is None

    def test_dot(self):
        path, gf = _parse_argument(".")
        assert path == pathlib.Path(".")
        assert gf is None

    def test_relative_path(self):
        path, gf = _parse_argument("./docs")
        assert path == pathlib.Path("./docs")
        assert gf is None

    def test_home_expansion(self):
        path, gf = _parse_argument("~/projects")
        assert "~" not in str(path)
        assert gf is None

    def test_glob_filter_single(self):
        path, gf = _parse_argument("./docs?glob=*.md")
        assert path == pathlib.Path("./docs")
        assert gf is not None
        assert gf.match_file("README.md")
        assert not gf.match_file("main.py")

    def test_glob_filter_multiple(self):
        path, gf = _parse_argument(".?glob=*.py,*.js")
        assert path == pathlib.Path(".")
        assert gf is not None
        assert gf.match_file("main.py")
        assert gf.match_file("app.js")
        assert not gf.match_file("README.md")

    def test_glob_filter_no_path(self):
        path, gf = _parse_argument("?glob=*.md")
        assert path == pathlib.Path(".")
        assert gf is not None
        assert gf.match_file("README.md")

    def test_glob_filter_with_negation(self):
        path, gf = _parse_argument(".?glob=*.py,!*_test.py")
        assert path == pathlib.Path(".")
        assert gf is not None
        assert gf.match_file("main.py")
        assert not gf.match_file("main_test.py")

    def test_glob_filter_dotfiles(self):
        path, gf = _parse_argument("~?glob=.*")
        assert "~" not in str(path)
        assert gf is not None
        assert gf.match_file(".bashrc")
        assert not gf.match_file("main.py")


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

    def test_default_includes_known_dotfiles(self, sample_folder):
        files = _walk_folder(sample_folder)
        names = {f.name for f in files}
        assert ".bashrc" in names
        assert ".gitconfig" in names
        assert ".vimrc" in names

    def test_glob_only_markdown(self, sample_folder):
        gf = _compile_glob_filter("*.md")
        files = _walk_folder(sample_folder, glob_filter=gf)
        names = {f.name for f in files}
        assert "README.md" in names
        assert "guide.md" in names
        assert "main.py" not in names
        assert "config.yaml" not in names
        assert "api.txt" not in names

    def test_glob_multiple_types(self, sample_folder):
        gf = _compile_glob_filter("*.md,*.py")
        files = _walk_folder(sample_folder, glob_filter=gf)
        names = {f.name for f in files}
        assert "README.md" in names
        assert "main.py" in names
        assert "config.yaml" not in names

    def test_glob_no_matches(self, sample_folder):
        gf = _compile_glob_filter("*.xyz")
        files = _walk_folder(sample_folder, glob_filter=gf)
        assert files == []

    def test_glob_dotfiles(self, sample_folder):
        gf = _compile_glob_filter(".*")
        files = _walk_folder(sample_folder, glob_filter=gf)
        names = {f.name for f in files}
        assert ".bashrc" in names
        assert ".gitconfig" in names
        assert ".vimrc" in names
        assert "README.md" not in names
        assert "main.py" not in names

    def test_glob_with_negation(self, sample_folder):
        gf = _compile_glob_filter("*.md,*.py,*.yaml,*.txt,!*.md")
        files = _walk_folder(sample_folder, glob_filter=gf)
        names = {f.name for f in files}
        assert "README.md" not in names
        assert "guide.md" not in names
        assert "main.py" in names
        assert "config.yaml" in names
        assert "api.txt" in names

    def test_glob_exclude_directory(self, sample_folder):
        gf = _compile_glob_filter("*.md,!docs/**")
        files = _walk_folder(sample_folder, glob_filter=gf)
        names = {f.name for f in files}
        assert "README.md" in names
        assert "guide.md" not in names

    def test_glob_binary_files_skipped_by_read(self, sample_folder):
        """Binary files matched by glob are still skipped by _read_file_safe."""
        (sample_folder / "data.bin").write_bytes(b"\x00\x01\x02\x03")
        gf = _compile_glob_filter("*.bin,*.md")
        files = _walk_folder(sample_folder, glob_filter=gf)
        names = {f.name for f in files}
        # .bin is matched by the glob filter (it passes _walk_folder)
        assert "data.bin" in names
        # But _read_file_safe will skip it due to null bytes (tested via loader)

    def test_gitignore_respected(self, git_project):
        files = _walk_folder(git_project, respect_gitignore=True)
        names = {f.name for f in files}
        assert "README.md" in names
        assert "app.py" in names
        # .env files should be ignored by .gitignore
        assert "secret.env" not in names
        # dist/ should be ignored
        assert "bundle.js" not in names


class TestFolderLoader:
    def test_loads_fragments(self, sample_folder):
        fragments = folder_loader(str(sample_folder))
        assert len(fragments) >= 3
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

    def test_glob_filter(self, sample_folder):
        fragments = folder_loader(f"{sample_folder}?glob=*.md")
        assert all("---" in str(f) for f in fragments)
        assert any("My Project" in str(f) for f in fragments)
        assert not any("print('hello')" in str(f) for f in fragments)

    def test_glob_exclude(self, sample_folder):
        fragments = folder_loader(f"{sample_folder}?glob=*.py,*.yaml,*.txt,.*")
        assert not any("My Project" in str(f) for f in fragments)
        assert any("print('hello')" in str(f) for f in fragments)

    def test_binary_files_skipped_in_output(self, sample_folder):
        (sample_folder / "data.bin").write_bytes(b"\x00\x01\x02\x03")
        fragments = folder_loader(f"{sample_folder}?glob=*.bin,*.md")
        contents = [str(f) for f in fragments]
        assert not any("data.bin" in c for c in contents)
        assert any("My Project" in c for c in contents)


class TestProjectLoader:
    def test_includes_file_tree(self, sample_folder):
        fragments = project_loader(str(sample_folder))
        assert "FILE_TREE" in fragments[0].source
        assert sample_folder.name in str(fragments[0])

    def test_loads_file_contents(self, sample_folder):
        fragments = project_loader(str(sample_folder))
        assert len(fragments) >= 4  # tree + at least 3 files
        assert any("My Project" in str(f) for f in fragments[1:])
