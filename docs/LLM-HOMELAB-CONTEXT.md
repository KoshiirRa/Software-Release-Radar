# Software Release Radar: homelab context for an LLM

> Implementation snapshot: Software Release Radar 2.8.0, repository state reviewed 2026-08-20.
>
> Purpose: give an LLM enough precise context to analyse a homelab that runs Software Release Radar without mistaking the application for an updater, an asset-discovery authority, or an AI-dependent monitoring system.

## Identity and scope

Software Release Radar is a self-hosted Python 3.13 Flask application for monitoring software releases and reviewing upgrades. Its primary deployment is Docker Compose.

It answers questions such as:

- What is the newest upstream release of software I run?
- Which version is installed or detected in my homelab?
- Which machine, container, Compose service, stack, or folder is associated with it?
- Is the service reachable or healthy?
- Have I chosen to update, wait, ignore, investigate, or mark the release deployed?
- Which users should receive a notification?

It is **not** an unattended updater, configuration manager, vulnerability scanner, container orchestrator, or source of truth for the full homelab. It does not deploy upgrades. A human makes and executes the upgrade decision outside the application, then records the result.

Core release checking, due scheduling, version comparison, probes, health checks, and normal notifications are deterministic. The optional LLM integration interprets release information; it does not control whether monitoring works.

## Canonical mental model

Keep these four kinds of state separate:

1. **Upstream release state**: the latest GitHub release or tag found for a tracker.
2. **Installed state**: a manually entered version or a version detected by a local-service or inventory probe.
3. **Operational state**: machine, endpoint, container, health, and inventory-provider context.
4. **Decision state**: the operator's review status, priority, risk, timing, checklist, rollback notes, and deployment record.

An upstream release event does not prove that the homelab instance is outdated, vulnerable, compatible with the new release, or ready to upgrade. A failed probe does not invalidate a successful upstream check. A successful upstream check does not prove that the local service is healthy.

## Default runtime topology

The standard Compose deployment builds one application image and runs it as three services:

| Compose service | Process | Responsibility |
|---|---|---|
| `radar` | Gunicorn serving the Flask application | Browser UI, authentication, tracker and decision management, manual checks and probes, settings, job submission, and the optional assistant |
| `scheduler` | `python -m radar.scheduler` | Poll for due trackers, check upstream releases, create release events, and dispatch event notifications |
| `inventory-worker` | `python -m radar.inventory_worker` | Claim and process queued Portainer or Dockhand synchronisation and bulk-import jobs |

All three services mount the same Compose-scoped `radar-data` volume. The database path inside the containers is `/data/radar.db`. The web service publishes container port `8080`; the default host binding is `0.0.0.0:9120`.

```text
Browser or reverse proxy
          |
          v
   Flask/Gunicorn web -------- optional OpenAI-compatible API
          |       \
          |        \---- optional Portainer or Dockhand API
          |
          +--------------------+
                               v
Scheduler ----------------> SQLite <---------------- Inventory worker
   |                           ^                           |
   +---- GitHub API            |                           +---- Portainer/Dockhand API
   +---- local probes          |
   +---- notifications --------+
```

The scheduler and inventory worker depend on the web health check before starting. They have no Compose health checks of their own; confirm that they are running and inspect their logs. The inventory worker is expected to sleep when no work is queued.

## Authoritative state and persistence

SQLite is the application state authority. Connections enable foreign keys, WAL journal mode, a 30-second busy timeout, and a 30-second connection timeout. Schema creation and migrations are idempotent and occur during application initialisation.

Important logical records are:

| Record | Meaning |
|---|---|
| `trackers` | One tracked GitHub repository plus upstream, installed, probe, machine, and inventory-link state |
| `events` | A unique observed upstream version transition for a tracker |
| `upgrade_decisions` | Human review and deployment decision for a tracker and release version |
| `users` | Local account, role, and notification-channel preferences |
| `notification_deliveries` | Idempotency record per event, user, and channel, including policy skips |
| `settings` | Application settings, including encrypted integration values |
| `portainer_environments` and `portainer_services` | Normalised inventory state for both Portainer and Dockhand; legacy names remain for upgrade compatibility |
| `portainer_sync_jobs` and `portainer_import_jobs` | Durable background queues; legacy names also serve both providers |
| `ai_conversations`, `ai_messages`, and `ai_analyses` | Optional assistant history and cached analysis |
| `audit_log` | Security- and administration-relevant actions |
| `password_reset_tokens` | One-hour, single-use reset tokens stored as digests |

Do not infer provider choice from the legacy `portainer_*` table names. Provider columns namespace Portainer and Dockhand data. Existing Portainer installations remain compatible.

## Tracker lifecycle

### Creation and baseline

A tracker names a GitHub repository as `owner/repository` and chooses either the latest published GitHub Release or the latest Git tag. Prereleases are excluded unless explicitly enabled.

