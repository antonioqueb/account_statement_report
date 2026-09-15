import base64
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from ..add_services import cfdi


class AddDocument(models.Model):
    _name = 'som.add.document'
    _description = 'Documento fiscal ADD'
    _inherit = 'som.add.security'
    _order = 'fiscal_date desc, id desc'
    _rec_name = 'uuid'

    company_id = fields.Many2one('res.company', required=True, index=True)
    batch_id = fields.Many2one('som.add.batch', required=True, index=True, ondelete='restrict')
    uuid = fields.Char(required=True, index=True)
    original_uuid = fields.Char()
    filename = fields.Char()
    sha256 = fields.Char(required=True)
    vault_id = fields.Integer(required=True, string='Referencia de almacenamiento privado')
    issued = fields.Boolean(index=True)
    received = fields.Boolean(index=True)
    kind = fields.Selection([('I', 'I · Factura'), ('E', 'E · Ajuste'), ('P', 'P · Pago'), ('T', 'T · Traslado')], index=True)
    version = fields.Char()
    series = fields.Char()
    folio = fields.Char()
    fiscal_date = fields.Date(index=True, string='Fecha fiscal local')
    fiscal_datetime = fields.Char(string='Fecha/hora fiscal original')
    stamp_date = fields.Date(index=True)
    stamp_datetime = fields.Char()
    emitter_rfc = fields.Char(index=True)
    receiver_rfc = fields.Char(index=True)
    emitter_name = fields.Char()
    receiver_name = fields.Char()
    currency = fields.Char(index=True)
    exchange_rate = fields.Float(digits=(24, 12))
    subtotal = fields.Float(digits=(24, 6), aggregator='sum')
    discount = fields.Float(digits=(24, 6), aggregator='sum')
    base = fields.Float(digits=(24, 6), aggregator='sum')
    total = fields.Float(digits=(24, 6), aggregator='sum')
    vat = fields.Float(digits=(24, 6), aggregator='sum')
    withheld = fields.Float(digits=(24, 6), aggregator='sum')
    method = fields.Char(index=True)
    payment_form = fields.Char()
    cfdi_use = fields.Char()
    complements = fields.Char()
    has_carta_porte = fields.Boolean(index=True)
    consistency = fields.Selection([('ok', 'Consistente'), ('warning', 'Con alertas'), ('partial', 'Validación parcial')], index=True)
    sat_state = fields.Selection([('unknown', 'No consultado'), ('valid', 'Vigencia confirmada'), ('cancelled', 'Cancelado confirmado'),
                                  ('not_found', 'No encontrado'), ('error', 'Error de consulta')], default='unknown', required=True, index=True)
    sat_checked_at = fields.Datetime()
    sat_source = fields.Char()
    sat_error = fields.Char()
    parser_version = fields.Char()
    parsed = fields.Json()
    alerts = fields.Text()
    active = fields.Boolean(default=True)
    archive_reason = fields.Text()
    notes = fields.Text(string='Notas administrativas')
    reference = fields.Char(string='Referencia administrativa')
    classification = fields.Char(string='Categoría administrativa', index=True)
    concept_ids = fields.One2many('som.add.concept', 'document_id')
    tax_ids = fields.One2many('som.add.tax', 'document_id')
    payment_ids = fields.One2many('som.add.payment', 'document_id')
    relation_ids = fields.One2many('som.add.relation', 'document_id')
    _uuid_company_unique = models.Constraint('UNIQUE(company_id, uuid)', 'UUID ya incorporado a esta compañía.')

    @api.model
    def _values(self, parsed):
        h, e, r, stamp = (parsed[k] for k in ('header', 'emitter', 'receiver', 'stamp'))
        taxes = [t for t in parsed['taxes'] if t['level'] in ('global', 'local')]
        return dict(uuid=parsed['uuid'], original_uuid=stamp['UUID'], sha256=parsed['sha256'],
                    issued=parsed['issued'], received=parsed['received'], kind=h['TipoDeComprobante'], version=h['Version'],
                    series=h.get('Serie'), folio=h.get('Folio'), fiscal_date=parsed['fiscal_date'], fiscal_datetime=h['Fecha'],
                    stamp_date=cfdi.fiscal_date(stamp['FechaTimbrado']) if stamp.get('FechaTimbrado') else False,
                    stamp_datetime=stamp.get('FechaTimbrado'), emitter_rfc=cfdi.rfc(e.get('Rfc')), receiver_rfc=cfdi.rfc(r.get('Rfc')),
                    emitter_name=e.get('Nombre'), receiver_name=r.get('Nombre'), currency=h.get('Moneda'),
                    exchange_rate=float(cfdi.number(h.get('TipoCambio'))), subtotal=float(cfdi.number(h.get('SubTotal'))),
                    discount=float(cfdi.number(h.get('Descuento'))), total=float(cfdi.number(h.get('Total'))),
                    base=float(cfdi.number(h.get('SubTotal')) - cfdi.number(h.get('Descuento'))),
                    vat=float(sum(cfdi.number(t['amount']) for t in taxes if t['tax'] == '002' and t['kind'] == 'transfer')),
                    withheld=float(sum(cfdi.number(t['amount']) for t in taxes if t['kind'] == 'withholding')),
                    method=h.get('MetodoPago'), payment_form=h.get('FormaPago'), cfdi_use=r.get('UsoCFDI'),
                    complements=', '.join(c['name'] for c in parsed['complements']),
                    has_carta_porte=any(c['name'] == 'CartaPorte' for c in parsed['complements']),
                    consistency=parsed['consistency'], parsed=parsed, alerts='\n'.join(parsed['warnings']), parser_version=cfdi.PARSER_VERSION)

    def _raw(self):
        self.ensure_one()
        self._guard(company=self.company_id)
        self.check_access('read')
        # The only read elevation: a vault ID taken from an authorized document.
        return base64.b64decode(self.env['som.add.vault'].sudo().browse(self.vault_id).data)

    def detail(self):
        self.ensure_one()
        self._guard(company=self.company_id)
        self.check_access('read')
        data = self.read(['uuid', 'company_id', 'batch_id', 'filename', 'sha256', 'parser_version', 'create_date',
                          'create_uid', 'notes', 'reference', 'classification', 'sat_state', 'sat_checked_at', 'sat_source',
                          'sat_error', 'alerts', 'parsed', 'active', 'archive_reason'])[0]
        # Unicode for display only. Download always returns the exact original bytes.
        raw = self._raw()
        root = cfdi.etree.fromstring(raw, cfdi.etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))
        encoding = root.getroottree().docinfo.encoding or 'UTF-8'
        data['xml'] = raw.decode(encoding, errors='replace')
        data['relations'] = self.relation_ids.read(['kind', 'target_uuid', 'target_id'])
        data['applications'] = self.env['som.add.application'].search_read([('document_id', '=', self.id)],
                                 ['target_uuid', 'target_id', 'currency', 'paid', 'balance', 'partiality'])
        return data

    def write(self, vals):
        if self._is_internal():
            return super().write(vals)
        if set(vals) - {'notes', 'reference', 'classification'}:
            raise AccessError('El XML original y los datos fiscales son inmutables por RPC.')
        self.check_access('write')
        for doc in self:
            doc._guard('operator', doc.company_id)
        result = super(AddDocument, self._internal()).write(vals)
        for doc in self:
            self.env['som.add.audit']._log(doc.company_id, 'classify', doc.uuid)
        return result

    def archive_document(self, reason):
        self.check_access('write')
        if not isinstance(reason, str) or not reason.strip():
            raise UserError('Indique un motivo de archivo.')
        for doc in self:
            doc._guard('admin', doc.company_id)
            doc._internal().write({'active': False, 'archive_reason': reason.strip()[:2000]})
            self.env['som.add.audit']._log(doc.company_id, 'archive', doc.uuid)
        return True

    def reprocess(self):
        self.check_access('write')
        for doc in self:
            doc._guard('operator', doc.company_id)
            parsed = cfdi.parse(doc._raw(), doc.company_id.vat, 'issued' if doc.issued else 'received')
            doc._internal().write(doc._values(parsed))
            doc._persist_children(parsed, replace=True)
            self.env['som.add.audit']._log(doc.company_id, 'reprocess', doc.uuid)
        return True

    def _record_sat_result(self, state, source, checked_at, error=None):
        """Private extension hook for a future authorized, real SAT provider."""
        self.ensure_one()
        self._guard('operator', self.company_id)
        if state not in ('valid', 'cancelled', 'not_found', 'error') or not source or not checked_at:
            raise UserError('Resultado SAT sin evidencia de proveedor y fecha.')
        vals = {'sat_checked_at': checked_at, 'sat_source': source, 'sat_error': error or False}
        # A failed refresh does not erase the last confirmed status.
        if state != 'error' or self.sat_state in ('unknown', 'error'):
            vals['sat_state'] = state
        self._internal().write(vals)
        self.env['som.add.audit']._log(self.company_id, 'sat', self.uuid, result=state)

    def _persist_children(self, parsed, replace=False):
        self.ensure_one()
        if replace:
            for name in ('som.add.tax', 'som.add.application', 'som.add.payment', 'som.add.relation', 'som.add.concept'):
                self.env[name].search([('document_id', '=', self.id)])._internal().unlink()
        common = dict(document_id=self.id)
        line_ids = {}
        for line in parsed['concepts']:
            a = line['attributes']
            record = self.env['som.add.concept']._internal().create(dict(common, sequence=line['index'],
                     description=a.get('Descripcion'), identification=a.get('NoIdentificacion'), sat_code=a.get('ClaveProdServ'),
                     unit=a.get('Unidad'), unit_code=a.get('ClaveUnidad'), quantity=float(cfdi.number(a.get('Cantidad'))),
                     unit_price=float(cfdi.number(a.get('ValorUnitario'))), amount=float(cfdi.number(a.get('Importe'))),
                     discount=float(cfdi.number(a.get('Descuento'))), tax_object=a.get('ObjetoImp'),
                     commercial=line['commercial'], original=a))
            line_ids[line['index']] = record.id
        def tax_values(tax, **extra):
            return dict(common, level=tax['level'], kind=tax['kind'], tax=tax['tax'], factor=tax['factor'],
                        rate=format(cfdi.number(tax['rate']).normalize(), 'f') if tax['rate'] is not None else False,
                        base=float(cfdi.number(tax['base'])), amount=float(cfdi.number(tax['amount'])),
                        original=tax['raw'], **extra)
        taxes = [tax_values(t, concept_id=line_ids.get(t['concept_index'])) for t in parsed['taxes']]
        for pay in parsed['payments']:
            a = pay['attributes']
            p = self.env['som.add.payment']._internal().create(dict(common, sequence=pay['index'],
                 payment_date=cfdi.fiscal_date(a['FechaPago']), fiscal_datetime=a['FechaPago'], currency=a.get('MonedaP'),
                 exchange_rate=float(cfdi.number(a.get('TipoCambioP'))), amount=float(cfdi.number(a.get('Monto'))),
                 form=a.get('FormaDePagoP'), operation=a.get('NumOperacion'), original=a))
            taxes.extend(tax_values(t, payment_id=p.id) for t in pay['taxes'])
            for app in pay['applications']:
                a = app['attributes']
                record = self.env['som.add.application']._internal().create(dict(common, payment_id=p.id,
                    target_uuid=a['IdDocumento'].upper(), currency=a.get('MonedaDR'), partiality=a.get('NumParcialidad'),
                    previous=float(cfdi.number(a.get('ImpSaldoAnt'))), paid=float(cfdi.number(a.get('ImpPagado'))),
                    balance=float(cfdi.number(a.get('ImpSaldoInsoluto'))), equivalence=a.get('EquivalenciaDR', a.get('TipoCambioDR')),
                    tax_object=a.get('ObjetoImpDR'), original=a))
                taxes.extend(tax_values(t, payment_id=p.id, application_id=record.id) for t in app['taxes'])
        if taxes:
            self.env['som.add.tax']._internal().create(taxes)
        if parsed['relations']:
            self.env['som.add.relation']._internal().create([dict(common, kind=r['kind'], target_uuid=r['uuid']) for r in parsed['relations']])
        self._resolve_relations()

    def _resolve_relations(self):
        """Resolve new outgoing references and existing incoming references, one company."""
        for doc in self:
            for model in ('som.add.relation', 'som.add.application'):
                refs = self.env[model].search([('company_id', '=', doc.company_id.id), '|',
                                               ('document_id', '=', doc.id), ('target_uuid', '=', doc.uuid)])
                targets = self.with_context(active_test=False).search([('company_id', '=', doc.company_id.id), ('uuid', 'in', refs.mapped('target_uuid'))])
                by_uuid = {t.uuid: t.id for t in targets}
                for ref in refs:
                    ref._internal().write({'target_id': by_uuid.get(ref.target_uuid, False)})


