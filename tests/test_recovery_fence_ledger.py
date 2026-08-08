from __future__ import annotations

import errno
import hashlib
import json
import multiprocessing
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from swinglab.cli import build_parser, main
from swinglab.backups.cli import run_recovery_fence_command
from swinglab.web.mobile_schema import HMACDigest, VersionedHMAC
from swinglab.web.users import UserStore


def _hold_cross_process_writer_lock(
    local_root: str,
    attempting,
    entered,
    release,
) -> None:
    from swinglab.web import recovery_fence_ledger as ledger_module

    attempting.set()
    with ledger_module._writer_lock(Path(local_root)):
        entered.set()
        release.wait(timeout=10)


def _api():
    try:
        from swinglab.web import recovery_fence_ledger as ledger_module
    except ImportError as exc:  # A feature-missing RED is a test failure, not collection.
        pytest.fail(f"recovery_fence_ledger is missing: {exc}")
    required = (
        "BaselineApprovals",
        "CutoverBaselineEvent",
        "CutoverBaselineInitializer",
        "RecoveryFenceError",
        "RecoveryFenceLedger",
        "PushEnvironmentCutoffEvent",
        "ReviewAccessRevisionEvent",
        "ScratchVerificationProof",
        "StartupRecoveryInputs",
        "TokenRevokeEvent",
        "VerifiedBackupFacts",
        "decide_startup_recovery",
    )
    missing = [name for name in required if not hasattr(ledger_module, name)]
    assert not missing, f"missing Gate 3B API: {', '.join(missing)}"
    return ledger_module


def _keyring(*, old: bool = True) -> VersionedHMAC:
    keys = {"recovery-k1": b"k" * 32}
    if old:
        keys["recovery-old"] = b"o" * 32
    return VersionedHMAC("recovery-k1", keys)


def _baseline_event(module, *, event_id: str | None = None, cutoff_at: float = 10.0):
    return module.CutoverBaselineEvent(
        event_id=event_id or str(uuid.uuid4()),
        cutoff_at=cutoff_at,
        lineage_id="11111111-2222-4333-8444-555555555555",
        minimum_backup_created_at=10.0,
        baseline_backup_id="20260807T120000Z-abcdef123456",
        manifest_sha256="a" * 64,
        schema_generation=1,
        baseline_db_checkpoint="b" * 64,
    )


class _MemoryRemote:
    def __init__(self, module):
        from swinglab.backups import store as store_module

        self._module = module
        self._store_module = store_module
        self.records: dict[str, bytes] = {}
        self.head_body: bytes | None = None
        self.head_etag: str | None = None
        self.calls: list[tuple[str, str | None]] = []
        self._etag_counter = 0
        self._lock = threading.RLock()
        self.cas_barrier: threading.Barrier | None = None
        self._barrier_threads: set[int] = set()
        self.fail_next_cas = False

    @staticmethod
    def record_key(sequence: int, record_hash: str) -> str:
        return f"fence/records/{sequence}-{record_hash}.json"

    @staticmethod
    def _object(key: str, body: bytes, etag: str):
        return SimpleNamespace(key=key, body=body, etag=etag)

    def read_head(self):
        with self._lock:
            self.calls.append(("read_head", None))
            if self.head_body is None:
                return None
            return self._object("fence/HEAD", self.head_body, self.head_etag)

    def read_record(self, key: str):
        with self._lock:
            self.calls.append(("read_record", key))
            try:
                body = self.records[key]
            except KeyError:
                error = getattr(
                    self._store_module,
                    "RecoveryFenceStoreError",
                    RuntimeError,
                )
                raise error("The recovery-fence record is missing.") from None
            etag = f'"{hashlib.sha256(body).hexdigest()}"'
            return self._object(key, body, etag)

    def put_immutable_record(self, key: str, body: bytes):
        with self._lock:
            self.calls.append(("put_record", key))
            existing = self.records.get(key)
            if existing is not None and existing != body:
                error = getattr(
                    self._store_module,
                    "RecoveryFenceStoreError",
                    RuntimeError,
                )
                raise error("A different immutable record already exists.")
            self.records[key] = body
            return self._object(
                key, body, f'"{hashlib.sha256(body).hexdigest()}"'
            )

    def compare_and_swap_head(self, body: bytes, *, expected_etag: str | None):
        barrier = self.cas_barrier
        thread_id = threading.get_ident()
        if barrier is not None:
            with self._lock:
                first = thread_id not in self._barrier_threads
                if first:
                    self._barrier_threads.add(thread_id)
            if first:
                barrier.wait(timeout=5)
        with self._lock:
            self.calls.append(("cas_head", expected_etag))
            conflict = getattr(
                self._store_module,
                "RecoveryFenceCASConflict",
                RuntimeError,
            )
            if self.fail_next_cas:
                self.fail_next_cas = False
                error = getattr(
                    self._store_module,
                    "RecoveryFenceStoreError",
                    RuntimeError,
                )
                raise error("synthetic CAS outage")
            if self.head_etag != expected_etag:
                raise conflict("synthetic CAS conflict")
            self._etag_counter += 1
            self.head_body = body
            self.head_etag = f'"head-{self._etag_counter}"'
            return self._object("fence/HEAD", body, self.head_etag)


def _ledger(tmp_path: Path, *, remote=None, keyring=None, db_path=None):
    module = _api()
    remote = remote or _MemoryRemote(module)
    ledger = module.RecoveryFenceLedger(
        remote_store=remote,
        keyring=keyring or _keyring(),
        local_root=tmp_path,
        db_path=db_path,
    )
    return module, remote, ledger


