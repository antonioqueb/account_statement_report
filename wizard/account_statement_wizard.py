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

    # Selección manual de órdenes abiertas
    order_ids = fields.Many2many(
        'sale.order',
        'account_statement_wizard_sale_order_rel',
        'wizard_id', 'order_id',
        string='Órdenes Seleccionadas',
        help='Seleccione manualmente las órdenes a incluir. Si no selecciona ninguna, se incluirán todas las órdenes abiertas según los filtros.',
    )
    available_order_ids = fields.Many2many(
        'sale.order',
        'account_statement_wizard_available_order_rel',
        'wizard_id', 'order_id',
        string='Órdenes Disponibles',
        compute='_compute_available_orders',
        store=False,
    )

    # Campo para mostrar el tipo de cambio actual
    exchange_rate = fields.Float(
        string='Tipo de Cambio Banorte', digits=(12, 4),
        readonly=True, compute='_compute_exchange_rate',
    )

    @api.depends_context('uid')
    def _compute_exchange_rate(self):
        rate = self._get_banorte_rate()
        for rec in self:
            rec.exchange_rate = rate

    @api.depends('partner_id', 'project_id', 'date_from', 'date_to', 'include_draft')
    def _compute_available_orders(self):
        for rec in self:
            if rec.partner_id:
                rec.available_order_ids = rec._get_open_orders()
            else:
                rec.available_order_ids = self.env['sale.order']

    @api.onchange('partner_id', 'project_id', 'date_from', 'date_to', 'include_draft')
    def _onchange_filters(self):
        """Cuando cambian los filtros, resetear la selección manual"""
        self.order_ids = False

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

    def _get_base_domain(self):
        """Construye el dominio base según filtros del wizard"""
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

        return domain

    def _get_open_orders(self):
        """Obtiene las órdenes abiertas (con saldo pendiente) según filtros"""
        domain = self._get_base_domain()
        orders = self.env['sale.order'].search(domain, order='date_order asc')

        # Filtrar solo las que tienen saldo pendiente (no pagadas al 100%)
        open_orders = self.env['sale.order']
        banorte_rate = self._get_banorte_rate()
        for order in orders:
            data = order._get_statement_data(banorte_rate)
            if data['balance'] > 0.01:
                open_orders |= order
        return open_orders

    def _get_sale_orders(self):
        """Obtiene las órdenes de venta filtradas"""
        domain = self._get_base_domain()
        orders = self.env['sale.order'].search(domain, order='date_order asc')
        return orders

    def action_select_all_open(self):
        """Botón para seleccionar todas las órdenes abiertas"""
        self.ensure_one()
        if not self.partner_id:
            raise UserError("Seleccione un cliente primero.")
        self.order_ids = self._get_open_orders()
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'context': self.env.context,
        }

    def action_print_statement(self):
        """Genera el reporte PDF"""
        self.ensure_one()

        # Si hay órdenes seleccionadas manualmente, usar esas
        if self.order_ids:
            orders = self.order_ids.sorted(key=lambda o: o.date_order or '')
        else:
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
        orders_usd_count = 0
        orders_mxn_count = 0

        for order in orders:
            data = order._get_statement_data(banorte_rate)

            # Filtrar pagadas si no se quieren (solo cuando NO hay selección manual)
            if not self.order_ids and not self.include_fully_paid and data['balance'] <= 0.01:
                continue

            orders_data.append(data)
            total_balance_usd += data['balance_usd']
            total_balance_mxn += data['balance_mxn']
            total_amount_usd += data['total_usd']
            total_amount_mxn += data['total_mxn']

            # Conteo por moneda
            if data['currency'] == 'USD':
                orders_usd_count += 1
                total_paid_usd += data['total_paid']
                total_paid_mxn += data['total_paid'] * banorte_rate if banorte_rate > 0 else 0
            else:
                orders_mxn_count += 1
                total_paid_mxn += data['total_paid']
                total_paid_usd += data['total_paid'] / banorte_rate if banorte_rate > 0 else 0

        if not orders_data:
            raise UserError("Todas las órdenes encontradas están pagadas al 100%. Active 'Incluir Pagadas al 100%' para verlas.")

        # Determinar escenario de moneda
        if orders_usd_count > 0 and orders_mxn_count > 0:
            currency_scenario = 'multi_currency'
        elif orders_usd_count > 0 and orders_mxn_count == 0:
            currency_scenario = 'usd_only'
        else:
            currency_scenario = 'mxn_only'

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
            'orders_usd_count': orders_usd_count,
            'orders_mxn_count': orders_mxn_count,
            'currency_scenario': currency_scenario,
        }

        return self.env.ref('account_statement_report.action_report_account_statement').report_action(self, data=data)