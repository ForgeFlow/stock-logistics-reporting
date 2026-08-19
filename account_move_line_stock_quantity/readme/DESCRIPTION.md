Since Odoo 19.0, stock valuation journal items (`account.move.line`) generated
by stock moves no longer track a quantity: the standard `quantity` field is
reset to 0 for any line that is not part of an invoice (`display_type !=
'product'`).

This module adds a dedicated `stock_quantity` field on `account.move.line` to
record the quantity, in the product's base unit of measure, moved by the
stock operation that generated the journal item: positive for incoming
valuation entries, negative for outgoing ones. Landed cost and price
difference adjustments, which are pure monetary corrections with no physical
stock movement, keep this field at 0.

This gives other modules (e.g. valuation reports) a reliable way to
reconstruct the accounting stock quantity as the sum of `stock_quantity` on
a product's valuation account.
