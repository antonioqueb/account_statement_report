# -*- coding: utf-8 -*-
from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _get_related_invoices(self):
        """Retorna las facturas relacionadas a esta orden de venta"""
        self.ensure_one()
        return self.invoice_ids.filtered(lambda inv: inv.state == 'posted' and inv.move_type == 'out_invoice')

    def _get_related_payments(self):
        """Retorna los pagos relacionados a las facturas de esta orden"""
        self.ensure_one()
        invoices = self._get_related_invoices()
        payments = self.env['account.payment']
        for inv in invoices:
            for partial in inv._get_reconciled_payments():
                payments |= partial
        return payments

    def _get_statement_data(self, banorte_rate=0.0):
        """
        Retorna datos consolidados para el estado de cuenta.
        100% datos primitivos serializables - sin recordsets.
        """
        self.ensure_one()
        currency_name = self.currency_id.name or 'USD'
        
        material_lines = []
        service_lines = []
        
        for line in self.order_line:
            if line.display_type:
                continue
            if not line.product_id:
                continue
            
            qty_delivered = line.qty_delivered or 0.0
            qty_ordered = line.product_uom_qty or 0.0
            qty_pending = max(qty_ordered - qty_delivered, 0.0)
            pct_delivered = (qty_delivered / qty_ordered * 100) if qty_ordered > 0 else 0.0
            
            line_data = {
                'product_name': line.product_id.display_name or line.name,
                'qty_ordered': qty_ordered,
                'qty_delivered': qty_delivered,
                'qty_pending': qty_pending,
                'pct_delivered': pct_delivered,
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