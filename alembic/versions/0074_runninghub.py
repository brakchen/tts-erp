"""RunningHub durable batches, immutable workflow versions and cloud reservations."""

from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision = "0074_runninghub"
down_revision = "0073_media_assets"
branch_labels = None
depends_on = None

DDL = """
CREATE SCHEMA runninghub;
CREATE TABLE runninghub.workflow_versions (
 id text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(),
 workflow_id text NOT NULL, content_hash text NOT NULL, graph jsonb NOT NULL, validation jsonb NOT NULL,
 UNIQUE(workflow_id, content_hash));
CREATE TABLE runninghub.profiles (
 id text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(), credential_ref text NOT NULL,
 workflow_id text NOT NULL, instance_type text NOT NULL CHECK(instance_type IN ('default','plus','ultra')),
 concurrency integer NOT NULL CHECK(concurrency BETWEEN 1 AND 5),
 workflow_version_id text REFERENCES runninghub.workflow_versions(id));
CREATE TABLE runninghub.templates (
 id text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(),
 human_id bigint NOT NULL REFERENCES media.digital_humans(id), name text NOT NULL CHECK(name IN ('A','B','C')), clips jsonb NOT NULL);
CREATE TABLE runninghub.batches (
 id text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(), request_hash text NOT NULL,
 mode text NOT NULL CHECK(mode IN ('dry-run','live')), status text NOT NULL, error text, config jsonb NOT NULL, created_by text);
CREATE TABLE runninghub.jobs (
 id text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(), batch_id text NOT NULL REFERENCES runninghub.batches(id),
 identity text NOT NULL, inputs jsonb NOT NULL, seed bigint NOT NULL CHECK(seed BETWEEN 0 AND 9007199254640991),
 status text NOT NULL, error text, current_attempt_id text, UNIQUE(batch_id, identity));
CREATE INDEX ix_rh_jobs_dispatch ON runninghub.jobs(status, created_at);
CREATE TABLE runninghub.quotas (id text PRIMARY KEY, limit_count integer NOT NULL CHECK(limit_count BETWEEN 1 AND 5), paused_reason text);
CREATE TABLE runninghub.attempts (
 id text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(), job_id text NOT NULL REFERENCES runninghub.jobs(id),
 number integer NOT NULL, quota_id text NOT NULL REFERENCES runninghub.quotas(id), reserved boolean NOT NULL,
 task_id text UNIQUE, cloud_status text, request_hash text, receipt jsonb NOT NULL, usage jsonb, error text,
 asset_hashes jsonb NOT NULL, lease_token text, lease_until timestamptz, next_poll_at timestamptz,
 last_polled_at timestamptz, poll_failures integer NOT NULL, resolution jsonb, UNIQUE(job_id, number));
CREATE INDEX ix_rh_attempts_quota ON runninghub.attempts(quota_id, reserved);
CREATE TABLE runninghub.asset_uploads (
 id text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(), quota_id text NOT NULL REFERENCES runninghub.quotas(id),
 sha256 text NOT NULL, file_name text NOT NULL, uploaded_at timestamptz NOT NULL, UNIQUE(quota_id, sha256));
CREATE TABLE runninghub.artifacts (
 id text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(), attempt_id text NOT NULL REFERENCES runninghub.attempts(id),
 kind text NOT NULL CHECK(kind IN ('raw','final')), bucket text NOT NULL, object_key text NOT NULL,
 sha256 text NOT NULL, spec jsonb NOT NULL, UNIQUE(attempt_id, kind));
INSERT INTO runninghub.profiles(id, credential_ref, workflow_id, instance_type, concurrency)
 VALUES ('default','env:RUNNINGHUB_API_KEY','2111762608670990337','plus',5);
INSERT INTO security.permissions(code, kind, name) VALUES ('page:runninghub','page','换装视频') ON CONFLICT DO NOTHING;
INSERT INTO security.role_permissions(role_code, permission_code)
 SELECT code, 'page:runninghub' FROM security.roles WHERE code IN ('admin','operator') ON CONFLICT DO NOTHING;
"""


def upgrade() -> None:
    # Frozen additive DDL; no imports of mutable application model definitions.
    for statement in DDL.split(";"):
        if statement.strip():
            # pi-lens-ignore: python-sql-injection
            op.execute(text(statement))


def downgrade() -> None:
    # Attempts and costs are audit facts. Never offer a destructive rollback path.
    raise RuntimeError(
        "RunningHub audit history is retained; downgrade requires a separately reviewed migration"
    )
