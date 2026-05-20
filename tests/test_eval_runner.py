from pathlib import Path

from ganglion.analyzer.metrics import summarize
from ganglion.cli import run_eval
from ganglion.lm.rules import RuleBasedJSONDSLClient


def test_rule_model_matches_dataset() -> None:
    results = run_eval(
        RuleBasedJSONDSLClient(),
        Path("examples/iot_light/dataset.jsonl"),
        limit=None,
    )

    summary = summarize(results)
    assert summary["syntax_valid_rate"] == 1.0
    assert summary["exact_match_rate"] == 1.0
    assert summary["failures"] == []
