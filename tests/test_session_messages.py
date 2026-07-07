"""Tests for SessionMessageConsumer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lightspeed_agentic.session_messages import SessionMessageConsumer


class TestMessageParsing:
    """Test that JSONL lines are correctly parsed into message dicts."""

    def test_parse_valid_message(self, tmp_path: Path) -> None:
        msg_file = tmp_path / "messages.jsonl"
        msg = {"message": "hello world", "timestamp": "2026-07-07T00:00:00Z"}
        msg_file.write_text(json.dumps(msg) + "\n")

        consumer = SessionMessageConsumer(path=msg_file)
        messages = consumer.get_new_messages()
        assert len(messages) == 1
        assert messages[0]["message"] == "hello world"
        assert messages[0]["timestamp"] == "2026-07-07T00:00:00Z"

    def test_parse_multiple_messages(self, tmp_path: Path) -> None:
        msg_file = tmp_path / "messages.jsonl"
        lines = [
            json.dumps({"message": "first", "timestamp": "2026-07-07T00:00:00Z"}),
            json.dumps({"message": "second", "timestamp": "2026-07-07T00:00:01Z"}),
        ]
        msg_file.write_text("\n".join(lines) + "\n")

        consumer = SessionMessageConsumer(path=msg_file)
        messages = consumer.get_new_messages()
        assert len(messages) == 2
        assert messages[0]["message"] == "first"
        assert messages[1]["message"] == "second"

    def test_skip_blank_lines(self, tmp_path: Path) -> None:
        msg_file = tmp_path / "messages.jsonl"
        content = (
            json.dumps({"message": "a", "timestamp": "2026-07-07T00:00:00Z"})
            + "\n\n"
            + json.dumps({"message": "b", "timestamp": "2026-07-07T00:00:01Z"})
            + "\n"
        )
        msg_file.write_text(content)

        consumer = SessionMessageConsumer(path=msg_file)
        messages = consumer.get_new_messages()
        assert len(messages) == 2

    def test_skip_malformed_json_lines(self, tmp_path: Path) -> None:
        msg_file = tmp_path / "messages.jsonl"
        content = (
            json.dumps({"message": "good", "timestamp": "2026-07-07T00:00:00Z"})
            + "\n"
            + "not valid json\n"
            + json.dumps({"message": "also good", "timestamp": "2026-07-07T00:00:01Z"})
            + "\n"
        )
        msg_file.write_text(content)

        consumer = SessionMessageConsumer(path=msg_file)
        messages = consumer.get_new_messages()
        assert len(messages) == 2
        assert messages[0]["message"] == "good"
        assert messages[1]["message"] == "also good"


class TestFileNotExists:
    """Test graceful handling when the messages file does not exist."""

    def test_returns_empty_when_file_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.jsonl"
        consumer = SessionMessageConsumer(path=missing)
        messages = consumer.get_new_messages()
        assert messages == []

    def test_picks_up_file_after_creation(self, tmp_path: Path) -> None:
        msg_file = tmp_path / "messages.jsonl"
        consumer = SessionMessageConsumer(path=msg_file)

        # First call: file doesn't exist
        assert consumer.get_new_messages() == []

        # Create the file
        msg_file.write_text(
            json.dumps({"message": "appeared", "timestamp": "2026-07-07T00:00:00Z"}) + "\n"
        )

        # Second call: file now exists
        messages = consumer.get_new_messages()
        assert len(messages) == 1
        assert messages[0]["message"] == "appeared"


class TestNewMessageDetection:
    """Test that only new messages (since last check) are returned."""

    def test_second_call_returns_only_new(self, tmp_path: Path) -> None:
        msg_file = tmp_path / "messages.jsonl"
        msg_file.write_text(
            json.dumps({"message": "first", "timestamp": "2026-07-07T00:00:00Z"}) + "\n"
        )

        consumer = SessionMessageConsumer(path=msg_file)
        first_batch = consumer.get_new_messages()
        assert len(first_batch) == 1

        # Append a new message
        with msg_file.open("a") as f:
            f.write(
                json.dumps({"message": "second", "timestamp": "2026-07-07T00:00:01Z"}) + "\n"
            )

        second_batch = consumer.get_new_messages()
        assert len(second_batch) == 1
        assert second_batch[0]["message"] == "second"

    def test_no_new_messages_returns_empty(self, tmp_path: Path) -> None:
        msg_file = tmp_path / "messages.jsonl"
        msg_file.write_text(
            json.dumps({"message": "only", "timestamp": "2026-07-07T00:00:00Z"}) + "\n"
        )

        consumer = SessionMessageConsumer(path=msg_file)
        consumer.get_new_messages()

        # No new messages appended
        assert consumer.get_new_messages() == []

    def test_multiple_new_messages_between_checks(self, tmp_path: Path) -> None:
        msg_file = tmp_path / "messages.jsonl"
        msg_file.write_text(
            json.dumps({"message": "initial", "timestamp": "2026-07-07T00:00:00Z"}) + "\n"
        )

        consumer = SessionMessageConsumer(path=msg_file)
        consumer.get_new_messages()

        with msg_file.open("a") as f:
            f.write(
                json.dumps({"message": "new1", "timestamp": "2026-07-07T00:00:01Z"}) + "\n"
            )
            f.write(
                json.dumps({"message": "new2", "timestamp": "2026-07-07T00:00:02Z"}) + "\n"
            )

        batch = consumer.get_new_messages()
        assert len(batch) == 2
        assert batch[0]["message"] == "new1"
        assert batch[1]["message"] == "new2"


class TestAppWiring:
    """Test that session_messages is wired into the FastAPI app module."""

    def test_app_module_exposes_consumer(self) -> None:
        from lightspeed_agentic.app import session_messages

        assert isinstance(session_messages, SessionMessageConsumer)
        assert session_messages.path == Path("/var/run/cli-session/messages.jsonl")


class TestEnvVarConfiguration:
    """Test CLI_SESSION_MESSAGES_PATH env var configuration."""

    def test_default_path(self) -> None:
        consumer = SessionMessageConsumer()
        assert consumer.path == Path("/var/run/cli-session/messages.jsonl")

    def test_env_var_overrides_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        custom = tmp_path / "custom.jsonl"
        monkeypatch.setenv("CLI_SESSION_MESSAGES_PATH", str(custom))
        consumer = SessionMessageConsumer()
        assert consumer.path == custom

    def test_explicit_path_overrides_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_path = tmp_path / "env.jsonl"
        explicit_path = tmp_path / "explicit.jsonl"
        monkeypatch.setenv("CLI_SESSION_MESSAGES_PATH", str(env_path))
        consumer = SessionMessageConsumer(path=explicit_path)
        assert consumer.path == explicit_path