class AddChild(models.AbstractModel):
    _name = 'som.add.child'
    _description = 'Detalle fiscal ADD'
    _inherit = 'som.add.security'

    document_id = fields.Many2one('som.add.document', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='document_id.company_id', store=True, index=True)
    currency = fields.Char(related='document_id.currency', store=True)
    fiscal_date = fields.Date(related='document_id.fiscal_date', store=True, index=True)
    original = fields.Json()


class AddConcept(models.Model):
    _name = 'som.add.concept'
    _description = 'Concepto CFDI'
    _inherit = 'som.add.child'
    _order = 'document_id, sequence'
    _rec_name = 'description'
    sequence = fields.Integer()
    description = fields.Text()
    identification = fields.Char()
    sat_code = fields.Char(index=True)
    unit = fields.Char()
    unit_code = fields.Char()
    quantity = fields.Float(digits=(24, 6), aggregator=None)
    unit_price = fields.Float(digits=(24, 6), aggregator=None)
    amount = fields.Float(digits=(24, 6))
    discount = fields.Float(digits=(24, 6))
    tax_object = fields.Char()
    commercial = fields.Boolean(index=True)


class AddTax(models.Model):
    _name = 'som.add.tax'
    _description = 'Impuesto documentado'
    _inherit = 'som.add.child'
    concept_id = fields.Many2one('som.add.concept', ondelete='cascade', index=True)
    payment_id = fields.Many2one('som.add.payment', ondelete='cascade')
    application_id = fields.Many2one('som.add.application', ondelete='cascade')
    currency = fields.Char(related=None, compute='_compute_currency', store=True)
    level = fields.Selection([(k, v) for k, v in [('global', 'Global'), ('concept', 'Concepto'), ('local', 'Local'), ('payment', 'Pago'), ('application', 'Aplicación')]])
    kind = fields.Selection([('transfer', 'Traslado'), ('withholding', 'Retención')])
    tax = fields.Char(index=True)
    factor = fields.Char()
    rate = fields.Char()
    base = fields.Float(digits=(24, 6))
    amount = fields.Float(digits=(24, 6))

    @api.depends('application_id.currency', 'payment_id.currency', 'document_id.currency')
    def _compute_currency(self):
        for tax in self:
            tax.currency = tax.application_id.currency or tax.payment_id.currency or tax.document_id.currency


