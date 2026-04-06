"""
Test anthropic_beta header support for AWS Bedrock.

Tests that anthropic-beta headers are correctly processed and passed to AWS Bedrock
for enabling beta features like 1M context window, computer use tools, etc.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from litellm.llms.bedrock.chat.converse_transformation import AmazonConverseConfig
from litellm.llms.bedrock.chat.invoke_transformations.anthropic_claude3_transformation import (
    AmazonAnthropicClaudeConfig,
)
from litellm.llms.bedrock.common_utils import get_anthropic_beta_from_headers
from litellm.llms.bedrock.messages.invoke_transformations.anthropic_claude3_transformation import (
    AmazonAnthropicClaudeMessagesConfig,
)


class TestAnthropicBetaHeaderSupport:
    """Test anthropic_beta header functionality across Bedrock APIs."""

    def test_get_anthropic_beta_from_headers_empty(self):
        """Test header extraction with no headers."""
        headers = {}
        result = get_anthropic_beta_from_headers(headers)
        assert result == []

    def test_get_anthropic_beta_from_headers_single(self):
        """Test header extraction with single beta header."""
        headers = {"anthropic-beta": "context-1m-2025-08-07"}
        result = get_anthropic_beta_from_headers(headers)
        assert result == ["context-1m-2025-08-07"]

    def test_get_anthropic_beta_from_headers_multiple(self):
        """Test header extraction with multiple comma-separated beta headers."""
        headers = {"anthropic-beta": "context-1m-2025-08-07,computer-use-2024-10-22"}
        result = get_anthropic_beta_from_headers(headers)
        assert result == ["context-1m-2025-08-07", "computer-use-2024-10-22"]

    def test_get_anthropic_beta_from_headers_whitespace(self):
        """Test header extraction handles whitespace correctly."""
        headers = {
            "anthropic-beta": " context-1m-2025-08-07 , computer-use-2024-10-22 "
        }
        result = get_anthropic_beta_from_headers(headers)
        assert result == ["context-1m-2025-08-07", "computer-use-2024-10-22"]

    def test_invoke_transformation_anthropic_beta(self):
        """Test that Invoke API transformation includes anthropic_beta in request."""
        config = AmazonAnthropicClaudeConfig()
        headers = {"anthropic-beta": "context-1m-2025-08-07,computer-use-2024-10-22"}

        result = config.transform_request(
            model="anthropic.claude-opus-4-5-20250514-v1:0",
            messages=[{"role": "user", "content": "Test"}],
            optional_params={},
            litellm_params={},
            headers=headers,
        )

        assert "anthropic_beta" in result
        # Beta flags are stored as sets, so order may vary
        assert set(result["anthropic_beta"]) == {
            "context-1m-2025-08-07",
            "computer-use-2024-10-22",
        }

    def test_converse_transformation_anthropic_beta(self):
        """Test that Converse API transformation includes anthropic_beta in additionalModelRequestFields."""
        config = AmazonConverseConfig()
        headers = {
            "anthropic-beta": "context-1m-2025-08-07,interleaved-thinking-2025-05-14"
        }

        result = config._transform_request_helper(
            model="anthropic.claude-opus-4-5-20250514-v1:0",
            system_content_blocks=[],
            optional_params={},
            messages=[{"role": "user", "content": "Test"}],
            headers=headers,
        )

        assert "additionalModelRequestFields" in result
        additional_fields = result["additionalModelRequestFields"]
        assert "anthropic_beta" in additional_fields
        # Sort both arrays before comparing to avoid flakiness from ordering differences
        assert sorted(additional_fields["anthropic_beta"]) == sorted(
            ["context-1m-2025-08-07", "interleaved-thinking-2025-05-14"]
        )

    def test_messages_transformation_anthropic_beta(self):
        """Test that Messages API transformation includes anthropic_beta in request."""
        config = AmazonAnthropicClaudeMessagesConfig()
        headers = {"anthropic-beta": "output-128k-2025-02-19"}

        result = config.transform_anthropic_messages_request(
            model="anthropic.claude-3-5-sonnet-20241022-v2:0",
            messages=[{"role": "user", "content": "Test"}],
            anthropic_messages_optional_request_params={"max_tokens": 100},
            litellm_params={},
            headers=headers,
        )

        assert "anthropic_beta" in result
        # Sort both arrays before comparing to avoid flakiness from ordering differences
        assert sorted(result["anthropic_beta"]) == sorted(["output-128k-2025-02-19"])

    def test_converse_computer_use_compatibility(self):
        """Test that user anthropic_beta headers work with computer use tools."""
        config = AmazonConverseConfig()
        headers = {"anthropic-beta": "context-1m-2025-08-07"}

        # Computer use tools should automatically add computer-use-2024-10-22
        tools = [
            {
                "type": "computer_20241022",
                "name": "computer",
                "display_width_px": 1024,
                "display_height_px": 768,
            }
        ]

        result = config._transform_request_helper(
            model="anthropic.claude-opus-4-5-20250514-v1:0",
            system_content_blocks=[],
            optional_params={"tools": tools},
            messages=[{"role": "user", "content": "Test"}],
            headers=headers,
        )

        additional_fields = result["additionalModelRequestFields"]
        betas = additional_fields["anthropic_beta"]

        # Should contain both user-provided and auto-added beta headers
        assert "context-1m-2025-08-07" in betas
        # Opus 4.5 gets computer-use-2025-11-24 (not the older 2024-10-22)
        assert "computer-use-2025-11-24" in betas
        assert len(betas) == 2  # No duplicates

    def test_no_anthropic_beta_headers(self):
        """Test that transformations work correctly when no anthropic_beta headers are provided."""
        config = AmazonConverseConfig()
        headers = {}

        result = config._transform_request_helper(
            model="anthropic.claude-3-5-sonnet-20241022-v2:0",
            system_content_blocks=[],
            optional_params={},
            messages=[{"role": "user", "content": "Test"}],
            headers=headers,
        )

        additional_fields = result.get("additionalModelRequestFields", {})
        assert "anthropic_beta" not in additional_fields


class TestContextManagementBodyParamStripping:
    """Test that context_management is stripped from request body for Bedrock APIs.

    Bedrock doesn't support context_management as a request body parameter.
    The feature is enabled via the anthropic-beta header instead. If left in the body,
    Bedrock returns: 'context_management: Extra inputs are not permitted'.
    """

    def test_messages_api_strips_context_management(self):
        """Test that Messages API removes context_management from request body."""
        config = AmazonAnthropicClaudeMessagesConfig()
        headers = {}

        result = config.transform_anthropic_messages_request(
            model="anthropic.claude-sonnet-4-5-20250514-v1:0",
            messages=[{"role": "user", "content": "Test"}],
            anthropic_messages_optional_request_params={
                "max_tokens": 100,
                "context_management": {
                    "type": "automatic",
                    "max_context_tokens": 50000,
                },
            },
            litellm_params={},
            headers=headers,
        )

        # context_management must NOT be in the request body
        assert "context_management" not in result

    def test_invoke_chat_api_strips_context_management(self):
        """Test that Invoke Chat API removes context_management from request body."""
        config = AmazonAnthropicClaudeConfig()
        headers = {}

        result = config.transform_request(
            model="anthropic.claude-sonnet-4-5-20250514-v1:0",
            messages=[{"role": "user", "content": "Test"}],
            optional_params={
                "context_management": {
                    "type": "automatic",
                    "max_context_tokens": 50000,
                },
            },
            litellm_params={},
            headers=headers,
        )

        # context_management must NOT be in the request body
        assert "context_management" not in result

    def test_converse_api_strips_context_management(self):
        """Test that Converse API doesn't pass context_management in additionalModelRequestFields."""
        config = AmazonConverseConfig()
        headers = {}

        result = config._transform_request_helper(
            model="anthropic.claude-sonnet-4-5-20250514-v1:0",
            system_content_blocks=[],
            optional_params={
                "context_management": {
                    "type": "automatic",
                    "max_context_tokens": 50000,
                },
            },
            messages=[{"role": "user", "content": "Test"}],
            headers=headers,
        )

        additional_fields = result.get("additionalModelRequestFields", {})
        # context_management must NOT leak into additionalModelRequestFields
        assert "context_management" not in additional_fields


