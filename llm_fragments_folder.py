"""
LLM plugin to load folder contents as fragments.

Provides two fragment loaders:
  - folder:<path>  Load all text files recursively from a directory
  - project:<path> Load project files, respecting .gitignore
"""

from __future__ import annotations

import logging
import os
import pathlib
import subprocess
from types import ModuleType
from typing import Any

import llm

logger = logging.getLogger(__name__)

pathspec: ModuleType | None
try:
    import pathspec
except ImportError:
    pathspec = None


# File extensions considered "text" by default
TEXT_EXTENSIONS = {
    # Documents
    ".md",
    ".qmd",
    ".txt",
    ".rst",
    ".adoc",
    ".tex",
    ".org",
    # Code
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".rb",
    ".go",
    ".rs",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".swift",
    ".kt",
    ".scala",
    ".r",
    ".jl",
    ".lua",
    ".pl",
    ".pm",
    ".php",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".ps1",
    ".bat",
    ".cmd",
    # Web
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".svg",
    ".xml",
    ".xsl",
    # Data / Config
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".env",
    ".properties",
    ".csv",
    ".tsv",
    # Build / CI
    ".dockerfile",
    ".makefile",
    ".cmake",
    ".gradle",
    ".sbt",
    # Other
    ".sql",
    ".graphql",
    ".proto",
    ".tf",
    ".hcl",
    ".ipynb",
    ".bib",
    ".vim",
    ".el",
}

# Filenames (no extension) that are always text
TEXT_FILENAMES = {
    # Build / project files
    "Makefile",
    "Dockerfile",
    "Jenkinsfile",
    "Vagrantfile",
    "Procfile",
    "Gemfile",
    "Rakefile",
    "Brewfile",
    "CMakeLists.txt",
    # Documentation
    "LICENSE",
    "LICENCE",
    "COPYING",
    "README",
    "CHANGELOG",
    "CHANGES",
    "AUTHORS",
    "CONTRIBUTING",
    "CLAUDE.md",
    # Shell dotfiles
    ".bashrc",
    ".bash_profile",
    ".bash_login",
    ".bash_logout",
    ".profile",
    ".zshrc",
    ".zprofile",
    ".zshenv",
    ".zlogin",
    ".zlogout",
    # Editor / tool dotfiles
    ".vimrc",
    ".gvimrc",
    ".nanorc",
    ".inputrc",
    ".tmux.conf",
    # Git dotfiles
    ".gitignore",
    ".gitconfig",
    ".gitattributes",
    ".gitmodules",
    # Other config dotfiles
    ".dockerignore",
    ".editorconfig",
    ".env.example",
    ".eslintrc",
    ".prettierrc",
    ".flake8",
    ".pylintrc",
    ".npmrc",
    ".yarnrc",
    ".curlrc",
    ".wgetrc",
    ".screenrc",
    ".hushlogin",
}

# Directories to always skip
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".tox",
    ".nox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "venv",
    ".venv",
    "env",
    ".env",
    ".eggs",
    "*.egg-info",
    "dist",
    "build",
    ".idea",
    ".vscode",
    ".DS_Store",
}


def _is_text_file(path: pathlib.Path) -> bool:
    """Check if a file is likely a text file based on extension or name."""
    if path.name in TEXT_FILENAMES:
        return True
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    # Check for extensionless files that might be scripts (shebang line)
    if not path.suffix:
        try:
            with open(path, "rb") as f:
                first_bytes = f.read(2)
                if first_bytes == b"#!":
                    return True
        except (OSError, PermissionError):
            return False
    return False


def _should_skip_dir(dirname: str) -> bool:
    """Check if a directory should be skipped."""
    return dirname in SKIP_DIRS or dirname.endswith(".egg-info")


def _read_file_safe(path: pathlib.Path, max_size: int = 1_000_000) -> str | None:
    """Read a file, returning None if it can't be read, is too large, or is binary."""
    try:
        size = path.stat().st_size
        if size > max_size:
            return None
        # Check for binary content (null bytes)
        raw = path.read_bytes()
        if b"\x00" in raw:
            logger.warning("Skipping binary file: %s", path)
            return None
        return raw.decode("utf-8", errors="replace")
    except (OSError, PermissionError):
        return None


def _get_gitignore_spec(root: pathlib.Path) -> Any:
    """Parse .gitignore into a pathspec matcher, if available."""
    if pathspec is None:
        return None
    gitignore_path = root / ".gitignore"
    if not gitignore_path.exists():
        return None
    try:
        patterns = gitignore_path.read_text().splitlines()
        return pathspec.PathSpec.from_lines("gitignore", patterns)
    except Exception:
        return None