When a new tracker is first checked, Release Radar stores the current upstream version as a **baseline**. It does not create a new-release alert for that initial observation. This prevents a newly configured tracker from generating a historical alert merely because upstream already has releases.

### Scheduled checking

The scheduler wakes every 60 seconds by default. This is a polling interval, not the check interval of every tracker. Each tracker has its own refresh interval, allowed values being 1, 2, 3, 6, 12, 24, 48, 72, or 168 hours.

```text
scheduler cycle
  -> select enabled trackers
  -> retain only trackers whose last check is due
  -> optionally refresh linked inventory once for the batch
  -> fetch the configured GitHub release or tag for each tracker
  -> unchanged: update last-check success state
  -> changed: update tracker and insert one unique event
  -> optionally request automatic AI analysis
  -> probe installed service independently
  -> dispatch notifications for newly created events
```

An event is unique by `(tracker_id, version)`, so repeated checks do not create duplicate release events. The tracker retains both the Git tag/version and the human-facing release name because they can differ.

### Failure semantics

An upstream checker error updates `last_checked_at`, `last_status`, and `last_error`, but does not replace the last known upstream version or create a release event. A probe exception is caught separately and cannot turn an otherwise successful upstream release check into a checker failure.

Therefore:

- `last_status=error` means the latest upstream check failed, not that a new version exists.
- `last_probe_status=error` means local state could not be verified, not that GitHub monitoring failed.
- retained versions are last-known values and may be stale after an error.
- absence of an event is not proof that no newer release exists if checks have been failing or are overdue.

## Version and health interpretation

The tracker can contain:

- `current_version`: newest observed upstream Git tag/version;
- `current_release_name`: upstream display name;
- `installed_version`: operator-entered local version;
- `detected_installed_version`: most recent deterministic probe result; and
- timestamps and status fields for the last upstream check and last local probe.

The UI classifies these values after deterministic normalisation because release titles, Git tags, and container labels often format versions differently. Do not perform a raw string inequality and immediately declare an update. Preserve `unknown` or `needs attention` when comparison data is missing or ambiguous.

Supported installed-state probes are:

- manual version plus optional TCP reachability;
- HTTP automatic discovery;
- HTTP JSON path extraction;
- HTTP regular-expression extraction;
- constrained SSH Docker inspection; and
- Portainer or Dockhand inventory state.

HTTP regular expressions execute with pattern, response, output, and time bounds. SSH probes use strict host-key checking and a fixed Docker inspection command. They do not expose arbitrary remote shell execution.

## Inventory providers

Portainer and Dockhand are optional sources of Docker environment and container metadata. Provider adapters normalise their responses before the common reconciliation path persists environments, services, image metadata, Compose labels, ports, health, and tracker links.

Inventory has two execution paths:

- small connection tests and reads may run synchronously in the web application;
- full synchronisation and bulk imports are durable SQLite jobs handled by `inventory-worker`.

Important reconciliation rules:

- An explicit tracker-to-container mapping is authoritative.
- Automatic rebinding after container recreation is conservative and requires sufficient matching context.
- Provider names for environments, Compose services, containers, stacks, and folders remain source data.
- Local display-name overrides remain visible while source names continue synchronising; selecting **Follow provider** removes the override.
- A provider failure must not be treated as proof that previously known containers disappeared.
- With Dockhand, Release Radar tests each environment before accepting its container list. This is required because some Dockhand versions return an empty list for both a genuinely empty environment and a failed Docker connection.
- A failed Dockhand environment test preserves last-known services. A successful test followed by an empty list means the environment is genuinely empty and may be reconciled as such.

Inventory is therefore useful evidence about the Docker estate, but it is not guaranteed to cover non-Docker services, disconnected environments, ignored containers, or providers the operator has not configured.

## Upgrade review workflow

Each tracker and release version can have one decision record. Decision states are `review`, `update`, `wait`, `ignore`, and `deployed`. The record may also include:

- priority: low, normal, high, or urgent;
- risk: unknown, low, medium, high, or critical;
- maintenance date;
- checklist;
- rollback notes;
- change-record URL;
- decision notes;
- previous and deployed versions; and
- deployment timestamp and user.

These fields express operator intent and history. They do not execute an upgrade or verify that a deployment actually changed the running software. When analysing readiness, cross-check decision state against fresh detected versions and service health rather than assuming `deployed` is live verification.

## Notifications

Supported release-alert channels in the implementation are SMTP email, Pushover, and per-user Discord webhooks. SMTP also supports password recovery, which is separate from release-alert policy.

Release notification policy is evaluated from the administrator-wide switch, the per-tracker user preference (`on`, `off`, or `inherit`), the user's personal default, and the user's enabled channels. Delivery state is unique per `(event, user, channel)`.

