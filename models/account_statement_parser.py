# -*- coding: utf-8 -*-
from odoo import models, api
import logging

_logger = logging.getLogger(__name__)


class AccountStatementReportParser(models.AbstractModel):
    _name = 'report.account_statement_report.account_statement'
    _description = 'Parser para Estado de Cuenta'

    @api.model
    def _get_report_values(self, docids, data=None):
        if not data:
            data = {}

        wizard = self.env['account.statement.wizard'].browse(docids)

        values = {
            'doc_ids': docids,
            'doc_model': 'account.statement.wizard',
            'docs': wizard,
            'data': data,
            # Variables directas para el template
            'banorte_rate': data.get('banorte_rate', 0),
            'orders_data': data.get('orders_data', []),
            'partner_name': data.get('partner_name', ''),
            'partner_vat': data.get('partner_vat', ''),
            'project_name': data.get('project_name', ''),
            'statement_date': data.get('statement_date', ''),
            'total_balance_usd': data.get('total_balance_usd', 0),
            'total_balance_mxn': data.get('total_balance_mxn', 0),
            'total_amount_usd': data.get('total_amount_usd', 0),
            'total_amount_mxn': data.get('total_amount_mxn', 0),
            'total_paid_usd': data.get('total_paid_usd', 0),
            'total_paid_mxn': data.get('total_paid_mxn', 0),
            'total_orders': data.get('total_orders', 0),
        }
        _logger.info("PARSER RETURNING: orders_data len=%s, partner=%s", 
                      len(values['orders_data']), values['partner_name'])
        return values