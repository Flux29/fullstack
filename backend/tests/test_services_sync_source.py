"""Tests for the sync-source service — connector credentials at rest.

Only the persistence boundary is mocked. The connector registry and the crypto run real,
because the service's whole job is the seam between them: plaintext secrets arrive once,
are Fernet-encrypted before the repository sees them, come back masked on every read, and
survive updates hidden behind the mask sentinel. A mistake in any direction here is a
credential leak or a credential loss, so each direction is pinned separately.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.config import settings
from app.core.crypto import decrypt_value, is_encrypted
from app.core.exceptions import BadRequestError, NotFoundError
from app.schemas.sync_source import SyncSourceClone, SyncSourceCreate, SyncSourceUpdate
from app.services.sync_source import (
    _SECRET_MASK,
    SyncSourceService,
    _decrypt_config,
    _encrypt_config,
)

S3_CONFIG = {"bucket": "docs", "prefix": "", "access_key_id": "AKIA123", "secret_access_key": "shhh"}
S3_SECRETS = ("access_key_id", "secret_access_key")


class MockSyncSource:
    """Mock sync source row for testing."""

    def __init__(
        self,
        id=None,
        organization_id=None,
        name="Docs bucket",
        connector_type="s3",
        collection_name="kb-main",
        config=None,
        sync_mode="new_only",
        schedule_minutes=None,
        is_active=True,
        last_sync_at=None,
        last_sync_status=None,
        last_error=None,
        created_at=None,
    ):
        self.id = id or uuid4()
        self.organization_id = organization_id
        self.name = name
        self.connector_type = connector_type
        self.collection_name = collection_name
        self.config = config if config is not None else _encrypt_config(dict(S3_CONFIG), "s3")
        self.sync_mode = sync_mode
        self.schedule_minutes = schedule_minutes
        self.is_active = is_active
        self.last_sync_at = last_sync_at
        self.last_sync_status = last_sync_status
        self.last_error = last_error
        self.created_at = created_at


@pytest.fixture
def service() -> SyncSourceService:
    return SyncSourceService(AsyncMock())


def _repo(**methods):
    mock = MagicMock()
    for name, value in methods.items():
        setattr(mock, name, AsyncMock(return_value=value) if not callable(value) else value)
    return mock


class TestCreatePersistsEncryptedAndReadsBackMasked:
    """The core flow: create source -> persist encrypted config -> read back masked."""

    @pytest.mark.anyio
    async def test_create_source_encrypts_at_rest_and_masks_on_read(self, service):
        created_row = {}

        async def capture_create(db, **kwargs):
            created_row.update(kwargs)
            return MockSyncSource(config=kwargs["config"])

        with patch("app.services.sync_source.sync_source_repo") as repo:
            repo.create = AsyncMock(side_effect=capture_create)

            read = await service.create_source(
                SyncSourceCreate(name="Docs bucket", connector_type="s3", config=dict(S3_CONFIG))
            )

        persisted = created_row["config"]
        for field in S3_SECRETS:
            assert is_encrypted(persisted[field]), f"{field} reached the repository in plaintext"
        assert persisted["bucket"] == "docs"

        for field in S3_SECRETS:
            assert read.config[field] == _SECRET_MASK, f"{field} leaked through the read model"
        assert read.config["bucket"] == "docs"

    @pytest.mark.anyio
    async def test_persisted_ciphertext_decrypts_back_to_the_original_plaintext(self, service):
        created_row = {}

        async def capture_create(db, **kwargs):
            created_row.update(kwargs)
            return MockSyncSource(config=kwargs["config"])

        with patch("app.services.sync_source.sync_source_repo") as repo:
            repo.create = AsyncMock(side_effect=capture_create)
            await service.create_source(
                SyncSourceCreate(name="Docs bucket", connector_type="s3", config=dict(S3_CONFIG))
            )

        for field in S3_SECRETS:
            assert decrypt_value(created_row["config"][field], settings.SECRET_KEY) == S3_CONFIG[field]

    @pytest.mark.anyio
    async def test_list_sources_masks_secrets_from_a_dict_config(self, service):
        with patch("app.services.sync_source.sync_source_repo") as repo:
            repo.get_all = AsyncMock(return_value=[MockSyncSource()])

            listing = await service.list_sources()

        assert listing.total == 1
        item = listing.items[0]
        for field in S3_SECRETS:
            assert item.config[field] == _SECRET_MASK
        assert item.config["bucket"] == "docs"

    @pytest.mark.anyio
    async def test_read_masks_secrets_from_a_json_string_config(self, service):
        row = MockSyncSource(config=json.dumps(_encrypt_config(dict(S3_CONFIG), "s3")))
        with patch("app.services.sync_source.sync_source_repo") as repo:
            repo.get_all = AsyncMock(return_value=[row])

            listing = await service.list_sources()

        for field in S3_SECRETS:
            assert listing.items[0].config[field] == _SECRET_MASK


class TestUpdatePreservesCredentials:
    """The mask-skip branch: a masked value in an update means keep what is stored."""

    @pytest.mark.anyio
    async def test_masked_secret_in_update_keeps_the_original_ciphertext(self, service):
        existing = MockSyncSource()
        original_cipher = existing.config["secret_access_key"]
        updated_row = {}

        async def capture_update(db, source_id, **kwargs):
            updated_row.update(kwargs)
            return MockSyncSource(id=existing.id, config=kwargs["config"])

        with patch("app.services.sync_source.sync_source_repo") as repo:
            repo.get_by_id = AsyncMock(return_value=existing)
            repo.update = AsyncMock(side_effect=capture_update)

            await service.update_source(
                str(existing.id),
                SyncSourceUpdate(
                    config={
                        "bucket": "new-bucket",
                        "access_key_id": _SECRET_MASK,
                        "secret_access_key": _SECRET_MASK,
                    }
                ),
            )

        merged = updated_row["config"]
        assert merged["secret_access_key"] == original_cipher
        assert decrypt_value(merged["secret_access_key"], settings.SECRET_KEY) == "shhh"
        assert merged["bucket"] == "new-bucket"

    @pytest.mark.anyio
    async def test_a_new_plaintext_secret_in_update_is_re_encrypted(self, service):
        existing = MockSyncSource()
        updated_row = {}

        async def capture_update(db, source_id, **kwargs):
            updated_row.update(kwargs)
            return MockSyncSource(id=existing.id, config=kwargs["config"])

        with patch("app.services.sync_source.sync_source_repo") as repo:
            repo.get_by_id = AsyncMock(return_value=existing)
            repo.update = AsyncMock(side_effect=capture_update)

            await service.update_source(
                str(existing.id), SyncSourceUpdate(config={"secret_access_key": "rotated"})
            )

        stored = updated_row["config"]["secret_access_key"]
        assert is_encrypted(stored)
        assert decrypt_value(stored, settings.SECRET_KEY) == "rotated"

    @pytest.mark.anyio
    async def test_clone_re_encrypts_without_corrupting_the_plaintext(self, service):
        existing = MockSyncSource()
        created_row = {}

        async def capture_create(db, **kwargs):
            created_row.update(kwargs)
            return MockSyncSource(config=kwargs["config"])

        with patch("app.services.sync_source.sync_source_repo") as repo:
            repo.get_by_id = AsyncMock(return_value=existing)
            repo.create = AsyncMock(side_effect=capture_create)

            await service.clone_source(str(existing.id), SyncSourceClone(collection_name="kb-two"))

        cloned = created_row["config"]
        assert cloned["secret_access_key"] != existing.config["secret_access_key"]  # fresh Fernet token
        assert decrypt_value(cloned["secret_access_key"], settings.SECRET_KEY) == "shhh"
        assert created_row["name"] == "Docs bucket (copy)"
        assert created_row["collection_name"] == "kb-two"


class TestErrorContracts:
    @pytest.mark.anyio
    async def test_create_source_with_unknown_connector_raises_bad_request(self, service):
        with pytest.raises(BadRequestError):
            await service.create_source(
                SyncSourceCreate(name="x", connector_type="carrier-pigeon", config={})
            )

    @pytest.mark.anyio
    async def test_create_source_missing_required_field_raises_bad_request(self, service):
        config = {key: value for key, value in S3_CONFIG.items() if key != "bucket"}
        with pytest.raises(BadRequestError):
            await service.create_source(SyncSourceCreate(name="x", connector_type="s3", config=config))

    @pytest.mark.anyio
    async def test_get_source_not_found_raises_not_found(self, service):
        with patch("app.services.sync_source.sync_source_repo") as repo:
            repo.get_by_id = AsyncMock(return_value=None)
            with pytest.raises(NotFoundError):
                await service.get_source(str(uuid4()))

    @pytest.mark.anyio
    async def test_update_source_raises_not_found_when_repo_update_returns_none(self, service):
        with patch("app.services.sync_source.sync_source_repo") as repo:
            repo.get_by_id = AsyncMock(return_value=MockSyncSource())
            repo.update = AsyncMock(return_value=None)
            with pytest.raises(NotFoundError):
                await service.update_source(str(uuid4()), SyncSourceUpdate(name="renamed"))

    @pytest.mark.anyio
    async def test_delete_source_verifies_existence_first(self, service):
        with patch("app.services.sync_source.sync_source_repo") as repo:
            repo.get_by_id = AsyncMock(return_value=None)
            repo.delete = AsyncMock()
            with pytest.raises(NotFoundError):
                await service.delete_source(str(uuid4()))
            repo.delete.assert_not_awaited()


class TestTriggerAndWorkerPaths:
    @pytest.mark.anyio
    async def test_trigger_sync_without_a_collection_raises_bad_request(self, service):
        with patch("app.services.sync_source.sync_source_repo") as repo:
            repo.get_by_id = AsyncMock(return_value=MockSyncSource(collection_name=None))
            with pytest.raises(BadRequestError):
                await service.trigger_sync(str(uuid4()))

    @pytest.mark.anyio
    async def test_trigger_sync_persists_a_log_and_dispatches_the_task(self, service):
        source = MockSyncSource()
        sync_log = MagicMock(id=uuid4())
        with (
            patch("app.services.sync_source.sync_source_repo") as repo,
            patch("app.services.sync_source.sync_log_repo") as log_repo,
            patch("app.worker.tasks.rag_tasks.sync_single_source_task") as task,
        ):
            repo.get_by_id = AsyncMock(return_value=source)
            log_repo.create = AsyncMock(return_value=sync_log)

            result = await service.trigger_sync(str(source.id))

        assert result is sync_log
        log_repo.create.assert_awaited_once()
        assert log_repo.create.call_args.kwargs["collection_name"] == "kb-main"
        task.delay.assert_called_once_with(str(source.id), str(sync_log.id))

    def test_decrypt_config_dict_is_the_workers_way_back_to_plaintext(self):
        encrypted = _encrypt_config(dict(S3_CONFIG), "s3")
        assert SyncSourceService.decrypt_config_dict(encrypted) == S3_CONFIG

    def test_list_connectors_still_marks_the_secret_fields(self):
        connectors = {item.type: item for item in SyncSourceService.list_connectors().items}
        s3_schema = connectors["s3"].config_schema
        for field in S3_SECRETS:
            assert s3_schema[field].secret, f"{field} lost its secret flag - masking would stop"


class TestSyncSourceConfigEncryption:
    """Pins the path the first governance-sample census found broken: connector secrets
    were encrypted against settings.CHANNEL_ENCRYPTION_KEY, a name Settings never defined,
    so these functions raised AttributeError the moment a secret field was present."""

    def test_secret_fields_round_trip_through_the_settings_key(self):
        config = {"bucket": "docs", "access_key_id": "AKIA123", "secret_access_key": "shhh"}

        encrypted = _encrypt_config(config, "s3")

        assert encrypted["bucket"] == "docs"
        assert is_encrypted(encrypted["access_key_id"])
        assert is_encrypted(encrypted["secret_access_key"])
        assert _decrypt_config(encrypted) == config

    def test_already_encrypted_values_are_not_double_encrypted(self):
        config = {"access_key_id": "AKIA123", "secret_access_key": "shhh", "bucket": "docs"}

        once = _encrypt_config(config, "s3")
        twice = _encrypt_config(once, "s3")

        assert twice == once
        assert _decrypt_config(twice) == config