def test_genesis_is_canonical_hmac_chained_and_strictly_validated(tmp_path):
    module, remote, ledger = _ledger(tmp_path)
    event = _baseline_event(module)

    accepted = ledger.append_and_publish(event)
    chain = ledger.load_chain()

    assert accepted.sequence == 1
    assert accepted.kind == "cutover_baseline"
    assert [record.sequence for record in chain] == [1]
    payload = json.loads(accepted.body)
    assert payload["previous_record_key"] is None
    assert payload["previous_record_hash"] is None
    assert payload["chain_hmac_key_id"] == "recovery-k1"
    assert accepted.body == (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")
    assert accepted.record_key.endswith(
        f"/1-{payload['record_hash']}.json"
    )

    tampered = dict(payload)
    tampered["cutoff_at"] = 11.0
    remote.records[accepted.record_key] = (
        json.dumps(tampered, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    with pytest.raises(module.RecoveryFenceError, match="hash|HMAC|record"):
        ledger.load_chain()


def test_validated_chain_snapshot_binds_the_exact_head_etag_without_reread(tmp_path):
    module, remote, ledger = _ledger(tmp_path)
    published = ledger.append_and_publish(_baseline_event(module))
    remote.calls.clear()

    snapshot = ledger.load_chain_snapshot()

    assert isinstance(snapshot, module.ValidatedRecoveryChain)
    assert snapshot.head_etag == remote.head_etag
    assert snapshot.records[-1].record_hash == published.record_hash
    assert snapshot.records[-1].head_etag == remote.head_etag
    assert [call[0] for call in remote.calls].count("read_head") == 1
    assert ledger.load_chain() == snapshot.records


def test_chain_validation_reads_each_immutable_record_once(tmp_path):
    module, remote, ledger = _ledger(tmp_path)
    ledger.append_and_publish(_baseline_event(module))
    ledger.append_and_publish(
        module.TokenRevokeEvent.from_raw(
            event_id=str(uuid.uuid4()),
            cutoff_at=20.0,
            selector="one-read-selector",
            stored_token_verifier="one-read-verifier",
            keyring=_keyring(),
        )
    )
    remote.calls.clear()

    assert len(ledger.load_chain_snapshot().records) == 2
    assert [call[0] for call in remote.calls].count("read_record") == 2


def test_missing_head_record_key_or_hmac_key_fails_closed(tmp_path):
    module, remote, ledger = _ledger(tmp_path)
    with pytest.raises(module.RecoveryFenceError, match="HEAD|baseline"):
        ledger.load_chain()

    accepted = ledger.append_and_publish(_baseline_event(module))
    body = remote.records.pop(accepted.record_key)
    with pytest.raises(module.RecoveryFenceError, match="missing"):
        ledger.load_chain()
    remote.records[accepted.record_key] = body

    missing_key_ledger = module.RecoveryFenceLedger(
        remote_store=remote,
        keyring=VersionedHMAC("other", {"other": b"z" * 32}),
        local_root=tmp_path / "other-local",
    )
    with pytest.raises(module.RecoveryFenceError, match="key|HMAC"):
        missing_key_ledger.load_chain()


@pytest.mark.parametrize(
    "missing_payload_key",
    [
        "token_selector",
        "token_verifier",
        "push_project",
        "review_credential",
    ],
)
def test_load_chain_requires_every_closed_payload_hmac_key(
    tmp_path,
    missing_payload_key,
):
    module = _api()
    missing_key_id = f"payload-missing-{missing_payload_key}"
    raw_value = f"raw-{missing_payload_key}-must-not-leak"
    publishing_keyring = VersionedHMAC(
        "recovery-k1",
        {
            "recovery-k1": b"k" * 32,
            missing_key_id: b"m" * 32,
        },
    )
    remote = _MemoryRemote(module)
    publisher = module.RecoveryFenceLedger(
        remote_store=remote,
        keyring=publishing_keyring,
        local_root=tmp_path / "publisher",
    )
    publisher.append_and_publish(_baseline_event(module))
    current_selector = publishing_keyring.digest_with_key(
        "recovery-k1",
        module.MobileStateDomain.RECOVERY_SELECTOR,
        "current-selector",
    )
    current_verifier = publishing_keyring.digest_with_key(
        "recovery-k1",
        module.MobileStateDomain.RECOVERY_TOKEN_VERIFIER,
        "current-verifier",
    )
    missing_digest = publishing_keyring.digest_with_key(
        missing_key_id,
        (
            module.MobileStateDomain.RECOVERY_TOKEN_VERIFIER
            if missing_payload_key == "token_verifier"
            else module.MobileStateDomain.RECOVERY_SELECTOR
        ),
        raw_value,
    )
    if missing_payload_key in {"token_selector", "token_verifier"}:
        event = module.TokenRevokeEvent(
            event_id=str(uuid.uuid4()),
            cutoff_at=20.0,
            selector_hmac_key_id=(
                missing_key_id
                if missing_payload_key == "token_selector"
                else "recovery-k1"
            ),
            selector_hmac=(
                missing_digest
                if missing_payload_key == "token_selector"
                else current_selector
            ),
            token_verifier_hmac_key_id=(
                missing_key_id
                if missing_payload_key == "token_verifier"
                else "recovery-k1"
            ),
            token_verifier_hmac=(
                missing_digest
                if missing_payload_key == "token_verifier"
                else current_verifier
            ),
        )
    elif missing_payload_key == "push_project":
        event = module.PushEnvironmentCutoffEvent(
            event_id=str(uuid.uuid4()),
            cutoff_at=30.0,
            deployment_environment="production",
            expo_project_hmac_key_id=missing_key_id,
            expo_project_hmac=missing_digest,
            activation_revision=1,
            cutoff_revision=2,
            last_provider_started_at=27.0,
            last_provider_accepted_at=28.0,
            provider_may_accept_until=29.0,
            closed_at=30.0,
            cutoff_skew_seconds=1.0,
            provider_safe_after=31.0,
        )
    else:
        event = module.ReviewAccessRevisionEvent(
            event_id=str(uuid.uuid4()),
            cutoff_at=40.0,
            provider="apple",
            lane_revision=1,
            supported_builds=(
                {
                    "application_id": "com.caddieinsight.mobile",
                    "platform": "ios",
                    "version": "1.0",
                    "build": "1",
                },
            ),
            window_state="closed",
            purchase_test_state="complete",
            credential_hmacs=(
                HMACDigest(key_id=missing_key_id, digest=missing_digest),
            ),
        )
    published = publisher.append_and_publish(event)
    assert raw_value.encode() not in published.body
    assert {
        json.loads(body)["chain_hmac_key_id"] for body in remote.records.values()
    } == {"recovery-k1"}

    validator = module.RecoveryFenceLedger(
        remote_store=remote,
        keyring=VersionedHMAC("recovery-k1", {"recovery-k1": b"k" * 32}),
        local_root=tmp_path / "validator",
    )
    with pytest.raises(module.RecoveryFenceError, match="payload|key|HMAC") as caught:
        validator.load_chain()
    assert raw_value not in str(caught.value)
    assert missing_key_id not in str(caught.value)
    assert caught.value.__cause__ is None


def test_token_revoke_stores_only_domain_hmacs_and_exact_retry_is_idempotent(
    tmp_path,
):
    module, remote, ledger = _ledger(tmp_path)
    ledger.append_and_publish(_baseline_event(module))
    raw_selector = "selector-must-not-persist"
    raw_verifier = "stored-verifier-must-not-persist"
    event_id = str(uuid.uuid4())
    event = module.TokenRevokeEvent.from_raw(
        event_id=event_id,
        cutoff_at=20.0,
        selector=raw_selector,
        stored_token_verifier=raw_verifier,
        keyring=_keyring(),
    )

    first = ledger.append_and_publish(event)
    replay = ledger.append_and_publish(event)

    assert first.sequence == replay.sequence == 2
    assert first.record_hash == replay.record_hash
    assert len(remote.records) == 2
    assert raw_selector.encode() not in first.body
    assert raw_verifier.encode() not in first.body
    payload = json.loads(first.body)["payload"]
    assert payload["selector_hmac_key_id"] == "recovery-k1"
    assert payload["token_verifier_hmac_key_id"] == "recovery-k1"
    assert payload["selector_hmac"] != payload["token_verifier_hmac"]

    conflict = replace(event, cutoff_at=21.0)
    with pytest.raises(module.RecoveryFenceError, match="logical event|event_id"):
        ledger.append_and_publish(conflict)


def test_record_kinds_are_closed_and_reserved_shapes_store_no_provider_secrets(
    tmp_path,
):
    module, _remote, ledger = _ledger(tmp_path)
    ledger.append_and_publish(_baseline_event(module))
    push = ledger.append_and_publish(
        module.PushEnvironmentCutoffEvent(
            event_id=str(uuid.uuid4()),
            cutoff_at=60.0,
            deployment_environment="production",
            expo_project_hmac_key_id="recovery-k1",
            expo_project_hmac="c" * 64,
            activation_revision=3,
            cutoff_revision=4,
            last_provider_started_at=57.0,
            last_provider_accepted_at=58.0,
            provider_may_accept_until=59.0,
            closed_at=60.0,
            cutoff_skew_seconds=1.0,
            provider_safe_after=61.0,
        )
    )
    review = ledger.append_and_publish(
        module.ReviewAccessRevisionEvent(
            event_id=str(uuid.uuid4()),
            cutoff_at=70.0,
            provider="apple",
            lane_revision=2,
            supported_builds=(
                {
                    "application_id": "com.caddieinsight.mobile",
                    "platform": "ios",
                    "version": "1.0",
                    "build": "7",
                },
            ),
            window_state="closed",
            purchase_test_state="complete",
            credential_hmacs=(
                HMACDigest(key_id="recovery-k1", digest="d" * 64),
            ),
        )
    )

    assert {kind.value for kind in module.RecoveryRecordKind} == {
        "cutover_baseline",
        "token_revoke",
        "history_reset",
        "account_delete",
        "push_environment_cutoff",
        "review_access_revision",
    }
    push_payload = json.loads(push.body)["payload"]
    review_payload = json.loads(review.body)["payload"]
    assert "expo_project_id" not in push_payload
    assert set(push_payload) >= {"expo_project_hmac_key_id", "expo_project_hmac"}
    assert review_payload["credential_hmacs"] == [
        {"key_id": "recovery-k1", "digest": "d" * 64}
    ]
    assert not ({"account", "password", "credential"} & set(review_payload))
    assert [record.kind for record in ledger.load_chain()] == [
        "cutover_baseline",
        "push_environment_cutoff",
        "review_access_revision",
    ]


def test_integer_cutoff_normalizes_for_exact_lost_response_retry(tmp_path):
    module, _remote, ledger = _ledger(tmp_path)
    event = _baseline_event(module, cutoff_at=10)

    first = ledger.append_and_publish(event)
    replay = ledger.append_and_publish(event)

    assert replay.record_hash == first.record_hash


def test_parallel_writers_rebase_after_cas_conflict_without_duplicate_events(tmp_path):
    module = _api()
    remote = _MemoryRemote(module)
    first_ledger = module.RecoveryFenceLedger(
        remote_store=remote,
        keyring=_keyring(),
        local_root=tmp_path / "host-a",
    )
    second_ledger = module.RecoveryFenceLedger(
        remote_store=remote,
        keyring=_keyring(),
        local_root=tmp_path / "host-b",
    )
    first_ledger.append_and_publish(_baseline_event(module))
    remote.cas_barrier = threading.Barrier(2)
    events = [
        module.TokenRevokeEvent.from_raw(
            event_id=str(uuid.uuid4()),
            cutoff_at=30.0 + index,
            selector=f"selector-{index}",
            stored_token_verifier=f"verifier-{index}",
            keyring=_keyring(),
        )
        for index in range(2)
    ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda pair: pair[0].append_and_publish(pair[1]),
                zip((first_ledger, second_ledger), events),
            )
        )

    assert sorted(record.sequence for record in results) == [2, 3]
    chain = first_ledger.load_chain()
    assert [record.sequence for record in chain] == [1, 2, 3]
    assert {record.event_id for record in chain[1:]} == {
        event.event_id for event in events
    }
    assert len([call for call in remote.calls if call[0] == "cas_head"]) >= 4


def test_orphan_record_is_not_acceptance_and_exact_retry_resumes(tmp_path):
    module, remote, ledger = _ledger(tmp_path)
    ledger.append_and_publish(_baseline_event(module))
    event = module.TokenRevokeEvent.from_raw(
        event_id=str(uuid.uuid4()),
        cutoff_at=40.0,
        selector="selector",
        stored_token_verifier="verifier",
        keyring=_keyring(),
    )
    remote.fail_next_cas = True

    with pytest.raises(module.RecoveryFenceError):
        ledger.append_and_publish(event)
    assert len(remote.records) == 2
    assert len(ledger.load_chain()) == 1

    accepted = ledger.append_and_publish(event)
    assert accepted.sequence == 2
    assert len(ledger.load_chain()) == 2


def test_journaled_orphan_rebases_after_another_writer_advances_head(tmp_path):
    module = _api()
    remote = _MemoryRemote(module)
    writer_a = module.RecoveryFenceLedger(
        remote_store=remote,
        keyring=_keyring(),
        local_root=tmp_path / "writer-a",
    )
    writer_b = module.RecoveryFenceLedger(
        remote_store=remote,
        keyring=_keyring(),
        local_root=tmp_path / "writer-b",
    )
    writer_a.append_and_publish(_baseline_event(module))
    event_a = module.TokenRevokeEvent.from_raw(
        event_id=str(uuid.uuid4()),
        cutoff_at=41.0,
        selector="writer-a-selector",
        stored_token_verifier="writer-a-verifier",
        keyring=_keyring(),
    )
    journaled_identity: tuple[str, str, str] | None = None

    def lose_record_response(phase, record):
        nonlocal journaled_identity
        if phase == "record_published":
            journaled_identity = (
                record.record_key,
                record.record_hash,
                record.chain_hmac_key_id,
            )
            raise RuntimeError("synthetic lost record-published response")

    with pytest.raises(RuntimeError, match="lost record-published"):
        writer_a.append_and_publish(event_a, on_phase=lose_record_response)
    assert journaled_identity is not None
    original_orphan_key = journaled_identity[0]
    assert original_orphan_key in remote.records
    assert len(writer_a.load_chain()) == 1

    event_b = module.TokenRevokeEvent.from_raw(
        event_id=str(uuid.uuid4()),
        cutoff_at=42.0,
        selector="writer-b-selector",
        stored_token_verifier="writer-b-verifier",
        keyring=_keyring(),
    )
    assert writer_b.append_and_publish(event_b).sequence == 2

    retry_phases: list[tuple[str, int]] = []
    accepted = writer_a.append_and_publish(
        event_a,
        resume_record_identity=journaled_identity,
        on_phase=lambda phase, record: retry_phases.append((phase, record.sequence)),
    )

    assert accepted.sequence == 3
    assert accepted.record_key != original_orphan_key
    assert original_orphan_key in remote.records
    assert retry_phases == [("record_published", 3), ("head_published", 3)]
    chain = writer_a.load_chain()
    assert [record.event_id for record in chain] == [
        chain[0].event_id,
        event_b.event_id,
        event_a.event_id,
    ]
    assert sum(record.event_id == event_a.event_id for record in chain) == 1
    assert len(remote.records) == 4


def test_journaled_orphan_accepts_same_logical_event_winner_after_key_rotation(
    tmp_path,
):
    module = _api()
    remote = _MemoryRemote(module)
    original_keyring = VersionedHMAC(
        "recovery-k1",
        {"recovery-k1": b"k" * 32, "recovery-k2": b"n" * 32},
    )
    rotated_keyring = VersionedHMAC(
        "recovery-k2",
        {"recovery-k1": b"k" * 32, "recovery-k2": b"n" * 32},
    )
    writer_a = module.RecoveryFenceLedger(
        remote_store=remote,
        keyring=original_keyring,
        local_root=tmp_path / "writer-a",
    )
    writer_b = module.RecoveryFenceLedger(
        remote_store=remote,
        keyring=rotated_keyring,
        local_root=tmp_path / "writer-b",
    )
    writer_a.append_and_publish(_baseline_event(module))
    event = module.TokenRevokeEvent.from_raw(
        event_id=str(uuid.uuid4()),
        cutoff_at=43.0,
        selector="same-event-selector",
        stored_token_verifier="same-event-verifier",
        keyring=original_keyring,
    )
    journaled_identity: tuple[str, str, str] | None = None

    def lose_record_response(phase, record):
        nonlocal journaled_identity
        if phase == "record_published":
            journaled_identity = (
                record.record_key,
                record.record_hash,
                record.chain_hmac_key_id,
            )
            raise RuntimeError("synthetic lost record-published response")

    with pytest.raises(RuntimeError, match="lost record-published"):
        writer_a.append_and_publish(event, on_phase=lose_record_response)
    assert journaled_identity is not None
    winner = writer_b.append_and_publish(event)
    assert winner.sequence == 2
    assert winner.chain_hmac_key_id == "recovery-k2"
    assert winner.record_key != journaled_identity[0]

    accepted = writer_a.append_and_publish(
        event,
        resume_record_identity=journaled_identity,
    )

    assert accepted.record_key == winner.record_key
    assert accepted.chain_hmac_key_id == "recovery-k2"
    assert sum(record.event_id == event.event_id for record in writer_a.load_chain()) == 1
    assert len(remote.records) == 3


def test_local_record_head_lock_and_parent_fsync_are_published(tmp_path, monkeypatch):
    module = _api()
    fsynced_directories: list[Path] = []
    monkeypatch.setattr(
        module,
        "_fsync_directory",
        lambda path: fsynced_directories.append(Path(path)),
    )
    _, _, ledger = _ledger(tmp_path)

    accepted = ledger.append_and_publish(_baseline_event(module))

    state_root = tmp_path / ".recovery-fence"
    assert (tmp_path / ".recovery-fence.lock").read_bytes() == b"\0"
    assert (state_root / "HEAD").is_file()
    assert (state_root / "records" / Path(accepted.record_key).name).read_bytes() == (
        accepted.body
    )
    assert state_root in fsynced_directories
    assert state_root / "records" in fsynced_directories


def test_same_root_ledger_writers_are_serialized_before_remote_io(tmp_path):
    module = _api()

    class HoldingRemote(_MemoryRemote):
        def __init__(self):
            super().__init__(module)
            self.first_entered = threading.Event()
            self.release_first = threading.Event()
            self.read_entries = 0

        def read_head(self):
            with self._lock:
                self.read_entries += 1
                entry = self.read_entries
            if entry == 1:
                self.first_entered.set()
                assert self.release_first.wait(timeout=5)
            return super().read_head()

    remote = HoldingRemote()
    ledgers = [
        module.RecoveryFenceLedger(
            remote_store=remote,
            keyring=_keyring(),
            local_root=tmp_path,
        )
        for _ in range(2)
    ]
    event = _baseline_event(module)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(ledgers[0].append_and_publish, event)
        assert remote.first_entered.wait(timeout=5)
        second = pool.submit(ledgers[1].append_and_publish, event)
        assert not second.done()
        assert remote.read_entries == 1
        remote.release_first.set()
        results = (first.result(timeout=5), second.result(timeout=5))

    assert [record.sequence for record in results] == [1, 1]
    assert len(remote.records) == 1


def test_cross_process_writer_lock_serializes_the_fixed_lock_byte(tmp_path):
    context = multiprocessing.get_context("spawn")
    first_attempting = context.Event()
    first_entered = context.Event()
    first_release = context.Event()
    second_attempting = context.Event()
    second_entered = context.Event()
    second_release = context.Event()
    first = context.Process(
        target=_hold_cross_process_writer_lock,
        args=(str(tmp_path), first_attempting, first_entered, first_release),
    )
    second = context.Process(
        target=_hold_cross_process_writer_lock,
        args=(str(tmp_path), second_attempting, second_entered, second_release),
    )
    try:
        first.start()
        assert first_attempting.wait(timeout=10)
        assert first_entered.wait(timeout=10)
        second.start()
        assert second_attempting.wait(timeout=10)
        assert not second_entered.wait(timeout=0.5)
        first_release.set()
        assert second_entered.wait(timeout=10)
        second_release.set()
        first.join(timeout=10)
        second.join(timeout=10)
        assert first.exitcode == second.exitcode == 0
    finally:
        first_release.set()
        second_release.set()
        for process in (first, second):
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)

    assert (tmp_path / ".recovery-fence.lock").read_bytes() == b"\0"


@pytest.mark.parametrize("fault_at", ["open", "fsync"])
def test_directory_fsync_real_io_failures_fail_closed(tmp_path, monkeypatch, fault_at):
    module = _api()
    if fault_at == "open":
        monkeypatch.setattr(
            module.os,
            "open",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError(errno.EIO, "synthetic directory open failure")
            ),
        )
    else:
        monkeypatch.setattr(module.os, "open", lambda *_args, **_kwargs: 123)
        monkeypatch.setattr(
            module.os,
            "fsync",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError(errno.EIO, "synthetic directory fsync failure")
            ),
        )
        monkeypatch.setattr(module.os, "close", lambda *_args, **_kwargs: None)

    with pytest.raises(module.RecoveryFenceError, match="directory|durab"):
        module._fsync_directory(tmp_path)


