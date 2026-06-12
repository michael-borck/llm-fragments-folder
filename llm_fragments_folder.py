"""
LLM plugin to load folder contents as fragments.

Provides two fragment loaders:
  - folder:<path>  Load all text files recursively from a directory
  - project:<path> Load project files, respecting .gitignore
"""

from __future__ import annotations

import fnmatch
import logging
import os
import pathlib
import subprocess
from typing import Any

import llm
import pathspec

logger = logging.getLogger(__name__)

# Maximum number of files loaded per loader call
MAX_FILES = 500


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
    "dist",
    "build",
    ".idea",
    ".vscode",
}

# Directories that commonly hold credentials — never traversed, even when
# matched by a ?glob= filter or tracked by git
SENSITIVE_DIRS = {
    ".ssh",
    ".gnupg",
    ".aws",
    ".kube",
    ".docker",
    ".gcloud",
    ".azure",
}

# Filenames that commonly hold credentials — never loaded, even with ?glob=
SENSITIVE_FILENAMES = {
    ".netrc",
    ".pgpass",
    ".env",
}

# Filename patterns that commonly hold credentials or private keys
SENSITIVE_FILE_PATTERNS = [
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "*.env",
    ".env.*",
]

# Safe templates exempted from the sensitive patterns
SENSITIVE_EXCEPTIONS = {
    ".env.example",
    ".env.sample",
    ".env.template",
}


def _is_sensitive_file(name: str) -> bool:
    """Check if a filename looks like it holds credentials or private keys."""
    if name in SENSITIVE_EXCEPTIONS:
        return False
    if name in SENSITIVE_FILENAMES:
        return True
    return any(fnmatch.fnmatch(name, pattern) for pattern in SENSITIVE_FILE_PATTERNS)


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
            logger.warning("Skipping large file (%d bytes): %s", size, path)
            return None
        with open(path, "rb") as f:
            # Check for binary content (null bytes) in the first 8KB
            head = f.read(8192)
            if b"\x00" in head:
                logger.warning("Skipping binary file: %s", path)
                return None
            rest = f.read()
        raw = head + rest
        return raw.decode("utf-8", errors="replace")
    except (OSError, PermissionError):
        return None


def _get_gitignore_spec(directory: pathlib.Path) -> Any:
    """Parse a directory's .gitignore into a pathspec matcher, if available."""
    gitignore_path = directory / ".gitignore"
    if not gitignore_path.exists():
        return None
    try:
        patterns = gitignore_path.read_text().splitlines()
        return pathspec.PathSpec.from_lines("gitignore", patterns)
    except Exception:
        return None


def _is_ignored(rel_posix: str, specs: list[tuple[str, Any]]) -> bool:
    """Match a root-relative posix path against per-directory gitignore specs.

    Each spec applies only to paths under its base directory, matched
    relative to that base (gitignore semantics). Pass a trailing slash to
    match directories.
    """
    for base, spec in specs:
        if base:
            if not rel_posix.startswith(base + "/"):
                continue
            candidate = rel_posix[len(base) + 1 :]
        else:
            candidate = rel_posix
        if spec.match_file(candidate):
            return True
    return False


