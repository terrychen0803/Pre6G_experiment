from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from pre6g_experiment.power_adapter import (  # noqa: E402
    build_power_smoke_result,
    load_bundle_manifest,
)
from pre6g_experiment.power_eq_model import (  # noqa: E402
    load_records,
    load_scaler,
    predict_records,
    write_records,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run a power-model smoke from aligned Netdata/DCGM telemetry. "
            "The result remains validation_required until the bundle is bound "
            "to a node/GPU and target/idle-power semantics are verified."
        )
    )
    parser.add_argument("--aligned-telemetry", type=Path, required=True)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--output-series", type=Path, required=True)
    parser.add_argument("--output-summary", type=Path, required=True)
    parser.add_argument("--alignment-quality", type=Path)
    parser.add_argument("--reject-ood", action="store_true")
    args = parser.parse_args()

    bundle = args.bundle_dir.resolve()
    manifest = load_bundle_manifest(bundle / "manifest.yaml")
    scaler = load_scaler(bundle / "scaler.json")
    required_features = list(scaler["feature_cols"])

    records = load_records(args.aligned_telemetry)
    predicted_rows, ood_messages = predict_records(
        records,
        bundle / "model.onnx",
        bundle / "scaler.json",
        reject_out_of_domain=args.reject_ood,
    )

    alignment_quality = None
    if args.alignment_quality is not None:
        alignment_quality = json.loads(
            args.alignment_quality.read_text(encoding="utf-8")
        )

    summary = build_power_smoke_result(
        manifest=manifest,
        required_features=required_features,
        predicted_rows=predicted_rows,
        ood_messages=ood_messages,
        alignment_quality=alignment_quality,
    )

    write_records(args.output_series, predicted_rows)
    args.output_summary.parent.mkdir(parents=True, exist_ok=True)
    args.output_summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
