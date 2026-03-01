# llm-fragments-folder

<!-- BADGES:START -->
[![chat-bot](https://img.shields.io/badge/-chat--bot-blue?style=flat-square)](https://github.com/topics/chat-bot) [![cli-tool](https://img.shields.io/badge/-cli--tool-blue?style=flat-square)](https://github.com/topics/cli-tool) [![file-loader](https://img.shields.io/badge/-file--loader-blue?style=flat-square)](https://github.com/topics/file-loader) [![knowledge-base](https://img.shields.io/badge/-knowledge--base-blue?style=flat-square)](https://github.com/topics/knowledge-base) [![library](https://img.shields.io/badge/-library-blue?style=flat-square)](https://github.com/topics/library) [![llm](https://img.shields.io/badge/-llm-ff6f00?style=flat-square)](https://github.com/topics/llm) [![python](https://img.shields.io/badge/-python-3776ab?style=flat-square)](https://github.com/topics/python) [![software-project](https://img.shields.io/badge/-software--project-blue?style=flat-square)](https://github.com/topics/software-project) [![llm-tools](https://img.shields.io/badge/-llm--tools-blue?style=flat-square)](https://github.com/topics/llm-tools) [![llm-plugins](https://img.shields.io/badge/-llm--plugins-blue?style=flat-square)](https://github.com/topics/llm-plugins)
<!-- BADGES:END -->

An [LLM](https://llm.datasette.io/) plugin that loads entire folder contents as fragments, turning any directory into a chat-ready knowledge base.

## Installation

```bash
llm install llm-fragments-folder
```

Or install from source:

```bash
cd llm-fragments-folder
pip install -e .
```

## Usage

Two fragment loaders are provided: `folder:` for general document collections and `project:` for software projects.

### folder: - Load documents from a directory

```bash
# Chat against all docs in a folder
llm chat -f folder:./docs

# Ask a question about files in the current directory
llm -f folder:. "What are these documents about?"

# Combine with a specific model
llm -f folder:~/notes -m claude-sonnet-4-5 "Find all action items"

# Use with system fragments for custom instructions
llm -f folder:./research --sf "You are a research assistant" "Summarize the key findings"

# Only load specific file types
llm -f "folder:./docs?glob=*.md,*.txt" "Summarize the docs"
llm -f "folder:.?glob=*.json,*.yaml" "Explain these configs"
```

### project: - Load a software project (respects .gitignore)

```bash
# Explain a codebase
llm chat -f project:.

# Ask about a specific project
llm -f project:./my-app "What framework does this use?"

# Code review
llm -f project:. "Review this code for security issues"

# Architecture overview
llm -f project:~/repos/my-api -m claude-sonnet-4-5 "Describe the architecture"

# Only Python files
llm -f "project:.?glob=*.py" "Review this code"
```

The `project:` loader:

- Uses `git ls-files` when inside a git repo (most accurate)
- Falls back to parsing `.gitignore` patterns if git is not available
- Prepends a file tree summary as the first fragment
- Automatically skips `node_modules`, `__pycache__`, `.git`, `venv`, `dist`, `build`, etc.

### Combining with other fragments

Fragments compose naturally with each other and with LLM's other features:

```bash
# Folder + URL context
llm -f folder:./docs -f https://example.com/api-spec "Compare our docs to the spec"

# Folder + system prompt
llm -f folder:./meeting-notes --system "Extract action items with owners and dates" ""

# Project + GitHub issue
llm install llm-fragments-github
llm -f project:. -f issue:user/repo/42 "Implement this feature"
```

## What gets loaded

### With `?glob=` — you choose

When you specify `?glob=`, the default extension allowlist is bypassed entirely. Every file matching your patterns is included — nothing is silently dropped. Use gitignore-style glob patterns, comma-separated. Negate with `!` to exclude specific patterns.

```bash
# Only markdown files
llm -f "folder:./docs?glob=*.md" "Summarize these"

# Python files, excluding tests
llm -f "project:.?glob=*.py,!*_test.py,!tests/**" "Review the code"

# All dotfiles
llm -f "folder:~?glob=.*" "Explain my shell config"

# Multiple file types
llm -f "folder:.?glob=*.md,*.txt,*.json" "What's in here?"

# Files containing a keyword, excluding a type
llm -f "folder:.?glob=*finance*,!*.txt" "Summarize the finance docs"
```

### Without `?glob=` — sensible defaults

Without a filter, only files with recognized extensions and filenames are included. This curated allowlist keeps your context relevant — files like `.log`, `.lock`, and `package-lock.json` are excluded by default. If the defaults don't cover your needs, use `?glob=` to take full control. Supported types include:

- **Documents**: `.md`, `.qmd`, `.txt`, `.rst`, `.adoc`, `.tex`, `.org`
- **Code**: `.py`, `.js`, `.ts`, `.jsx`, `.tsx`, `.go`, `.rs`, `.java`, `.rb`, `.c`, `.cpp`, `.h`, `.cs`, `.swift`, `.kt`, `.scala`, `.r`, `.jl`, `.lua`, `.pl`, `.php`, `.sh`, `.bash`, `.zsh`, `.fish`, `.ps1`, `.bat`
- **Config**: `.json`, `.yaml`, `.yml`, `.toml`, `.ini`, `.cfg`, `.conf`, `.env`, `.properties`
- **Web**: `.html`, `.css`, `.scss`, `.sass`, `.less`, `.svg`, `.xml`, `.xsl`
- **Data**: `.csv`, `.tsv`, `.sql`, `.graphql`, `.proto`
- **Build**: `.dockerfile`, `.makefile`, `.cmake`, `.gradle`, `.sbt`
- **Other**: `.tf`, `.hcl`, `.ipynb`, `.bib`, `.vim`, `.el`
- **Dotfiles**: `.bashrc`, `.zshrc`, `.vimrc`, `.gitconfig`, `.tmux.conf`, `.profile`, `.editorconfig`, `.npmrc`, `.prettierrc`, `.eslintrc`, etc.
- **Special files**: `Makefile`, `Dockerfile`, `LICENSE`, `Jenkinsfile`, `Procfile`, `Gemfile`, etc.
- **Shebang scripts**: extensionless files starting with `#!`

### Always applies

- **Skipped directories**: `.git`, `.hg`, `.svn`, `node_modules`, `__pycache__`, `.tox`, `.nox`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `venv`, `.venv`, `env`, `.env`, `.eggs`, `dist`, `build`, `.idea`, `.vscode` (plus any `*.egg-info` directory).
- **Binary files**: Files containing null bytes are skipped automatically, even if matched by a glob pattern. No garbled PDFs or images in your context.
- **Safety limits**: Files larger than 1MB are skipped. Maximum 500 files per loader call.

## How it works

Each file becomes a separate LLM fragment, wrapped with a filename header:

```
--- path/to/file.py ---
<file contents>
```

This means LLM's fragment deduplication works at the file level. If you reference the same folder across multiple prompts, files that haven't changed won't be stored again in the log database.

## Development

```bash
# Clone and install for development
git clone https://github.com/michael-borck/llm-fragments-folder.git
cd llm-fragments-folder
uv sync

# Run tests
uv run pytest

# Lint and format
uv run ruff check .
uv run ruff format .

# Type checking
uv run mypy llm_fragments_folder.py
```

## Acknowledgments

- [Simon Willison](https://simonwillison.net/) for [LLM](https://llm.datasette.io/) and the excellent fragment plugin API
- Inspired by [files-to-prompt](https://github.com/simonw/files-to-prompt) and [llm-fragments-github](https://github.com/simonw/llm-fragments-github)

## License

Apache 2.0
