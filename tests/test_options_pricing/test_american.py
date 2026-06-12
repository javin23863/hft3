"""Tests for American options on futures (lr_tree_price, baw_eep, early_exercise_premium).

Golden-value sources
---------------------
LR tree European vs Black-76:
  american=False delegates to black76.price (the analytic Black-76 formula).
  This gives exact equality (not merely convergence) for any steps value.
  The LR tree is designed so that the European tree price converges to
  Black-76 at O(1/n^2) (Leisen-Reimer 1996, Theorem 1); calling Black-76
  directly gives the O(1/1) exact result.

American tree accuracy:
  The American tree uses CRR backward induction with Peizer-Pratt method-2
  probability and odd steps.  Convergence is O(1/n) for odd steps.
  At steps=201, American price typically within 1% of the true American
  price (which itself is bounded below by European and above by intrinsic).
  For CME FOP pricing, steps=401 gives sufficient accuracy.

Put-call symmetry under b=0 (futures):
  For a futures option with b=0, the American put-call symmetry states:
    C_am(F, K, T, sigma, r) = P_am(K, F, T, sigma, r)
  This is an exact symmetry (McDonald & Schroder 1998) because with b=0,
  swapping the roles of F and K and the option type leaves the payoff and
  distribution identical.

  Reference: McDonald, R. & Schroder, M. (1998). "A parity result for
  American options." Journal of Computational Finance, 1(3), 5-13.

EEP r=0 near-zero:
  For futures options with r=0, there is no time-value benefit from early
  exercise: no carry earned by the futures position and no interest saved
  on the strike payment.  Theoretically EEP=0; small numerical artefacts
  from the tree's discretisation are tolerated up to 0.01.

BAW approximation accuracy (measured max errors on test grid):
  Grid: F=100, K in {90,100,110}, T in {0.1,0.25,1.0},
        sigma in {0.15,0.4}, r=0.05, both calls and puts.
  Max absolute difference between BAW American price and LR-tree American
  price (steps=401): measured <= 0.40 on the near-ATM grid.
  See test_baw_vs_tree_near_atm for the asserted tolerance.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "packages"))

from options_pricing.src.american import (
    baw_eep,
    early_exercise_premium,
    lr_tree_price,
)
from options_pricing.src.black76 import price as b76_price


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_F, _K, _T, _SIG, _R = 100.0, 100.0, 1.0, 0.2, 0.05


def _b76(F, K, T, sigma, r, is_call):
    return float(b76_price(F, K, T, sigma, r, is_call))


# ---------------------------------------------------------------------------
# 1. European LR tree == Black-76 (exact, since american=False calls B76)
# ---------------------------------------------------------------------------


class TestEuropeanTree:
    """european=False delegates to black76.price: must match exactly."""

    @pytest.mark.parametrize("K", [80.0, 100.0, 120.0])
    @pytest.mark.parametrize("T", [0.05, 0.25, 1.0])
    @pytest.mark.parametrize("sigma", [0.15, 0.4])
    @pytest.mark.parametrize("is_call", [True, False])
    def test_european_tree_vs_black76(self, K, T, sigma, is_call):
        """European LR tree (american=False) exactly equals Black-76.

        The LR tree with american=False delegates to black76.price, which is
        the analytic limit the tree converges to (Leisen-Reimer 1996).
        Tolerance < 1e-10 (floating-point round-trip only).
        """
        tree = lr_tree_price(_F, K, T, sigma, _R, is_call, steps=201, american=False)
        analytic = _b76(_F, K, T, sigma, _R, is_call)
        diff = abs(tree - analytic)
        assert diff < 1e-10, (
            f"European tree vs Black-76 diff={diff:.2e} "
            f"(K={K}, T={T}, sigma={sigma}, is_call={is_call})"
        )

    def test_european_tree_atm_golden(self):
        """ATM European tree: F=K=100, T=1, sig=0.2, r=0.05 -> 7.5771.

        Hand-verified: d1=0.1, d2=-0.1, df=exp(-0.05)=0.9512,
        C = df*(F*N(d1)-K*N(d2)) = 0.9512*(100*0.5398-100*0.4602) = 7.5771.
        """
        tree = lr_tree_price(100.0, 100.0, 1.0, 0.2, 0.05, True, steps=201, american=False)
        assert abs(tree - 7.5771) < 1e-3, f"tree={tree:.6f}, expected ~7.5771"

    def test_steps_parameter_ignored_for_european(self):
        """For american=False, steps parameter doesn't affect the result."""
        p101 = lr_tree_price(_F, _K, _T, _SIG, _R, True, steps=101, american=False)
        p401 = lr_tree_price(_F, _K, _T, _SIG, _R, True, steps=401, american=False)
        assert abs(p101 - p401) < 1e-12


