"""ADD capability checks. Context strings can never authorize a mutation."""
from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

INTERNAL = object()  # identity capability, inaccessible via JSON/XML RPC
PREFIX = 'account_statement_report.'


class AddUsers(models.Model):
    _inherit = 'res.users'

    add_company_ids = fields.Many2many('res.company', 'som_add_user_company_rel', 'user_id', 'company_id',
                                       string='Compañías autorizadas ADD', copy=False)

    def _add_check_grant(self, vals):
        if 'add_company_ids' in vals and not self.env.su:
            if not (self.env.user.has_group('base.group_erp_manager') and
                    self.env.user.has_group(PREFIX + 'group_add_access')):
                raise AccessError('Se requiere gestión de usuarios y el permiso independiente de accesos ADD.')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._add_check_grant(vals)
        return super().create(vals_list)

    def write(self, vals):
        self._add_check_grant(vals)
        result = super().write(vals)
        if 'add_company_ids' in vals:
            # ir.rule domains cache the evaluated company grant list. Revoke in
            # this worker and signal the registry cache to other workers.
            self.env.registry.clear_cache()
        return result


class AddSecurity(models.AbstractModel):
    _name = 'som.add.security'
    _description = 'Control de alcance ADD'

    def _guard(self, role='reader', company=None, extraction=False):
        user = self.env.user
        if user.share or not user.active or not user.has_group(PREFIX + 'group_add_' + role):
            raise AccessError('No tiene autorización ADD para esta operación.')
        # env.companies validates requested allowed_company_ids against Odoo grants.
        scope = user.company_ids & user.add_company_ids & self.env.companies
        if not scope or (company is not None and (not company.exists() or company not in scope)):
            raise AccessError('Compañía fuera del alcance autorizado ADD.')
        if extraction and not user.has_group(PREFIX + 'group_add_export'):
            raise AccessError('Se requiere ADD: exportar y descargar.')
        return scope

    def _internal(self):
        return self.with_context(_add_capability=INTERNAL)

    def _is_internal(self):
        return self.env.context.get('_add_capability') is INTERNAL

    def _require_internal(self):
        if not self._is_internal():
            raise AccessError('Los datos fiscales y las evidencias solo pueden modificarse mediante el procesador ADD.')

    @api.model_create_multi
    def create(self, vals_list):
        self._require_internal()
        return super().create(vals_list)

    def write(self, vals):
        self._require_internal()
        return super().write(vals)

    def unlink(self):
        self._require_internal()
        return super().unlink()

    def export_data(self, fields_to_export):
        self._guard(extraction=True)
        self.check_access('read')
        result = super().export_data(fields_to_export)
        from ..add_services.export import safe_cell
        result['datas'] = [[safe_cell(cell) for cell in row] for row in result['datas']]
        for company in self.mapped('company_id'):
            self.env['som.add.audit']._log(company, 'export', self._name, len(self))
        return result


class AddVault(models.Model):
    """No ACL, no attachments, no access token and no link to mail.thread.

    Only a checked document/batch method can cross this narrow sudo boundary.
    Raw fields are never returned by search_read on a document or import item.
    """
    _name = 'som.add.vault'
    _description = 'Almacenamiento privado ADD'
    _inherit = 'som.add.security'

    data = fields.Binary(attachment=False, required=True)
    company_id = fields.Many2one('res.company', required=True, index=True)


class AddAudit(models.Model):
    _name = 'som.add.audit'
    _description = 'Evidencia inmutable ADD'
    _inherit = 'som.add.security'
    _order = 'id desc'

    company_id = fields.Many2one('res.company', required=True, index=True)
    user_id = fields.Many2one('res.users', required=True)
    operation = fields.Selection([(v, v) for v in ('import', 'download', 'export', 'archive', 'reprocess', 'classify', 'cancel', 'sat')], required=True)
    reference = fields.Char()
    result = fields.Char()
    count = fields.Integer(default=1)

    @api.model
    def _log(self, company, operation, reference, count=1, result='correcto'):
        self._guard(company=company)
        # Technical write only: actor and company are fixed by checked caller.
        return self.sudo()._internal().create(dict(company_id=company.id, user_id=self.env.uid,
                        operation=operation, reference=str(reference)[:160], count=count, result=result))


class AddAttachment(models.Model):
    _inherit = 'ir.attachment'

    @api.model_create_multi
    def create(self, vals_list):
        if any(str(v.get('res_model', '')).startswith('som.add.') for v in vals_list):
            raise AccessError('ADD no admite adjuntos genéricos; utilice la carga privada de XML.')
        return super().create(vals_list)

    def write(self, vals):
        if str(vals.get('res_model', '')).startswith('som.add.') or any(str(a.res_model).startswith('som.add.') for a in self):
            raise AccessError('Los archivos ADD no pueden publicarse ni adjuntarse a otros registros.')
        return super().write(vals)


class AddConfig(models.Model):
    _name = 'som.add.config'
    _description = 'Límites y conservación ADD'

    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company, index=True)
    xml_mb = fields.Integer(default=5, required=True, string='Máximo XML (MiB)')
    upload_mb = fields.Integer(default=25, required=True, string='Máximo archivo recibido (MiB)')
    batch_mb = fields.Integer(default=100, required=True, string='Máximo lote descomprimido (MiB)')
    entries = fields.Integer(default=1000, required=True, string='Máximo archivos por lote')
    block_size = fields.Integer(default=20, required=True, string='Archivos por bloque')
    retention_days = fields.Integer(default=7, required=True, string='Días de conservación de archivos fallidos')
    _company_unique = models.Constraint('UNIQUE(company_id)', 'Solo una configuración por compañía.')

    @api.constrains('xml_mb', 'upload_mb', 'batch_mb', 'entries', 'block_size', 'retention_days')
    def _limits(self):
        for rec in self:
            if not (1 <= rec.xml_mb <= 10 and rec.xml_mb <= rec.upload_mb <= 50 and
                    rec.upload_mb <= rec.batch_mb <= 250 and 1 <= rec.entries <= 2000 and
                    1 <= rec.block_size <= 100 and 1 <= rec.retention_days <= 90):
                raise ValidationError('Límites fuera de rango: XML 1–10 MiB, carga ≤50 MiB, lote ≤250 MiB, archivos ≤2000, bloque ≤100, conservación ≤90 días.')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self.env['som.add.document']._guard('admin', self.env['res.company'].browse(vals.get('company_id') or self.env.company.id))
        return super().create(vals_list)

    def write(self, vals):
        if 'company_id' in vals:
            raise AccessError('La compañía de configuración es inmutable.')
        for rec in self:
            self.env['som.add.document']._guard('admin', rec.company_id)
        return super().write(vals)

    @api.model
    def _for_company(self, company):
        self.env['som.add.document']._guard(company=company)
        config = self.search([('company_id', '=', company.id)], limit=1)
        return dict(xml_bytes=(config.xml_mb or 5) * 1048576, upload_bytes=(config.upload_mb or 25) * 1048576,
                    batch_bytes=(config.batch_mb or 100) * 1048576, entries=config.entries or 1000,
                    block_size=config.block_size or 20, retention_days=config.retention_days or 7)

    def export_data(self, fields_to_export):
        self.env['som.add.document']._guard(extraction=True)
        return super().export_data(fields_to_export)
