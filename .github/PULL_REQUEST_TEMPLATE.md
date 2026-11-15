# Pull Request

## Summary
<!-- 1–2 sentences. What does this PR do and why? -->

## Build step
<!-- See the README for setup, tests and deploy. -->
- [ ] 1 — Copy, git init, remove VPC/ALB/ECS
- [ ] 2 — api/ without chat
- [ ] 3 — agent/ graph in-process
- [ ] 4 — chat relay (local) + build_index.py
- [ ] 5 — CDK: api Lambda + Function URL behind CloudFront
- [ ] 6 — AgentCore Runtime
- [ ] 7 — relay to AgentCore, caps, budget
- [ ] 8 — destroy old backend stacks, cost check

## Changes
<!-- Bullet list of the meaningful changes -->
-
-

## Type
- [ ] `feat` — new feature
- [ ] `fix` — bug fix
- [ ] `chore` — tooling / deps
- [ ] `docs` — documentation
- [ ] `refactor` — no behaviour change
- [ ] `test` — adding tests
- [ ] `ci` — CI/CD

## Testing
<!-- How did you verify this works? -->
-

## Checklist
- [ ] Pre-commit hooks pass locally (`pre-commit run --all-files`)
- [ ] CI is green
- [ ] Tests added/updated where applicable
- [ ] Docs updated (`README.md`, `DECISIONS.md`) where applicable
- [ ] `SESSION_LOG.md` updated if this concludes a session
- [ ] No secrets committed

## Screenshots / demo
<!-- Optional — drop screenshots, gifs, or Loom links here -->

## Related issues
<!-- e.g. Closes #12 -->