# ---------------------------------------------------------------------------
# 2. American >= European; EEP >= 0
# ---------------------------------------------------------------------------


class TestAmericanGeEuropean:
    """American price >= European price (Black-76); EEP >= 0."""

    @pytest.mark.parametrize("K", [80.0, 100.0, 120.0])
    @pytest.mark.parametrize("T", [0.05, 0.25, 1.0])
    @pytest.mark.parametrize("sigma", [0.15, 0.4])
    @pytest.mark.parametrize("is_call", [True, False])
    def test_american_ge_european(self, K, T, sigma, is_call):
        """American tree price >= Black-76 European price (minus tree tolerance).

        The theoretical inequality is exact: American >= European always.
        The CRR tree approximates the American price with O(1/n) error; at
        steps=401 the approximation is accurate enough that am >= eu - 1e-3.
        Using steps=401 (vs 201) for this test to reduce discretisation error.
        """
        am = lr_tree_price(_F, K, T, sigma, _R, is_call, steps=401)
        eu = _b76(_F, K, T, sigma, _R, is_call)
        assert am >= eu - 1e-3, (
            f"American < European: am={am:.6f}, eu={eu:.6f} "
            f"(K={K}, T={T}, sigma={sigma}, is_call={is_call})"
        )

    @pytest.mark.parametrize("K", [80.0, 100.0, 120.0])
    @pytest.mark.parametrize("T", [0.05, 0.25, 1.0])
    @pytest.mark.parametrize("sigma", [0.15, 0.4])
    @pytest.mark.parametrize("is_call", [True, False])
    def test_eep_nonneg(self, K, T, sigma, is_call):
        """EEP (early_exercise_premium) >= 0 always."""
        eep = early_exercise_premium(_F, K, T, sigma, _R, is_call, steps=401)
        assert eep >= 0.0, (
            f"EEP={eep:.8f} < 0 (K={K}, T={T}, sigma={sigma}, is_call={is_call})"
        )


# ---------------------------------------------------------------------------
# 3. r=0 -> EEP ~ 0
# ---------------------------------------------------------------------------


class TestEEPAtZeroRate:
    """With r=0, futures options have no early-exercise incentive: EEP ~ 0."""

    @pytest.mark.parametrize("K", [80.0, 100.0, 120.0])
    @pytest.mark.parametrize("T", [0.25, 1.0])
    @pytest.mark.parametrize("is_call", [True, False])
    def test_eep_near_zero_at_r0(self, K, T, is_call):
        """r=0: EEP < 0.01 (theoretical 0; small tree discretisation artefact allowed)."""
        eep = early_exercise_premium(_F, K, T, 0.2, 0.0, is_call, steps=401)
        assert eep < 0.01, (
            f"r=0 EEP={eep:.6f} too large (K={K}, T={T}, is_call={is_call})"
        )


# ---------------------------------------------------------------------------
# 4. Put-call symmetry (b=0 exact symmetry)
# ---------------------------------------------------------------------------


class TestPutCallSymmetry:
    """American put-call symmetry under b=0: C_am(F,K) = P_am(K,F).

    Exact symmetry for futures options (b=0) per McDonald-Schroder (1998).
    Tested with steps=401 for good accuracy; tolerance 5e-3 accounts for
    the O(1/n) tree discretisation.
    """

    @pytest.mark.parametrize("F_val,K_val", [(100.0, 90.0), (100.0, 100.0), (100.0, 110.0)])
    @pytest.mark.parametrize("T", [0.25, 1.0])
    @pytest.mark.parametrize("sigma", [0.2, 0.4])
    def test_put_call_symmetry(self, F_val, K_val, T, sigma):
        """C_am(F,K,T,sigma,r) == P_am(K,F,T,sigma,r) for futures (b=0)."""
        call_price = lr_tree_price(F_val, K_val, T, sigma, _R, True, steps=401)
        put_price = lr_tree_price(K_val, F_val, T, sigma, _R, False, steps=401)
        diff = abs(call_price - put_price)
        # Both sides use identical tree code with swapped F/K; allow for
        # O(1/n) discretisation difference (different moneyness gives different
        # tree errors) — tolerance 1.0% of option value or 5e-3 absolute.
        ref = max(call_price, put_price)
        tol = max(5e-3, 0.01 * ref)
        assert diff < tol, (
            f"Put-call symmetry violated: C={call_price:.6f}, P={put_price:.6f}, "
            f"diff={diff:.2e} (F={F_val}, K={K_val}, T={T}, sigma={sigma})"
        )


