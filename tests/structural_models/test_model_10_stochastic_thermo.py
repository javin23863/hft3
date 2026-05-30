"""Tests for PDF_MODEL_10 stochastic thermodynamics."""

from features_engine.src.structural_models.model_10_stochastic_thermo import (
    StochasticThermoModel,
    free_energy,
    gibbs_probabilities,
    partition_function,
)


def test_partition_normalizes():
    work = [0.0, 1.0, 2.0]
    probs = gibbs_probabilities(work, beta=1.0)
    assert abs(float(probs.sum()) - 1.0) < 1e-9


def test_free_energy_finite():
    f = free_energy(expected_work=1.0, entropy=0.5, beta=1.0)
    assert f == 0.5


def test_model_outputs_valid_probs():
    model = StochasticThermoModel()
    out = model.evaluate(strategy_work=[0.0, 0.5, 1.0, 2.0], beta=1.0)
    assert out.payload.partition_function >= 1.0
    assert out.payload.entropy >= 0.0
    assert out.payload.mean_reversion_signal is False


def test_mean_reversion_when_observed_work_explicit():
    model = StochasticThermoModel()
    out = model.evaluate(
        strategy_work=[0.0, 0.5, 1.0, 2.0],
        beta=1.0,
        observed_dissipative_work=10.0,
    )
    assert out.payload.mean_reversion_signal is True
