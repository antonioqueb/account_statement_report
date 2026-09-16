# -*- coding: utf-8 -*-
"""Pagos y aplicaciones ADD: la moneda guardada era la del documento (XXX)
porque el campo heredaba el related de som.add.child. Se restaura desde el
nodo original conservado en JSON (MonedaP / MonedaDR)."""


def migrate(cr, version):
    cr.execute("""
        UPDATE som_add_payment
           SET currency = original->>'MonedaP'
         WHERE original ? 'MonedaP'
           AND (currency IS NULL OR currency = 'XXX' OR currency <> original->>'MonedaP')
    """)
    payments = cr.rowcount
    cr.execute("""
        UPDATE som_add_application
           SET currency = original->>'MonedaDR'
         WHERE original ? 'MonedaDR'
           AND (currency IS NULL OR currency = 'XXX' OR currency <> original->>'MonedaDR')
    """)
    cr.execute("""
        UPDATE som_add_tax t
           SET currency = p.currency
          FROM som_add_payment p
         WHERE t.payment_id = p.id AND t.application_id IS NULL AND t.currency IS DISTINCT FROM p.currency
    """)
    cr.execute("""
        UPDATE som_add_tax t
           SET currency = a.currency
          FROM som_add_application a
         WHERE t.application_id = a.id AND a.currency IS NOT NULL AND t.currency IS DISTINCT FROM a.currency
    """)
    print('[ADD MIGRATE] pagos con moneda restaurada: %s' % payments)
