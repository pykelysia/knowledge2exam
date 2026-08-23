"""编排层。"""

__all__ = ["run_pipeline", "run_mock_pipeline"]


def run_pipeline(*args, **kwargs):
    from app.orchestration.stages import run_pipeline as _run_pipeline
    return _run_pipeline(*args, **kwargs)


def run_mock_pipeline(*args, **kwargs):
    from app.orchestration.stages import run_mock_pipeline as _run_mock_pipeline
    return _run_mock_pipeline(*args, **kwargs)
