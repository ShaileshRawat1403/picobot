#!/usr/bin/env python3
"""Tests for Telegram channel implementation."""

from unittest.mock import MagicMock

import pytest

from picobot.bus.queue import MessageBus
from picobot.channels.telegram import (
    TelegramChannel,
    _markdown_to_telegram_html,
    _render_table_box,
    _strip_md,
)
from picobot.config.schema import TelegramConfig


class TestMarkdownConversion:
    """Test markdown to Telegram HTML conversion."""

    def test_strip_md_bold(self):
        assert _strip_md("**bold**") == "bold"
        assert _strip_md("__bold__") == "bold"

    def test_strip_md_italic(self):
        # _strip_md only handles double asterisks, not single
        assert _strip_md("**italic**") == "italic"

    def test_strip_md_code(self):
        assert _strip_md("`code`") == "code"

    def test_strip_md_strikethrough(self):
        assert _strip_md("~~strike~~") == "strike"

    def test_strip_md_combined(self):
        text = "**bold** and `code` with ~~strike~~"
        result = _strip_md(text)
        assert "bold" in result
        assert "code" in result
        assert "strike" in result

    def test_render_table_box_simple(self):
        lines = [
            "| Header 1 | Header 2 |",
            "| --- | --- |",
            "| Cell 1 | Cell 2 |",
        ]
        result = _render_table_box(lines)
        assert "Header 1" in result
        assert "Header 2" in result
        assert "Cell 1" in result
        assert "Cell 2" in result

    def test_render_table_box_no_separator(self):
        lines = ["No table here"]
        result = _render_table_box(lines)
        assert result == "No table here"

    def test_markdown_to_html_headers(self):
        assert _markdown_to_telegram_html("# Title") == "Title"
        assert _markdown_to_telegram_html("## Subtitle") == "Subtitle"

    def test_markdown_to_html_bold(self):
        result = _markdown_to_telegram_html("**bold text**")
        assert "<b>bold text</b>" in result

    def test_markdown_to_html_italic(self):
        result = _markdown_to_telegram_html("_italic text_")
        assert "<i>italic text</i>" in result

    def test_markdown_to_html_code(self):
        result = _markdown_to_telegram_html("`inline code`")
        assert "<code>inline code</code>" in result

    def test_markdown_to_html_code_block(self):
        result = _markdown_to_telegram_html("```python\nprint('hello')\n```")
        assert "<pre><code>" in result
        assert "print('hello')" in result

    def test_markdown_to_html_links(self):
        result = _markdown_to_telegram_html("[link text](https://example.com)")
        assert '<a href="https://example.com">link text</a>' in result

    def test_markdown_to_html_lists(self):
        result = _markdown_to_telegram_html("- Item 1\n- Item 2")
        assert "• Item 1" in result
        assert "• Item 2" in result

    def test_markdown_to_html_blockquote(self):
        result = _markdown_to_telegram_html("> quoted text")
        assert "quoted text" in result
        assert ">" not in result

    def test_markdown_to_html_escape_html(self):
        result = _markdown_to_telegram_html("<script>alert('xss')</script>")
        assert "&lt;script&gt;" in result

    def test_markdown_to_html_table(self):
        lines = [
            "| Col 1 | Col 2 |",
            "| --- | --- |",
            "| A | B |",
        ]
        result = _markdown_to_telegram_html("\n".join(lines))
        assert "Col 1" in result
        assert "Col 2" in result


class TestAuthorization:
    """Test Telegram authorization logic."""

    def setup_method(self):
        self.config = TelegramConfig(
            enabled=True,
            token="test_token",
            allow_from=["123456789", "987654321"],
        )
        self.bus = MagicMock(spec=MessageBus)
        self.channel = TelegramChannel(self.config, self.bus)

    def test_is_allowed_with_id_in_allowlist(self):
        assert self.channel.is_allowed("123456789") is True

    def test_is_allowed_with_id_not_in_allowlist(self):
        assert self.channel.is_allowed("111111111") is False

    def test_is_allowed_with_id_username_format(self):
        assert self.channel.is_allowed("123456789|username") is True

    def test_is_allowed_with_username_only(self):
        assert self.channel.is_allowed("987654321|testuser") is True

    def test_is_allowed_empty_allowlist_denies_all(self):
        # Empty allowlist denies all access (no superuser bypass in base)
        self.config.allow_from = []
        self.channel = TelegramChannel(self.config, self.bus)
        assert self.channel.is_allowed("123456789") is False

    def test_is_allowed_wildcard(self):
        self.config.allow_from = ["*"]
        self.channel = TelegramChannel(self.config, self.bus)
        assert self.channel.is_allowed("anyone") is True

    def test_is_allowed_empty_allowlist(self):
        self.config.allow_from = []
        self.channel = TelegramChannel(self.config, self.bus)
        assert self.channel.is_allowed("123456789") is False


