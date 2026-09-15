"""Offline CFDI reader. No schemas, network, Odoo or accounting side effects.

Decimal strings in the returned payload are authoritative; ORM floats are only
indexed projections for Odoo's grouped reports. Fiscal datetimes remain local.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import re
import time
from lxml import etree

PARSER_VERSION = '1.0.0'
CFDI = {'http://www.sat.gob.mx/cfd/4': '4.0', 'http://www.sat.gob.mx/cfd/3': '3.3'}
TFD = 'http://www.sat.gob.mx/TimbreFiscalDigital'
PAGOS = {'http://www.sat.gob.mx/Pagos20': '2.0', 'http://www.sat.gob.mx/Pagos': '1.0'}
LOCAL = 'http://www.sat.gob.mx/implocal'
UUID = re.compile(r'^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$')
DEFAULT_LIMITS = dict(xml_bytes=5 * 1024 * 1024, depth=48, nodes=60000, seconds=5)


class Rejected(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def rfc(value):
    return (value or '').strip().upper()


def number(value, default='0'):
    text = default if value in (None, '') else str(value)
    if len(text) > 40:
        raise Rejected('structure', 'Número fuera de límites.')
    try:
        result = Decimal(text)
    except InvalidOperation:
        raise Rejected('structure', 'Número decimal inválido.') from None
    if not result.is_finite() or abs(result) > Decimal('1e20') or result.as_tuple().exponent < -12:
        raise Rejected('structure', 'Precisión o magnitud numérica fuera de límites.')
    return result


def fiscal_date(value):
    try:
        datetime.fromisoformat(value)
        return value[:10]
    except (ValueError, TypeError):
        raise Rejected('structure', 'Fecha fiscal inválida.') from None


def parse(raw, company_rfc, direction, limits=None):
    limits = dict(DEFAULT_LIMITS, **(limits or {}))
    started = time.monotonic()
    if direction not in ('issued', 'received'):
        raise Rejected('direction', 'Dirección inválida.')
    if not raw or len(raw) > limits['xml_bytes']:
        raise Rejected('size', 'XML vacío o mayor al límite.')
    # Reject declarations before parsing, including UTF-16/32 documents.
    probe = raw.replace(b'\x00', b'').upper()
    if b'<!DOCTYPE' in probe or b'<!ENTITY' in probe:
        raise Rejected('structure', 'DTD y entidades no permitidas.')
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False,
                             huge_tree=False, recover=False, remove_comments=False)
    try:
        root = etree.fromstring(raw, parser)
    except (etree.XMLSyntaxError, ValueError):
        raise Rejected('structure', 'XML mal formado.') from None
    stack, count = [(root, 1)], 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if depth > limits['depth'] or count > limits['nodes'] or time.monotonic() - started > limits['seconds']:
            raise Rejected('limits', 'XML excede profundidad, nodos o tiempo permitido.')
        stack.extend((child, depth + 1) for child in node if isinstance(child.tag, str))
    q = etree.QName(root)
    ns = q.namespace
    if q.localname != 'Comprobante' or ns not in CFDI:
        raise Rejected('unsupported', 'Esquema no compatible; retenciones e información de pagos no soportado.')
    a = dict(root.attrib)
    if a.get('Version') != CFDI[ns]:
        raise Rejected('unsupported', 'Solo se admiten CFDI 3.3 y 4.0.')
    kind = a.get('TipoDeComprobante')
    if kind == 'N' or any('nomina' in str(n.tag).lower() for n in root.iter()):
        raise Rejected('payroll', 'Nómina no admitida en ADD.')
    if kind not in ('I', 'E', 'P', 'T'):
        raise Rejected('unsupported', 'Tipo de comprobante no compatible.')
    def child(name):
        return root.find('{%s}%s' % (ns, name))
    emitter, receiver = child('Emisor'), child('Receptor')
    if emitter is None or receiver is None:
        raise Rejected('structure', 'Falta emisor o receptor.')
    emitter, receiver = dict(emitter.attrib), dict(receiver.attrib)
    own = rfc(company_rfc)
    if not own:
        raise Rejected('company', 'La compañía no tiene RFC configurado.')
    issued, received = rfc(emitter.get('Rfc')) == own, rfc(receiver.get('Rfc')) == own
    if not (issued or received):
        raise Rejected('company', 'RFC ajeno a la compañía del lote.')
    if not (issued if direction == 'issued' else received):
        raise Rejected('direction', 'Dirección incorrecta. Vuelva a cargar mediante la acción contraria.')
    stamps = root.findall('.//{%s}TimbreFiscalDigital' % TFD)
    if len(stamps) != 1 or not UUID.fullmatch(stamps[0].get('UUID', '')):
        raise Rejected('stamp', 'Se requiere un único timbre con UUID válido.')
    stamp = dict(stamps[0].attrib)
    # Do not store signatures in parsed metadata. Original bytes remain intact.
    for key in ('Sello', 'Certificado'):
        a.pop(key, None)
    stamp = {k: v for k, v in stamp.items() if k not in ('SelloCFD', 'SelloSAT')}
    warnings = ['Emisor y receptor son la misma compañía; una sola perspectiva documental.'] if issued and received else []
    concepts, taxes, payments, relations, complements = [], [], [], [], []

    def read_taxes(parent, namespace, level, index=0):
        result = []
        if parent is None:
            return result
        for node in parent.iter():
            if not isinstance(node.tag, str):
                continue
            name = etree.QName(node).localname
            if etree.QName(node).namespace != namespace or name not in ('Traslado', 'Retencion', 'TrasladoDR', 'RetencionDR', 'TrasladoP', 'RetencionP'):
                continue
            suffix = 'DR' if name.endswith('DR') else ('P' if name.endswith('P') else '')
            item = dict(level=level, concept_index=index, kind='withholding' if name.startswith('Retencion') else 'transfer',
                        tax=node.get('Impuesto' + suffix, ''), factor=node.get('TipoFactor' + suffix, ''),
                        base=node.get('Base' + suffix), rate=node.get('TasaOCuota' + suffix), amount=node.get('Importe' + suffix), raw=dict(node.attrib))
            for key in ('base', 'rate', 'amount'):
                if item[key] is not None:
                    number(item[key])
            result.append(item)
        return result

    for index, c in enumerate(root.findall('{%s}Conceptos/{%s}Concepto' % (ns, ns)), 1):
        values = dict(c.attrib)
        for key in ('Cantidad', 'ValorUnitario', 'Importe', 'Descuento'):
            number(values.get(key))
        line_taxes = read_taxes(c.find('{%s}Impuestos' % ns), ns, 'concept', index)
        concepts.append(dict(index=index, attributes=values, taxes=line_taxes, commercial=kind != 'P'))
        taxes.extend(line_taxes)
    taxes.extend(read_taxes(child('Impuestos'), ns, 'global'))
    for rel in root.findall('{%s}CfdiRelacionados' % ns):
        for target in rel.findall('{%s}CfdiRelacionado' % ns):
            uuid = target.get('UUID', '')
            if not UUID.fullmatch(uuid):
                raise Rejected('structure', 'UUID relacionado inválido.')
            relations.append(dict(kind=rel.get('TipoRelacion', ''), uuid=uuid.upper(), original_uuid=uuid))
    partial = False
    payment_totals = {}
    for group in root.findall('{%s}Complemento' % ns) + root.findall('{%s}Addenda' % ns):
        for node in group:
            if not isinstance(node.tag, str):
                continue
            uri, name = etree.QName(node).namespace, etree.QName(node).localname
            if uri == TFD:
                continue
            specialized = uri in PAGOS or uri == LOCAL or (uri or '').startswith('http://www.sat.gob.mx/CartaPorte')
            complements.append(dict(namespace=uri, name=name, version=node.get('Version', node.get('version', '')),
                                    specialized=specialized, tree=tree(node)))
            if uri in PAGOS:
                if node.get('Version') != PAGOS[uri]:
                    raise Rejected('unsupported', 'Versión del complemento de pagos no compatible.')
                totals = node.find('{%s}Totales' % uri)
                if totals is not None:
                    payment_totals = dict(totals.attrib)
                    for val in payment_totals.values():
                        number(val)
                for p in node.findall('{%s}Pago' % uri):
                    attrs = dict(p.attrib)
                    fiscal_date(attrs.get('FechaPago'))
                    number(attrs.get('Monto'))
                    apps = []
                    for dr in p.findall('{%s}DoctoRelacionado' % uri):
                        da = dict(dr.attrib)
                        if not UUID.fullmatch(da.get('IdDocumento', '')):
                            raise Rejected('structure', 'UUID de aplicación de pago inválido.')
                        for key in ('ImpSaldoAnt', 'ImpPagado', 'ImpSaldoInsoluto', 'EquivalenciaDR', 'TipoCambioDR'):
                            number(da.get(key))
                        apps.append(dict(attributes=da, taxes=read_taxes(dr, uri, 'application')))
                    payments.append(dict(index=len(payments) + 1, attributes=attrs, applications=apps,
                                         taxes=read_taxes(p.find('{%s}ImpuestosP' % uri), uri, 'payment')))
            elif uri == LOCAL:
                for t in node:
                    name = etree.QName(t).localname
                    withholding = name == 'RetencionesLocales'
                    taxes.append(dict(level='local', concept_index=0, kind='withholding' if withholding else 'transfer',
                                      tax=t.get('ImpLocRetenido' if withholding else 'ImpLocTrasladado', ''),
                                      factor='Tasa', base=None, rate=t.get('TasadeRetencion' if withholding else 'TasadeTraslado'),
                                      amount=t.get('Importe'), raw=dict(t.attrib)))
            elif not specialized:
                partial = True
                warnings.append('Complemento conservado; sin desglose especializado: %s.' % name)
    if kind == 'P' and not payments:
        warnings.append('CFDI P sin nodos Pago compatibles.')
        partial = True
    if kind != 'P' and not concepts:
        raise Rejected('structure', 'Comprobante sin conceptos.')
    for key in ('SubTotal', 'Descuento', 'Total', 'TipoCambio'):
        number(a.get(key))
    # Global taxes are canonical for invoice arithmetic; concept taxes never add twice.
    canonical = [t for t in taxes if t['level'] in ('global', 'local')]
    expected = number(a.get('SubTotal')) - number(a.get('Descuento'))
    expected += sum((number(t['amount']) * (-1 if t['kind'] == 'withholding' else 1) for t in canonical), Decimal(0))
    difference = number(a.get('Total')) - expected
    # Half a minor unit per separately rounded component, minimum two cents.
    tolerance = max(Decimal('.02'), Decimal('.005') * (len(canonical) + 2))
    if abs(difference) > tolerance and kind not in ('P', 'T'):
        warnings.append('Diferencia entre total y componentes: %s %s.' % (difference, a.get('Moneda', '')))
    line_difference = sum((number(c['attributes'].get('Importe')) for c in concepts), Decimal(0)) - number(a.get('SubTotal'))
    if abs(line_difference) > max(Decimal('.02'), Decimal('.005') * len(concepts)):
        warnings.append('Diferencia entre conceptos y subtotal: %s.' % line_difference)
    if not any(t['level'] == 'global' for t in taxes) and any(t['level'] == 'concept' for t in taxes):
        partial = True
    return dict(parser_version=PARSER_VERSION, sha256=hashlib.sha256(raw).hexdigest(), header=a, emitter=emitter,
                receiver=receiver, stamp=stamp, uuid=stamp['UUID'].upper(), issued=issued, received=received,
                fiscal_date=fiscal_date(a.get('Fecha')), concepts=concepts, taxes=taxes, payments=payments,
                payment_totals_mxn=payment_totals, relations=relations, complements=complements,
                warnings=warnings, difference=str(difference), tolerance=str(tolerance),
                consistency='partial' if partial else ('warning' if warnings else 'ok'))


def tree(node):
    """JSON text tree, never HTML; namespace identity is retained."""
    return dict(tag=node.tag, attributes=dict(node.attrib), text=node.text or '',
                children=[tree(c) for c in node if isinstance(c.tag, str)])
