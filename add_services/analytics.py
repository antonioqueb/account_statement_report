"""Pure decimal helpers shared by reference tests and analytical definitions."""
from collections import defaultdict
from decimal import Decimal
from .cfdi import number


def summarize(payloads):
    documents, bases, payments = defaultdict(Decimal), defaultdict(Decimal), defaultdict(Decimal)
    count = commercial = 0
    seen = set()
    for p in payloads:
        if p['uuid'] in seen:
            continue
        seen.add(p['uuid'])
        count += 1
        h = p['header']
        if h['TipoDeComprobante'] in ('I', 'E'):
            key = (h['Moneda'], h['TipoDeComprobante'])
            documents[key] += number(h['Total'])
            bases[key] += number(h['SubTotal']) - number(h.get('Descuento'))
        commercial += sum(c['commercial'] for c in p['concepts'])
        for pay in p['payments']:
            a = pay['attributes']
            payments[a['MonedaP']] += number(a['Monto'])
    return dict(count=count, commercial_concepts=commercial, totals=dict(documents), bases=dict(bases), payments=dict(payments))


def concentration(rows, total, limit=10):
    total = number(total)
    cumulative = Decimal(0)
    result = []
    for row in rows[:limit]:
        value = number(row['value'])
        cumulative += value
        result.append(dict(row, percent=float(value / total * 100) if total else 0,
                           cumulative=float(cumulative / total * 100) if total else 0))
    return result
