# Copyright 2020 ForgeFlow S.L.
# Copyright 2019 Aleph Objects, Inc.
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).


from odoo import api, fields, models
from odoo.fields import Domain
from odoo.tools import float_compare

COMPARISONS = {
    "=": lambda comparison: comparison == 0,
    "!=": lambda comparison: comparison != 0,
    "<": lambda comparison: comparison < 0,
    "<=": lambda comparison: comparison <= 0,
    ">": lambda comparison: comparison > 0,
    ">=": lambda comparison: comparison >= 0,
}


def _matches(discrepancy, operator, value, rounding):
    """Compare the discrepancy with the value of the domain, at the precision of
    the currency, so that the rounding of the computation is not searched on."""
    if operator in ("in", "not in"):
        found = any(
            float_compare(discrepancy, v or 0.0, precision_rounding=rounding) == 0
            for v in value
        )
        return found if operator == "in" else not found
    comparison = float_compare(discrepancy, value or 0.0, precision_rounding=rounding)
    return COMPARISONS[operator](comparison)


class ProductProduct(models.Model):
    _inherit = "product.product"

    stock_value = fields.Monetary(
        "Inventory Value",
        compute="_compute_inventory_value",
        currency_field="cost_currency_id",
    )
    account_value = fields.Monetary(
        "Accounting Value",
        compute="_compute_inventory_value",
        currency_field="cost_currency_id",
    )
    qty_at_date = fields.Float(
        "Inventory Quantity",
        compute="_compute_inventory_value",
        digits="Product Unit of Measure",
    )
    valuation_discrepancy = fields.Monetary(
        compute="_compute_inventory_value",
        search="_search_valuation_discrepancy",
        currency_field="cost_currency_id",
    )

    @api.model
    def _search_valuation_discrepancy(self, operator, value):
        """The discrepancy is computed, so the condition of the domain is applied
        on the discrepancy of every product that can hold one, the ones valued in
        real time."""
        if operator not in COMPARISONS and operator not in ("in", "not in"):
            return NotImplemented
        rounding = self.env.company.currency_id.rounding
        discrepancies = self._get_valuation_discrepancies()
        return Domain(
            "id",
            "in",
            [
                product_id
                for product_id, discrepancy in discrepancies.items()
                if _matches(discrepancy, operator, value, rounding)
            ],
        )

    def _convert_to_current_company(self, to_date=False):
        """The value of the stock is expressed in the currency of the company in
        use, so the balances of the other companies are converted to it before
        being added up."""
        company = self.env.company
        target = company.currency_id
        date = fields.Date.to_date(to_date) or fields.Date.context_today(self)

        def convert(amount, from_company):
            if from_company.currency_id == target:
                return amount
            # the rates of the company in use, the ones the value of the stock
            # is converted with
            return from_company.currency_id._convert(amount, target, company, date)

        return convert

    def _get_stock_qty_and_value(self, to_date=False):
        products = self.sudo().with_company(self.env.company)._with_valuation_context()
        if to_date:
            products = products.with_context(at_date=to_date, to_date=to_date)
        return {p.id: (p.qty_available, p.total_value) for p in products}

    @api.model
    def _get_discrepancy_product_ids(self):
        """Return product IDs where stock and accounting values diverge."""
        rounding = self.env.company.currency_id.rounding
        return [
            product_id
            for product_id, discrepancy in self._get_valuation_discrepancies().items()
            if float_compare(discrepancy, 0.0, precision_rounding=rounding) != 0
        ]

    @api.model
    def _get_valuation_discrepancies(self):
        """Return, per product valued in real time, how much its stock value
        diverges from its accounting value."""
        to_date = self.env.context.get("at_date", False)
        # The value of the stock is the one of every allowed company, see
        # _get_stock_qty_and_value, so the accounting value is read from the
        # same companies, each with the valuation account it resolves to.
        categ_field = self.env["product.category"]._fields[
            "property_stock_valuation_account_id"
        ]
        accounts_by_company = {}
        for company in self.env.companies:
            categories = (
                self.env["product.category"]
                .with_company(company)
                .search(Domain("property_valuation", "=", "real_time"))
            )
            categ_accounts = {}
            for c in categories:
                acc = (
                    c.property_stock_valuation_account_id
                    or categ_field.get_company_dependent_fallback(c)
                    or company.account_stock_valuation_id
                )
                if acc:
                    categ_accounts[c.id] = acc.id
            if categ_accounts:
                accounts_by_company[company] = categ_accounts
        categ_ids = sorted(
            {categ for accounts in accounts_by_company.values() for categ in accounts}
        )
        if not categ_ids:
            return {}
        # pylint: disable=E8103
        self.env.cr.execute(
            """
            SELECT pp.id, pt.categ_id
            FROM product_product pp
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            WHERE pt.categ_id = ANY(%s)
            """,
            (categ_ids,),
        )
        rows = self.env.cr.fetchall()
        if not rows:
            return {}
        categ_by_product = dict(rows)
        product_ids = list(categ_by_product)
        date_param = (to_date,) if to_date else ()
        aml_date_clause = "AND aml.date <= %s" if to_date else ""
        stock_vals = {
            pid: value
            for pid, (_qty, value) in self.browse(product_ids)
            ._get_stock_qty_and_value(to_date)
            .items()
        }
        self.env["account.move.line"].flush_model()
        _convert = self._convert_to_current_company(to_date)
        acct_vals = {}
        for company, categ_accounts in accounts_by_company.items():
            company_id = company.id
            account_ids = sorted(set(categ_accounts.values()))
            # pylint: disable=E8103
            self.env.cr.execute(
                f"""
                SELECT aml.product_id, aml.account_id, SUM(aml.balance)
                FROM account_move_line aml
                WHERE aml.parent_state = 'posted'
                AND aml.company_id = %s
                AND aml.product_id = ANY(%s)
                AND aml.account_id = ANY(%s)
                {aml_date_clause}
                GROUP BY aml.product_id, aml.account_id
                """,
                (company_id, product_ids, account_ids) + date_param,
            )
            for product_id, account_id, balance in self.env.cr.fetchall():
                # only the account this company resolves to for the product,
                # the same pairing the displayed accounting value uses
                if categ_accounts.get(categ_by_product.get(product_id)) != account_id:
                    continue
                acct_vals[product_id] = acct_vals.get(product_id, 0.0) + _convert(
                    balance, company
                )
        return {
            pid: stock_vals.get(pid, 0.0) - acct_vals.get(pid, 0.0)
            for pid in product_ids
        }

    def _get_valuation_aml_ids(self, product, to_date):
        # the journal items of every allowed company, each with the valuation
        # account it resolves to, so they add up to the accounting value shown
        valuation_account_ids = set()
        for company in self.env.companies:
            account = product.with_company(company)._get_product_accounts()[
                "stock_valuation"
            ]
            if account:
                valuation_account_ids.add(account.id)
        if not valuation_account_ids:
            return []
        self.env["account.move.line"].flush_model()
        # pylint: disable=E8103
        query = """
            SELECT array_agg(aml.id)
            FROM account_move_line AS aml
            WHERE aml.product_id = %s
            AND aml.parent_state = 'posted'
            AND aml.company_id = ANY(%s)
            AND aml.account_id = ANY(%s)
            {where_date_clause}
        """
        params = (product.id, self.env.companies.ids, sorted(valuation_account_ids))
        where_date_clause = "AND aml.date <= %s" if to_date else ""
        query = query.format(where_date_clause=where_date_clause)
        if to_date:
            params = params + (to_date,)
        self.env.cr.execute(query, params=params)
        row = self.env.cr.fetchone()
        return list(row[0]) if row and row[0] else []

    def _compute_inventory_value(self):
        self.env["account.move.line"].check_access("read")
        to_date = self.env.context.get("at_date", False)
        # 1) ACCOUNTING VALUES — restrict to valuation accounts only
        # Use _get_product_accounts() for the full 3-level fallback on account lookup.
        # The value of the stock is the one of every allowed company, see
        # _get_stock_qty_and_value, so the journal items of all of them are added
        # up, each company with the valuation account it resolves to.
        accounting_values = {}
        valued_product_ids = set()
        _convert = self._convert_to_current_company(to_date)
        for company in self.env.companies:
            product_valuation_account = {}
            for product in self.with_company(company):
                if product.valuation == "real_time":
                    acc_id = product._get_product_accounts()["stock_valuation"].id
                    if acc_id:
                        product_valuation_account[product.id] = acc_id
                        valued_product_ids.add(product.id)
            if not product_valuation_account:
                continue
            self.env["account.move.line"].flush_model()
            # pylint: disable=E8103
            query = """
                SELECT aml.product_id, aml.account_id, sum(aml.balance)
                FROM account_move_line AS aml
                WHERE aml.product_id IN %s
                AND aml.parent_state = 'posted'
                AND aml.company_id = %s
                AND aml.account_id IN %s
                {where_date_clause}
                GROUP BY aml.product_id, aml.account_id
            """
            params = (
                tuple(product_valuation_account),
                company.id,
                tuple(set(product_valuation_account.values())),
            )
            where_date_clause = "AND aml.date <= %s" if to_date else ""
            query = query.format(where_date_clause=where_date_clause)
            if to_date:
                params = params + (to_date,)
            self.env.cr.execute(query, params=params)
            for product_id, account_id, balance in self.env.cr.fetchall():
                if product_valuation_account.get(product_id) != account_id:
                    continue
                accounting_values[product_id] = accounting_values.get(
                    product_id, 0
                ) + _convert(balance, company)
        # 2) INVENTORY VALUES
        stock_data = self._get_stock_qty_and_value(to_date)
        for product in self:
            product.account_value = accounting_values.get(product.id, 0)
            qty, value = stock_data.get(product.id, (0, 0))
            product.qty_at_date = qty
            product.stock_value = value
            if product.id in valued_product_ids:
                product.valuation_discrepancy = (
                    product.stock_value - product.account_value
                )
            else:
                product.valuation_discrepancy = 0

    def action_view_amls(self):
        self.ensure_one()
        to_date = self.env.context.get("at_date", False)
        aml_ids = self._get_valuation_aml_ids(self, to_date)
        list_view_ref = self.env.ref("account.view_move_line_tree")
        form_view_ref = self.env.ref("account.view_move_line_form")
        return {
            "name": self.env._("Accounting Valuation at date"),
            "type": "ir.actions.act_window",
            "view_type": "form",
            "view_mode": "list,form",
            "context": self.env.context,
            "res_model": "account.move.line",
            "domain": Domain("id", "in", aml_ids),
            "views": [(list_view_ref.id, "list"), (form_view_ref.id, "form")],
        }

    def _get_move_ids(self, product_ids, to_date):
        # pylint: disable=E8103
        query = """
            SELECT id FROM stock_move
            WHERE state = 'done' AND company_id = ANY(%s) AND product_id = ANY(%s)
            {where_date_clause}
        """
        params = (self.env.companies.ids, product_ids)
        date_clause = "AND date <= %s" if to_date else ""
        query = query.format(where_date_clause=date_clause)
        if to_date:
            params = params + (to_date,)
        self.env.cr.execute(query, params=params)
        return [row[0] for row in self.env.cr.fetchall()]

    def action_view_valuation_layers(self):
        to_date = self.env.context.get("at_date", False)
        move_ids = self._get_move_ids(self.ids, to_date)
        action = self.env["ir.actions.actions"]._for_xml_id(
            "stock_account.stock_valuation_layer_report_action"
        )
        action["domain"] = Domain("id", "in", move_ids)
        action["context"] = {}
        return action

    def action_view_valuation_moves(self):
        self.ensure_one()
        to_date = self.env.context.get("at_date", False)
        move_ids = self._get_move_ids(self.ids, to_date)
        action = self.env["ir.actions.actions"]._for_xml_id("stock.stock_move_action")
        action["domain"] = Domain("id", "in", move_ids)
        return action
