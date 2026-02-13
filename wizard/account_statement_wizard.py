# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class AccountStatementWizard(models.TransientModel):
    _name = 'account.statement.wizard'
    _description = 'Wizard para Estado de Cuenta'

    partner_id = fields.Many2one('res.partner', string='Cliente', required=True)
    project_id = fields.Many2one('project.project', string='Proyecto (Filtro Opcional)')
    date_from = fields.Date(string='Desde', help='Filtrar órdenes desde esta fecha')
    date_to = fields.Date(string='Hasta', help='Filtrar órdenes hasta esta fecha')
    
    include_draft = fields.Boolean(string='Incluir Cotizaciones (Borrador)', default=False)
    include_fully_paid = fields.Boolean(string='Incluir Pagadas al 100%', default=False)
    
    # Campo para mostrar el tipo de cambio actual
    exchange_rate = fields.Float(string='Tipo de Cambio Banorte', digits=(12, 4), readonly=True, compute='_compute_exchange_rate')
    
    @api.depends_context('uid')
    def _compute_exchange_rate(self):
        rate = self._get_banorte_rate()
        for rec in self:
            rec.exchange_rate = rate
    
    def _get_banorte_rate(self):
        """Obtiene el tipo de cambio Banorte del parámetro del sistema"""
        rate_param = self.env['ir.config_parameter'].sudo().get_param('banorte.last_rate', '0')
        try:
            rate = float(rate_param)
        except (ValueError, TypeError):
            rate = 0.0
        
        # Si no hay rate de Banorte, usar el rate de Odoo
        if rate <= 0:
            usd = self.env.ref('base.USD', raise_if_not_found=False)
            company_currency = self.env.company.currency_id
            if usd and company_currency and company_currency.name == 'MXN':
                rate = usd._convert(1.0, company_currency, self.env.company, fields.Date.today())
            elif usd and company_currency and company_currency.name == 'USD':
                mxn = self.env.ref('base.MXN', raise_if_not_found=False)
                if mxn:
                    rate = mxn._convert(1.0, usd, self.env.company, fields.Date.today())
                    rate = 1 / rate if rate > 0 else 0
        
        return rate

    def _get_sale_orders(self):
        """Obtiene las órdenes de venta filtradas"""
        domain = [('partner_id', '=', self.partner_id.id)]
        
        states = ['sale', 'done']
        if self.include_draft:
            states.extend(['draft', 'sent'])
        domain.append(('state', 'in', states))
        
        if self.project_id:
            domain.append(('x_project_id', '=', self.project_id.id))
        
        if self.date_from:
            domain.append(('date_order', '>=', fields.Datetime.to_datetime(self.date_from)))
        
        if self.date_to:
            domain.append(('date_order', '<=', fields.Datetime.to_datetime(self.date_to).replace(hour=23, minute=59, second=59)))
        
        # Excluir respaldos de cotización si el campo existe
        try:
            domain.append(('x_is_quote_backup', '=', False))
        except Exception:
            pass
        
        orders = self.env['sale.order'].search(domain, order='date_order asc')
        return orders

    def action_print_statement(self):
        """Genera el reporte PDF"""
        self.ensure_one()
        
        orders = self._get_sale_orders()
        if not orders:
            raise UserError("No se encontraron órdenes de venta para este cliente con los filtros seleccionados.")
        
        banorte_rate = self._get_banorte_rate()
        
        # Recopilar datos
        orders_data = []
        total_balance_usd = 0.0
        total_balance_mxn = 0.0
        total_amount_usd = 0.0
        total_amount_mxn = 0.0
        total_paid_usd = 0.0
        total_paid_mxn = 0.0
        
        for order in orders:
            data = order._get_statement_data(banorte_rate)
            
            # Filtrar pagadas si no se quieren
            if not self.include_fully_paid and data['balance'] <= 0.01:
                continue
            
            orders_data.append(data)
            total_balance_usd += data['balance_usd']
            total_balance_mxn += data['balance_mxn']
            total_amount_usd += data['total_usd']
            total_amount_mxn += data['total_mxn']
            
            # Pagos en cada moneda
            if data['currency'] == 'USD':
                total_paid_usd += data['total_paid']
                total_paid_mxn += data['total_paid'] * banorte_rate if banorte_rate > 0 else 0
            else:
                total_paid_mxn += data['total_paid']
                total_paid_usd += data['total_paid'] / banorte_rate if banorte_rate > 0 else 0
        
        if not orders_data:
            raise UserError("Todas las órdenes encontradas están pagadas al 100%. Active 'Incluir Pagadas al 100%' para verlas.")
        
        # Guardar datos en contexto para el reporte
        data = {
            'wizard_id': self.id,
            'partner_id': self.partner_id.id,
            'partner_name': self.partner_id.name,
            'partner_vat': self.partner_id.vat or '',
            'project_name': self.project_id.name if self.project_id else '',
            'date_from': str(self.date_from) if self.date_from else '',
            'date_to': str(self.date_to) if self.date_to else '',
            'banorte_rate': banorte_rate,
            'statement_date': str(fields.Date.today()),
            'orders_data': orders_data,
            'total_balance_usd': total_balance_usd,
            'total_balance_mxn': total_balance_mxn,
            'total_amount_usd': total_amount_usd,
            'total_amount_mxn': total_amount_mxn,
            'total_paid_usd': total_paid_usd,
            'total_paid_mxn': total_paid_mxn,
            'total_orders': len(orders_data),
        }
        
        return self.env.ref('account_statement_report.action_report_account_statement').report_action(self, data=data)