def test_remote_chain_reconstructs_after_immediate_local_volume_loss(tmp_path):
    module, remote, ledger = _ledger(tmp_path / "lost")
    ledger.append_and_publish(_baseline_event(module))
    token_event = module.TokenRevokeEvent.from_raw(
        event_id=str(uuid.uuid4()),
        cutoff_at=50.0,
        selector="selector",
        stored_token_verifier="verifier",
        keyring=_keyring(),
    )
    ledger.append_and_publish(token_event)

    replacement = module.RecoveryFenceLedger(
        remote_store=remote,
        keyring=_keyring(),
        local_root=tmp_path / "replacement",
    )
    chain = replacement.load_chain()

    assert [record.kind for record in chain] == [
        "cutover_baseline",
        "token_revoke",
    ]
    assert not (tmp_path / "replacement" / ".recovery-fence" / "HEAD").exists()


def test_local_checkpoint_must_remain_in_the_validated_remote_ancestry(tmp_path):
    module = _api()
    db_path = tmp_path / "swinglab.db"
    store = UserStore(db_path, mobile_state_hmac=_keyring())
    store.close()
    module, remote, ledger = _ledger(tmp_path, db_path=db_path)
    baseline = ledger.append_and_publish(_baseline_event(module))
    accepted = ledger.append_and_publish(
        module.TokenRevokeEvent.from_raw(
            event_id=str(uuid.uuid4()),
            cutoff_at=80.0,
            selector="accepted-selector",
            stored_token_verifier="accepted-verifier",
            keyring=_keyring(),
        )
    )
    assert accepted.sequence == 2
    alternate_event = module.TokenRevokeEvent.from_raw(
        event_id=str(uuid.uuid4()),
        cutoff_at=81.0,
        selector="alternate-selector",
        stored_token_verifier="alternate-verifier",
        keyring=_keyring(),
    )
    alternate = module._build_record(
        alternate_event,
        sequence=2,
        previous_record_key=baseline.record_key,
        previous_record_hash=baseline.record_hash,
        keyring=_keyring(),
        record_key=remote.record_key,
    )
    remote.records[alternate.record_key] = alternate.body
    remote.head_body = module._head_body(alternate)
    remote.head_etag = '"alternate-head"'

    with pytest.raises(module.RecoveryFenceError, match="checkpoint|ancestry|diverg"):
        ledger.load_chain()


