"""Entirely synthetic XML. No customer original or authentic signature is used."""
from decimal import Decimal
from uuid import uuid5, NAMESPACE_URL
from lxml import etree as E

COMPANY = 'AAA010101AAA'
OTHER = 'BBB010101BBB'
NS4 = 'http://www.sat.gob.mx/cfd/4'
TFD = 'http://www.sat.gob.mx/TimbreFiscalDigital'


def uid(name):
    return str(uuid5(NAMESPACE_URL, 'urn:synthetic-add-tests:' + name)).upper()


def invoice(name='basic', company=COMPANY, direction='received', version='4.0', kind='I', currency='MXN',
            subtotal='100', total='116', lines=None, taxes=None, date='2026-05-04T12:00:00', exchange=None,
            related=None, unknown=False, carta=False, prefix='cfdi'):
    ns = NS4 if version == '4.0' else 'http://www.sat.gob.mx/cfd/3'
    root = E.Element('{%s}Comprobante' % ns, nsmap={prefix: ns, 'stamp': TFD},
                     Version=version, TipoDeComprobante=kind, Fecha=date, Moneda=currency,
                     SubTotal=subtotal, Total=total, Serie='SINTETICO', Folio=name, MetodoPago='PUE',
                     FormaPago='03', LugarExpedicion='64000')
    if exchange:
        root.set('TipoCambio', exchange)
    issued = direction == 'issued'
    E.SubElement(root, '{%s}Emisor' % ns, Rfc=company if issued else OTHER, Nombre='EMISOR SINTETICO', RegimenFiscal='601')
    E.SubElement(root, '{%s}Receptor' % ns, Rfc=OTHER if issued else company, Nombre='RECEPTOR SINTETICO', UsoCFDI='G03', RegimenFiscalReceptor='601', DomicilioFiscalReceptor='64000')
    if related:
        rel = E.SubElement(root, '{%s}CfdiRelacionados' % ns, TipoRelacion='04')
        E.SubElement(rel, '{%s}CfdiRelacionado' % ns, UUID=related)
    concepts = E.SubElement(root, '{%s}Conceptos' % ns)
    if lines is None:
        lines = [dict(amount=subtotal, taxes=[dict(tax='002', rate='0.160000', amount='16.00')])]
    for index, line in enumerate(lines):
        node = E.SubElement(concepts, '{%s}Concepto' % ns, ClaveProdServ=line.get('code', '78101800'),
                            Cantidad=line.get('quantity', '1'), ClaveUnidad=line.get('unit', 'E48'),
                            Descripcion=line.get('description', 'Servicio sintético %s' % (index + 1)),
                            ValorUnitario=line.get('price', line['amount']), Importe=line['amount'], ObjetoImp=line.get('object', '02'))
        if line.get('discount'):
            node.set('Descuento', line['discount'])
        if line.get('taxes'):
            _taxes(node, ns, line['taxes'], line['amount'])
    if taxes is None:
        taxes = [dict(tax='002', rate='0.160000', amount=str(Decimal(total) - Decimal(subtotal)))]
    if taxes:
        _taxes(root, ns, taxes, subtotal)
    comp = E.SubElement(root, '{%s}Complemento' % ns)
    E.SubElement(comp, '{%s}TimbreFiscalDigital' % TFD, UUID=uid(name), FechaTimbrado=date, Version='1.1', SelloCFD='SINTETICO-NO-VALIDO')
    if unknown:
        E.SubElement(comp, '{urn:synthetic:unknown}Ejemplo', text='=HYPERLINK("https://example.invalid")').text = '<script>alert(1)</script>'
    if carta:
        uri = 'http://www.sat.gob.mx/CartaPorte31'
        cp = E.SubElement(comp, '{%s}CartaPorte' % uri, Version='3.1', IdCCP=uid('cp-' + name))
        locs = E.SubElement(cp, '{%s}Ubicaciones' % uri)
        E.SubElement(locs, '{%s}Ubicacion' % uri, TipoUbicacion='Origen', IDUbicacion='OR000001')
        goods = E.SubElement(cp, '{%s}Mercancias' % uri, PesoBrutoTotal='1000', UnidadPeso='KGM', NumTotalMercancias='1')
        good = E.SubElement(goods, '{%s}Mercancia' % uri, BienesTransp='11111600', Descripcion='Mercancía sintética', Cantidad='1')
        E.SubElement(good, '{%s}DocumentacionAduanera' % uri, NumPedimento='26 16 0000 6001355')
        E.SubElement(goods, '{%s}Autotransporte' % uri, PermSCT='TPAF01', NumPermisoSCT='SINTETICO')
    return E.tostring(root, xml_declaration=True, encoding='UTF-8')


