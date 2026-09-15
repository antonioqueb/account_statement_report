"""Odoo 19 integration/security tests. Run only in a disposable development DB."""
from io import BytesIO
import zipfile
from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user
from . import fixtures_add as fx

PREFIX = 'account_statement_report.'


@tagged('post_install', '-at_install', 'add_viewer')
class TestAddBackend(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env['res.company'].create({'name': 'ADD prueba A', 'vat': fx.COMPANY})
        cls.other = cls.env['res.company'].create({'name': 'ADD prueba B', 'vat': 'CCC010101CCC'})
        cls.users = {}
        for name, group in [('none', ''), ('reader', 'reader'), ('operator', 'operator'), ('admin', 'admin')]:
            groups = 'base.group_user' + (',' + PREFIX + 'group_add_' + group if group else '')
            cls.users[name] = new_test_user(cls.env, 'add_test_' + name, groups=groups,
                company_id=cls.company.id, company_ids=[Command.set([cls.company.id, cls.other.id])])
            cls.users[name].sudo().write({'add_company_ids': [Command.set([cls.company.id])]})
        cls.users['exporter'] = new_test_user(cls.env, 'add_test_exporter',
            groups='base.group_user,' + PREFIX + 'group_add_reader,' + PREFIX + 'group_add_export',
            company_id=cls.company.id, company_ids=[Command.set([cls.company.id, cls.other.id])])
        cls.users['exporter'].sudo().write({'add_company_ids': [Command.set([cls.company.id])]})

    def model(self, name, role='operator', company=None):
        return self.env[name].with_user(self.users[role]).with_context(allowed_company_ids=[(company or self.company).id])

    def load_xml(self, raws, direction='received', role='operator', company=None):
        batches = self.model('som.add.batch', role, company)
        batch = batches.browse(batches.begin((company or self.company).id, direction))
        for i, raw in enumerate(raws):
            batch._receive('sintetico-%s.xml' % i, raw)
        batch.seal()
        for _ in range(20):
            result = batch.process_block()
            if result['state'] != 'processing':
                break
        self.assertNotEqual(batch.state, 'processing', 'El lote no terminó')
        return batch

    def document(self):
        batch = self.load_xml([fx.invoice()])
        return self.model('som.add.document').search([('batch_id', '=', batch.id)])

    def test_unauthorized_models_search_metrics_and_methods(self):
        doc = self.document()
        for model in ('document', 'concept', 'tax', 'payment', 'application', 'relation', 'batch', 'item', 'audit'):
            with self.assertRaises(AccessError): self.model('som.add.' + model, 'none').search([])
        with self.assertRaises(AccessError): doc.with_user(self.users['none']).detail()
        with self.assertRaises(AccessError): self.model('som.add.document', 'none').dashboard({})
        with self.assertRaises(AccessError): self.model('som.add.batch', 'none').begin(self.company.id, 'received')

    def test_reader_can_inspect_but_not_mutate(self):
        doc = self.document().with_user(self.users['reader'])
        self.assertIn('<', doc.detail()['xml'])
        for operation in (lambda: doc.write({'notes': 'x'}), lambda: doc.reprocess(), lambda: doc.archive_document('x'),
                          lambda: self.model('som.add.batch', 'reader').begin(self.company.id, 'received')):
            with self.assertRaises(AccessError): operation()

    def test_operator_cannot_forge_context_fiscal_data_or_evidence(self):
        doc = self.document()
        for values in ({'total': 0}, {'company_id': self.other.id}, {'vault_id': 1}, {'active': False}):
            with self.assertRaises(AccessError): doc.with_context(_add_capability=True).write(values)
        with self.assertRaises(AccessError): self.model('som.add.document').with_context(_add_capability='INTERNAL').create({'uuid': fx.uid('forged')})
        with self.assertRaises(AccessError): doc.unlink()
        with self.assertRaises(AccessError): self.model('som.add.audit').search([], limit=1).write({'result': 'forged'})
        with self.assertRaises(AccessError): self.model('som.add.concept').search([], limit=1).write({'amount': 0})

    def test_metadata_and_archival_are_audited(self):
        doc = self.document()
        doc.write({'notes': 'REVISADO', 'classification': 'FLETE'})
        self.assertEqual(doc.notes, 'REVISADO')
        with self.assertRaises(AccessError): doc.archive_document('operator is not admin')
        doc.with_user(self.users['admin']).archive_document('Duplicidad administrativa revisada')
        self.assertFalse(doc.active)
        self.assertTrue(self.model('som.add.audit', 'admin').search_count([('operation', '=', 'archive')]))

    def test_context_defaults_cannot_invent_sat_or_batch_owner(self):
        batches = self.model('som.add.batch').with_context(default_sat_state='valid', default_active=False,
                                                          default_user_id=self.users['admin'].id, default_state='done')
        batch = batches.browse(batches.begin(self.company.id, 'received'))
        self.assertEqual(batch.user_id, self.users['operator'])
        self.assertEqual(batch.state, 'pending')
        batch._receive('context.xml', fx.invoice()); batch.seal(); batch.process_block()
        doc = self.model('som.add.document').search([('batch_id', '=', batch.id)])
        self.assertTrue(doc.active)
        self.assertEqual(doc.sat_state, 'unknown')

    def test_admin_cannot_expand_grants_or_set_self_permissions(self):
        admin = self.users['admin'].with_user(self.users['admin'])
        with self.assertRaises(AccessError): admin.write({'add_company_ids': [Command.link(self.other.id)]})
        with self.assertRaises(AccessError): admin.write({'group_ids': [Command.link(self.env.ref(PREFIX + 'group_add_access').id)]})

    def test_manipulated_companies_domains_and_children(self):
        self.users['operator'].sudo().write({'add_company_ids': [Command.set([self.company.id, self.other.id])]})
        batch = self.load_xml([fx.invoice(company=self.other.vat)], company=self.other)
        doc = self.model('som.add.document', company=self.other).search([('batch_id', '=', batch.id)])
        reader = doc.with_user(self.users['reader']).with_context(allowed_company_ids=[self.other.id])
        with self.assertRaises(AccessError): reader.read(['total'])
        with self.assertRaises(AccessError): reader.detail()
        with self.assertRaises(AccessError): doc.concept_ids.with_user(self.users['reader']).read(['amount'])
        with self.assertRaises(UserError): self.model('som.add.document', 'reader').explore({'companies': [self.other.id]})
        self.assertFalse(self.model('som.add.document', 'reader').search([('company_id', '=', self.other.id)]))

    def test_private_vault_and_no_generic_attachments(self):
        doc = self.document()
        with self.assertRaises(AccessError): self.model('som.add.vault', 'reader').browse(doc.vault_id).read(['data'])
        with self.assertRaises(AccessError): self.env['ir.attachment'].with_user(self.users['operator']).create({
            'name': 'leak.xml', 'res_model': 'som.add.document', 'res_id': doc.id, 'public': True})
        self.assertFalse(self.env['ir.attachment'].search([('res_model', 'like', 'som.add.%')]))

    def test_exports_need_independent_permission_and_revocation(self):
        doc = self.document()
        filters = {'companies': [self.company.id], 'direction': 'received'}
        with self.assertRaises(AccessError): doc.export_data(['uuid'])
        with self.assertRaises(AccessError): self.model('som.add.document')._export_file(filters)
        body, mime, name = self.model('som.add.document', 'exporter')._export_file(filters, [doc.id], 'xml')
        self.assertEqual(body, fx.invoice())
        self.users['exporter'].sudo().write({'add_company_ids': [Command.clear()]})
        with self.assertRaises(AccessError): self.model('som.add.document', 'exporter')._export_file(filters, [doc.id], 'xml')
        with self.assertRaises(AccessError): doc.with_user(self.users['exporter']).read(['total'])

    def test_eight_references_metrics_idempotence_no_accounting_effects(self):
        before = {name: self.env[name].search_count([]) for name in ('account.move', 'account.payment', 'stock.move', 'res.partner', 'product.product')}
        batch = self.load_xml(fx.references())
        self.assertEqual(batch.imported_count, 8)
        docs = self.model('som.add.document')
        filters = {'companies': [self.company.id], 'direction': 'received', 'start': '2025-01-01', 'end': '2026-12-31'}
        result = docs.explore(filters)
        self.assertEqual(result['count'], 8)
        totals = {(r['currency'], r['kind']): r['total'] for r in result['totals']}
        self.assertAlmostEqual(totals[('MXN', 'I')], 186177.52, places=2)
        self.assertAlmostEqual(totals[('USD', 'I')], 4542.55, places=2)
        self.assertEqual(self.model('som.add.concept').search_count([('commercial', '=', True)]), 12)
        second = self.load_xml(fx.references())
        self.assertEqual(second.duplicate_count, 8)
        self.assertEqual(docs.explore(filters)['count'], 8)
        self.assertEqual(before, {name: self.env[name].search_count([]) for name in before})

    def test_payments_filter_uses_payment_date_not_header_or_upload(self):
        self.load_xml([fx.payment()])
        doc = self.model('som.add.document')
        result = doc.dashboard({'companies': [self.company.id], 'direction': 'received', 'start': '2026-01-01', 'end': '2026-12-31'})
        payments = [r for r in result['rows'] if r['model'] == 'som.add.payment']
        self.assertFalse(payments)

    def test_mixed_zip_and_payroll_do_not_abort_or_keep_payroll(self):
        stream = BytesIO()
        with zipfile.ZipFile(stream, 'w') as z:
            for name, raw in [('good.xml', fx.invoice()), ('invalid.xml', b'<broken'), ('private-name.xml', fx.invoice(kind='N'))]:
                z.writestr(name, raw)
        batches = self.model('som.add.batch'); batch = batches.browse(batches.begin(self.company.id, 'received'))
        batch._receive('mixed.zip', stream.getvalue()); batch.seal(); batch.process_block()
        self.assertEqual((batch.imported_count, batch.rejected_count, batch.total_count), (1, 2, 3))
        payroll = batch.item_ids.filtered(lambda r: r.code == 'payroll')
        self.assertFalse(payroll.vault_id); self.assertFalse(payroll.parsed)
        self.assertEqual(payroll.filename, 'Contenido no admitido')

    def test_conflict_preserves_original_and_reprocess(self):
        doc = self.document(); original = doc._raw()
        batch = self.load_xml([original.replace(b'Folio="basic"', b'Folio="different-format"')])
        self.assertEqual(batch.rejected_count, 1)
        self.assertEqual(batch.item_ids.code, 'conflict')
        doc.reprocess()
        self.assertEqual(doc._raw(), original)
        self.assertEqual(len(doc.concept_ids), 1)

    def test_late_uuid_resolution(self):
        self.load_xml([fx.payment(targets=[fx.uid('later')]), fx.invoice(name='replacement', related=fx.uid('later'))])
        self.assertFalse(self.model('som.add.application').search([]).target_id)
        self.load_xml([fx.invoice(name='later')])
        self.assertTrue(self.model('som.add.application').search([]).target_id)
        self.assertTrue(self.model('som.add.relation').search([]).target_id)

    def test_owner_fixed_cancel_and_revoked_worker(self):
        batches = self.model('som.add.batch'); batch = batches.browse(batches.begin(self.company.id, 'received'))
        batch._receive('one.xml', fx.invoice()); batch.seal(); batch.cancel()
        self.assertEqual(batch.pending_count, 1)
        with self.assertRaises(AccessError): batch.write({'user_id': self.users['admin'].id})
        batch.retry()
        self.users['operator'].sudo().write({'add_company_ids': [Command.clear()]})
        with self.assertRaises(AccessError): batch.process_block()
        self.env['som.add.batch']._cron_process()
        self.assertEqual(batch.sudo().state, 'failed')

    def test_menu_and_public_portal_denial(self):
        root = self.env.ref(PREFIX + 'menu_add_root')
        self.assertIn(self.env.ref(PREFIX + 'group_add_reader'), root['group_ids' if 'group_ids' in root._fields else 'groups_id'])
        doc = self.document()
        public = self.env.ref('base.public_user')
        with self.assertRaises(AccessError): doc.with_user(public).detail()
        with self.assertRaises(AccessError): doc.with_user(public).read(['uuid'])
