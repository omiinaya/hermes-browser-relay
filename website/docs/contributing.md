# Contributing

Thanks for your interest in browser-relay!

## Quick Start

```bash
git clone https://github.com/omiinaya/hermes-browser-relay.git
cd browser-relay
make install
make test
```

## Code Standards

- **Python:** ruff lint + format (configured in `pyproject.toml`). Run `make lint` before committing.
- **JavaScript:** ESLint (flat config in `eslint.config.js`). Run `npm run lint`.
- **Tests:** All PRs must pass `make test` (119+ tests, +4 E2E with Chromium).

## Versioning

The canonical version is in `VERSION`. Before release, update `VERSION` and sync all versioned files:

```bash
make update-version VERSION=2.x.y
make check-version    # Verify sync
make test && make lint-all
```

## Pull Requests

- One feature/fix per PR
- Include tests for new functionality
- Verify with `make test && make lint-all`
- Commits should use conventional commit format: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`

## Questions?

Open an issue at https://github.com/omiinaya/hermes-browser-relay/issues.
