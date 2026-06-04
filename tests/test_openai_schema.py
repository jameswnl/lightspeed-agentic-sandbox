"""Tests for OpenAI provider strict schema transform."""

from unittest.mock import patch

from lightspeed_agentic.providers.openai import _make_strict, _RawJsonSchema


def test_adds_additional_properties_false():
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    result = _make_strict(schema)
    assert result["additionalProperties"] is False


def test_sets_required_to_all_keys():
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
        },
        "required": ["name"],
    }
    result = _make_strict(schema)
    assert sorted(result["required"]) == ["age", "name"]


def test_adds_required_when_missing():
    schema = {
        "type": "object",
        "properties": {"x": {"type": "string"}},
    }
    result = _make_strict(schema)
    assert result["required"] == ["x"]


def test_recurses_into_nested_objects():
    schema = {
        "type": "object",
        "properties": {
            "inner": {
                "type": "object",
                "properties": {
                    "val": {"type": "string"},
                },
            },
        },
    }
    result = _make_strict(schema)
    inner = result["properties"]["inner"]
    assert inner["additionalProperties"] is False
    assert inner["required"] == ["val"]


def test_recurses_into_array_items():
    schema = {
        "type": "object",
        "properties": {
            "items_list": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                    },
                },
            },
        },
    }
    result = _make_strict(schema)
    items_obj = result["properties"]["items_list"]["items"]
    assert items_obj["additionalProperties"] is False
    assert items_obj["required"] == ["id"]


def test_recurses_into_anyof():
    schema = {
        "anyOf": [
            {"type": "object", "properties": {"a": {"type": "string"}}},
            {"type": "string"},
        ],
    }
    result = _make_strict(schema)
    assert result["anyOf"][0]["additionalProperties"] is False
    assert result["anyOf"][0]["required"] == ["a"]
    assert result["anyOf"][1] == {"type": "string"}


def test_recurses_into_oneof():
    schema = {
        "oneOf": [
            {"type": "object", "properties": {"b": {"type": "integer"}}},
        ],
    }
    result = _make_strict(schema)
    assert result["oneOf"][0]["additionalProperties"] is False


def test_recurses_into_allof():
    schema = {
        "allOf": [
            {"type": "object", "properties": {"c": {"type": "boolean"}}},
        ],
    }
    result = _make_strict(schema)
    assert result["allOf"][0]["additionalProperties"] is False


def test_recurses_into_not():
    schema = {
        "not": {"type": "object", "properties": {"d": {"type": "string"}}},
    }
    result = _make_strict(schema)
    assert result["not"]["additionalProperties"] is False


def test_recurses_into_defs():
    schema = {
        "$defs": {
            "thing": {"type": "object", "properties": {"e": {"type": "string"}}},
        },
    }
    result = _make_strict(schema)
    assert result["$defs"]["thing"]["additionalProperties"] is False


def test_does_not_modify_original():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "required": ["a"],
    }
    _make_strict(schema)
    assert "additionalProperties" not in schema


def test_non_object_passthrough():
    schema = {"type": "string"}
    result = _make_strict(schema)
    assert result == {"type": "string"}


def test_non_dict_passthrough():
    assert _make_strict("not a dict") == "not a dict"


@patch.dict("os.environ", {}, clear=True)
def test_strict_enabled_no_base_url():
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    wrapper = _RawJsonSchema(schema)
    assert wrapper.is_strict_json_schema() is True
    assert wrapper.json_schema()["additionalProperties"] is False


@patch.dict("os.environ", {"OPENAI_BASE_URL": "https://api.openai.com/v1"})
def test_strict_enabled_for_explicit_openai_url():
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    wrapper = _RawJsonSchema(schema)
    assert wrapper.is_strict_json_schema() is True
    assert wrapper.json_schema()["additionalProperties"] is False


@patch.dict("os.environ", {"OPENAI_BASE_URL": "http://vllm:8000/v1"})
def test_strict_disabled_for_custom_endpoint():
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    wrapper = _RawJsonSchema(schema)
    assert wrapper.is_strict_json_schema() is False
    assert "additionalProperties" not in wrapper.json_schema()
