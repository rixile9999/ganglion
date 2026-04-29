from pathlib import Path

from ganglion.eval.metrics import summarize
from ganglion.eval.runner import run_eval
from ganglion.runtime.rules import RuleBasedJSONDSLClient


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
