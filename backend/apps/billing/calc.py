"""Line and document totals, shared by quotations, orders, invoices and bills."""
from decimal import Decimal

from apps.core.money import ZERO, D, round_to


def compute_line(quantity, unit_price, discount_pct, tax, decimals):
    """Returns (subtotal, tax_amount, total) in document currency, rounded per line.
    Tax-inclusive prices are split so that subtotal + tax == gross exactly."""
    gross = D(quantity) * D(unit_price) * (Decimal(100) - D(discount_pct)) / Decimal(100)
    rate = D(tax.rate) / Decimal(100) if tax else ZERO
    if tax and tax.price_include:
        total = round_to(gross, decimals)
        subtotal = round_to(gross / (1 + rate), decimals)
        tax_amount = total - subtotal
    else:
        subtotal = round_to(gross, decimals)
        tax_amount = round_to(subtotal * rate, decimals)
        total = subtotal + tax_amount
    return subtotal, tax_amount, total


def apply_totals(doc, lines, decimals):
    """Recompute each line and the document header totals (lines must have quantity/unit_price/
    discount_pct/tax attributes). Saves lines; caller saves the document."""
    untaxed = tax_total = ZERO
    for ln in lines:
        ln.subtotal, ln.tax_amount, ln.total = compute_line(ln.quantity, ln.unit_price, ln.discount_pct, ln.tax,
                                                            decimals)
        ln.save(update_fields=["subtotal", "tax_amount", "total"])
        untaxed += ln.subtotal
        tax_total += ln.tax_amount
    doc.amount_untaxed = untaxed
    doc.amount_tax = tax_total
    doc.amount_total = untaxed + tax_total
