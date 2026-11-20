from app.models.schemas import ScanRequest


def test_scan_request_defaults_match_conservative_product_preset() -> None:
    request = ScanRequest(path="/tmp/image.png")

    assert request.allow_clip_fallback is True
    assert request.thresholds.model_dump() == {
        "phash_distance": 10,
        "histogram_similarity": 0.90,
        "clip_similarity": 0.92,
        "clip_enabled": False,
        "precision_mode": "conservative",
        "performance_profile": "standard",
    }
