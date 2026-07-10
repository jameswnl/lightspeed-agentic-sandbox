"""Tests for MCP header resolution and MCPServerConfig parsing."""

import json
import os
import tempfile

import pytest

from lightspeed_agentic.mcp import resolve_headers
from lightspeed_agentic.types import MCPServerConfig, ProviderQueryOptions


class TestResolveHeaders:
    def test_returns_empty_dict_for_none(self):
        assert resolve_headers(None) == {}

    def test_returns_empty_dict_for_empty(self):
        assert resolve_headers({}) == {}

    def test_passes_through_plain_headers(self):
        headers = {"Authorization": "Bearer tok123", "X-Custom": "value"}
        assert resolve_headers(headers) == headers

    def test_resolves_file_reference(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("secret-token-value\n")
            f.flush()
            path = f.name
        try:
            headers = {"Authorization": f"file:{path}"}
            result = resolve_headers(headers)
            assert result["Authorization"] == "secret-token-value"
        finally:
            os.unlink(path)

    def test_file_reference_strips_whitespace(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("  token-with-spaces  \n\n")
            f.flush()
            path = f.name
        try:
            result = resolve_headers({"Auth": f"file:{path}"})
            assert result["Auth"] == "token-with-spaces"
        finally:
            os.unlink(path)

    def test_file_reference_falls_back_on_missing_file(self):
        headers = {"Auth": "file:/nonexistent/path/token.txt"}
        result = resolve_headers(headers)
        assert result["Auth"] == "file:/nonexistent/path/token.txt"

    def test_mixed_plain_and_file_headers(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("from-file")
            f.flush()
            path = f.name
        try:
            headers = {"Plain": "value", "FromFile": f"file:{path}"}
            result = resolve_headers(headers)
            assert result["Plain"] == "value"
            assert result["FromFile"] == "from-file"
        finally:
            os.unlink(path)


class TestMCPServerConfig:
    def test_basic_construction(self):
        cfg = MCPServerConfig(name="kubectl", url="http://localhost:8082/mcp")
        assert cfg.name == "kubectl"
        assert cfg.url == "http://localhost:8082/mcp"
        assert cfg.headers is None

    def test_with_headers(self):
        cfg = MCPServerConfig(
            name="test", url="http://host/mcp", headers={"Auth": "Bearer x"}
        )
        assert cfg.headers == {"Auth": "Bearer x"}


class TestMCPServerConfigParsing:
    def test_parse_valid_json(self):
        env_val = json.dumps([
            {"name": "kubectl", "url": "http://mcp:8082/mcp"},
            {"name": "fs", "url": "http://mcp:8081/sse", "headers": {"X-Key": "val"}},
        ])
        configs = [
            MCPServerConfig(
                name=s.get("name", ""),
                url=s.get("url", ""),
                headers=s.get("headers"),
            )
            for s in json.loads(env_val)
        ]
        assert len(configs) == 2
        assert configs[0].name == "kubectl"
        assert configs[0].url == "http://mcp:8082/mcp"
        assert configs[0].headers is None
        assert configs[1].name == "fs"
        assert configs[1].headers == {"X-Key": "val"}

    def test_parse_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            json.loads("not valid json")

    def test_parse_empty_string(self):
        env_val = ""
        assert not env_val


class TestProviderQueryOptionsMCPField:
    def test_mcp_servers_default_none(self):
        opts = ProviderQueryOptions(
            prompt="test",
            system_prompt="sys",
            model="gpt-4o",
            max_turns=5,
            max_budget_usd=1.0,
            allowed_tools=[],
            cwd="/tmp",
        )
        assert opts.mcp_servers is None

    def test_mcp_servers_with_configs(self):
        configs = [MCPServerConfig(name="k", url="http://x/mcp")]
        opts = ProviderQueryOptions(
            prompt="test",
            system_prompt="sys",
            model="gpt-4o",
            max_turns=5,
            max_budget_usd=1.0,
            allowed_tools=[],
            cwd="/tmp",
            mcp_servers=configs,
        )
        assert opts.mcp_servers is not None
        assert len(opts.mcp_servers) == 1
        assert opts.mcp_servers[0].name == "k"
