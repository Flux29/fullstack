"""Tests for repository layer."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.repositories import chat_file as chat_file_repo
from app.repositories import user as user_repo


class TestUserRepository:
    """Tests for user repository functions."""

    @pytest.fixture
    def mock_session(self):
        """Create a mock async session."""
        session = MagicMock()
        session.execute = AsyncMock()
        return session

    @pytest.mark.anyio
    async def test_get_by_email(self, mock_session):
        """Test get_by_email returns user."""
        mock_user = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_session.execute.return_value = mock_result

        result = await user_repo.get_by_email(mock_session, "test@example.com")

        assert result == mock_user

    @pytest.mark.anyio
    async def test_get_by_email_not_found(self, mock_session):
        """Test get_by_email returns None when not found."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await user_repo.get_by_email(mock_session, "notfound@example.com")

        assert result is None


class TestChatFileRepository:
    """Tests for chat file repository ownership scoping."""

    @pytest.fixture
    def mock_session(self):
        """Create a mock async session."""
        session = MagicMock()
        session.execute = AsyncMock()
        session.flush = AsyncMock()
        return session

    @pytest.mark.anyio
    async def test_get_many_with_user_id_filters_by_owner(self, mock_session):
        """get_many with user_id restricts the query to that user's files."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        await chat_file_repo.get_many(mock_session, [uuid4()], user_id=uuid4())

        stmt = mock_session.execute.call_args[0][0]
        assert "user_id" in str(stmt.whereclause)

    @pytest.mark.anyio
    async def test_get_many_without_user_id_is_the_trusted_path(self, mock_session):
        """get_many without user_id keeps the unfiltered trusted-path query."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        await chat_file_repo.get_many(mock_session, [uuid4()])

        stmt = mock_session.execute.call_args[0][0]
        assert "user_id" not in str(stmt.whereclause)

    @pytest.mark.anyio
    async def test_link_to_message_with_user_id_filters_by_owner(self, mock_session):
        """link_to_message with user_id only re-links files the actor owns."""
        await chat_file_repo.link_to_message(
            mock_session, message_id=uuid4(), file_ids=[uuid4()], user_id=uuid4()
        )

        stmt = mock_session.execute.call_args[0][0]
        assert "user_id" in str(stmt.whereclause)

    @pytest.mark.anyio
    async def test_link_to_message_without_user_id_is_the_trusted_path(self, mock_session):
        """link_to_message without user_id keeps the unfiltered trusted-path update."""
        await chat_file_repo.link_to_message(mock_session, message_id=uuid4(), file_ids=[uuid4()])

        stmt = mock_session.execute.call_args[0][0]
        assert "user_id" not in str(stmt.whereclause)
