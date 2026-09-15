"""Run without Odoo: python3 tests/test_add_offline.py -v"""
import csv
from decimal import Decimal
import importlib.util
from io import BytesIO, StringIO
from pathlib import Path
import stat
import sys
import unittest
import zipfile
from lxml import etree as E

# Load pure services without executing the addon's Odoo __init__.
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('add_offline_services', ROOT / 'add_services/__init__.py', submodule_search_locations=[str(ROOT / 'add_services')])
service = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = service
spec.loader.exec_module(service)
sys.path.insert(0, str(Path(__file__).parent))
import fixtures_add as fx
cfdi, archive, analytics, export = service.cfdi, service.archive, service.analytics, service.export


class ParserTests(unittest.TestCase):
    def parse(self, raw, **kwargs):
        return cfdi.parse(raw, fx.COMPANY, kwargs.pop('direction', 'received'), **kwargs)

    def rejected(self, raw, code=None, **kwargs):
        with self.assertRaises(cfdi.Rejected) as error:
            self.parse(raw, **kwargs)
        if code:
            self.assertEqual(error.exception.code, code)

    def test_eight_synthetic_reference_totals(self):
        payloads = [self.parse(raw) for raw in fx.references()]
        summary = analytics.summarize(payloads)
        self.assertEqual(summary['count'], 8)
        self.assertEqual(summary['commercial_concepts'], 12)
        self.assertEqual(summary['totals'][('MXN', 'I')], Decimal('186177.52'))
        self.assertEqual(summary['totals'][('USD', 'I')], Decimal('4542.55'))
        self.assertEqual(summary['payments'], {'MXN': Decimal('17777.00')})
        self.assertEqual(sum(any(c['name'] == 'CartaPorte' for c in p['complements']) for p in payloads), 2)
        self.assertEqual(analytics.summarize(payloads + payloads), summary)
        self.assertEqual(payloads[-1]['payments'][0]['attributes']['FechaPago'][:4], '2025')
        self.assertEqual(payloads[-1]['header']['Moneda'], 'XXX')

    def test_received_references_wrong_direction(self):
        for raw in fx.references():
            self.rejected(raw, 'direction', direction='issued')

    def test_issued_and_credit(self):
        self.assertTrue(self.parse(fx.invoice(direction='issued'), direction='issued')['issued'])
        p = self.parse(fx.invoice(kind='E'))
        self.assertEqual(p['header']['TipoDeComprobante'], 'E')

    def test_other_company_rejected(self):
        self.rejected(fx.invoice(company='CCC010101CCC'), 'company')

    def test_same_rfc_both_conditions_one_uuid(self):
        raw = fx.invoice().replace(fx.OTHER.encode(), fx.COMPANY.encode())
        p = self.parse(raw)
        self.assertTrue(p['issued'] and p['received'])
        self.assertEqual(len(p['warnings']), 1)

    def test_prefix_and_version_independence(self):
        for version in ('3.3', '4.0'):
            p = self.parse(fx.invoice(version=version, prefix='different'))
            self.assertEqual(p['header']['Version'], version)

    def test_payments_versions_multiple_applications_no_double_amount(self):
        for version in ('1.0', '2.0'):
            p = self.parse(fx.payment(version=version, currency='USD', amount='100', count=2, targets=[fx.uid('one'), fx.uid('two')]))
            self.assertEqual(len(p['payments']), 2)
            self.assertEqual(sum(len(pay['applications']) for pay in p['payments']), 4)
            self.assertEqual(analytics.summarize([p])['payments'], {'USD': Decimal('200')})

    def test_precision_and_canonical_taxes(self):
        p = self.parse(fx.references()[4])
        self.assertEqual(p['concepts'][0]['attributes']['ValorUnitario'], '4422.413000')
        self.assertEqual(p['difference'], '0.00')
        self.assertEqual(p['consistency'], 'ok')

    def test_withholding_belongs_only_to_freight(self):
        p = self.parse(fx.references()[6])
        line_taxes = [t for t in p['taxes'] if t['kind'] == 'withholding' and t['level'] == 'concept']
        self.assertEqual([(t['concept_index'], t['amount']) for t in line_taxes], [(1, '4750.00')])

    def test_exempt_zero_quota_and_non_object_distinct(self):
        lines = [dict(amount='10', taxes=[dict(tax='002', factor='Exento')]),
                 dict(amount='10', taxes=[dict(tax='002', rate='0.000000', amount='0')]),
                 dict(amount='10', taxes=[dict(tax='003', factor='Cuota', rate='1.5', amount='1.5')]),
                 dict(amount='10', object='01')]
        p = self.parse(fx.invoice(subtotal='40', total='41.5', lines=lines, taxes=[dict(tax='003', factor='Cuota', rate='1.5', amount='1.5')]))
        t = [t for t in p['taxes'] if t['level'] == 'concept']
        self.assertEqual(t[0]['factor'], 'Exento'); self.assertIsNone(t[0]['rate'])
        self.assertEqual(t[1]['rate'], '0.000000'); self.assertEqual(t[2]['factor'], 'Cuota')
        self.assertFalse(p['concepts'][3]['taxes'])

    def test_unknown_preserved_safe_text_partial(self):
        p = self.parse(fx.invoice(unknown=True))
        self.assertEqual(p['consistency'], 'partial')
        self.assertEqual(p['complements'][0]['tree']['text'], '<script>alert(1)</script>')

    def test_missing_stamp(self):
        root = E.fromstring(fx.invoice())
        node = root.find('.//{%s}TimbreFiscalDigital' % fx.TFD)
        node.getparent().remove(node)
        self.rejected(E.tostring(root), 'stamp')

    def test_payroll_redacted(self):
        self.rejected(fx.invoice(kind='N'), 'payroll')

    def test_retenciones_schema_not_invoice_tax(self):
        self.rejected(b'<Retenciones xmlns="http://www.sat.gob.mx/esquemas/retencionpago/2"/>', 'unsupported')

    def test_dtd_xxe_utf16_and_expansion(self):
        for raw in (b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>',
                    b'<!DOCTYPE x [<!ENTITY e SYSTEM "https://example.invalid/">]><x>&e;</x>',
                    '<!DOCTYPE x [<!ENTITY e "boom">]><x>&e;</x>'.encode('utf-16')):
            self.rejected(raw, 'structure')

    def test_deep_size_nodes_and_timeout_limits(self):
        self.rejected(b'<x>' * 55 + b'</x>' * 55, 'limits')
        self.rejected(fx.invoice(), 'size', limits={'xml_bytes': 20})
        self.rejected(fx.invoice(), 'limits', limits={'nodes': 2})
        self.rejected(fx.invoice(), 'limits', limits={'seconds': 0})

    def test_no_network_schema_location(self):
        raw = fx.invoice().replace(b'Version="4.0"', b'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="file:///definitely-absent https://example.invalid" Version="4.0"')
        self.assertEqual(self.parse(raw)['consistency'], 'ok')

    def test_uuid_normalization_original_and_hash(self):
        raw = fx.invoice(); original = fx.uid('basic')
        other = raw.replace(original.encode(), original.lower().encode())
        a, b = self.parse(raw), self.parse(other)
        self.assertEqual(a['uuid'], b['uuid'])
        self.assertEqual(b['stamp']['UUID'], original.lower())
        self.assertNotEqual(a['sha256'], b['sha256'])

    def test_nan_exponent_and_large_numbers_rejected(self):
        for value in ('NaN', 'Infinity', '1e9000', '.0000000000000001'):
            with self.assertRaises(cfdi.Rejected): cfdi.number(value)

    def test_rfc_does_not_remove_valid_characters(self):
        self.assertEqual(cfdi.rfc(' ñ&a010101aaa '), 'Ñ&A010101AAA')


