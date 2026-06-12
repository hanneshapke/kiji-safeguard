# Contributing to kiji-safeguard

Thanks for your interest in contributing! This guide covers how to set up a
development environment, the conventions we follow, and how releases are cut.

## Prerequisites

- Python 3.10 or newer (the test matrix runs 3.10–3.13)
- [uv](https://docs.astral.sh/uv/) for dependency and environment management

## Getting started

```bash
git clone https://github.com/hanneshapke/kiji-safeguard.git
cd kiji-safeguard

# Create the virtual environment and install all dependencies, including the
# dev group (pytest, ruff, commitizen, the FastAPI server extras, ...).
uv sync
```

`uv run <cmd>` executes a command inside the project environment without
needing to activate it manually.

## Development workflow

### Run the tests

```bash
uv run pytest
```

### Lint

We use [Ruff](https://docs.astral.sh/ruff/). Check and auto-fix before opening a
pull request:

```bash
uv run ruff check .          # report issues
uv run ruff check . --fix    # auto-fix what it can
uv run ruff format .         # format
```

CI runs `ruff check .` and the full pytest suite on every push and pull request
(`.github/workflows/linting-testing.yml`), so make sure both pass locally first.

### Try the CLI / server locally

```bash
uv run kiji-safeguard --help
uv run kiji-safeguard serve     # starts the registry server (FastAPI)
```

## Commit conventions

This project uses [Conventional Commits](https://www.conventionalcommits.org/)
via [Commitizen](https://commitizen-tools.github.io/commitizen/). Commit
messages drive the version bump and changelog, so format them as:

```
<type>: <short summary>
```

Common types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`.
A `feat` bumps the minor version, a `fix` bumps the patch. Breaking changes (a
`!` after the type, or a `BREAKING CHANGE:` footer) bump the minor while we are
on `0.x`.

You can commit interactively with:

```bash
uv run cz commit
```

## Pull requests

1. Fork the repo and create a branch off `main`.
2. Make your change, with tests where it makes sense.
3. Ensure `uv run ruff check .` and `uv run pytest` pass.
4. Open a pull request against `main` with a clear description.
5. Add yourself to [CONTRIBUTORS.md](CONTRIBUTORS.md).

## Releases

Releases are automated. Maintainers cut a release by bumping the version on a
branch and merging it to `main`:

```bash
uv run cz bump        # bumps [project].version, updates CHANGELOG.md, tags
```

Open a PR with that bump. When it merges to `main`, the release workflow
(`.github/workflows/release.yml`) detects the version change in
`pyproject.toml`, builds the sdist and wheel, publishes to PyPI via Trusted
Publishing (OIDC), and creates the matching GitHub Release.
