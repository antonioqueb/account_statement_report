# -*- coding: utf-8 -*-
from odoo import models, api
import logging

_logger = logging.getLogger(__name__)


class AccountStatementReportParser(models.AbstractModel):
    _name = 'report.account_statement_report.account_statement'
    _description = 'Parser para Estado de Cuenta'

    @api.model
    def _get_report_values(self, docids, data=None):
        if data is None:
            data = {}

        report_data = data.get('data', data)

        wizard_id = report_data.get('wizard_id')
        if wizard_id:
            wizard = self.env['account.statement.wizard'].browse(wizard_id)
        else:
            wizard = self.env['account.statement.wizard'].browse(docids)

        values = {
            'doc_ids': [wizard_id] if wizard_id else docids,
            'doc_model': 'account.statement.wizard',
            'docs': wizard,
            'data': report_data,
            'banorte_rate': report_data.get('banorte_rate', 0),
            'orders_data': report_data.get('orders_data', []),
            'partner_name': report_data.get('partner_name', ''),
            'partner_vat': report_data.get('partner_vat', ''),
            'project_name': report_data.get('project_name', ''),
            'statement_date': report_data.get('statement_date', ''),
            'total_balance_usd': report_data.get('total_balance_usd', 0),
            'total_balance_mxn': report_data.get('total_balance_mxn', 0),
            'total_amount_usd': report_data.get('total_amount_usd', 0),
            'total_amount_mxn': report_data.get('total_amount_mxn', 0),
            'total_paid_usd': report_data.get('total_paid_usd', 0),
            'total_paid_mxn': report_data.get('total_paid_mxn', 0),
            'total_orders': report_data.get('total_orders', 0),
            'orders_usd_count': report_data.get('orders_usd_count', 0),
            'orders_mxn_count': report_data.get('orders_mxn_count', 0),
            'report_currency': report_data.get('report_currency', 'mxn'),
        }
        _logger.info(
            "PARSER: orders=%s, partner=%s, report_currency=%s, usd=%s, mxn=%s",
            len(values['orders_data']), values['partner_name'],
            values['report_currency'], values['orders_usd_count'], values['orders_mxn_count'],
        )
        return values