def test_read_only_checkpoint_mode_uses_uri_query_only_and_never_mutates_local_state(
    tmp_path,
    monkeypatch,
):
    module = _api()
    remote = _MemoryRemote(module)
    publisher = module.RecoveryFenceLedger(
        remote_store=remote,
        keyring=_keyring(),
        local_root=tmp_path / "publisher",
    )
    publisher.append_and_publish(_baseline_event(module))
    db_path = tmp_path / "swinglab.db"
    store = UserStore(db_path, mobile_state_hmac=_keyring())
    store.close()
    before = {
        path.name: (path.stat().st_size, path.stat().st_mtime_ns)
        for path in tmp_path.glob("swinglab.db*")
    }
    original_connect = module.sqlite3.connect
    connect_calls = []
    traced_statements = []

    def traced_connect(database, *args, **kwargs):
        connect_calls.append((database, dict(kwargs)))
        connection = original_connect(database, *args, **kwargs)
        connection.set_trace_callback(traced_statements.append)
        return connection

    monkeypatch.setattr(module.sqlite3, "connect", traced_connect)
    reader = module.RecoveryFenceLedger(
        remote_store=remote,
        keyring=_keyring(),
        local_root=tmp_path,
        db_path=db_path,
        checkpoint_mode="read_only",
    )

    assert len(reader.load_chain()) == 1
    assert reader.checkpoint_mode == "read_only"
    assert len(connect_calls) == 1
    database, options = connect_calls[0]
    assert isinstance(database, str) and database.startswith("file:")
    assert "mode=ro" in database
    assert options.get("uri") is True
    assert any(
        statement.replace(" ", "").casefold() == "pragmaquery_only=on"
        for statement in traced_statements
    )
    assert {
        path.name: (path.stat().st_size, path.stat().st_mtime_ns)
        for path in tmp_path.glob("swinglab.db*")
    } == before

    prior_head = remote.head_body
    prior_records = dict(remote.records)
    with pytest.raises(module.RecoveryFenceError, match="read-only"):
        reader.append_and_publish(
            module.TokenRevokeEvent.from_raw(
                event_id=str(uuid.uuid4()),
                cutoff_at=90.0,
                selector="read-only-selector",
                stored_token_verifier="read-only-verifier",
                keyring=_keyring(),
            )
        )
    assert remote.head_body == prior_head
    assert remote.records == prior_records


