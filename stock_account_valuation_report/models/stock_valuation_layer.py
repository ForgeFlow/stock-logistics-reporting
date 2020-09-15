# Copyright 2018 Eficent Business and IT Consulting Services, S.L.
#   (<https://www.eficent.com>)
# Copyright 2018 Aleph Objects, Inc.
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).

from odoo import api, fields, models, tools


class StockValuationLayer(models.Model):
    """Stock Valuation Layer"""

    _inherit = "stock.valuation.layer"

    account_value = fields.Monetary(
        "Accounting Value", compute="_compute_inventory_value", store=True
    )
    account_qty_at_date = fields.Float(
        "Accounting Quantity", compute="_compute_inventory_value", store=True
    )
    account_move_line_id = fields.Many2one('account.move.line', index=True)

    def init(self):
        super().init()
        tools.create_index(
            self._cr,
            "stock_valuation_layer_index",
            self._table,
            ["account_value", "account_qty_at_date"],
        )

    @api.depends("stock_move_id", "account_move_id", "stock_valuation_layer_id")
    def _compute_inventory_value(self):
        for rec in self:
            rec.account_value = sum(
                rec.account_move_id.line_ids.filtered(
                    lambda aml: aml.product_id == rec.product_id
                    and aml.product_id.categ_id.property_stock_valuation_account_id
                    == aml.account_id
                ).mapped("debit")
            ) - sum(
                rec.account_move_id.line_ids.filtered(
                    lambda aml: aml.product_id == rec.product_id
                    and aml.product_id.categ_id.property_stock_valuation_account_id
                    == aml.account_id
                ).mapped("credit")
            )
            rec.account_qty_at_date = sum(
                rec.account_move_id.line_ids.filtered(
                    lambda aml: aml.product_id == rec.product_id
                    and aml.product_id.categ_id.property_stock_valuation_account_id
                    == aml.account_id
                ).mapped("quantity")
            )
