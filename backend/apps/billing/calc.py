"""Line and document totals, shared by quotations, orders, invoices and bills.

Egyptian e-invoice arithmetic (ETA): netTotal = salesTotal - discount; VAT (T1) is added on the net;
withholding tax (T4) is computed on the net and *deducted*; total = net + VAT - WHT.
"""
from decimal import Decimal

from apps.core.money import ZERO, D, round_to


def compute_line(quantity, unit_price, discount_pct, tax, decimals, wht=None):
    """Returns (subtotal, tax_amount, wht_amount, total) in document currency, rounded per line.
    Tax-inclusive prices are split so that subtotal + VAT == gross exactly."""
    gross = D(quantity) * D(unit_price) * (Decimal(100) - D(discount_pct)) / Decimal(100)
    rate = D(tax.rate) / Decimal(100) if tax else ZERO
    if tax and tax.price_include:
        subtotal = round_to(gross / (1 + rate), decimals)
        tax_amount = round_to(gross, decimals) - subtotal
    else:
        subtotal = round_to(gross, decimals)
        tax_amount = round_to(subtotal * rate, decimals)
    wht_amount = round_to(subtotal * D(wht.rate) / Decimal(100), decimals) if wht else ZERO
    return subtotal, tax_amount, wht_amount, subtotal + tax_amount - wht_amount


def apply_totals(doc, lines, decimals, wht_base_before=None):
    """Recompute each line and the document header totals. Saves lines; caller saves the document.

    Withholding is only applied once the *cumulative* base paid/received for this payee under a given
    withholding-tax code reaches the tax's minimum base (e.g. EGP 300) -- the threshold applies per
    amount paid to a payee, not per document (FC review, Section 11/20: "EGP 300 applies to each
    amount paid, not to each document"). `wht_base_before` is an optional {tax_id: Decimal} map, in
    the document's base currency, of the amount already recognised for this payee under each
    withholding-tax code earlier in the current filing period, from other posted documents -- see
    apps.billing.services._wht_base_before. Callers that don't support WHT (sales/purchase orders)
    simply omit it, which preserves the old per-document-only check."""
    wht_base_before = wht_base_before or {}
    untaxed = tax_total = wht_total = ZERO
    for ln in lines:
        wht = getattr(ln, "withholding_tax", None)
        ln.subtotal, ln.tax_amount, w, ln.total = compute_line(ln.quantity, ln.unit_price, ln.discount_pct, ln.tax,
                                                               decimals, wht)
        untaxed += ln.subtotal
        tax_total += ln.tax_amount
        ln._wht = w
    rate = D(getattr(doc, "rate", 1) or 1)
    for ln in lines:
        wht = getattr(ln, "withholding_tax", None)
        if wht is not None:
            prior = D(wht_base_before.get(wht.id, ZERO))
            if prior + untaxed * rate < D(wht.min_base):
                ln.total += ln._wht
                ln._wht = ZERO
        fields = ["subtotal", "tax_amount", "total"]
        if hasattr(ln, "wht_amount"):
            ln.wht_amount = ln._wht
            fields.append("wht_amount")
        wht_total += ln._wht
        ln.save(update_fields=fields)
    doc.amount_untaxed = untaxed
    doc.amount_tax = tax_total
    if hasattr(doc, "amount_wht"):
        doc.amount_wht = wht_total
    doc.amount_total = untaxed + tax_total - wht_total
