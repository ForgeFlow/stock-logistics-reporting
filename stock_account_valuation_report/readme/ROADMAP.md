* In Odoo 19.0, stock valuation journal items (`account.move.line`) generated
  by stock moves no longer track `quantity` (it defaults to 0). Consequently,
  the `account_qty_at_date` and `qty_discrepancy` fields, along with related filters
  and comparison logic, have been removed since accounting quantities cannot be
  derived from valuation journal lines in 19.0.
