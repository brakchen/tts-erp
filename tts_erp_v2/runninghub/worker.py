"""Restart-safe worker: intent commit -> one create -> track -> local stages."""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import re
import tempfile
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.runninghub import (
    AssetUpload,
    CloudQuota,
    GenerationArtifact,
    GenerationAttempt,
    GenerationBatch,
    GenerationJob,
    WorkflowVersion,
    new_id,
)
from tts_erp_v2.proxy.errors import DecryptionError, SigningError
from tts_erp_v2.proxy.token_service import load_runninghub_api_key
from tts_erp_v2.runninghub.client import (
    DEFINITE_REJECTION_CODES,
    FATAL_CONFIGURATION_CODES,
    RemoteError,
    RunningHubClient,
    download,
    sanitize,
)
from tts_erp_v2.runninghub.media import (
    MediaError,
    ObjectStore,
    dry_run_video,
    finalize,
    is_main,
    probe,
    validate_image,
)
from tts_erp_v2.runninghub.repository import (
    STOPPING_BATCH_STATES,
    complete_batch,
    lock_state,
    owned,
    pause_failure,
    recover_and_claim,
    reserve,
)
from tts_erp_v2.runninghub.workflow import WorkflowError, digest, prepare

log = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        sessions: Callable[[], Session],
        store: ObjectStore,
        client_factory: Callable[[str], RunningHubClient] | None = None,
    ) -> None:
        self.sessions, self.store = sessions, store
        self.client_factory = client_factory or (
            lambda key: RunningHubClient(
                key, os.environ.get("RUNNINGHUB_BASE_URL", "https://www.runninghub.cn")
            )
        )

    def run_once(self) -> bool:
        # Recovery/tracking is considered before dispatch, including paused batches.
        with self.sessions() as session, session.begin():
            attempt_id = recover_and_claim(session)
        if not attempt_id:
            with self.sessions() as session:
                ranked = (
                    select(
                        GenerationJob.id.label("job_id"),
                        func.row_number()
                        .over(
                            partition_by=GenerationJob.batch_id,
                            order_by=(GenerationJob.created_at, GenerationJob.id),
                        )
                        .label("position"),
                    )
                    .join(GenerationBatch, GenerationBatch.id == GenerationJob.batch_id)
                    .where(
                        GenerationBatch.status == "ACTIVE",
                        GenerationJob.status == "PLANNED",
                    )
                    .subquery()
                )
                candidates = list(
                    session.scalars(
                        select(GenerationJob)
                        .join(ranked, ranked.c.job_id == GenerationJob.id)
                        .where(ranked.c.position == 1)
                        .order_by(GenerationJob.created_at)
                    )
                )
                checked_quotas: set[str] = set()
                for job in sorted(candidates, key=lambda item: item.created_at):
                    batch = session.get(GenerationBatch, job.batch_id)
                    try:
                        key = (
                            load_runninghub_api_key(
                                session, batch.config["credentialRef"]
                            )
                            if batch.mode == "live"
                            else "dry-run"
                        )
                    except (SigningError, DecryptionError):
                        session.refresh(batch, with_for_update=True)
                        pause_failure(batch, "CREDENTIAL_UNAVAILABLE")
                        session.commit()
                        continue
                    quota_id = hashlib.sha256(key.encode()).hexdigest()
                    if quota_id in checked_quotas:
                        continue
                    checked_quotas.add(quota_id)
                    attempt_id = reserve(
                        session, job.id, quota_id, batch.config["concurrency"]
                    )
                    session.commit()
                    if attempt_id:
                        break
        if not attempt_id:
            return False
        self.process(attempt_id)
        return True

    def process(self, attempt_id: str) -> None:
        with self.sessions() as session:
            attempt = session.get(GenerationAttempt, attempt_id)
            job = session.get(GenerationJob, attempt.job_id)
            batch = session.get(GenerationBatch, job.batch_id)
            token, stage, mode, config, inputs, seed = (
                attempt.lease_token,
                job.status,
                batch.mode,
                batch.config,
                job.inputs,
                job.seed,
            )
            graph = session.get(WorkflowVersion, config["workflowVersionId"]).graph
            quota_id = attempt.quota_id
            stopping = batch.status in STOPPING_BATCH_STATES
        client = None
        try:
            if stage == "PREPARING" and stopping:
                with self.sessions() as session, session.begin():
                    state = owned(session, attempt_id, token)
                    if state:
                        self._finish_termination(session, *state)
                return
            if mode == "live" and stage != "FINALIZING":
                with self.sessions() as session:
                    key = load_runninghub_api_key(session, config["credentialRef"])
                if hashlib.sha256(key.encode()).hexdigest() != quota_id:
                    raise RemoteError("CREDENTIAL_REFERENCE_CHANGED")
                client = self.client_factory(key)
            if stage == "PREPARING":
                self._prepare(attempt_id, token, client, config, inputs, seed, graph)
            elif stage in {"QUEUED", "RUNNING"}:
                self._poll(attempt_id, token, client)
            elif stage == "DOWNLOADING":
                self._download(attempt_id, token, client)
            elif stage == "FINALIZING":
                self._finalize(attempt_id, token)
        except Exception as error:  # noqa: BLE001 -- boundary retains uncertain paid writes and emits only fixed diagnostics
            # Any unclassified exception is a local failure, never evidence that create failed.
            with self.sessions() as session, session.begin():
                state = owned(session, attempt_id, token)
                if state:
                    attempt, job, batch = state
                    if job.status == "SUBMITTING":
                        job.status, job.error = (
                            "SUBMISSION_UNKNOWN",
                            "CREATE_OUTCOME_REQUIRES_RECONCILIATION",
                        )
                    elif (
                        job.status == "PREPARING"
                        and batch.status in STOPPING_BATCH_STATES
                    ):
                        self._finish_termination(session, attempt, job, batch)
                        return
                    elif stage in {"QUEUED", "RUNNING"}:
                        if (
                            isinstance(error, (SigningError, DecryptionError))
                            or str(error) == "CREDENTIAL_REFERENCE_CHANGED"
                        ):
                            job.status, job.error = (
                                "TRACKING_INTERRUPTED",
                                "RESTORE_ORIGINAL_CREDENTIAL_REFERENCE",
                            )
                        else:
                            self._query_failure(session, attempt, job)
                    elif attempt.task_id and stage == "PREPARING":
                        job.status, job.error = (
                            "TRACKING_INTERRUPTED",
                            "RESUME_KNOWN_TASK_ID",
                        )
                    else:
                        job.status = {
                            "PREPARING": "PREPARATION_FAILED",
                            "DOWNLOADING": "DOWNLOAD_FAILED",
                            "FINALIZING": "FINALIZE_FAILED",
                        }.get(stage, "TRACKING_INTERRUPTED")
                        code = (
                            str(error).split(":", 1)[0]
                            if isinstance(
                                error, (RemoteError, MediaError, WorkflowError)
                            )
                            else "LOCAL_STAGE_FAILED"
                        )
                        job.error = (
                            code
                            if re.fullmatch(r"[A-Z_]{1,80}", code)
                            else "LOCAL_STAGE_FAILED"
                        )
                        if isinstance(error, RemoteError) and error.receipt:
                            attempt.receipt = {
                                **attempt.receipt,
                                "stageError": error.receipt,
                            }
                            if (
                                type(error.receipt.get("code")) is int
                                and error.receipt["code"] in FATAL_CONFIGURATION_CODES
                            ):
                                session.get(
                                    CloudQuota, attempt.quota_id
                                ).paused_reason = "UPSTREAM_CONFIGURATION_REJECTED"
                        if stage == "PREPARING":
                            attempt.reserved = False
                    attempt.error, attempt.lease_until = (
                        job.error or attempt.error,
                        None,
                    )
                    pause_failure(batch, job.error or job.status)
                    if batch.status == "TERMINATING":
                        complete_batch(session, batch)
            log.warning(
                "RunningHub stage interrupted: attempt=%s stage=%s", attempt_id, stage
            )
        finally:
            if client:
                client.close()

    def _uploaded(
        self,
        attempt_id: str,
        name: str,
        source: dict,
        path: Path,
        client: RunningHubClient | None,
    ) -> str:
        content_hash = self.store.fetch(source, path)
        if name == "video":
            probe(
                path
            )  # Fully decode/count reference frames before spending on generation.
        else:
            validate_image(path)
        with self.sessions() as session, session.begin():
            attempt = session.get(GenerationAttempt, attempt_id)
            attempt.asset_hashes = {**attempt.asset_hashes, name: content_hash}
            now = session.scalar(select(func.now()))
            cached = session.scalar(
                select(AssetUpload).where(
                    AssetUpload.quota_id == attempt.quota_id,
                    AssetUpload.sha256 == content_hash,
                )
            )
            if (
                cached
                and attempt.number == 1
                and cached.uploaded_at > now - timedelta(hours=20)
            ):
                return cached.file_name
            quota_id = attempt.quota_id
        file_name = client.upload(path) if client else "dry-run/" + content_hash
        with self.sessions() as session, session.begin():
            statement = insert(AssetUpload).values(
                id=new_id(),
                quota_id=quota_id,
                sha256=content_hash,
                file_name=file_name,
                uploaded_at=func.now(),
            )
            session.execute(
                statement.on_conflict_do_update(
                    index_elements=["quota_id", "sha256"],
                    set_={"file_name": file_name, "uploaded_at": func.now()},
                )
            )
        return file_name

    def _prepare(
        self,
        attempt_id: str,
        token: str,
        client: RunningHubClient | None,
        config: dict,
        inputs: dict,
        seed: int,
        graph: dict,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="rh-prepare-", dir=os.environ.get("RUNNINGHUB_SPOOL_DIR")
        ) as directory:
            root = Path(directory)
            image_ext = (
                mimetypes.guess_extension(inputs["image"]["contentType"]) or ".png"
            )
            video_ext = (
                mimetypes.guess_extension(inputs["video"]["contentType"]) or ".mp4"
            )
            image = self._uploaded(
                attempt_id,
                "image",
                inputs["image"],
                root / ("image" + image_ext),
                client,
            )
            video = self._uploaded(
                attempt_id,
                "video",
                inputs["video"],
                root / ("reference" + video_ext),
                client,
            )
        workflow, nodes = prepare(graph, image, video, seed, config["instructions"])
        body = {
            "workflowId": config["workflowId"],
            "instanceType": config["instanceType"],
            "workflow": json.dumps(workflow, ensure_ascii=False),
            "nodeInfoList": nodes,
        }
        with self.sessions() as session, session.begin():
            state = owned(session, attempt_id, token)
            if not state:
                return
            attempt, job, batch = state
            # Stop requests win until the create intent is durable.
            if batch.status in STOPPING_BATCH_STATES:
                self._finish_termination(session, attempt, job, batch)
                return
            if (
                batch.status != "ACTIVE"
                or session.get(CloudQuota, attempt.quota_id).paused_reason
            ):
                job.status, attempt.reserved, attempt.lease_until = (
                    "PLANNED",
                    False,
                    None,
                )
                return
            attempt.request_hash, job.status = digest(body), "SUBMITTING"
        if client:
            try:
                receipt = client.create(body)
            except RemoteError:
                with self.sessions() as session, session.begin():
                    attempt, job, batch = lock_state(session, attempt_id)
                    if (
                        job.current_attempt_id == attempt_id
                        and job.status == "SUBMITTING"
                    ):
                        job.status, job.error, attempt.lease_until = (
                            "SUBMISSION_UNKNOWN",
                            "CREATE_OUTCOME_REQUIRES_RECONCILIATION",
                            None,
                        )
                        pause_failure(batch, job.error)
                return
        else:
            receipt = {
                "code": 0,
                "data": {
                    "taskId": "dry-" + attempt_id,
                    "taskStatus": "QUEUED",
                    "promptTips": {"result": True},
                },
            }
        # Saving taskId is deliberately independent of a possibly expired worker lease.
        # Recovery may have marked UNKNOWN, but must never discard a late accepted ID.
        with self.sessions() as session, session.begin():
            attempt, job, batch = lock_state(session, attempt_id)
            data = receipt.get("data") if isinstance(receipt.get("data"), dict) else {}
            task_id = data.get("taskId")
            if (
                isinstance(task_id, (str, int))
                and not isinstance(task_id, bool)
                and str(task_id)
                and attempt.task_id is None
            ):
                attempt.task_id = str(task_id)
                attempt.reserved = True
                attempt.cloud_status = "ACCEPTED"
                if batch.status == "TERMINATED":
                    batch.status = "TERMINATING"
                if job.current_attempt_id == attempt_id:
                    job.status = "QUEUED"
                    attempt.lease_until = None
                    attempt.next_poll_at = session.scalar(
                        select(func.now())
                    ) + timedelta(
                        seconds=10 if client and batch.status != "TERMINATING" else 0
                    )
                else:
                    attempt.error = "LATE_ACCEPTANCE_REQUIRES_RECONCILIATION"
                    pause_failure(batch, attempt.error)
                    return
        # The accepted taskId is durable before parsing diagnostics.
        with self.sessions() as session, session.begin():
            attempt, job, batch = lock_state(session, attempt_id)
            attempt.receipt = {
                **attempt.receipt,
                **(sanitize(receipt, (client.key,)) if client else receipt),
            }
            code = receipt.get("code") if type(receipt.get("code")) is int else None
            if attempt.task_id:
                if attempt.last_polled_at is None and "cancel" not in attempt.receipt:
                    attempt.cloud_status = str(data.get("taskStatus") or "QUEUED")
                tips = data.get("promptTips")
                try:
                    tips = json.loads(tips) if isinstance(tips, str) else tips
                except ValueError:
                    tips = {"result": False}
                if isinstance(tips, dict) and (
                    tips.get("result") is False or tips.get("node_errors")
                ):
                    attempt.error = "WORKFLOW_VALIDATION_REPORTED_ERRORS"
                    pause_failure(
                        session.get(GenerationBatch, job.batch_id), attempt.error
                    )
                if code not in {0, 804, 813}:
                    attempt.error = "UPSTREAM_REPORTED_ERROR_WITH_TASK_ID"
                    pause_failure(batch, attempt.error)
            elif job.current_attempt_id != attempt_id or attempt.resolution is not None:
                return
            elif code in DEFINITE_REJECTION_CODES:
                attempt.lease_until = None
                job.status, job.error, attempt.reserved = (
                    "FAILED",
                    "CREATE_REJECTED",
                    False,
                )
                attempt.cloud_status = "REJECTED"
                pause_failure(session.get(GenerationBatch, job.batch_id), job.error)
                if batch.status == "TERMINATING":
                    complete_batch(session, batch)
            else:
                attempt.lease_until = None
                job.status, job.error = (
                    "SUBMISSION_UNKNOWN",
                    "CREATE_RESPONSE_MISSING_TASK_ID",
                )
                pause_failure(session.get(GenerationBatch, job.batch_id), job.error)

            if code in FATAL_CONFIGURATION_CODES:
                session.get(
                    CloudQuota, attempt.quota_id
                ).paused_reason = "UPSTREAM_CONFIGURATION_REJECTED"

    def _query_failure(
        self,
        session: Session,
        attempt: GenerationAttempt,
        job: GenerationJob,
        reason: str = "QUERY_INTERRUPTED",
    ) -> None:
        attempt.poll_failures += 1
        attempt.next_poll_at = session.scalar(select(func.now())) + timedelta(
            seconds=min(300, 10 * 2 ** min(attempt.poll_failures, 5))
        )
        attempt.lease_until = None
        attempt.error = reason
        if attempt.poll_failures >= 12:
            job.status, job.error = "TRACKING_INTERRUPTED", "RESUME_KNOWN_TASK_ID"

    @staticmethod
    def _finish_termination(
        session: Session,
        attempt: GenerationAttempt,
        job: GenerationJob,
        batch: GenerationBatch,
    ) -> None:
        job.status, job.error = "TERMINATED", None
        attempt.reserved = False
        attempt.error = attempt.lease_until = attempt.next_poll_at = None
        complete_batch(session, batch)

    def _cancel(
        self,
        attempt_id: str,
        token: str,
        client: RunningHubClient | None,
        task_id: str,
        result: dict | None,
    ) -> None:
        failure = None
        try:
            receipt = client.cancel(task_id) if client else {"simulated": True}
        except RemoteError as error:
            failure = str(error)
            receipt = {**error.receipt, "error": failure}
        with self.sessions() as session, session.begin():
            state = owned(session, attempt_id, token)
            if not state:
                return
            attempt, job, batch = state
            if result is not None and client is not None:
                attempt.cloud_status = result["status"]
                self._record_query(attempt, result, (client.key,))
                attempt.last_polled_at = session.scalar(select(func.now()))
            attempt.receipt = {**attempt.receipt, "cancel": receipt}
            if failure:
                self._query_failure(session, attempt, job, "CANCELLATION_UNCONFIRMED")
                return
            self._finish_termination(session, attempt, job, batch)

    def _poll(
        self, attempt_id: str, token: str, client: RunningHubClient | None
    ) -> None:
        with self.sessions() as session:
            attempt = session.get(GenerationAttempt, attempt_id)
            task_id = attempt.task_id
            job = session.get(GenerationJob, attempt.job_id)
            stopping = (
                session.get(GenerationBatch, job.batch_id).status == "TERMINATING"
            )
        if stopping and client is None:
            self._cancel(attempt_id, token, None, task_id, None)
            return
        try:
            result = (
                client.query(task_id)
                if client
                else {"status": "SUCCESS", "results": []}
            )
        except RemoteError:
            if not stopping:
                raise
            result = None
        if stopping and (
            result is None or result["status"] not in {"SUCCESS", "FAILED"}
        ):
            self._cancel(attempt_id, token, client, task_id, result)
            return
        with self.sessions() as session, session.begin():
            state = owned(session, attempt_id, token)
            if not state:
                return
            attempt, job, batch = state
            attempt.cloud_status = result["status"]
            self._record_query(attempt, result, (client.key,) if client else ())
            attempt.last_polled_at = session.scalar(select(func.now()))
            attempt.poll_failures, attempt.error, attempt.lease_until = 0, None, None
            attempt.next_poll_at = attempt.last_polled_at + timedelta(
                seconds=10 if client else 0
            )
            if result["status"] == "SUCCESS":
                job.status, attempt.reserved, attempt.next_poll_at = (
                    "DOWNLOADING",
                    False,
                    None,
                )
            elif result["status"] == "FAILED":
                job.status, job.error, attempt.reserved = (
                    "FAILED",
                    "CLOUD_TASK_FAILED",
                    False,
                )
                pause_failure(batch, job.error)
                if batch.status == "TERMINATING":
                    complete_batch(session, batch)
            elif result["status"] in {"QUEUED", "RUNNING"}:
                job.status = result["status"]
                if (attempt.last_polled_at - attempt.created_at).total_seconds() > 3600:
                    job.status, job.error = (
                        "TRACKING_INTERRUPTED",
                        "TRACKING_WAIT_LIMIT",
                    )
            else:
                attempt.error = "UNKNOWN_CLOUD_STATUS"
                pause_failure(batch, attempt.error)

    def _record_query(
        self, attempt: GenerationAttempt, result: dict, secrets: tuple[str, ...]
    ) -> None:
        """Every observed query can supply additional usage, including link refreshes."""
        attempt.receipt = {**attempt.receipt, "query": sanitize(result, secrets)}
        if isinstance(result.get("usage"), dict):
            attempt.usage = {
                **(attempt.usage or {}),
                **sanitize(result["usage"], secrets),
            }
        if isinstance(result.get("taskUsageList"), list):
            attempt.receipt = {
                **attempt.receipt,
                "taskUsageList": sanitize(result["taskUsageList"], secrets),
            }

    def _artifact(
        self, session: Session, attempt_id: str, kind: str, stored: dict, spec: dict
    ) -> None:
        statement = insert(GenerationArtifact).values(
            id=new_id(),
            attempt_id=attempt_id,
            kind=kind,
            bucket=stored["bucket"],
            object_key=stored["objectKey"],
            sha256=stored["sha256"],
            spec=spec,
        )
        session.execute(
            statement.on_conflict_do_nothing(index_elements=["attempt_id", "kind"])
        )

    def _renew(self, attempt_id: str, token: str) -> None:
        from tts_erp_v2.runninghub.repository import LEASE

        with self.sessions() as session, session.begin():
            state = owned(session, attempt_id, token)
            if state is None:
                raise MediaError("WORKER_FENCED")
            state[0].lease_until = session.scalar(select(func.now())) + LEASE

    def _download(
        self, attempt_id: str, token: str, client: RunningHubClient | None
    ) -> None:
        with self.sessions() as session:
            attempt = session.get(GenerationAttempt, attempt_id)
            existing = session.scalar(
                select(GenerationArtifact).where(
                    GenerationArtifact.attempt_id == attempt_id,
                    GenerationArtifact.kind == "raw",
                )
            )
            task_id = attempt.task_id
        with tempfile.TemporaryDirectory(
            prefix="rh-download-", dir=os.environ.get("RUNNINGHUB_SPOOL_DIR")
        ) as directory:
            root = Path(directory)
            if not existing:
                if client:
                    result = client.query(
                        task_id
                    )  # Refresh expired result links using the original ID.
                    with self.sessions() as session, session.begin():
                        state = owned(session, attempt_id, token)
                        if state is None:
                            return
                        self._record_query(state[0], result, (client.key,))
                        state[0].last_polled_at = session.scalar(select(func.now()))
                    if result["status"] != "SUCCESS" or not isinstance(
                        result.get("results"), list
                    ):
                        raise MediaError("SUCCESS_RESULTS_UNAVAILABLE")
                    videos = [
                        r
                        for r in result["results"]
                        if isinstance(r, dict)
                        and str(r.get("outputType", r.get("fileType", ""))).lower()
                        in {"mp4", "video", "video/mp4"}
                    ]
                    explicit = [r for r in videos if str(r.get("nodeId", "")) == "150"]
                    candidates = explicit or [r for r in videos if not r.get("nodeId")]
                    if not candidates or len(candidates) > 10:
                        raise MediaError("MAIN_VIDEO_CANDIDATES_INVALID")
                    valid: list[Path] = []
                    for index, candidate in enumerate(candidates):
                        self._renew(attempt_id, token)
                        path = root / f"candidate-{index}.mp4"
                        url = candidate.get("url") or candidate.get("fileUrl")
                        if not isinstance(url, str):
                            raise MediaError("RESULT_URL_MISSING")
                        download(url, path)
                        self._renew(attempt_id, token)
                        if is_main(probe(path)):
                            valid.append(path)
                    if len(valid) != 1:
                        raise MediaError("MAIN_VIDEO_AMBIGUOUS")
                    raw = valid[0]
                else:
                    raw = root / "raw.mp4"
                    dry_run_video(raw)
                self._renew(attempt_id, token)
                stored = self.store.put(raw, f"attempts/{attempt_id}/raw-{token}.mp4")
                raw_spec = probe(raw)
            with self.sessions() as session, session.begin():
                state = owned(session, attempt_id, token)
                if state:
                    if not existing:
                        self._artifact(session, attempt_id, "raw", stored, raw_spec)
                    state[1].status, state[0].lease_until, state[0].next_poll_at = (
                        "FINALIZING",
                        None,
                        None,
                    )

    def _finalize(self, attempt_id: str, token: str) -> None:
        with self.sessions() as session:
            raw_artifact = session.scalar(
                select(GenerationArtifact).where(
                    GenerationArtifact.attempt_id == attempt_id,
                    GenerationArtifact.kind == "raw",
                )
            )
        with tempfile.TemporaryDirectory(
            prefix="rh-final-", dir=os.environ.get("RUNNINGHUB_SPOOL_DIR")
        ) as directory:
            raw, final = Path(directory) / "raw.mp4", Path(directory) / "final.mp4"
            self.store.fetch_artifact(raw_artifact, raw)
            spec = finalize(raw, final)
            stored = self.store.put(final, f"attempts/{attempt_id}/final-{token}.mp4")
        with self.sessions() as session, session.begin():
            state = owned(session, attempt_id, token)
            if state:
                self._artifact(session, attempt_id, "final", stored, spec)
                state[1].status, state[1].error, state[0].lease_until = (
                    "SUCCEEDED",
                    None,
                    None,
                )
                session.flush()
                complete_batch(session, state[2])


def main() -> None:
    import argparse

    from tts_erp_v2.db.base import get_session_factory

    parser = argparse.ArgumentParser(
        description="Persistent RunningHub worker; active dry-run batches never call RunningHub"
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    worker = Worker(get_session_factory(), ObjectStore())
    while True:
        worker.run_once()
        if args.once:
            return
        time.sleep(1)


if __name__ == "__main__":
    main()