Both successful sends and deliberate policy skips are persisted. This prevents duplicate sends and prevents muted historical events from becoming a backlog when notifications are later enabled. A failed delivery remains distinguishable from a policy skip.

## Optional AI integration

The assistant calls an OpenAI-compatible Chat Completions endpoint. It supports tracker questions, release comparison, and optional automatic analysis of a new release event.

Treat assistant content as advisory, untrusted text. It cannot establish installed state, health, compatibility, or successful deployment by itself. Core monitoring continues if the assistant is disabled or unavailable.

The application applies persistent, SQLite-backed controls across Gunicorn workers, including per-user and per-address request rates, user and global concurrency, bounded question length, provider timeout, response limits, and short-term reuse of matching analyses. Automatic analysis may incur provider cost when each new event is detected.

## Authentication and trust boundaries

Authentication uses local SQLite accounts with administrator and standard-user roles. Controls include PBKDF2-SHA256 password hashes, CSRF validation on state-changing browser requests, HTTP-only same-site cookies, optional secure-only cookies, session invalidation after a password change, database-backed login throttling, password-reset throttling, and single-use reset tokens stored as SHA-256 digests.

Forwarded headers are ignored unless `TRUST_PROXY_HEADERS=true`. If enabled, the application trusts one directly connected proxy for the original address, scheme, and host. Only enable this when clients cannot bypass that proxy and submit forged forwarding headers.

Treat all of the following as untrusted:

- GitHub release titles, bodies, tags, and URLs;
- Portainer and Dockhand responses, labels, names, and image metadata;
- remote HTTP probe responses;
- user-entered tracker and decision fields; and
- optional model output.

Reusable credentials are rejected over remote plain HTTP by default. This includes AI authorisation, Portainer API keys, Dockhand bearer tokens, and insecure SMTP authentication. `ALLOW_INSECURE_INTEGRATIONS=true` is an explicit trusted-network exception, not a general troubleshooting switch. Loopback integrations receive narrower exceptions.

## Secrets and configuration ownership

There are two configuration layers:

1. `.env` supplies deployment identity and bootstrap secrets before the application starts.
2. The administrator Settings UI stores operational settings and encrypted integration credentials in SQLite.

Deployment secrets include `SECRET_KEY`, `ENCRYPTION_KEY`, the initial administrator password hash, and optionally `GITHUB_TOKEN`. Integration secrets include SMTP credentials, Pushover tokens and user keys, Discord webhook URLs, Portainer or Dockhand tokens, and the optional AI API key.

The Fernet `ENCRYPTION_KEY` stays in `.env`; encrypted integration values stay in SQLite. Backing up only the database is insufficient for recovery because a replacement encryption key cannot decrypt existing secrets. Never expose `.env`, the live database, backup files, SSH keys, private hostnames, or tokens to an LLM.

## Network relationships to map in a homelab

For analysis, identify these independent paths:

| Source | Destination | Purpose | Typical requirement |
|---|---|---|---|
| User or reverse proxy | `radar:8080` via host port 9120 | Web UI and `/healthz` | HTTP on a trusted LAN or HTTPS at a trusted proxy |
| All app processes | `/data/radar.db` | Shared state | Same Compose volume and writable ownership |
| Scheduler/web | `api.github.com` | Release or tag lookup | Outbound HTTPS; optional least-privileged token |
| Web/worker | Portainer or Dockhand | Inventory | HTTPS and dedicated read-oriented token preferred |
| Web/scheduler | monitored service | TCP or HTTP probe | Route and firewall access from the container network |
| Web/scheduler | SSH host | fixed Docker inspection probe | TCP 22 or configured port, dedicated key, known host |
| Scheduler/web | SMTP, Pushover, or Discord | Notifications and/or recovery email | Outbound provider access with protected credentials |
| Web/scheduler | model endpoint | Optional analysis | HTTPS unless a permitted loopback or explicit trusted-network exception |

`host.docker.internal` is mapped to the Docker host gateway in the default Compose file. Remember that `localhost` inside a container refers to that container, not the Docker host or another homelab machine.

## Health, logs, and operations

The public health endpoint is unauthenticated:

```http
GET /healthz
```

Expected response shape:

```json
{"name":"Software Release Radar","status":"ok","version":"2.8.0"}
```

This confirms that the web process can answer; it does not prove scheduler progress, inventory-worker progress, GitHub reachability, notification delivery, probe success, or database backup validity.

Useful read-only checks are:

```bash
docker compose ps
docker compose logs --tail=100 radar scheduler inventory-worker
curl -fsS http://localhost:9120/healthz
```

The scheduler logs one summary per cycle with counts for checked trackers, changes, errors, and notification results. Detailed tracker results appear only when `RADAR_SCHEDULER_LOG_RESULTS=true`.