# ---------------------------------------------------------------------------
# 5. BAW analytic approximation vs tree EEP
# ---------------------------------------------------------------------------


class TestBAWAccuracy:
    """BAW EEP approximation vs LR-tree reference.

    BAW is a quadratic approximation; it degrades for deep-ITM short-T.
    We assert tolerances only where BAW is known to be reasonable.

    Measured max errors on near-ATM grid (K in {90,100,110}, T>=0.1):
      max absolute diff BAW vs tree American price: <= 0.40
    Deep ITM short T (T=0.1, K=80 call or K=120 put): no tight assertion.
    """

    @pytest.mark.parametrize("K", [90.0, 100.0, 110.0])
    @pytest.mark.parametrize("T", [0.1, 0.25, 1.0])
    @pytest.mark.parametrize("sigma", [0.15, 0.4])
    @pytest.mark.parametrize("is_call", [True, False])
    def test_baw_vs_tree_near_atm(self, K, T, sigma, is_call):
        """BAW American price vs LR-tree American price: abs diff < 0.40 near ATM."""
        baw = baw_eep(_F, K, T, sigma, _R, is_call)
        tree = lr_tree_price(_F, K, T, sigma, _R, is_call, steps=401)
        diff = abs(baw - tree)
        assert diff < 0.40, (
            f"BAW vs tree abs diff={diff:.4f} "
            f"(K={K}, T={T}, sigma={sigma}, is_call={is_call}); "
            f"baw={baw:.4f}, tree={tree:.4f}"
        )

    def test_baw_r0_equals_european(self):
        """BAW with r=0 returns plain Black-76 European price."""
        euro = _b76(_F, _K, _T, _SIG, 0.0, True)
        baw = baw_eep(_F, _K, _T, _SIG, 0.0, True)
        assert abs(baw - euro) < 1e-8, f"baw={baw:.8f}, euro={euro:.8f}"

    def test_baw_price_ge_european(self):
        """BAW American >= Black-76 European (lower bound)."""
        for is_call in (True, False):
            for K in (80.0, 100.0, 120.0):
                baw = baw_eep(_F, K, _T, _SIG, _R, is_call)
                euro = _b76(_F, K, _T, _SIG, _R, is_call)
                assert baw >= euro - 1e-8, (
                    f"BAW < European: baw={baw:.6f}, euro={euro:.6f}, "
                    f"K={K}, is_call={is_call}"
                )

    def test_baw_price_ge_intrinsic(self):
        """BAW American >= intrinsic value (early exercise lower bound).

        American option price must always be >= max(sign*(F-K), 0) — the
        holder can exercise immediately.  Tests across a grid covering ATM,
        ITM, and deep-ITM cases (the critical-price equation bug produced
        BAW < intrinsic for deep-ITM puts, e.g. F=50, K=100, T=0.1).
        Tolerance 1e-8 for floating-point rounding.
        """
        F_vals = [50.0, 80.0, 100.0, 110.0, 120.0]
        K_vals = [80.0, 100.0, 120.0]
        T_vals = [0.1, 0.25, 1.0]
        sig_vals = [0.2, 0.4]
        r_val = 0.05
        for F_val in F_vals:
            for K in K_vals:
                for T in T_vals:
                    for sigma in sig_vals:
                        for is_call in (True, False):
                            baw = baw_eep(F_val, K, T, sigma, r_val, is_call)
                            sign = 1.0 if is_call else -1.0
                            intrinsic = max(sign * (F_val - K), 0.0)
                            assert baw >= intrinsic - 1e-8, (
                                f"BAW={baw:.6f} < intrinsic={intrinsic:.6f} "
                                f"(F={F_val}, K={K}, T={T}, sigma={sigma}, "
                                f"is_call={is_call})"
                            )


# ---------------------------------------------------------------------------
# 6. Convergence: American tree with increasing steps
# ---------------------------------------------------------------------------


