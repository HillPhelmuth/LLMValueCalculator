$ErrorActionPreference = "Stop"
uv run --locked python -m unittest discover -s tests -v
uv run --locked python -m calibration run manifests/smoke.yaml --output .calibration-runs
