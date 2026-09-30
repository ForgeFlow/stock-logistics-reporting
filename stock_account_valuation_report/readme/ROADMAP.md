* `account_qty_at_date` is summed from `stock_quantity`, added by
  `account_move_line_stock_quantity`, for the journal items a stock move
  posts, and from the line quantity for vendor bills and customer invoices
  hitting the valuation account. The standard `quantity` cannot be used for
  the former: 19.0 leaves it at 1 on those lines.
