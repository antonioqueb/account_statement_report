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
        
        _logger.info("PARSER DATA KEYS: %s", list(data.keys()))
        _logger.info("INNER DATA KEYS: %s", list((data.get('data', {})).keys()))
        
        # Odoo envuelve data dentro de {'data': {...}} al usar report_action
        report_data = data.get('data', data)
        
        wizard = self.env['account.statement.wizard'].browse(docids)
        
        return {
            'doc_ids': docids,
            'doc_model': 'account.statement.wizard',
            'docs': wizard,
            'data': report_data,
        }