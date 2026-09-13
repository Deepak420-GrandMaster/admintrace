"""Section-aware chunking.

Splits on the boundaries the source documents already carry. A list of
required documents is never split across chunks, and a numbered procedure is
never split mid-sequence; those rules override the size target. Mutually
exclusive applicability branches stay separated, so rules for one situation
cannot be read as rules for another.
"""
