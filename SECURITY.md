# Security Policy

## Reporting a vulnerability

Please do not open a public GitHub issue for sensitive security findings.

Instead, report privately to the repository maintainer through a private channel you control. Include:

- affected component/file
- impact
- reproduction steps
- suggested remediation if available

## Scope

Pay special attention to:

- secret handling (`.env`, tokens, API keys)
- Hermes delivery targets and messaging integrations
- Cloudflare D1 access paths
- ChromaDB exposure and network boundaries
- accidental leakage in generated reports or outputs

## Secure development rules

- never commit real credentials
- do not hardcode personal chat IDs or private targets
- prefer `.env.example` for documentation
- review generated artifacts before publishing them publicly
- keep `examples/` fixtures synthetic and free of real customer/business data
- ensure CI/bootstrap workflows only use `.env.example` and never depend on private secrets for smoke tests