class TestTelegramUtilities:
    def setup_method(self):
        self.config = TelegramConfig(
            enabled=True,
            token="test_token",
            allow_from=["123456789"],
            show_model_footer=True,
        )
        self.bus = MagicMock(spec=MessageBus)
        self.channel = TelegramChannel(self.config, self.bus)

    def test_apply_utility_footer_appends_served_model(self):
        text = self.channel._apply_utility_footer(
            "Hello from picobot",
            {"served_by": "gemini_oauth", "served_model": "gemini-2.5-pro"},
        )
        assert text == "Hello from picobot"

    def test_apply_utility_footer_skips_progress(self):
        text = self.channel._apply_utility_footer(
            "Working...",
            {"_progress": True, "served_by": "gemini_oauth", "served_model": "gemini-2.5-pro"},
        )
        assert text == "Working..."

    def test_apply_utility_footer_is_minimal_for_codex_fallback(self):
        text = self.channel._apply_utility_footer(
            "Hello from picobot",
            {"served_by": "openai_codex", "served_model": "openai-codex/gpt-5.1-codex"},
        )
        assert text.endswith("(codex fallback)")


class TestSenderId:
    """Test sender ID building."""

    def test_sender_id_with_username(self):
        user = MagicMock()
        user.id = 123456789
        user.username = "testuser"
        result = TelegramChannel._sender_id(user)
        assert result == "123456789|testuser"

    def test_sender_id_without_username(self):
        user = MagicMock()
        user.id = 123456789
        user.username = None
        result = TelegramChannel._sender_id(user)
        assert result == "123456789"


class TestMessageMetadata:
    """Test message metadata building."""

    def test_build_message_metadata_private(self):
        message = MagicMock()
        message.message_id = 1
        message.chat.type = "private"
        message.chat.is_forum = False
        message.reply_to_message = None
        user = MagicMock()
        user.id = 123456789
        user.username = "testuser"
        user.first_name = "Test"

        metadata = TelegramChannel._build_message_metadata(message, user)

        assert metadata["message_id"] == 1
        assert metadata["user_id"] == 123456789
        assert metadata["username"] == "testuser"
        assert metadata["first_name"] == "Test"
        assert metadata["is_group"] is False
        assert metadata["reply_to_message_id"] is None

    def test_build_message_metadata_group(self):
        message = MagicMock()
        message.message_id = 1
        message.chat.type = "group"
        message.chat.is_forum = False
        message.message_thread_id = 5
        message.reply_to_message = None
        user = MagicMock()
        user.id = 123456789

        metadata = TelegramChannel._build_message_metadata(message, user)

        assert metadata["is_group"] is True
        assert metadata["message_thread_id"] == 5


class TestTopicSessionKey:
    """Test topic session key derivation."""

    def test_private_chat_no_topic(self):
        message = MagicMock()
        message.chat.type = "private"
        message.message_thread_id = None
        result = TelegramChannel._derive_topic_session_key(message)
        assert result is None

    def test_group_chat_with_topic(self):
        message = MagicMock()
        message.chat.type = "supergroup"
        message.chat_id = 123
        message.message_thread_id = 5
        result = TelegramChannel._derive_topic_session_key(message)
        assert result == "telegram:123:topic:5"


class TestMediaType:
    """Test media type detection."""

    def test_get_media_type_photo(self):
        assert TelegramChannel._get_media_type("photo.jpg") == "photo"
        assert TelegramChannel._get_media_type("photo.png") == "photo"
        assert TelegramChannel._get_media_type("image.jpeg") == "photo"

    def test_get_media_type_voice(self):
        assert TelegramChannel._get_media_type("voice.ogg") == "voice"

    def test_get_media_type_audio(self):
        assert TelegramChannel._get_media_type("audio.mp3") == "audio"
        assert TelegramChannel._get_media_type("audio.m4a") == "audio"

    def test_get_media_type_document(self):
        assert TelegramChannel._get_media_type("document.pdf") == "document"
        assert TelegramChannel._get_media_type("file.txt") == "document"


class TestExtractReplyContext:
    """Test reply context extraction."""

    def test_extract_reply_context_no_message(self):
        result = TelegramChannel._extract_reply_context(None)
        assert result is None

    def test_extract_reply_context_no_reply(self):
        class MockMessage:
            reply_to_message = None
        result = TelegramChannel._extract_reply_context(MockMessage())
        assert result is None

    def test_extract_reply_context_with_reply(self):
        class MockReply:
            text = "Original message"
            caption = None
        class MockMessage:
            reply_to_message = MockReply()
        result = TelegramChannel._extract_reply_context(MockMessage())
        assert result is not None
        assert "Original message" in result

    def test_extract_reply_context_long_text(self):
        long_text = "x" * 5000
        class MockReply:
            text = long_text
            caption = None
        class MockMessage:
            reply_to_message = MockReply()
        result = TelegramChannel._extract_reply_context(MockMessage())
        assert result is not None
        assert len(result) < len(long_text) + 20  # Truncated


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
