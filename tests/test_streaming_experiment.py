import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError


def experiment():
    spec = importlib.util.spec_from_file_location(
        "experiment", Path(__file__).resolve().parents[1] / "scripts/streaming_experiment.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_streaming_only_returns_valid_complete_output():
    result = await experiment().collect('{"summary":"Complete summary"}')
    assert result["summary"] == "Complete summary"
    assert result["first_chunk_ms"] < result["first_useful_output_ms"]


async def test_truncated_stream_is_not_a_digest():
    with pytest.raises(ValidationError):
        await experiment().collect('{"summary":"Incomplete')
