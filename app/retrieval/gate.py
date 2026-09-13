"""Relevance gating and the decision to refuse.

Chunks below the threshold are dropped before they can reach a prompt. If
nothing survives, this module returns a refusal, and the answer layer must
honour it rather than attempting a best-effort answer.
"""
