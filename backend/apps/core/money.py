from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0")


def D(value) -> Decimal:
    if value is None or value == "":
        return ZERO
    if isinstance(value, float):
        # floats are never accepted silently for money; convert via str to avoid binary noise
        value = repr(value)
    return Decimal(value)


def round_to(value, decimals: int) -> Decimal:
    q = Decimal(1).scaleb(-decimals)
    return D(value).quantize(q, rounding=ROUND_HALF_UP)
