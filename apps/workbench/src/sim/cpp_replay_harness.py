"""Backward-compatible aliases — use cpp_stack_verify for new code."""

from workbench.src.sim.cpp_stack_verify import (
    CppStackVerifyHarness,
    CppStackVerifyResult,
    get_cached_stack_verify,
)

CppReplayResult = CppStackVerifyResult


class CppReplayHarness(CppStackVerifyHarness):
    """Deprecated name: stack verify only, not historical NPZ replay."""

    def replay(self, npz_path, model_id):  # noqa: ARG002 — API compat
        return self.verify()