class ArchiveTests(unittest.TestCase):
    def zip(self, entries):
        stream = BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in entries: archive.writestr(name, data)
        return stream.getvalue()

    def test_mixed_zip_valid_and_unsafe_entries(self):
        raw = self.zip([('ok.xml', fx.invoice()), ('../../outside.xml', b'x'), ('bad.xml', b'<bad'), ('nested.zip', b'x')])
        entries = archive.expand('batch.zip', raw)
        self.assertEqual(len(entries), 4)
        self.assertFalse(entries[0][2]); self.assertTrue(entries[1][2]); self.assertTrue(entries[3][2])
        with self.assertRaises(cfdi.Rejected): cfdi.parse(entries[2][1], fx.COMPANY, 'received')

    def test_symlink_and_absolute_path(self):
        link = zipfile.ZipInfo('symlink.xml'); link.create_system = 3; link.external_attr = (stat.S_IFLNK | 0o777) << 16
        entries = archive.expand('x.zip', self.zip([(link, '/etc/passwd'), ('/abs.xml', 'x'), ('C:\\absolute.xml', 'x')]))
        self.assertTrue(all(e[2] for e in entries))

    def test_zip_bomb_entry_size_count_and_total(self):
        self.assertTrue(archive.expand('x.zip', self.zip([('bomb.xml', b'x' * 100000)]))[0][2])
        for limits in ({'entries': 1}, {'batch_bytes': 5}):
            with self.assertRaises(cfdi.Rejected): archive.expand('x.zip', self.zip([('a.xml', b'1234'), ('b.xml', b'1234')]), limits)

    def test_safe_names_and_bad_zip(self):
        self.assertEqual(archive.safe_name('../../test.xml'), 'test.xml')
        with self.assertRaises(cfdi.Rejected): archive.expand('broken.zip', b'not a zip')


class ExportTests(unittest.TestCase):
    def test_csv_formula_injection(self):
        values = ['=SUM(1,2)', '+cmd', '-cmd', '@SUM(A1)', '\t=1', ' \r=1', 'safe', 42]
        raw = export.csv_bytes(['value'], [[v] for v in values])
        rows = list(csv.reader(StringIO(raw.decode('utf-8-sig'))))
        for row in rows[1:7]: self.assertTrue(row[0].startswith("'"))
        self.assertEqual(rows[-1][0], '42')

    def test_xlsx_separate_sheets_and_literal_cells(self):
        raw = export.xlsx_bytes([('Documentos', ['UUID', 'Total'], [['abc', '116.00']]), ('Conceptos', ['Descripción'], [['=HYPERLINK("x")'], ['<script/>']])])
        with zipfile.ZipFile(BytesIO(raw)) as book:
            for path in book.namelist(): E.fromstring(book.read(path))
            sheet = E.fromstring(book.read('xl/worksheets/sheet2.xml'))
            self.assertEqual(len(sheet.xpath('//*[local-name()="f"]')), 0)
            self.assertIn('HYPERLINK', E.tostring(sheet).decode())


if __name__ == '__main__':
    unittest.main()
