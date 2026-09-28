from __future__ import annotations

import argparse
import json
from pathlib import Path

from pre6g_experiment.runtime_model import (
    RuntimeModelError,
    load_feature_artifact,
    load_model,
    predict_runtime,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run control-side inference with a frozen Pre6G runtime model. "
            "This command never fits or tunes a model."
        )
    )
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument(
        "--features",
        type=Path,
        required=True,
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        model = load_model(args.model)
        features = load_feature_artifact(args.features)
        prediction = predict_runtime(model, features)
    except RuntimeModelError as exc:
        raise SystemExit(str(exc)) from exc

    payload = prediction.to_dict()
    payload.update(
        {
            "node": features.get("node"),
            "workload_id": features.get("workload_id"),
            "detector_profile": features.get(
                "detector_profile"
            ),
            "detected_period_ms": features.get(
                "detected_period_ms"
            ),
            "detector_confidence": features.get(
                "detector_confidence"
            ),
            "model_role": model.get("role"),
        }
    )

    if args.output is not None:
        args.output.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        args.output.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
