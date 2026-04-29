from collections import Counter

from examples.iot_light.generate_dataset import TARGET_COUNTS
from ganglion.eval.dataset import DEFAULT_DATASET, load_dataset


def test_iot_dataset_has_target_distribution() -> None:
    cases = load_dataset(DEFAULT_DATASET)
    counts = Counter(case.expected.calls[0].action for case in cases)

    assert len(cases) == sum(TARGET_COUNTS.values())
    assert dict(counts) == TARGET_COUNTS
    assert len({case.id for case in cases}) == len(cases)
    assert len({case.prompt for case in cases}) == len(cases)
