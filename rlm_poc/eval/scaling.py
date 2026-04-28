from __future__ import annotations

import argparse
import json

from rlm_poc.schema import TIERS


def measure(tier: str) -> dict[str, int | str]:
    catalog = TIERS[tier]
    dsl = catalog.render_json_dsl()
    native = catalog.render_openai_tools()
    return {
        "tier": tier,
        "tools": len(catalog.tools),
        "dsl_chars": len(dsl),
        "native_chars": len(json.dumps(native, ensure_ascii=False)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    rows = [measure(name) for name in TIERS]

    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return

    headers = ("tier", "tools", "dsl_chars", "native_chars", "native/dsl")
    print(" | ".join(headers))
    print(" | ".join("---" for _ in headers))
    for row in rows:
        ratio = row["native_chars"] / row["dsl_chars"]
        print(
            " | ".join(
                [
                    str(row["tier"]),
                    str(row["tools"]),
                    str(row["dsl_chars"]),
                    str(row["native_chars"]),
                    f"{ratio:.2f}x",
                ]
            )
        )


if __name__ == "__main__":
    main()
