# CLAUDE.md

This file provides guidance to Claude Code and other AI assistants when working
in this repository.

## Repository status

**This repository is currently empty** — it was initialized but contains no
application code, build configuration, or history yet (no commits exist on the
default branch). The sections below capture the conventions that are already in
place and provide a scaffold to fill in as the codebase grows.

> When you add real code, **update this file**: replace the placeholder sections
> with the actual project structure, build/test/lint commands, and architecture
> notes. Keep it accurate — an out-of-date CLAUDE.md is worse than none.

## Project overview

_To be documented._ Add a short description of what this project does, its
primary language/runtime, and its intended users once the initial code lands.

## Repository structure

_To be documented._ Once files exist, describe the top-level layout, e.g.:

```
.
├── src/            # application source
├── tests/          # test suite
└── ...
```

## Development workflow

### Common commands

_To be documented._ When tooling is added, record the exact commands here so an
assistant can run them without guessing. For example:

- Install dependencies: `<command>`
- Build: `<command>`
- Run tests: `<command>`
- Run a single test: `<command>`
- Lint / format: `<command>`
- Start the app locally: `<command>`

### Git conventions

- **Branching**: Do all development on a dedicated feature branch — never commit
  directly to the default branch. Create the branch locally if it does not exist.
- **Commits**: Use clear, descriptive commit messages that explain the *why* of a
  change, not just the *what*.
- **Pushing**: Push with `git push -u origin <branch-name>`. On transient network
  failures, retry with exponential backoff.
- **Pull requests**: Only open a pull request when explicitly asked. If a PR
  template exists under `.github/`, mirror its structure.

## Conventions for AI assistants

- Keep this file up to date as the codebase evolves; treat it as the source of
  truth for project conventions.
- Match the style, naming, and structure of surrounding code when making changes.
- Prefer the smallest change that correctly solves the problem.
- Verify changes by building and running the test suite before considering work
  complete (add the relevant commands above once they exist).
- Do not introduce new dependencies or tools without a clear need.