class _BackupVerifier:
    def __init__(self, facts):
        self.facts = facts
        self.calls: list[str] = []

    def create_and_verify(self, *, lineage_id: str):
        self.calls.append(lineage_id)
        return self.facts


class _ScratchVerifier:
    def __init__(self, module, *, verified: bool = True, wrong_hash: bool = False):
        self.module = module
        self.verified = verified
        self.wrong_hash = wrong_hash
        self.calls = 0

    def verify_exact(self, *, lineage_id, facts, record):
        self.calls += 1
        return self.module.ScratchVerificationProof(
            verified=self.verified,
            lineage_id=lineage_id,
            backup_id=facts.backup_id,
            manifest_sha256=facts.manifest_sha256,
            baseline_db_checkpoint=facts.baseline_db_checkpoint,
            record_hash=("f" * 64 if self.wrong_hash else record.record_hash),
        )


def _baseline_initializer(tmp_path, *, hook=None, scratch=None):
    module = _api()
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "swinglab.db"
    store = UserStore(db_path, mobile_state_hmac=_keyring())
    store.close()
    facts = module.VerifiedBackupFacts(
        backup_id="20260807T120000Z-abcdef123456",
        backup_created_at=100.0,
        schema_generation=1,
        manifest_sha256="a" * 64,
        manifest_database_sha256="b" * 64,
        baseline_db_checkpoint="b" * 64,
    )
    backup = _BackupVerifier(facts)
    scratch = scratch or _ScratchVerifier(module)
    remote = _MemoryRemote(module)
    ledger = module.RecoveryFenceLedger(
        remote_store=remote,
        keyring=_keyring(),
        local_root=tmp_path,
        db_path=db_path,
    )
    initializer = module.CutoverBaselineInitializer(
        ledger=ledger,
        db_path=db_path,
        backup_verifier=backup,
        scratch_verifier=scratch,
        lineage_factory=lambda: "11111111-2222-4333-8444-555555555555",
        phase_hook=hook,
    )
    approvals = module.BaselineApprovals(
        erasure_inventory_complete=True,
        dependent_routes_held=True,
        fresh_backup_authorized=True,
        scratch_restore_authorized=True,
    )
    return module, db_path, remote, initializer, backup, scratch, approvals


