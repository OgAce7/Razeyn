"""
Deterministic payment-degradation detection engine.

Pure Python/pandas — no LLM. Compares recent behavior against a
leave-window-out historical baseline across several dimensions (payment
method, institution, geography, a method+institution pairing, failure
reason, transaction-value bucket, and an unsegmented "all" pass), and
flags segments whose failure rate is both statistically significant
(two-proportion z-test) and practically large (minimum relative change +
absolute rate + sample-size floors) relative to that baseline.

Public API:
    from app.detection.detector import detect_incidents
    from app.detection.config import DetectionConfig, DEFAULT_CONFIG

See docs/detection.md for methodology and app/detection/run.py for a CLI
that runs this against the generated synthetic dataset.
"""
