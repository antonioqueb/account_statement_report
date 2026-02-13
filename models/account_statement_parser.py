# -*- coding: utf-8 -*-
from odoo import models, api
import logging

_logger = logging.getLogger(__name__)


class AccountStatementReportParser(models.AbstractModel):
    _name = 'report.account_statement_report.report_account_statement_document'
    _description = 'Parser para Estado de Cuenta'

    @api.model
    def _get_report_values(self, docids, data=None):
        if not data:
            data = {}
        
        wizards = self.env['account.statement.wizard'].browse(docids)
        
        return {
            'doc_ids': docids,
            'doc_model': 'account.statement.wizard',
            'docs': wizards,
            'data': data,
        }