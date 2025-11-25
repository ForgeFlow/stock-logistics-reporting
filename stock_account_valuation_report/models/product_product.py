# Copyright 2020 ForgeFlow S.L.
# Copyright 2019 Aleph Objects, Inc.
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).


from odoo import _, api, fields, models
from odoo.tools import float_compare


class ProductProduct(models.Model):
    _inherit = "product.product"

    stock_value = fields.Float("Inventory Value", compute="_compute_inventory_value")
    account_value = fields.Float("Accounting Value", compute="_compute_inventory_value")
    qty_at_date = fields.Float("Inventory Quantity", compute="_compute_inventory_value")
    account_qty_at_date = fields.Float(
        "Accounting Quantity", compute="_compute_inventory_value"
    )
    stock_fifo_real_time_aml_ids = fields.Many2many(
        "account.move.line", compute="_compute_inventory_value"
    )
    stock_move_valuated_ids = fields.Many2many(
        "stock.move", compute="_compute_inventory_value"
    )
    valuation_discrepancy = fields.Float(
        compute="_compute_inventory_value",
        search="_search_valuation_discrepancy",
    )
    qty_discrepancy = fields.Float(
        compute="_compute_inventory_value",
        search="_search_qty_discrepancy",
    )

    @api.model
    def _search_qty_discrepancy(self, operator, value):
        products = self.with_context(active_test=False).search(
            [("is_storable", "=", True)],
        )
        products_with_discrepancy = products.filtered(
            lambda pp: float_compare(
                pp.qty_at_date,
                pp.account_qty_at_date,
                precision_rounding=pp.uom_id.rounding,
            )
        )
        return [("id", "in", products_with_discrepancy.ids)]

    @api.model
    def _search_valuation_discrepancy(self, operator, value):
        products = self.with_context(active_test=False).search(
            [("is_storable", "=", True)],
        )
        products_with_discrepancy = products.filtered(
            lambda pp: self.env.company.currency_id.compare_amounts(
                pp.stock_value, pp.account_value
            )
        )
        return [("id", "in", products_with_discrepancy.ids)]

    def _compute_inventory_value(self):
        self.env["account.move.line"].check_access("read")
        to_date = self.env.context.get("at_date", False)
        # 1) ACCOUNTING VALUES (same as your module)
        accounting_values = {}
        query = """
            SELECT aml.product_id, aml.account_id,
                sum(aml.balance), sum(quantity),
                array_agg(aml.id)
            FROM account_move_line AS aml
            INNER JOIN account_move AS am ON am.id = aml.move_id
            WHERE aml.product_id IN %s
            AND am.state = 'posted'
            AND aml.company_id=%s %s
            GROUP BY aml.product_id, aml.account_id
        """
        params = (tuple(self.ids), self.env.company.id)
        if to_date:
            query = query % ("AND aml.date <= %s",)
            params = params + (to_date,)
        else:
            query = query % ("",)
        self.env.cr.execute(query, params=params)
        for row in self.env.cr.fetchall():
            accounting_values[(row[0], row[1])] = (row[2], row[3], list(row[4]))
        # 2) INVENTORY VALUES (v19: computed from stock moves)
        # We use stock moves that impact valuation (value != 0)
        move_query = """
            SELECT sm.product_id,
                sum(sm.quantity_done),
                sum(sm.stock_valuation_amount),
                array_agg(sm.id)
            FROM stock_move AS sm
            WHERE sm.product_id IN %s
            AND sm.company_id=%s
            AND sm.state='done'
            AND sm.stock_valuation_amount IS NOT NULL
            %s
            GROUP BY sm.product_id
        """
        params2 = (tuple(self.ids), self.env.company.id)
        if to_date:
            move_query = move_query % ("AND sm.date <= %s",)
            params2 = params2 + (to_date,)
        else:
            move_query = move_query % ("",)
        self.env.cr.execute(move_query, params=params2)
        move_values = {row[0]: (row[1], row[2], list(row[3])) for row in self.env.cr.fetchall()}
        StockMove = self.env["stock.move"]
        for product in self:
            # ACCOUNTING SIDE
            if product.valuation == "real_time":
                valuation_account_id = product.categ_id.property_stock_valuation_account_id.id
                value, qty, aml_ids = accounting_values.get((product.id, valuation_account_id)) or (0, 0, [])
                product.account_value = value
                product.account_qty_at_date = qty
                product.stock_fifo_real_time_aml_ids = self.env["account.move.line"].browse(aml_ids)
            else:
                product.account_value = 0
                product.account_qty_at_date = 0
                product.stock_fifo_real_time_aml_ids = []
            # INVENTORY SIDE (move-based)
            qty, val, move_ids = move_values.get(product.id, (0, 0, []))
            product.qty_at_date = qty
            product.stock_value = val
            product.stock_move_valuated_ids = StockMove.browse(move_ids)  # repurpose field
            if product.valuation == "real_time":
                product.valuation_discrepancy = product.stock_value - product.account_value
                product.qty_discrepancy = product.qty_at_date - product.account_qty_at_date
            else:
                product.valuation_discrepancy = 0
                product.qty_discrepancy = 0


    def action_view_amls(self):
        self.ensure_one()
        list_view_ref = self.env.ref("account.view_move_line_tree")
        form_view_ref = self.env.ref("account.view_move_line_form")
        action = {
            "name": _("Accounting Valuation at date"),
            "type": "ir.actions.act_window",
            "view_type": "form",
            "view_mode": "list,form",
            "context": self.env.context,
            "res_model": "account.move.line",
            "domain": [
                (
                    "id",
                    "in",
                    self.stock_fifo_real_time_aml_ids.ids,
                )
            ],
            "views": [(list_view_ref.id, "list"), (form_view_ref.id, "form")],
        }
        return action

    def action_view_valuation_layers(self):
        action = self.env["ir.actions.actions"]._for_xml_id(
            "stock_account.stock_valuation_layer_report_action"
        )
        action["domain"] = [
            ("id", "in", self.stock_move_valuated_ids.ids),
        ]
        action["context"] = {}
        return action

    def action_view_valuation_moves(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("stock.stock_move_action")
        action["domain"] = [("id","in",self.stock_move_valuated_ids.ids)]
        return action