@pytest.mark.parametrize(
    "crash_phase",
    [
        "lineage_prepared",
        "backup_verified",
        "record_published",
        "head_published",
        "scratch_verified",
        "accepted",
    ],
)
def test_baseline_exact_retry_resumes_every_durable_phase(tmp_path, crash_phase):
    class CrashOnce:
        def __init__(self):
            self.fired = False

        def __call__(self, phase):
            if phase == crash_phase and not self.fired:
                self.fired = True
                raise RuntimeError("synthetic crash")

    hook = CrashOnce()
    module, db_path, remote, initializer, backup, scratch, approvals = (
        _baseline_initializer(tmp_path, hook=hook)
    )
    operation_id = "22222222-3333-4444-8555-666666666666"

    with pytest.raises(RuntimeError, match="synthetic crash"):
        initializer.initialize(
            operation_id=operation_id,
            request_hash="c" * 64,
            approvals=approvals,
        )
    accepted = initializer.initialize(
        operation_id=operation_id,
        request_hash="c" * 64,
        approvals=approvals,
    )
    replay = initializer.initialize(
        operation_id=operation_id,
        request_hash="c" * 64,
        approvals=approvals,
    )

    assert accepted.phase == replay.phase == "accepted"
    assert accepted.lineage_id == "11111111-2222-4333-8444-555555555555"
    assert len(remote.records) == 1
    assert len(backup.calls) == 1
    assert scratch.calls == 1
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT phase, baseline_db_checkpoint FROM "
            "mobile_recovery_baseline_journals WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        baseline = connection.execute(
            "SELECT baseline_backup_id, baseline_db_checkpoint FROM "
            "mobile_recovery_accepted_baselines"
        ).fetchone()
    assert row == ("accepted", "b" * 64)
    assert baseline == ("20260807T120000Z-abcdef123456", "b" * 64)
    journal_file = (
        tmp_path
        / ".recovery-fence"
        / "baseline-journals"
        / f"{operation_id}.json"
    )
    assert json.loads(journal_file.read_bytes())["phase"] == "accepted"


def test_baseline_orphan_retry_reuses_the_journaled_retained_chain_key(tmp_path):
    class CrashAfterRecord:
        def __call__(self, phase):
            if phase == "record_published":
                raise RuntimeError("synthetic record response loss")

    module, db_path, remote, initializer, backup, scratch, approvals = (
        _baseline_initializer(tmp_path, hook=CrashAfterRecord())
    )
    operation_id = "22222222-3333-4444-8555-666666666666"
    with pytest.raises(RuntimeError, match="response loss"):
        initializer.initialize(
            operation_id=operation_id,
            request_hash="c" * 64,
            approvals=approvals,
        )
    orphan_key = next(iter(remote.records))
    orphan_body = remote.records[orphan_key]
    assert remote.head_body is None

    rotated_keyring = VersionedHMAC(
        "recovery-k2",
        {
            "recovery-k1": b"k" * 32,
            "recovery-k2": b"n" * 32,
        },
    )
    rotated_ledger = module.RecoveryFenceLedger(
        remote_store=remote,
        keyring=rotated_keyring,
        local_root=tmp_path,
        db_path=db_path,
    )
    rotated_initializer = module.CutoverBaselineInitializer(
        ledger=rotated_ledger,
        db_path=db_path,
        backup_verifier=backup,
        scratch_verifier=scratch,
        lineage_factory=lambda: "11111111-2222-4333-8444-555555555555",
    )

    accepted = rotated_initializer.initialize(
        operation_id=operation_id,
        request_hash="c" * 64,
        approvals=approvals,
    )

    assert accepted.phase == "accepted"
    assert len(remote.records) == 1
    assert remote.records[orphan_key] == orphan_body
    assert json.loads(orphan_body)["chain_hmac_key_id"] == "recovery-k1"
    assert rotated_ledger.load_chain()[0].record_key == orphan_key


