from components.metrics.schemas import (
    CaseMetricsSchema,
    CasePageSchema,
    CaseTargetResultSchema,
    ComparisonSchema,
    LatencyStatsSchema,
    ResultCountsSchema,
    SummarySchema,
    TargetComparisonSchema,
    TargetCostSchema,
    TokenSumSchema,
    TokenUsageSchema,
)


def test_unknown_values_serialize_as_null() -> None:
    target = TargetComparisonSchema(
        provider="openai",
        model="m",
        counts=ResultCountsSchema(),
        error_kinds={},
        latency=LatencyStatsSchema(),
        tokens=TokenUsageSchema(
            input_tokens=TokenSumSchema(),
            output_tokens=TokenSumSchema(),
            total_tokens=TokenSumSchema(),
        ),
        evaluators=[],
        cost=TargetCostSchema(priced=False),
    )
    data = target.model_dump(mode="json")
    assert data["error_rate"] is None
    assert data["latency"] == {
        "count": 0,
        "min_ms": None,
        "mean_ms": None,
        "median_ms": None,
        "p95_ms": None,
        "max_ms": None,
    }
    assert data["tokens"]["input_tokens"] == {
        "total": None,
        "reported_count": 0,
    }
    assert data["cost"]["estimated_cost"] is None
    assert data["cost"]["pricing_version"] is None


def test_only_case_target_result_carries_response_text() -> None:
    schemas = (
        SummarySchema,
        ComparisonSchema,
        TargetComparisonSchema,
        CaseMetricsSchema,
        CasePageSchema,
        TargetCostSchema,
    )
    forbidden = {"response_text", "rendered_prompt", "expected_output"}
    for schema in schemas:
        assert not forbidden & set(schema.model_fields), schema
    assert "response_text" in CaseTargetResultSchema.model_fields
    assert "rendered_prompt" not in CaseTargetResultSchema.model_fields
    assert "expected_output" not in CaseTargetResultSchema.model_fields


def test_case_result_response_defaults_to_null() -> None:
    item = CaseTargetResultSchema(
        provider="openai",
        model="m",
        status="failed",
        attempts=1,
        evaluations=[],
    )
    data = item.model_dump(mode="json")
    assert data["response_text"] is None
    assert data["latency_ms"] is None
    assert data["input_tokens"] is None
