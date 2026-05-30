# GitHub Publishing Checklist

This file helps prepare `/opt/hermes/project` for public or shared repository publication.

## Must-do before push

- Remove any hardcoded real chat IDs, user IDs, or private delivery targets from source files
- Move environment-specific identifiers into `.env` or documented env vars
- Ensure `.env` is ignored
- Ensure `archive/` is ignored unless sample data is intentionally published
- Ensure `doctor-reports/` is ignored unless sample reports are intentionally published
- Review absolute paths such as `/opt/hermes/project` and `/root/.hermes` if portability is required
- Add a LICENSE file
- Add `.gitignore`
- Review scripts for organization-specific hostnames, account IDs, or comments that reveal private topology

## Recommended repository extras

- `LICENSE`
- `.gitignore`
- `CONTRIBUTING.md`
- `SECURITY.md`
- `docs/architecture/`
- `docs/runbooks/`
- CI workflow for syntax + secret scanning

## Suggested .gitignore

```gitignore
.venv/
.env
archive/
doctor-reports/
__pycache__/
*.pyc
*.pyo
*.pyd
```
