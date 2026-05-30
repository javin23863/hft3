"""Student-t CDF golden test."""

import pytest

from features_engine.src.structural_models.model_03_vpin_toxicity import student_t_cdf

try:
    from scipy.stats import t as scipy_t
except ImportError:
    scipy_t = None


@pytest.mark.parametrize("z", [-2.0, -1.0, 0.0, 1.0, 2.0])
def test_student_t_cdf_matches_scipy(z):
    if scipy_t is None:
        pytest.skip("scipy not installed")
    got = student_t_cdf(z, df=5.0)
    want = float(scipy_t.cdf(z, df=5))
    assert abs(got - want) < 1e-6


def test_student_t_monotonic():
    vals = [student_t_cdf(z, 5.0) for z in [-2, -1, 0, 1, 2]]
    assert vals == sorted(vals)