def _get_git_tracked_files(root: pathlib.Path) -> tuple[set[str], set[str]] | None:
    """Use git ls-files to get tracked + untracked (not ignored) files.

    Returns (all_files, tracked_files): all_files is everything git would
    include (tracked plus untracked-but-not-ignored); tracked_files is the
    subset git actually tracks, used to let tracked content override the
    SKIP_DIRS noise list.

    Uses -z so paths are NUL-separated and output verbatim — without it,
    git quotes and escapes non-ASCII filenames (core.quotePath), which
    would break set-membership checks. -t prefixes each entry with a
    status tag ("?" = untracked). Returns None when not in a git repo,
    git is unavailable, or git reports no files (e.g. the folder is
    itself ignored by an enclosing repo), so callers fall back to
    .gitignore parsing.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "ls-files",
                "-z",
                "-t",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            all_files: set[str] = set()
            tracked: set[str] = set()
            for entry in result.stdout.split("\0"):
                if not entry:
                    continue
                tag, _, name = entry.partition(" ")
                if not name:
                    continue
                all_files.add(name)
                if tag != "?":
                    tracked.add(name)
            if all_files:
                return all_files, tracked
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def _compile_glob_filter(glob_param: str) -> Any:
    """Compile a comma-separated glob pattern string into a pathspec matcher."""
    patterns = [p.strip() for p in glob_param.split(",") if p.strip()]
    if not patterns:
        return None
    return pathspec.PathSpec.from_lines("gitignore", patterns)


def _keep_dir(
    dirname: str,
    parent_rel: str,
    git_tracked: set[str] | None,
    gitignore_specs: list[tuple[str, Any]],
) -> bool:
    """Decide whether os.walk should descend into a directory."""
    if dirname in SENSITIVE_DIRS:
        return False
    rel = f"{parent_rel}/{dirname}" if parent_rel else dirname
    if git_tracked is not None:
        # Skip noise dirs unless git explicitly tracks files inside them
        if _should_skip_dir(dirname):
            prefix = rel + "/"
            return any(f.startswith(prefix) for f in git_tracked)
        return True
    if _should_skip_dir(dirname):
        return False
    return not _is_ignored(rel + "/", gitignore_specs)


def _walk_folder(
    root: pathlib.Path,
    respect_gitignore: bool = False,
    max_files: int | None = None,
    glob_filter: Any = None,
) -> list[pathlib.Path]:
    """Walk a folder and return a list of text file paths.

    If glob_filter is provided (a compiled pathspec.PathSpec), files are matched
    against the glob patterns instead of default text file detection.
    max_files defaults to MAX_FILES.
    """
    if max_files is None:
        max_files = MAX_FILES
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")

    files: list[pathlib.Path] = []
    git_files = None
    git_tracked = None
    gitignore_specs: list[tuple[str, Any]] = []
    use_gitignore_fallback = False

    if respect_gitignore:
        # Prefer git ls-files if we're in a git repo
        git_result = _get_git_tracked_files(root)
        if git_result is not None:
            git_files, git_tracked = git_result
        # Fall back to .gitignore parsing (nested files collected during walk)
        use_gitignore_fallback = git_files is None

    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = pathlib.Path(dirpath).relative_to(root)
        rel_dir_posix = "" if rel_dir == pathlib.Path(".") else rel_dir.as_posix()

        if use_gitignore_fallback:
            spec = _get_gitignore_spec(pathlib.Path(dirpath))
            if spec is not None:
                gitignore_specs.append((rel_dir_posix, spec))

        # Filter out skipped directories in-place
        dirnames[:] = [
            d
            for d in dirnames
            if _keep_dir(d, rel_dir_posix, git_tracked, gitignore_specs)
        ]
        dirnames.sort()

        for filename in sorted(filenames):
            if _is_sensitive_file(filename):
                logger.warning("Skipping sensitive file: %s/%s", dirpath, filename)
                continue

            filepath = pathlib.Path(dirpath) / filename
            # Posix-style relative path: matches git output and gitignore
            # patterns on all platforms (str() would use backslashes on Windows)
            rel_str = f"{rel_dir_posix}/{filename}" if rel_dir_posix else filename

            # Skip symlinks that point outside the tree being loaded
            if filepath.is_symlink():
                try:
                    if not filepath.resolve().is_relative_to(root):
                        continue
                except OSError:
                    continue

            # Git-based filtering
            if git_files is not None:
                if rel_str not in git_files:
                    continue
            elif _is_ignored(rel_str, gitignore_specs):
                continue

            # Glob filter or default text detection
            if glob_filter is not None:
                if not glob_filter.match_file(rel_str):
                    continue
            elif not _is_text_file(filepath):
                continue

            files.append(filepath)
            if len(files) >= max_files:
                logger.warning(
                    "File limit (%d) reached in %s; remaining files skipped",
                    max_files,
                    root,
                )
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
    back to parsing .gitignore patterns (including nested .gitignore
    files). Prepends a file tree summary as the first fragment for
    project context.

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
    seen_dirs: set[pathlib.Path] = set()
    for f in files:
        rel = f.relative_to(resolved_root)
        # Show parent directories that haven't been shown yet
        for i in range(len(rel.parts) - 1):
            dir_path = pathlib.Path(*rel.parts[: i + 1])
            if dir_path not in seen_dirs:
                seen_dirs.add(dir_path)
                indent = "  " * i
                tree_lines.append(f"{indent}{rel.parts[i]}/")
        indent = "  " * (len(rel.parts) - 1)
        tree_lines.append(f"{indent}{rel.name}")
    if len(files) >= MAX_FILES:
        tree_lines.append("")
        tree_lines.append(
            f"(file limit of {MAX_FILES} reached; this listing may be incomplete)"
        )
    tree_content = "\n".join(tree_lines)
    fragments.append(llm.Fragment(tree_content, f"project:{root}/FILE_TREE"))

    # Add file content fragments
    fragments.extend(_build_fragments(root, files, "project"))
    return fragments
