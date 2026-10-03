# Contributing

Use a local checkout when changing the library:

```console
python -m pip install -e ".[dev,docs,sqlite,postgres]"
```

Run the relevant tests, strict type checks, and formatting checks:

```console
python -m pytest
python -m pyright
python -m mypy
python -m ruff check .
python -m ruff format --check .
```

PostgreSQL integration tests read `DANKMEMER_TEST_POSTGRES_DSN`. Use a disposable
test database; those tests create and remove their own schemas.

## Compatibility and distributions

The test workflow checks Python 3.11–3.14 on Windows and Linux. Linux jobs
also run the PostgreSQL integration tests against a disposable service.
Separate jobs check the minimum runtime dependencies, strict typing, Ruff,
the documentation build, and distribution metadata. The quality job also checks
core and optional imports from the built wheel in a clean environment.

To check the minimum runtime versions locally:

```console
python -m pip install -c tests/minimum-dependencies.txt -e ".[dev,sqlite,postgres]"
python -m pytest -p no:cacheprovider
```

Build both distribution formats and check their metadata:

```console
python -m pip install build twine
python -m build
python -m twine check dist/*
```

The main distribution is `dankmemer.py`; `dankmemer` is a full mirror with the
same version, source, dependencies, and extras. Build the mirror from a separate
source copy with only `[project].name` changed to `dankmemer`. Keep the main
checkout's name unchanged. Renaming an archive does not change its metadata.

Before publishing, compare the packaged source, check both sets of archives,
and verify that neither includes environment files or credentials. Publish the
same version under both names and use a matching GitHub tag, such as `v1.0.0`.

Tests use local response fixtures and disposable databases; they do not contact
the Dank Memer API.

## Building the documentation

```console
python -m sphinx -W --keep-going -b html docs docs/_build/html
```

Open `docs/_build/html/index.html` to preview the result. The Read the Docs
configuration installs the documentation, Discord, and storage extras and treats
Sphinx warnings as build failures.

Write the published documentation for someone using the library in their own
application. Keep build instructions, implementation notes, and development
status out of the usage guides. Check changes to examples against the public API.