Expected idle conditions include no due trackers in a scheduler cycle and an inventory worker waiting without jobs. Concerning conditions include repeated scheduler-cycle exceptions, a growing queue of jobs stuck in `queued` or `running`, persistent checker errors, overdue `last_checked_at` timestamps, notification failures, or a web container that repeatedly becomes unhealthy.

## Backup and recovery invariants

Do not copy a live WAL-mode database file as the normal backup method. `scripts/backup.sh` uses SQLite's online backup API and validates the result with `PRAGMA integrity_check`.

The guarded restore process validates the requested backup, makes a pre-restore safety backup, stops database writers, restores the database, fixes ownership for the non-root application user, validates the result, and checks that the stack returns healthy. It attempts rollback to the safety backup if restoration fails.

A complete recovery plan preserves both:

- a verified database backup; and
- the protected `.env`, especially its original `ENCRYPTION_KEY`.

If SSH probes are used, the separately mounted `ssh/` material and `known_hosts` also require protected backup according to the operator's policy.

## Analysis rules for another LLM

When using this document to analyse a real deployment:

1. Prefer live, timestamped application state and logs over assumptions from configuration files.
2. State the observation time and timezone.
3. Redact usernames, emails, internal hostnames, addresses, repository details that are private, database contents, and all credentials.
4. Distinguish upstream-check freshness from installed-version freshness and service-health freshness.
5. Treat missing or failed evidence as unknown, not healthy, current, absent, or vulnerable.
6. Do not recommend changing or deleting tracker, inventory, decision, or notification state unless the user explicitly requests a mutation.
7. Never interpret an upgrade recommendation as authorisation to deploy it.
8. Never ask for `.env`, tokens, private keys, the live database, or raw backups. Request redacted excerpts or derived status instead.
9. Do not recommend enabling insecure transport merely to make an integration connect. Diagnose routing, DNS, certificates, trust stores, and reverse-proxy design first.
10. Do not assume `TRUST_PROXY_HEADERS=true` is safe without proving the application port is unreachable except through the trusted proxy.
11. Do not infer a removed container from an inventory-provider outage or a failed Dockhand environment test.
12. Do not infer successful upgrade deployment from a decision record alone; compare it with a fresh detected version and probe.

## Recommended homelab analysis output

A useful report should separate:

```text
Deployment status
  - web health, container state, image/application version
  - scheduler and worker liveness
  - database and volume relationship

Monitoring freshness
  - due or overdue trackers
  - upstream check successes and failures
  - installed-version and probe freshness

Upgrade queue
  - confirmed upstream changes
  - deterministic version comparison
  - decision status, risk, maintenance timing, and rollback readiness

Inventory coverage
  - provider and environment reachability
  - successful versus failed synchronisations
  - unmapped, ignored, stale, or ambiguously rebound services

Notification posture
  - configured channels and policy state
  - recent sent, skipped, and failed deliveries

Security and recovery
  - HTTPS and reverse-proxy boundary
  - integration transport exceptions
  - least-privileged tokens
  - verified backup freshness and preservation of the original encryption key

Unknowns and evidence needed
  - each uncertainty stated explicitly
  - safe, read-only commands or redacted fields that would resolve it
```

## Source map for code-aware analysis

Use these files when verifying behaviour against a newer checkout:

| Concern | Primary source |
|---|---|
| Flask application assembly | `radar/application.py`, `radar/web.py` |
| Database schema and migrations | `radar/db.py` |
| Upstream release checking and event creation | `radar/checker.py`, `radar/github.py` |
| Due scheduling | `radar/scheduler.py`, `radar/tracker_utils.py` |
| Version classification | `radar/versioning.py`, `radar/presentation.py` |
| Installed-service probes | `radar/probes.py`, `radar/safe_regex.py` |
| Inventory providers and reconciliation | `radar/inventory_providers.py`, `radar/portainer.py` |
| Background inventory jobs | `radar/inventory_jobs.py`, `radar/inventory_worker.py`, `radar/portainer_worker.py` |
| Notifications | `radar/notifications.py`, `radar/fleet_notifications.py` |
| Upgrade decisions | `radar/upgrade_workflow.py` |
| Optional assistant | `radar/ai_client.py`, `radar/analysis_service.py`, `radar/auto_analysis.py` |
| Authentication and request controls | `radar/auth.py`, `radar/auth_rate_limit.py`, `radar/security_controls.py` |
| Secret encryption and transport policy | `radar/secrets_store.py`, `radar/security_policy.py` |
| Compose topology | `docker-compose.yml`, `Dockerfile` |
| Backup and restore | `scripts/backup.sh`, `scripts/restore.sh` |

This file describes the reviewed implementation snapshot. If the running application version differs, treat this as orientation and re-verify the relevant source, configuration documentation, changelog, and live behaviour.
