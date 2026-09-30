from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE = (
    REPOSITORY_ROOT
    / "models"
    / "power"
    / "bundles"
    / "pdu1-outlet1-20260416-20260612"
)

if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from pre6g_experiment.power_eq_model import (  # noqa: E402
    load_records,
    predict_records,
    write_records,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Predict PDU1 Outlet1 power from CPU/GPU telemetry."
    )
    parser.add_argument("input", type=Path, help="Input .json or .csv telemetry file")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Output .json or .csv (prints JSON to stdout when omitted)",
    )
    parser.add_argument("--model", type=Path, default=DEFAULT_BUNDLE / "model.onnx")
    parser.add_argument("--scaler", type=Path, default=DEFAULT_BUNDLE / "scaler.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    records = load_records(args.input)
    predictions, range_warnings = predict_records(
        records,
        args.model,
        args.scaler,
    )
    if args.output:
        write_records(args.output, predictions)
        print(f"Wrote {len(predictions)} prediction(s) to {args.output}")
    else:
        json.dump(predictions, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    if range_warnings:
        print(
            f"Diagnostic: {len(range_warnings)} feature value(s) are outside "
            "the scaler reference range; predictions were still emitted.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
