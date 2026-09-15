import base64
from datetime import timedelta
from psycopg2 import IntegrityError
from psycopg2.errors import DeadlockDetected, SerializationFailure
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from ..add_services import archive, cfdi


class AddBatch(models.Model):
    _name = 'som.add.batch'
    _description = 'Lote de importación ADD'
    _inherit = 'som.add.security'
    _order = 'id desc'

    name = fields.Char(required=True)
    company_id = fields.Many2one('res.company', required=True, index=True)
    user_id = fields.Many2one('res.users', required=True, index=True)
    direction = fields.Selection([('issued', 'Emitidos'), ('received', 'Recibidos')], required=True)
    state = fields.Selection([('pending', 'Pendiente'), ('processing', 'Procesando'), ('done', 'Completado'),
                             ('issues', 'Completado con incidencias'), ('failed', 'Fallido'), ('cancelled', 'Cancelado')], default='pending', index=True)
    started_at = fields.Datetime()
    finished_at = fields.Datetime()
    sealed = fields.Boolean(default=False)
    byte_count = fields.Integer()
    item_ids = fields.One2many('som.add.item', 'batch_id')
    total_count = fields.Integer(compute='_counts')
    imported_count = fields.Integer(compute='_counts')
    duplicate_count = fields.Integer(compute='_counts')
    rejected_count = fields.Integer(compute='_counts')
    failed_count = fields.Integer(compute='_counts')
    pending_count = fields.Integer(compute='_counts')
    progress = fields.Float(compute='_counts')

    def _counts(self):
        counts = self.env['som.add.item']._read_group([('batch_id', 'in', self.ids)], ['batch_id', 'state'], ['__count'])
        mapping = {(batch.id, state): count for batch, state, count in counts}
        for batch in self:
            total = 0
            for state in ('imported', 'duplicate', 'rejected', 'failed', 'pending'):
                count = mapping.get((batch.id, state), 0)
                batch[state + '_count'] = count
                total += count
            batch.total_count = total
            batch.progress = 100 * (total - batch.pending_count) / total if total else 0

    @api.model
    def begin(self, company_id, direction):
        company = self.env['res.company'].browse(int(company_id))
        self._guard('operator', company)
        if direction not in ('issued', 'received'):
            raise UserError('Seleccione emitidos o recibidos.')
        if not cfdi.rfc(company.vat):
            raise UserError('Configure primero el RFC de la compañía en Odoo.')
        batch = self._internal().create(dict(name='XML %s · %s' % ('emitidos' if direction == 'issued' else 'recibidos', fields.Datetime.now()),
                                              company_id=company.id, direction=direction, user_id=self.env.uid))
        return batch.id

    def _owner_guard(self):
        self.ensure_one()
        self.check_access('read')
        self._guard('operator', self.company_id)
        if self.user_id != self.env.user:
            raise AccessError('Solo el usuario del lote puede cargar, procesar, reintentar o cancelarlo.')

    def _lock(self):
        self._owner_guard()
        self.env.cr.execute('SELECT id FROM som_add_batch WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset()

    def _receive(self, filename, raw):
        self._lock()
        if self.sealed or self.state != 'pending':
            raise UserError('El lote ya está cerrado para recepción.')
        limits = self.env['som.add.config']._for_company(self.company_id)
        try:
            entries = archive.expand(filename, raw, {k: v for k, v in limits.items() if k in archive.DEFAULT_LIMITS})
        except cfdi.Rejected as error:
            entries = [(archive.safe_name(filename), b'', str(error))]
        count = self.env['som.add.item'].search_count([('batch_id', '=', self.id)])
        size = sum(len(data) for _, data, _ in entries)
        if count + len(entries) > limits['entries'] or self.byte_count + size > limits['batch_bytes']:
            raise UserError('Límite acumulado del lote excedido. Inicie otro lote.')
        for name, data, issue in entries:
            values = dict(batch_id=self.id, filename=name, state='pending')
            if issue:
                values.update(state='rejected', code='archive', message=issue)
            else:
                # Parse once during reception. Persistence is resumable by blocks;
                # unsupported/payroll files are never kept in the private vault.
                try:
                    parsed = cfdi.parse(data, self.company_id.vat, self.direction, {'xml_bytes': limits['xml_bytes']})
                    vault = self.env['som.add.vault'].sudo()._internal().create(dict(company_id=self.company_id.id, data=base64.b64encode(data)))
                    values.update(parsed=parsed, vault_id=vault.id, warning=bool(parsed['warnings']))
                except cfdi.Rejected as error:
                    values.update(state='rejected', code=error.code, message=str(error))
                    if error.code == 'payroll':
                        values['filename'] = 'Contenido no admitido'
            item = self.env['som.add.item']._internal().create(values)
            if values['state'] == 'rejected':
                self.env['som.add.audit']._log(self.company_id, 'import', 'ítem %s' % item.id, result=values.get('code'))
        self._internal().write({'byte_count': self.byte_count + size})
        return self.status()

    def seal(self):
        self._lock()
        if self.state != 'pending':
            raise UserError('El lote ya no admite cambios de recepción.')
        self._internal().write({'sealed': True, 'state': 'processing', 'started_at': fields.Datetime.now()})
        return self.status()

    def status(self):
        self.ensure_one()
        self._guard(company=self.company_id)
        self.check_access('read')
        return self.read(['name', 'company_id', 'direction', 'state', 'sealed', 'total_count', 'imported_count',
                          'duplicate_count', 'rejected_count', 'failed_count', 'pending_count', 'progress'])[0]

    def process_block(self):
        self._lock()
        if self.state != 'processing' or not self.sealed:
            return self.status()
        limits = self.env['som.add.config']._for_company(self.company_id)
        items = self.env['som.add.item'].search([('batch_id', '=', self.id), ('state', '=', 'pending')], limit=limits['block_size'], order='id')
        for item in items:
            try:
                with self.env.cr.savepoint():
                    self._process_item(item)
            except AccessError:
                raise
            except (DeadlockDetected, SerializationFailure):
                # Let Odoo's transaction service retry concurrency failures.
                raise
            except Exception:
                # No XML, tax IDs, seals, personal data or exception text in logs.
                item._internal().write({'state': 'failed', 'code': 'technical', 'message': 'Fallo técnico al persistir; puede reintentar.'})
                self.env['som.add.audit']._log(self.company_id, 'import', 'ítem %s' % item.id, result='technical')
        if not self.env['som.add.item'].search_count([('batch_id', '=', self.id), ('state', '=', 'pending')]):
            issues = self.env['som.add.item'].search_count([('batch_id', '=', self.id), '|', ('state', 'in', ['rejected', 'failed']), ('warning', '=', True)])
            self._internal().write({'state': 'issues' if issues else 'done', 'finished_at': fields.Datetime.now()})
        return self.status()

    def _process_item(self, item):
        self._owner_guard()
        parsed = item.parsed
        docs = self.env['som.add.document'].with_context(active_test=False)
        domain = [('company_id', '=', self.company_id.id), ('uuid', '=', parsed['uuid'])]
        existing = docs.search(domain, limit=1)
        if not existing:
            try:
                with self.env.cr.savepoint():
                    values = docs._values(parsed)
                    values.update(company_id=self.company_id.id, batch_id=self.id, filename=item.filename, vault_id=item.vault_id)
                    doc = docs._internal().create(values)
                    doc._persist_children(parsed)
            except IntegrityError:
                # The database UNIQUE constraint is the final concurrent arbiter.
                # Under REPEATABLE READ, retry the request if the winning row is
                # not visible yet; never convert that case into a second document.
                existing = docs.search(domain, limit=1)
                if not existing:
                    # PostgreSQL repeatable-read snapshot predates the winner.
                    # Leave this item pending for the next block/transaction.
                    return
            else:
                item._internal().write(dict(state='imported', document_id=doc.id, parsed=False, vault_id=False,
                                            code='imported', message='Incorporado; estado SAT: No consultado.'))
                self.env['som.add.audit']._log(self.company_id, 'import', doc.uuid)
                return
        same = existing.sha256 == parsed['sha256']
        vault = self.env['som.add.vault'].browse(item.vault_id)
        item._internal().write(dict(state='duplicate' if same else 'rejected', document_id=existing.id, parsed=False, vault_id=False,
                         code='duplicate' if same else 'conflict', message='Duplicado omitido.' if same else
                         'Conflicto: mismo UUID con bytes diferentes; se conserva el original. Una diferencia de formato también cambia el hash.'))
        vault.sudo()._internal().unlink()
        self.env['som.add.audit']._log(self.company_id, 'import', existing.uuid, result='duplicate' if same else 'conflict')

    def cancel(self):
        self._lock()
        if self.state not in ('pending', 'processing', 'failed'):
            raise UserError('El lote ya finalizó.')
        self._internal().write({'state': 'cancelled', 'sealed': True, 'finished_at': fields.Datetime.now()})
        self.env['som.add.audit']._log(self.company_id, 'cancel', self.name)
        return self.status()

    def retry(self):
        self._lock()
        if self.state not in ('issues', 'failed', 'cancelled'):
            raise UserError('Solo se reintentan lotes con incidencias, fallidos o cancelados.')
        failed = self.env['som.add.item'].search([('batch_id', '=', self.id), ('state', '=', 'failed'), ('vault_id', '!=', False)])
        failed._internal().write({'state': 'pending', 'message': False})
        self._internal().write({'state': 'processing', 'sealed': True, 'finished_at': False})
        return self.status()

    @api.model
    def _cron_process(self):
        # Scheduler elevation only enumerates job metadata. Work runs as its
        # original owner, with one fixed company, rechecking current grants.
        jobs = self.sudo().search([('state', '=', 'processing'), ('sealed', '=', True)], limit=5, order='id')
        for job in jobs:
            worker = job.with_user(job.user_id).with_context(allowed_company_ids=[job.company_id.id])
            try:
                with self.env.cr.savepoint():
                    worker.process_block()
            except AccessError:
                job._internal().write({'state': 'failed'})
        # A subsequent transaction also resolves references that were inserted
        # concurrently in different batches (neither transaction saw the other).
        recent = self.sudo().search([('state', 'in', ['done', 'issues']),
                                      ('finished_at', '>=', fields.Datetime.now() - timedelta(days=1))], limit=20, order='id desc')
        seen = set()
        for job in recent:
            key = (job.user_id.id, job.company_id.id)
            if key in seen:
                continue
            seen.add(key)
            worker = job.with_user(job.user_id).with_context(allowed_company_ids=[job.company_id.id])
            try:
                with self.env.cr.savepoint():
                    worker._owner_guard()
                    for name in ('som.add.relation', 'som.add.application'):
                        refs = worker.env[name].search([('target_id', '=', False)], limit=200, order='id desc')
                        targets = worker.env['som.add.document'].with_context(active_test=False).search([('uuid', 'in', refs.mapped('target_uuid'))])
                        by_uuid = {d.uuid: d.id for d in targets}
                        for ref in refs:
                            if ref.target_uuid in by_uuid:
                                ref._internal().write({'target_id': by_uuid[ref.target_uuid]})
            except AccessError:
                continue
        # Transaction belongs to Odoo's scheduler; no arbitrary commits/threads.

    @api.model
    def _cron_cleanup(self):
        # Technical retention worker. Never exposes content; only purges vaults
        # belonging to terminal/abandoned non-imported items, max 200 per run.
        cutoff = fields.Datetime.now() - timedelta(days=1)
        items = self.env['som.add.item'].sudo().search([('vault_id', '!=', False), ('create_date', '<', cutoff)], limit=200)
        for item in items:
            cfg = self.env['som.add.config'].sudo().search([('company_id', '=', item.company_id.id)], limit=1)
            days = cfg.retention_days or 7
            if item.create_date >= fields.Datetime.now() - timedelta(days=days):
                continue
            if item.batch_id.state == 'processing':
                continue
            vault = self.env['som.add.vault'].sudo().browse(item.vault_id)
            item._internal().write({'vault_id': False, 'parsed': False, 'state': 'rejected', 'code': 'expired',
                                   'message': 'Archivo pendiente/fallido eliminado por política de conservación; vuelva a cargarlo.'})
            vault._internal().unlink()


class AddItem(models.Model):
    _name = 'som.add.item'
    _description = 'Resultado por archivo ADD'
    _inherit = 'som.add.security'
    _order = 'id'
    batch_id = fields.Many2one('som.add.batch', required=True, index=True, ondelete='restrict')
    company_id = fields.Many2one(related='batch_id.company_id', store=True, index=True)
    filename = fields.Char()
    state = fields.Selection([(k, v) for k, v in [('pending', 'Pendiente'), ('imported', 'Importado'), ('duplicate', 'Duplicado omitido'),
                                                 ('rejected', 'Rechazado'), ('failed', 'Fallido')]], required=True, index=True)
    code = fields.Char(index=True)
    message = fields.Text()
    warning = fields.Boolean()
    document_id = fields.Many2one('som.add.document', ondelete='restrict')
    vault_id = fields.Integer(string='Referencia de almacenamiento privado')
    parsed = fields.Json()
