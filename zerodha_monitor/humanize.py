"""Template formatting helpers."""

from __future__ import annotations


def signed_rupee(value: float, decimals: int = 0) -> str:
    """Format a signed rupee amount with the sign before the currency symbol.

    Python's ``{:+,.0f}`` format puts the sign on the digits, so a literal
    ``₹{{ "{:+,.0f}".format(x) }}`` in a template renders "₹+1,234" for a
    gain instead of the conventional "+₹1,234".
    """
    sign = "-" if value < 0 else "+"
    return f"{sign}₹{abs(value):,.{decimals}f}"
