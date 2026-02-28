# llm-fragments-folder

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

**Text file detection** is based on file extension and filename. Supported types include:

- Documents: `.md`, `.txt`, `.rst`, `.adoc`, `.tex`, `.org`
- Code: `.py`, `.js`, `.ts`, `.go`, `.rs`, `.java`, `.rb`, `.c`, `.cpp`, and many more
- Config: `.json`, `.yaml`, `.yml`, `.toml`, `.ini`, `.env`, `.cfg`
- Web: `.html`, `.css`, `.scss`, `.svg`, `.xml`
- Data: `.csv`, `.tsv`, `.sql`, `.graphql`
- Special files: `Makefile`, `Dockerfile`, `LICENSE`, etc.
- Shebang scripts: extensionless files starting with `#!`

**Always skipped directories**: `.git`, `node_modules`, `__pycache__`, `.venv`, `venv`, `dist`, `build`, `.idea`, `.vscode`, `.mypy_cache`, `.pytest_cache`, etc.

**Safety limits**: Files larger than 1MB are skipped. Maximum 500 files per loader call.

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