class AddPayment(models.Model):
    _name = 'som.add.payment'
    _description = 'Pago documentado (no bancario)'
    _inherit = 'som.add.child'
    sequence = fields.Integer()
    payment_date = fields.Date(index=True)
    fiscal_datetime = fields.Char()
    currency = fields.Char(index=True)
    exchange_rate = fields.Float(digits=(24, 12))
    amount = fields.Float(digits=(24, 6))
    form = fields.Char()
    operation = fields.Char()
    application_ids = fields.One2many('som.add.application', 'payment_id')


class AddApplication(models.Model):
    _name = 'som.add.application'
    _description = 'Aplicación de pago y saldo reportado'
    _inherit = 'som.add.child'
    payment_id = fields.Many2one('som.add.payment', required=True, ondelete='cascade', index=True)
    target_uuid = fields.Char(index=True)
    target_id = fields.Many2one('som.add.document', ondelete='set null')
    currency = fields.Char()
    equivalence = fields.Char()
    partiality = fields.Char()
    previous = fields.Float(digits=(24, 6), aggregator=None)
    paid = fields.Float(digits=(24, 6), aggregator=None)
    balance = fields.Float(digits=(24, 6), aggregator=None)
    tax_object = fields.Char()


class AddRelation(models.Model):
    _name = 'som.add.relation'
    _description = 'Relación fiscal por UUID'
    _inherit = 'som.add.child'
    kind = fields.Char()
    target_uuid = fields.Char(index=True)
    target_id = fields.Many2one('som.add.document', ondelete='set null')