def _get_git_tracked_files(root: pathlib.Path) -> set[str] | None:
    """Use git ls-files to get tracked + untracked (not ignored) files."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return set(result.stdout.strip().splitlines())
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def _compile_glob_filter(glob_param: str) -> Any:
    """Compile a comma-separated glob pattern string into a pathspec matcher."""
    if pathspec is None:
        raise ValueError("pathspec library is required for ?glob= filtering")
    patterns = [p.strip() for p in glob_param.split(",") if p.strip()]
    if not patterns:
        return None
    return pathspec.PathSpec.from_lines("gitignore", patterns)


def _walk_folder(
    root: pathlib.Path,
    respect_gitignore: bool = False,
    max_files: int = 500,
    glob_filter: Any = None,
) -> list[pathlib.Path]:
    """Walk a folder and return a list of text file paths.

    If glob_filter is provided (a compiled pathspec.PathSpec), files are matched
    against the glob patterns instead of default text file detection.
    """
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")

    files = []
    git_files = None
    gitignore_spec = None

    if respect_gitignore:
        # Prefer git ls-files if we're in a git repo
        git_files = _get_git_tracked_files(root)
        if git_files is None:
            # Fall back to .gitignore parsing
            gitignore_spec = _get_gitignore_spec(root)

    for dirpath, dirnames, filenames in os.walk(root):
        # Filter out skipped directories in-place
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
        dirnames.sort()

        for filename in sorted(filenames):
            filepath = pathlib.Path(dirpath) / filename
            rel_path = filepath.relative_to(root)
            rel_str = str(rel_path)

            # Git-based filtering
            if git_files is not None:
                if rel_str not in git_files:
                    continue
            elif gitignore_spec is not None and gitignore_spec.match_file(rel_str):
                continue

            # Glob filter or default text detection
            if glob_filter is not None:
                if not glob_filter.match_file(rel_str):
                    continue
            elif not _is_text_file(filepath):
                continue

            files.append(filepath)
            if len(files) >= max_files:
                return files

    return files


def _build_fragments(
    root: pathlib.Path,
    files: list[pathlib.Path],
    prefix: str,
) -> list[llm.Fragment]:
    """Build a list of Fragment objects from file paths."""
    fragments = []
    for filepath in files:
        content = _read_file_safe(filepath)
        if content is None:
            continue
        rel_path = filepath.relative_to(root.resolve())
        source = f"{prefix}:{root}/{rel_path}"
        # Wrap content with filename header for clarity
        wrapped = f"--- {rel_path} ---\n{content}"
        fragments.append(llm.Fragment(wrapped, source))
    return fragments


def _parse_argument(argument: str) -> tuple[pathlib.Path, Any]:
    """Parse the argument string into a Path and optional glob filter.

    Supports glob filtering:
      ?glob=*.md,*.txt        Include only markdown and text files
      ?glob=*.py,!*_test.py   Python files, excluding tests
      ?glob=.*                All dotfiles
      ?glob=*finance*,!*.txt  Files containing "finance", excluding .txt

    Returns (path, glob_filter) where glob_filter is a compiled pathspec
    matcher or None if no filter specified.
    """
    if not argument or argument.strip() == "":
        return pathlib.Path.cwd(), None

    path_str = argument

    if "?glob=" not in argument:
        return pathlib.Path(path_str).expanduser(), None

    path_str, _, glob_part = argument.partition("?glob=")
    if not path_str:
        path_str = "."

    glob_filter = _compile_glob_filter(glob_part)
    return pathlib.Path(path_str).expanduser(), glob_filter


@llm.hookimpl
def register_fragment_loaders(register: Any) -> None:
    """Register the folder: and project: fragment loaders."""
    register("folder", folder_loader)
    register("project", project_loader)


def folder_loader(argument: str) -> list[llm.Fragment]:
    """
    Load all text files from a folder as fragments.

    Usage: llm -f folder:./docs "Summarize these documents"
           llm -f folder:. "What is this about?"
           llm -f folder:~/notes "Find action items"
           llm -f "folder:./docs?glob=*.md,*.txt" "Summarize the docs"
           llm -f "folder:.?glob=*.py,!*_test.py" "Review non-test Python"
           llm -f "folder:~?glob=.*" "Show all dotfiles"

    Recursively walks the directory, loading all recognized text files.
    Skips common non-text directories (node_modules, .git, __pycache__, etc.)
    and binary files (detected via null bytes). Each file becomes a separate
    fragment.

    Filter syntax (gitignore-style glob patterns):
      ?glob=*.md,*.txt        Include only these file types
      ?glob=*.py,!*_test.py   Include Python, exclude test files
      ?glob=.*                All dotfiles
      ?glob=*finance*,!*.txt  Files with "finance", excluding .txt
    """
    root, glob_filter = _parse_argument(argument)
    if not root.is_dir():
        raise ValueError(f"folder:{argument} - '{root}' is not a directory")
    files = _walk_folder(root, respect_gitignore=False, glob_filter=glob_filter)
    if not files:
        raise ValueError(f"folder:{argument} - no text files found in '{root}'")
    return _build_fragments(root, files, "folder")


def project_loader(argument: str) -> list[llm.Fragment]:
    """
    Load project files from a folder, respecting .gitignore.

    Usage: llm -f project:. "Explain this codebase"
           llm -f project:./my-app "What does this project do?"
           llm chat -f project:.
           llm -f "project:.?glob=*.py,*.js" "Review the code"

    Like folder: but designed for software projects. Uses git ls-files
    when inside a git repo (the most accurate approach), otherwise falls
    back to parsing .gitignore patterns. Prepends a file tree summary as
    the first fragment for project context.

    Filter syntax (gitignore-style glob patterns):
      ?glob=*.py,*.js         Include only these file types
      ?glob=*.py,!tests/**    Python files, skip tests directory
      ?glob=*.md,*.txt        Documentation files only
    """
    root, glob_filter = _parse_argument(argument)
    if not root.is_dir():
        raise ValueError(f"project:{argument} - '{root}' is not a directory")
    files = _walk_folder(root, respect_gitignore=True, glob_filter=glob_filter)
    if not files:
        raise ValueError(f"project:{argument} - no text files found in '{root}'")

    resolved_root = root.resolve()
    fragments = []

    # Build a file tree summary as the first fragment
    tree_lines = [f"Project: {resolved_root.name}", ""]
    for f in files:
        rel = f.relative_to(resolved_root)
        indent = "  " * (len(rel.parts) - 1)
        tree_lines.append(f"{indent}{rel.name}")
    tree_content = "\n".join(tree_lines)
    fragments.append(llm.Fragment(tree_content, f"project:{root}/FILE_TREE"))

    # Add file content fragments
    fragments.extend(_build_fragments(root, files, "project"))
    return fragments
