# External Scheduler Fallback

GitHub Actions scheduled workflows are best-effort. GitHub can delay or drop cron events during platform incidents, so three GitHub cron entries do not provide an independent failure domain.

## Goal

Add one external weekday check after the normal GitHub window. It should dispatch the workflow only when an expected edition has not been delivered, then alert if delivery still does not complete.

## Proposed design

Use a Cloudflare Worker Cron Trigger at 14:30 UTC on weekdays.

For each enabled edition (`tech`, `ai`, `webdev`, `infosec`):

1. Resolve `https://tldr.tech/api/latest/<edition>` to the actual edition date.
2. Check durable delivery state for `(edition, edition_date)`.
3. If missing, call GitHub's `workflow_dispatch` API with that edition.
4. Recheck after a bounded delay or let a separate monitor verify completion.
5. Send an alert when the dispatch fails or delivery remains missing.

## Authentication

Prefer a GitHub App installation token scoped only to this repository and Actions workflow dispatch. A fine-grained personal access token is acceptable for an initial version but must live only in the external scheduler's secret store.

Never commit either token to this repository.

## Durable state

Do not infer delivery only from GitHub run presence. A successful run may be a deliberate no-op, and a failed run may happen after delivery.

Use the same canonical identity as the pipeline:

```text
<edition>/<edition-date>
```

For email delivery, external monitoring needs a durable marker outside GitHub Actions cache. Options:

- Cloudflare KV or R2 marker objects
- A small Durable Object
- GitHub artifact or cache inspection only as a temporary bridge

R2 publishing can use the episode object itself as delivery state.

## Safety requirements

- Dispatch must remain idempotent.
- External retries must never bypass pipeline delivery guards.
- Use bounded retries with backoff.
- Alert on missing runs and missing delivery; workflow-local alerts cannot detect a cron event that never starts.
- Record edition, resolved date, dispatch result, and final delivery result without secrets.

## Acceptance checks

- Disabling GitHub cron for one day still results in one delivery per enabled edition.
- Repeated external checks do not produce duplicate email or R2 objects.
- An edition published on a different cadence is delivered once while still inside the configured lookback window.
- A GitHub API outage produces an alert and bounded retries, not an infinite loop.
- Missing external scheduler execution also produces an independent dead-man alert.
