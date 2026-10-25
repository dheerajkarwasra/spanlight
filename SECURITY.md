# Security policy

Spanlight stores prompts, completions and API keys for the people who run it, so security reports are taken seriously. Thank you for taking the time to report one.

## Reporting a vulnerability

Report vulnerabilities privately through GitHub private vulnerability reporting:

**<https://github.com/dheerajkarwasra/spanlight/security/advisories/new>**

Do not open a public issue, pull request or discussion for a vulnerability. No email address is published for security reports; the form above is the only channel.

A useful report has:

- the affected component (server, web, Python SDK or deploy files) and the version, image tag or commit
- the steps to reproduce it, or a proof of concept
- the impact you believe it has
- whether you want to be credited, and under what name

Test only against your own instance, or against the public demo with an account you created. Do not include real user data, other people's secrets or live API keys in a report.

## Disclosure policy: 90 days

Spanlight follows coordinated disclosure with a 90-day limit.

- **Acknowledgement:** within 5 business days of your report.
- **Assessment:** after acknowledging, the maintainer tells you whether the report is accepted and how severe it is, and keeps you updated until it is fixed.
- **Fix or disclose:** within 90 days of your report the issue is fixed and released, or the details are disclosed publicly. If a fix needs longer, the maintainer asks you for an extension and explains why. You decide whether to agree.
- **Publication:** once a fix is released, the maintainer publishes a GitHub security advisory that credits you (unless you ask not to be named) and requests a CVE when the issue warrants one.

If you plan to publish your own write-up, please wait until the fix is released or the 90 days have passed, whichever comes first.

## Supported versions

Spanlight is pre-1.0. Only the latest `0.x` release is supported, for the server images (`ghcr.io/dheerajkarwasra/spanlight` and `ghcr.io/dheerajkarwasra/spanlight-web`) and for the `spanlight` Python SDK on PyPI. Fixes are released as a new version; they are not backported to older `0.x` releases.

Both images and their SBOMs are signed with Sigstore keyless signing by the release workflow. [Verify a release](docs-site/src/content/docs/self-hosting/upgrades.mdx#verify-a-release) has the `cosign verify` commands and how to pin the verified digest.

| Version | Supported |
|---|---|
| latest `0.x` release | yes |
| any older release | no |

## Scope

In scope:

- **Server:** the API, ingestion (native and OTLP), authentication and sessions, API keys, and the tenant isolation enforced by Postgres row-level security (`backend/`).
- **Web:** the dashboard and the security headers served by the web container (`frontend/`, `deploy/Caddyfile`).
- **Python SDK:** the `spanlight` package (`sdks/python/`).
- **Deploy configs:** the Dockerfile, Compose file and Railway files in `deploy/`, and the images and package built from them.

Out of scope:

- Exhausting the public demo's Anthropic budget. The demo's Anthropic spend is limited to about $1 per month: the job checks recorded month-to-date spend before each run and stops once it is reached, by design.
- Vulnerabilities in third-party services and software (GitHub, Railway, PyPI, Anthropic, Postgres, Caddy). Report those to their owners. A way Spanlight misuses one of them is in scope.
- Weaknesses that depend on an operator's own configuration, such as serving the dashboard over plain HTTP, a weak `SECRET_KEY`, or a reverse proxy that does not set the headers the deploy docs ask for.
- Denial of service by sending large volumes of traffic, and social engineering of the maintainer or other users.
- Reports from automated scanners that show no demonstrated impact.

## Threat model and known risks

- [Threat model](docs/security/threat-model.md): the assets, trust boundaries, threats and the controls that answer them, with the residual risks that are known and accepted. A report about a risk listed there is still welcome when it shows a worse impact than the one described.
- [OWASP ASVS Level 2 checklist](docs/security/asvs-l2-checklist.md): the status of each verification requirement, with a reference to the code that implements it.

## Safe harbour

If you act in good faith and follow this policy, the maintainer will not pursue or support legal action against you for your research. Acting in good faith means you report privately, avoid accessing data that is not yours, do not destroy data or degrade the service for others, and stop and report as soon as you confirm the issue.