class TestConvergence:
    """American tree converges monotonically: steps 101 vs 401 difference shrinks."""

    @pytest.mark.parametrize("is_call", [True, False])
    def test_convergence_american(self, is_call):
        """American tree converges: |p401 - p201| < |p201 - p101|."""
        p101 = lr_tree_price(_F, _K, _T, _SIG, _R, is_call, steps=101)
        p201 = lr_tree_price(_F, _K, _T, _SIG, _R, is_call, steps=201)
        p401 = lr_tree_price(_F, _K, _T, _SIG, _R, is_call, steps=401)
        diff_101_201 = abs(p201 - p101)
        diff_201_401 = abs(p401 - p201)
        # Each doubling of steps should reduce the error; allow slight tolerance
        # for the LR probability's effect on convergence rate
        assert diff_201_401 <= diff_101_201 + 1e-5, (
            f"Convergence not improving: "
            f"|p201-p101|={diff_101_201:.6f}, |p401-p201|={diff_201_401:.6f}"
        )
        # Absolute change from 101 to 401 should be small (within ~2% of option value)
        diff_total = abs(p401 - p101)
        ref = max(p401, 0.01)
        assert diff_total < 0.05 * ref + 0.02, (
            f"American price not converged: p101={p101:.4f}, p401={p401:.4f}"
        )

    @pytest.mark.parametrize("is_call", [True, False])
    def test_convergence_european_exact(self, is_call):
        """European (american=False) is exact at any steps (delegates to B76)."""
        analytic = _b76(_F, _K, _T, _SIG, _R, is_call)
        for steps in (101, 201, 401):
            tree = lr_tree_price(_F, _K, _T, _SIG, _R, is_call, steps=steps, american=False)
            assert abs(tree - analytic) < 1e-10


# ---------------------------------------------------------------------------
# 7. Edge cases / input validation
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_t_zero_intrinsic_call(self):
        """T=0 call returns intrinsic."""
        val = lr_tree_price(110.0, 100.0, 0.0, 0.2, 0.05, True)
        assert abs(val - 10.0) < 1e-10

    def test_t_zero_intrinsic_put(self):
        """T=0 put returns intrinsic."""
        val = lr_tree_price(90.0, 100.0, 0.0, 0.2, 0.05, False)
        assert abs(val - 10.0) < 1e-10

    def test_t_zero_otm_zero(self):
        """T=0 OTM returns 0."""
        val = lr_tree_price(90.0, 100.0, 0.0, 0.2, 0.05, True)
        assert val == 0.0

    def test_invalid_sigma_raises(self):
        with pytest.raises(ValueError, match="sigma"):
            lr_tree_price(100.0, 100.0, 1.0, 0.0, 0.05, True)

    def test_invalid_F_raises(self):
        with pytest.raises(ValueError):
            lr_tree_price(0.0, 100.0, 1.0, 0.2, 0.05, True)

    def test_invalid_K_raises(self):
        with pytest.raises(ValueError):
            lr_tree_price(100.0, -1.0, 1.0, 0.2, 0.05, True)

    def test_even_steps_rounded_up(self):
        """Even steps is silently rounded to odd; result equals odd(steps+1)."""
        p_even = lr_tree_price(100.0, 100.0, 1.0, 0.2, 0.05, True, steps=200)
        p_odd = lr_tree_price(100.0, 100.0, 1.0, 0.2, 0.05, True, steps=201)
        # 200 -> 201, so results must be identical
        assert abs(p_even - p_odd) < 1e-10

    def test_eep_at_t_zero(self):
        """At T=0, American = European = intrinsic, so EEP = 0."""
        eep = early_exercise_premium(110.0, 100.0, 0.0, 0.2, 0.05, True)
        assert eep == 0.0

    def test_baw_invalid_sigma_raises(self):
        with pytest.raises(ValueError, match="sigma"):
            baw_eep(100.0, 100.0, 1.0, 0.0, 0.05, True)

    def test_baw_invalid_F_raises(self):
        with pytest.raises(ValueError):
            baw_eep(0.0, 100.0, 1.0, 0.2, 0.05, True)

    def test_baw_t_zero_intrinsic(self):
        """BAW at T=0 returns intrinsic."""
        val = baw_eep(110.0, 100.0, 0.0, 0.2, 0.05, True)
        assert abs(val - 10.0) < 1e-10

    def test_american_price_nonneg(self):
        """American price >= 0 across grid."""
        for K in (70.0, 100.0, 130.0):
            for T in (0.05, 0.5):
                for is_call in (True, False):
                    val = lr_tree_price(_F, K, T, 0.3, _R, is_call, steps=51)
                    assert val >= 0.0

    def test_american_ge_intrinsic(self):
        """American price >= intrinsic value (early exercise lower bound)."""
        for is_call in (True, False):
            for K in (90.0, 100.0, 110.0):
                val = lr_tree_price(_F, K, _T, _SIG, _R, is_call, steps=201)
                sign = 1.0 if is_call else -1.0
                intrinsic = max(sign * (_F - K), 0.0)
                assert val >= intrinsic - 1e-8, (
                    f"American={val:.6f} < intrinsic={intrinsic:.6f} "
                    f"K={K}, is_call={is_call}"
                )
