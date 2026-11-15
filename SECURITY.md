# Security policy

MockSCIMTarget is a **test and demonstration tool**. It ships with fictitious data and
well-known demo tokens, and is designed to run on localhost or in an isolated test
environment. Don't expose it to the internet without the hardening described in the
documentation (API key, restricted ingress, TLS).

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please report vulnerabilities **privately** using GitHub's
[private vulnerability reporting](https://github.com/dddaaammmooo/scim-mock-target/security/advisories/new)
rather than opening a public issue. Include steps to reproduce and the affected version
(image tag or commit). You can expect an acknowledgement within a few days.

## Automated checks

Every push and pull request runs:

- **CodeQL**: Python, JavaScript and GitHub Actions analysis (security-extended queries)
- **Bandit**: Python static security analysis
- **pip-audit**: known-vulnerability audit of pinned Python dependencies
- **Trivy**: container image vulnerabilities, plus Dockerfile and Terraform misconfiguration
- **Gitleaks**: secret scanning across the full git history
- **Dependency review**: pull requests that introduce vulnerable dependencies are blocked

In addition, **GitHub secret scanning with push protection**, **Dependabot alerts** and
**Dependabot security updates** are enabled, and Dependabot keeps pip, GitHub Actions,
Docker and Terraform dependencies up to date.