class TestCacheControlScopeStripping:
    """Test that cache_control.scope is stripped for Bedrock APIs.

    Bedrock doesn't support the 'scope' field inside cache_control blocks.
    If left in, Bedrock returns: 'system.1.cache_control.ephemeral.scope: Extra inputs are not permitted'.
    """

    def test_invoke_chat_strips_scope_from_system(self):
        """Test that Invoke Chat API strips scope from system cache_control."""
        config = AmazonAnthropicClaudeConfig()

        result = config.transform_request(
            model="anthropic.claude-opus-4-6-20250514-v1:0",
            messages=[{"role": "user", "content": "Test"}],
            optional_params={
                "system": [
                    {"type": "text", "text": "You are helpful."},
                    {
                        "type": "text",
                        "text": "Context here.",
                        "cache_control": {"type": "ephemeral", "scope": "turn"},
                    },
                ],
            },
            litellm_params={},
            headers={},
        )

        # Find system blocks with cache_control
        system = result.get("system", [])
        for block in system:
            if isinstance(block, dict) and "cache_control" in block:
                assert (
                    "scope" not in block["cache_control"]
                ), "scope should be stripped from cache_control"

    def test_invoke_chat_strips_scope_from_messages(self):
        """Test that Invoke Chat API strips scope from message cache_control."""
        config = AmazonAnthropicClaudeConfig()

        result = config.transform_request(
            model="anthropic.claude-opus-4-6-20250514-v1:0",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Hello",
                            "cache_control": {"type": "ephemeral", "scope": "turn"},
                        }
                    ],
                }
            ],
            optional_params={},
            litellm_params={},
            headers={},
        )

        for msg in result.get("messages", []):
            if isinstance(msg, dict):
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and "cache_control" in block:
                            assert (
                                "scope" not in block["cache_control"]
                            ), "scope should be stripped from cache_control"
