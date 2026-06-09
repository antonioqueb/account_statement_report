# -*- coding: utf-8 -*-
from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    x_company_currency_id = fields.Many2one(
        'res.currency',
        string='Moneda de la Compañía',
        related='company_id.currency_id',
        readonly=True,
    )
    x_customer_credit_balance = fields.Monetary(
        string='Saldo a Favor del Cliente',
        compute='_compute_customer_credit_balance',
        currency_field='x_company_currency_id',
        help='Saldo a favor del cliente (cuando ha pagado de más). '
             'Se calcula a partir del balance por cobrar del cliente.',
    )
    x_has_customer_credit = fields.Boolean(
        compute='_compute_customer_credit_balance',
    )

    def _statement_banorte_rate(self):
        """Tipo de cambio Banorte, idéntico al usado por el wizard/reporte."""
        rate_param = self.env['ir.config_parameter'].sudo().get_param('banorte.last_rate', '0')
        try:
            rate = float(rate_param)
        except (ValueError, TypeError):
            rate = 0.0

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

    def _statement_balance_mxn(self, banorte_rate):
        """Saldo (balance) de ESTA orden expresado en MXN.

        Usa EXACTAMENTE la misma lógica que el reporte (`_get_statement_data`):
        balance = total de la orden - pagos conciliados (monto completo del pago),
        convertido a MXN con el tipo de cambio Banorte. Negativo = saldo a favor.
        """
        self.ensure_one()
        currency_name = self.currency_id.name or 'USD'

        total_paid = 0.0
        for inv in self._get_related_invoices():
            for payment in inv._get_reconciled_payments():
                if payment.currency_id == self.currency_id:
                    total_paid += payment.amount
                elif payment.currency_id.name == 'MXN' and currency_name == 'USD' and banorte_rate > 0:
                    total_paid += payment.amount / banorte_rate
                elif payment.currency_id.name == 'USD' and currency_name == 'MXN' and banorte_rate > 0:
                    total_paid += payment.amount * banorte_rate
                else:
                    total_paid += payment.amount

        balance = self.amount_total - total_paid  # en moneda de la orden

        if currency_name == 'USD' and banorte_rate > 0:
            return balance * banorte_rate
        # MXN (o moneda de compañía) se asume ya en pesos
        return balance

    @api.depends(
        'partner_id', 'amount_total',
        'invoice_ids', 'invoice_ids.state',
        'invoice_ids.amount_residual',
        'invoice_ids.payment_state',
        'invoice_ids.line_ids.matched_credit_ids',
    )
    def _compute_customer_credit_balance(self):
        banorte_rate = self._statement_banorte_rate()
        for order in self:
            partner = order.partner_id.commercial_partner_id or order.partner_id
            balance = 0.0
            if partner:
                # Saldo global del cliente = suma del balance (en MXN) de TODAS
                # sus órdenes confirmadas, con la misma lógica del reporte.
                # Si el neto es negativo, hay saldo a favor.
                client_orders = self.env['sale.order'].sudo().search([
                    ('partner_id.commercial_partner_id', '=', partner.id),
                    ('state', 'in', ['sale', 'done']),
                ])
                total_balance_mxn = 0.0
                for o in client_orders:
                    try:
                        total_balance_mxn += o._statement_balance_mxn(banorte_rate)
                    except Exception:
                        continue
                if total_balance_mxn < -0.01:
                    balance = -total_balance_mxn
            order.x_customer_credit_balance = balance
            order.x_has_customer_credit = balance > 0.01

    def _get_related_invoices(self):
        """Retorna las facturas relacionadas a esta orden de venta."""
        self.ensure_one()
        return self.invoice_ids.filtered(
            lambda inv: inv.state == 'posted' and inv.move_type == 'out_invoice'
        )

    def _get_related_payments(self):
        """Retorna los pagos relacionados a las facturas de esta orden."""
        self.ensure_one()
        invoices = self._get_related_invoices()
        payments = self.env['account.payment']
        for inv in invoices:
            for partial in inv._get_reconciled_payments():
                payments |= partial
        return payments

    def action_print_account_statement(self):
        """Genera el estado de cuenta solo para esta orden de venta."""
        self.ensure_one()
        currency_name = (self.currency_id.name or '').upper()
        if currency_name == 'USD':
            report_currency = 'usd'
        elif currency_name == 'MXN':
            report_currency = 'mxn'
        else:
            report_currency = 'mxn'

        wizard = self.env['account.statement.wizard'].create({
            'partner_id': self.partner_id.id,
            'order_ids': [(6, 0, [self.id])],
            'report_currency': report_currency,
            'include_fully_paid': True,
            'include_draft': self.state in ('draft', 'sent'),
        })
        return wizard.action_print_statement()

    # ═══════════════════════════════════════════════════════════════════
    # Devoluciones SOM para Estado de Cuenta
    # ═══════════════════════════════════════════════════════════════════

    def _statement_return_qty_from_doc_line(self, doc_line):
        """Cantidad devuelta real registrada en una línea SOM."""
        return (
            doc_line.qty_returned
            or doc_line.qty_done
            or doc_line.qty_selected
            or 0.0
        )

    def _get_statement_return_documents(self):
        """
        Devuelve devoluciones confirmadas de la orden.

        El módulo sale_delivery_wizard crea documentos `sale.delivery.document`
        con document_type='return'. Solo se consideran devoluciones confirmadas
        y, si tienen picking de devolución, con picking validado.
        """
        self.ensure_one()

        if 'delivery_document_ids' in self._fields:
            docs = self.delivery_document_ids
        else:
            docs = self.env['sale.delivery.document'].search([
                ('sale_order_id', '=', self.id),
            ])

        return docs.filtered(
            lambda d: d.document_type == 'return'
            and d.state == 'confirmed'
            and (
                not d.return_picking_id
                or d.return_picking_id.state == 'done'
            )
        )

    def _get_statement_return_lines_data(self, return_docs):
        """Construye líneas primitivas de devolución para QWeb."""
        self.ensure_one()
        result = []

        for doc in return_docs.sorted(lambda d: (fields.Datetime.to_string(d.delivery_date or d.create_date) if (d.delivery_date or d.create_date) else '', d.id)):
            action_label = ''
            if 'return_action' in doc._fields and doc.return_action:
                action_label = dict(doc._fields['return_action'].selection).get(
                    doc.return_action,
                    doc.return_action,
                )

            reason_name = (
                doc.return_reason_id.name
                if 'return_reason_id' in doc._fields and doc.return_reason_id
                else ''
            )

            doc_date = doc.delivery_date or doc.create_date
            doc_date_str = fields.Datetime.to_string(doc_date) if doc_date else ''

            for line in doc.line_ids.sorted(lambda l: (l.sequence, l.id)):
                qty = self._statement_return_qty_from_doc_line(line)
                if qty <= 0:
                    continue

                product = line.product_id
                origin_remission = ''
                if 'origin_remission_number' in line._fields and line.origin_remission_number:
                    origin_remission = line.origin_remission_number
                elif 'origin_remission_id' in line._fields and line.origin_remission_id:
                    origin_remission = (
                        line.origin_remission_id.remission_number
                        or line.origin_remission_id.name
                        or ''
                    )

                result.append({
                    'return_name': doc.name or '',
                    'return_date': doc_date_str,
                    'return_reason': reason_name,
                    'return_action': action_label,
                    'origin_remission': origin_remission,
                    'picking_name': doc.return_picking_id.name if doc.return_picking_id else '',
                    'product_name': product.display_name if product else '',
                    'lot_name': line.lot_id.name if line.lot_id else '',
                    'qty_returned': qty,
                    'uom': product.uom_id.name if product and product.uom_id else '',
                })

        return result

    def _get_statement_returned_qty_for_sale_line(self, line, return_docs):
        """Cantidad devuelta por línea de venta."""
        qty_from_docs = 0.0

        if return_docs:
            return_lines = return_docs.mapped('line_ids').filtered(
                lambda dl: dl.sale_line_id == line
                and dl.product_id == line.product_id
            )
            for return_line in return_lines:
                qty_from_docs += self._statement_return_qty_from_doc_line(return_line)

        qty_from_line = 0.0
        if 'x_returned_qty' in line._fields:
            qty_from_line = line.x_returned_qty or 0.0

        # max evita perder devoluciones si algún compute almacenado aún no se
        # recalculó y también evita doble conteo cuando ambos orígenes reflejan
        # la misma devolución.
        return max(qty_from_docs, qty_from_line)

    def _get_statement_delivered_net_qty_for_sale_line(self, line, returned_qty):
        """Cantidad entregada neta, preferentemente desde documentos SOM."""
        gross_from_docs = 0.0

        if hasattr(line, '_som_custom_delivery_gross_qty'):
            try:
                gross_from_docs = line._som_custom_delivery_gross_qty() or 0.0
            except Exception as exc:
                _logger.debug(
                    'No se pudo calcular entregado bruto SOM para línea %s: %s',
                    line.id,
                    exc,
                )

        if gross_from_docs > 0:
            return max(gross_from_docs - (returned_qty or 0.0), 0.0)

        if 'x_delivered_net_qty' in line._fields:
            return line.x_delivered_net_qty or 0.0

        return max((line.qty_delivered or 0.0) - (returned_qty or 0.0), 0.0)

    def _get_statement_data(self, banorte_rate=0.0):
        """
        Retorna datos consolidados para el estado de cuenta.
        100% datos primitivos serializables - sin recordsets.
        """
        self.ensure_one()
        currency_name = self.currency_id.name or 'USD'

        material_lines = []
        service_lines = []

        return_docs = self._get_statement_return_documents()
        return_lines = self._get_statement_return_lines_data(return_docs)
        total_returned_qty = sum(
            item.get('qty_returned', 0.0) or 0.0
            for item in return_lines
        )

        for line in self.order_line:
            if line.display_type:
                continue
            if not line.product_id:
                continue

            qty_ordered = line.product_uom_qty or 0.0
            qty_returned = self._get_statement_returned_qty_for_sale_line(
                line,
                return_docs,
            )
            qty_delivered_net = self._get_statement_delivered_net_qty_for_sale_line(
                line,
                qty_returned,
            )
            qty_delivered_gross = qty_delivered_net + qty_returned
            if not qty_returned and line.qty_delivered:
                qty_delivered_gross = line.qty_delivered or 0.0

            qty_pending = max(qty_ordered - qty_delivered_net, 0.0)
            pct_delivered = (
                qty_delivered_net / qty_ordered * 100
            ) if qty_ordered > 0 else 0.0

            line_data = {
                'product_name': line.product_id.display_name or line.name,
                'qty_ordered': qty_ordered,
                'qty_delivered': qty_delivered_net,
                'qty_delivered_net': qty_delivered_net,
                'qty_delivered_gross': qty_delivered_gross,
                'qty_returned': qty_returned,
                'qty_pending': qty_pending,
                'pct_delivered': pct_delivered,
                'pct_delivered_net': pct_delivered,
                'price_unit': line.price_unit,
                'subtotal': line.price_subtotal,
                'tax': line.price_tax,
                'total': line.price_total,
                'currency': currency_name,
                'uom': line.product_uom_id.name if line.product_uom_id else 'm²',
            }

            if currency_name == 'USD' and banorte_rate > 0:
                line_data['price_unit_alt'] = line.price_unit * banorte_rate
                line_data['subtotal_alt'] = line.price_subtotal * banorte_rate
                line_data['total_alt'] = line.price_total * banorte_rate
                line_data['currency_alt'] = 'MXN'
            elif currency_name == 'MXN' and banorte_rate > 0:
                line_data['price_unit_alt'] = line.price_unit / banorte_rate
                line_data['subtotal_alt'] = line.price_subtotal / banorte_rate
                line_data['total_alt'] = line.price_total / banorte_rate
                line_data['currency_alt'] = 'USD'
            else:
                line_data['price_unit_alt'] = 0.0
                line_data['subtotal_alt'] = 0.0
                line_data['total_alt'] = 0.0
                line_data['currency_alt'] = 'N/A'

            if line.product_id.type == 'service':
                service_lines.append(line_data)
            else:
                material_lines.append(line_data)

        # Pagos
        payments_data = []
        total_paid = 0.0
        invoices = self._get_related_invoices()

        for inv in invoices:
            for payment in inv._get_reconciled_payments():
                payments_data.append({
                    'name': payment.name or '',
                    'date': str(payment.date) if payment.date else '',
                    'amount': payment.amount,
                    'currency': payment.currency_id.name,
                })
                if payment.currency_id == self.currency_id:
                    total_paid += payment.amount
                elif payment.currency_id.name == 'MXN' and currency_name == 'USD' and banorte_rate > 0:
                    total_paid += payment.amount / banorte_rate
                elif payment.currency_id.name == 'USD' and currency_name == 'MXN' and banorte_rate > 0:
                    total_paid += payment.amount * banorte_rate
                else:
                    total_paid += payment.amount

        amount_total = self.amount_total
        amount_untaxed = self.amount_untaxed
        amount_tax = self.amount_tax
        balance = amount_total - total_paid

        if currency_name == 'USD' and banorte_rate > 0:
            balance_usd = balance
            balance_mxn = balance * banorte_rate
            total_usd = amount_total
            total_mxn = amount_total * banorte_rate
        elif currency_name == 'MXN' and banorte_rate > 0:
            balance_mxn = balance
            balance_usd = balance / banorte_rate
            total_mxn = amount_total
            total_usd = amount_total / banorte_rate
        else:
            balance_usd = balance if currency_name == 'USD' else 0.0
            balance_mxn = balance if currency_name == 'MXN' else 0.0
            total_usd = amount_total if currency_name == 'USD' else 0.0
            total_mxn = amount_total if currency_name == 'MXN' else 0.0

        return {
            'order_name': self.name,
            'order_date': str(self.date_order.date()) if self.date_order else '',
            'seller_name': self.user_id.name or '',
            'currency': currency_name,
            'material_lines': material_lines,
            'service_lines': service_lines,
            'return_lines': return_lines,
            'return_documents_count': len(return_docs),
            'total_returned_qty': total_returned_qty,
            'payments': payments_data,
            'amount_untaxed': amount_untaxed,
            'amount_tax': amount_tax,
            'amount_total': amount_total,
            'total_paid': total_paid,
            'balance': balance,
            'balance_usd': balance_usd,
            'balance_mxn': balance_mxn,
            'total_usd': total_usd,
            'total_mxn': total_mxn,
        }