def _taxes(parent, ns, taxes, base):
    node = E.SubElement(parent, '{%s}Impuestos' % ns)
    groups = {}
    for tax in taxes:
        withholding = tax.get('withholding', False)
        name = 'Retenciones' if withholding else 'Traslados'
        if name not in groups:
            groups[name] = E.SubElement(node, '{%s}%s' % (ns, name))
        attrs = dict(Impuesto=tax.get('tax', '002'), Base=tax.get('base', base), TipoFactor=tax.get('factor', 'Tasa'))
        if 'rate' in tax:
            attrs['TasaOCuota'] = tax['rate']
        if 'amount' in tax:
            attrs['Importe'] = tax['amount']
        E.SubElement(groups[name], '{%s}%s' % (ns, 'Retencion' if withholding else 'Traslado'), **attrs)


def payment(name='payment', version='2.0', currency='MXN', amount='17777.00', date='2025-07-04T12:00:00',
            targets=None, count=1, direction='received'):
    raw = invoice(name, kind='P', version='4.0' if version == '2.0' else '3.3', currency='XXX', subtotal='0', total='0',
                  date='2025-07-14T12:00:00', direction=direction, lines=[dict(amount='0', description='Pago', object='01')], taxes=[])
    root = E.fromstring(raw); ns = E.QName(root).namespace
    root.attrib.pop('MetodoPago', None); root.attrib.pop('FormaPago', None)
    comp = root.find('{%s}Complemento' % ns)
    pn = 'http://www.sat.gob.mx/Pagos20' if version == '2.0' else 'http://www.sat.gob.mx/Pagos'
    pagos = E.SubElement(comp, '{%s}Pagos' % pn, Version=version)
    if version == '2.0':
        E.SubElement(pagos, '{%s}Totales' % pn, MontoTotalPagos=str(Decimal(amount) * count))
    for _ in range(count):
        pay = E.SubElement(pagos, '{%s}Pago' % pn, FechaPago=date, MonedaP=currency, Monto=amount, FormaDePagoP='03')
        for target in targets or [uid('absent-invoice')]:
            E.SubElement(pay, '{%s}DoctoRelacionado' % pn, IdDocumento=target, MonedaDR=currency,
                         EquivalenciaDR='1', NumParcialidad='1', ImpSaldoAnt=amount, ImpPagado=amount, ImpSaldoInsoluto='0.00', ObjetoImpDR='02')
    return E.tostring(root, encoding='UTF-8', xml_declaration=True)


def references():
    """Eight synthetic equivalents of the numeric acceptance examples."""
    vat = lambda amount, **kw: dict(tax='002', rate='0.160000', amount=amount, **kw)
    iva_ret = lambda amount, **kw: dict(tax='002', rate='0.040000', amount=amount, withholding=True, **kw)
    special = [vat('888.20'), iva_ret('222.05'), dict(tax='001', rate='0.012500', amount='69.39', withholding=True)]
    freight = [vat('4880.00'), iva_ret('1220.00')]
    return [
        invoice('almacenaje', subtotal='1922.00', total='2229.52', lines=[dict(amount='1922.00', quantity='2', price='961.00', taxes=[vat('307.52')])], taxes=[vat('307.52')]),
        invoice('honorarios', subtotal='5551.24', total='6148.00', lines=[dict(amount='5551.24', taxes=special)], taxes=special),
        invoice('servicios', subtotal='3750.00', total='4350.00', date='2026-02-09T12:00:00', taxes=[vat('600.00')], lines=[dict(amount='3750.00', taxes=[vat('600.00')])]),
        invoice('flete-cp', subtotal='30500.00', total='34160.00', taxes=freight, carta=True,
                lines=[dict(amount='30500.00', taxes=freight, description='Flete sintético; contenedor TGHU1941404; referencia extraída 6001355')]),
        invoice('precision', subtotal='4422.41', total='5130.00', taxes=[vat('707.59')], lines=[dict(amount='4422.413000', price='4422.413000', taxes=[vat('707.586080')])]),
        invoice('usd', currency='USD', exchange='17.2688', subtotal='4520.95', total='4542.55',
                lines=[dict(amount=a, taxes=[dict(tax='002', rate='0.000000', amount='0.00')]) for a in ('1000','1000','1000','1385.95')] +
                      [dict(amount='135.00', taxes=[vat('21.60')])], taxes=[dict(tax='002', rate='0.000000', amount='0.00', base='4385.95'), vat('21.60', base='135.00')]),
        invoice('flete-lavado', subtotal='119750.00', total='134160.00', carta=True,
                lines=[dict(amount='118750.00', taxes=[vat('19000.00'), iva_ret('4750.00')]), dict(amount='1000.00', taxes=[vat('160.00')])], taxes=[vat('19160.00'), iva_ret('4750.00')]),
        payment(),
    ]
