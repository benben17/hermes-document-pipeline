# Contributing

Thanks for considering a contribution.

## Development workflow

1. Fork or clone the repository.
2. Create a feature branch.
3. Set up the local environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

4. Make your changes.
5. Run the basic checks:

```bash
./project-tool --help
./project-tool doctor --bootstrap --json
./project-tool doctor --no-qq-send
python3 -m py_compile *.py
```

6. If your change touches onboarding, docs, or sample data, verify the public-safe examples still work:

```bash
./project-tool invoice examples/invoice.sample.json
./project-tool doc examples/document.sample.json
```

## Contribution guidelines

- Keep secrets out of the repo.
- Prefer environment-driven configuration over hardcoded operator-specific values.
- Keep the CLI simple and documented.
- Update README or `.env.example` when changing setup or configuration.
- Preserve tool-first design: reusable scripts and commands over one-off glue.

## Pull requests

A good PR should include:

- what changed
- why it changed
- any config/schema impact
- how it was tested

If a change affects installation, onboarding, or operational behavior, update the docs in the same PR.