def test_baseline_refuses_a_second_cutover_operation_after_preparation_crash(
    tmp_path,
):
    class CrashAfterPrepare:
        def __init__(self):
            self.fired = False

        def __call__(self, phase):
            if phase == "lineage_prepared" and not self.fired:
                self.fired = True
                raise RuntimeError("synthetic preparation crash")

    module, _db_path, remote, initializer, backup, _scratch, approvals = (
        _baseline_initializer(tmp_path, hook=CrashAfterPrepare())
    )
    with pytest.raises(RuntimeError, match="preparation crash"):
        initializer.initialize(
            operation_id="22222222-3333-4444-8555-666666666666",
            request_hash="c" * 64,
            approvals=approvals,
        )

    with pytest.raises(module.RecoveryFenceError, match="operation|lineage|cutover"):
        initializer.initialize(
            operation_id="33333333-4444-4555-8666-777777777777",
            request_hash="d" * 64,
            approvals=approvals,
        )
    assert backup.calls == []
    assert remote.records == {}


def test_baseline_checkpoint_must_equal_verified_manifest_database_sha(tmp_path):
    module, _db_path, remote, initializer, backup, _scratch, approvals = (
        _baseline_initializer(tmp_path)
    )
    assert "manifest_database_sha256" in (
        module.VerifiedBackupFacts.__dataclass_fields__
    ), "verified backup facts must carry manifest database.sha256 separately"
    backup.facts = replace(
        backup.facts,
        manifest_database_sha256="e" * 64,
    )

    with pytest.raises(module.RecoveryFenceError, match="database|checkpoint|manifest"):
        initializer.initialize(
            operation_id="22222222-3333-4444-8555-666666666666",
            request_hash="c" * 64,
            approvals=approvals,
        )
    assert remote.records == {}


def test_baseline_rejects_unapproved_conflicting_or_unproven_inputs(tmp_path):
    module, db_path, remote, initializer, backup, scratch, approvals = (
        _baseline_initializer(tmp_path)
    )
    operation_id = "22222222-3333-4444-8555-666666666666"
    with pytest.raises(module.RecoveryFenceError, match="approval"):
        initializer.initialize(
            operation_id=operation_id,
            request_hash="c" * 64,
            approvals=replace(approvals, scratch_restore_authorized=False),
        )
    assert backup.calls == []
    assert remote.calls == []

    initializer.initialize(
        operation_id=operation_id,
        request_hash="c" * 64,
        approvals=approvals,
    )
    with pytest.raises(module.RecoveryFenceError, match="request|conflict"):
        initializer.initialize(
            operation_id=operation_id,
            request_hash="d" * 64,
            approvals=approvals,
        )

    wrong_scratch = _ScratchVerifier(module, wrong_hash=True)
    other = _baseline_initializer(tmp_path / "unproven", scratch=wrong_scratch)
    with pytest.raises(module.RecoveryFenceError, match="scratch|proof"):
        other[3].initialize(
            operation_id=operation_id,
            request_hash="c" * 64,
            approvals=other[-1],
        )
    with sqlite3.connect(other[1]) as connection:
        phase = connection.execute(
            "SELECT phase FROM mobile_recovery_baseline_journals"
        ).fetchone()[0]
        accepted_count = connection.execute(
            "SELECT COUNT(*) FROM mobile_recovery_accepted_baselines"
        ).fetchone()[0]
    assert phase == "head_published"
    assert accepted_count == 0


def test_accepted_baseline_exact_retry_still_requires_current_remote_chain(tmp_path):
    module, _db_path, remote, initializer, _backup, _scratch, approvals = (
        _baseline_initializer(tmp_path)
    )
    operation_id = "22222222-3333-4444-8555-666666666666"
    initializer.initialize(
        operation_id=operation_id,
        request_hash="c" * 64,
        approvals=approvals,
    )
    remote.head_body = None
    remote.head_etag = None

    with pytest.raises(module.RecoveryFenceError, match="HEAD|baseline"):
        initializer.initialize(
            operation_id=operation_id,
            request_hash="c" * 64,
            approvals=approvals,
        )


def test_cutover_baseline_rejects_generation_zero_backup_facts(tmp_path):
    module, _db_path, remote, initializer, backup, _scratch, approvals = (
        _baseline_initializer(tmp_path)
    )
    backup.facts = replace(backup.facts, schema_generation=0)

    with pytest.raises(module.RecoveryFenceError, match="schema generation"):
        initializer.initialize(
            operation_id="22222222-3333-4444-8555-666666666666",
            request_hash="c" * 64,
            approvals=approvals,
        )
    assert not remote.records


@pytest.mark.parametrize(
    "trigger, value",
    [
        ("recovery_fence_row_count", 1),
        ("baseline_journal_row_count", 1),
        ("nonterminal_revocation_count", 1),
        ("mobile_native_auth_enabled", True),
        ("mobile_device_management_enabled", True),
        ("mobile_privacy_enabled", True),
        ("history_reset_enabled", True),
        ("shopify_privacy_webhooks_enabled", True),
    ],
)
def test_startup_policy_models_every_remote_io_trigger(trigger, value):
    module = _api()
    facts = module.StartupRecoveryInputs(schema_generation=1)
    decision = module.decide_startup_recovery(replace(facts, **{trigger: value}))

    assert decision.remote_io_required is True
    assert decision.startup_allowed is False
    assert "accepted_baseline" in decision.missing_requirements

    ready = replace(
        facts,
        **{trigger: value},
        accepted_baseline=True,
        dedicated_credentials_available=True,
        immutable_record_round_trip_verified=True,
        head_cas_round_trip_verified=True,
        current_chain_validated=True,
    )
    assert module.decide_startup_recovery(ready).startup_allowed is True


def test_pristine_generation_zero_startup_policy_requires_zero_remote_io():
    module = _api()
    decision = module.decide_startup_recovery(
        module.StartupRecoveryInputs(schema_generation=0)
    )

    assert decision.remote_io_required is False
    assert decision.startup_allowed is True
    assert decision.missing_requirements == ()


