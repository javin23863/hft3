"""Autonomous model-maintenance orchestrator.

Drives degraded models back through the EXISTING gauntlet/promotion/shadow
machinery (orchestrated, never rewritten), subordinate to the autonomy safety
rails. Nothing here mutates a live model except rearm.py, and only after the
full gate chain passes with autonomy explicitly enabled.
"""
