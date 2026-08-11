# -*- coding: utf-8 -*-
from odoo import models, fields


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    # Pedimento(s) de los lotes vendidos en la línea: viaja desde la línea
    # de venta (lot_ids → x_pedimento). Varias placas de pedimentos
    # distintos se listan separados por coma.
    x_pedimento = fields.Char(
        string='Pedimento',
        compute='_compute_x_pedimento',
    )

    def _compute_x_pedimento(self):
        for line in self:
            peds = []
            for sol in line.sale_line_ids:
                for lot in getattr(sol, 'lot_ids', []):
                    ped = getattr(lot, 'x_pedimento', False)
                    if ped and ped not in peds:
                        peds.append(ped)
            line.x_pedimento = ', '.join(peds)