def test_cli_is_inert_without_gate_or_all_approvals(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("CADDIE_RECOVERY_FENCE_ENABLED", raising=False)
    argv = [
        "recovery-fence-ledger",
        "initialize-baseline",
        "--sessions-dir",
        str(tmp_path),
        "--operation-id",
        "22222222-3333-4444-8555-666666666666",
    ]
    assert main(argv) == 2
    assert "CADDIE_RECOVERY_FENCE_ENABLED=true" in capsys.readouterr().err
    assert not (tmp_path / ".recovery-fence.lock").exists()

    monkeypatch.setenv("CADDIE_RECOVERY_FENCE_ENABLED", "true")
    assert main(argv) == 2
    assert "--confirm-erasure-inventory" in capsys.readouterr().err
    assert not (tmp_path / ".recovery-fence.lock").exists()

    assert main(
        argv
        + [
            "--confirm-erasure-inventory",
            "--confirm-dependent-routes-held",
            "--confirm-fresh-backup",
            "--confirm-scratch-restore",
        ]
    ) == 1
    assert "Verified-backup and exact scratch-restore" in capsys.readouterr().err
    assert not (tmp_path / ".recovery-fence.lock").exists()


def test_cli_suppresses_untrusted_verifier_errors(monkeypatch, capsys, tmp_path):
    sentinel = "SENTINEL-VERIFIER-SECRET"
    monkeypatch.setenv("CADDIE_RECOVERY_FENCE_ENABLED", "true")
    args = build_parser().parse_args(
        [
            "recovery-fence-ledger",
            "initialize-baseline",
            "--sessions-dir",
            str(tmp_path),
            "--operation-id",
            "22222222-3333-4444-8555-666666666666",
            "--confirm-erasure-inventory",
            "--confirm-dependent-routes-held",
            "--confirm-fresh-backup",
            "--confirm-scratch-restore",
        ]
    )

    class FailingInitializer:
        def initialize(self, **_kwargs):
            raise RuntimeError(sentinel)

    assert run_recovery_fence_command(args, initializer=FailingInitializer()) == 1
    assert sentinel not in capsys.readouterr().err


def test_cli_lazily_composes_real_baseline_adapters_only_after_every_gate(
    monkeypatch, capsys, tmp_path
):
    argv = [
        "recovery-fence-ledger",
        "initialize-baseline",
        "--sessions-dir",
        str(tmp_path / "sessions"),
        "--operator-root",
        str(tmp_path / "operator"),
        "--operation-id",
        "22222222-3333-4444-8555-666666666666",
    ]
    args = build_parser().parse_args(argv)
    calls = []

    class Initializer:
        def initialize(self, **kwargs):
            calls.append(("initialize", kwargs))
            return SimpleNamespace(operation_id=kwargs["operation_id"], phase="accepted")

    def compose(received):
        calls.append(("compose", received.operator_root))
        return SimpleNamespace(initializer=Initializer(), service_restorer=None)

    monkeypatch.delenv("CADDIE_RECOVERY_FENCE_ENABLED", raising=False)
    assert run_recovery_fence_command(args, composition_factory=compose) == 2
    assert calls == []
    monkeypatch.setenv("CADDIE_RECOVERY_FENCE_ENABLED", "true")
    assert run_recovery_fence_command(args, composition_factory=compose) == 2
    assert calls == []

    approved = build_parser().parse_args(
        argv
        + [
            "--confirm-erasure-inventory",
            "--confirm-dependent-routes-held",
            "--confirm-fresh-backup",
            "--confirm-scratch-restore",
        ]
    )
    assert run_recovery_fence_command(
        approved, composition_factory=compose
    ) == 0
    assert [name for name, _value in calls] == ["compose", "initialize"]
    assert "accepted" in capsys.readouterr().out


def test_restore_to_service_cli_is_separately_gated_and_only_prepares_scratch(
    monkeypatch, capsys, tmp_path
):
    bundle = tmp_path / "candidate-bundle"
    operator_root = tmp_path / "operator"
    sessions = tmp_path / "live-sessions"
    argv = [
        "recovery-fence-ledger",
        "restore-to-service",
        "--sessions-dir",
        str(sessions),
        "--bundle",
        str(bundle),
        "--operator-root",
        str(operator_root),
        "--confirm-dependent-routes-held",
        "--confirm-scratch-restore",
        "--confirm-service-restore",
    ]
    args = build_parser().parse_args(argv)
    calls = []

    class Restorer:
        def prepare(self, received_bundle):
            calls.append(("prepare", received_bundle))
            return SimpleNamespace(
                backup_id="20260807T120000Z-abcdef123456",
                working_dir=operator_root / "service-working-candidate",
            )

    def compose(received):
        calls.append(("compose", received.operator_root))
        return SimpleNamespace(initializer=None, service_restorer=Restorer())

    monkeypatch.delenv("CADDIE_RECOVERY_FENCE_ENABLED", raising=False)
    monkeypatch.delenv("CADDIE_RESTORE_ENABLED", raising=False)
    assert run_recovery_fence_command(args, composition_factory=compose) == 2
    assert calls == []
    monkeypatch.setenv("CADDIE_RECOVERY_FENCE_ENABLED", "true")
    assert run_recovery_fence_command(args, composition_factory=compose) == 2
    assert calls == []
    monkeypatch.setenv("CADDIE_RESTORE_ENABLED", "true")

    assert run_recovery_fence_command(args, composition_factory=compose) == 0
    assert calls == [
        ("compose", operator_root),
        ("prepare", bundle),
    ]
    output = capsys.readouterr().out
    assert "prepared" in output
    assert "service-working-candidate" in output
    assert not sessions.exists()


def test_top_level_cli_injects_gate3c_operator_composition(monkeypatch, tmp_path):
    from swinglab.backups import restore_service

    monkeypatch.setenv("CADDIE_RECOVERY_FENCE_ENABLED", "true")
    calls = []

    class Initializer:
        def initialize(self, **kwargs):
            calls.append(kwargs["operation_id"])
            return SimpleNamespace(
                operation_id=kwargs["operation_id"], phase="accepted"
            )

    def compose(_args):
        return SimpleNamespace(initializer=Initializer(), service_restorer=None)

    monkeypatch.setattr(restore_service, "compose_recovery_fence_operator", compose)
    result = main(
        [
            "recovery-fence-ledger",
            "initialize-baseline",
            "--sessions-dir",
            str(tmp_path / "sessions"),
            "--operator-root",
            str(tmp_path / "operator"),
            "--operation-id",
            "22222222-3333-4444-8555-666666666666",
            "--confirm-erasure-inventory",
            "--confirm-dependent-routes-held",
            "--confirm-fresh-backup",
            "--confirm-scratch-restore",
        ]
    )

    assert result == 0
    assert calls == ["22222222-3333-4444-8555-666666666666"]
