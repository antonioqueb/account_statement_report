"""Static integration checks; these do not replace an Odoo module upgrade test."""
import ast
import csv
from pathlib import Path
import re
import unittest
from lxml import etree as E

ROOT = Path(__file__).resolve().parents[1]


class StructureTests(unittest.TestCase):
    def test_manifest_dependencies_and_all_files_exist(self):
        manifest = ast.literal_eval((ROOT / '__manifest__.py').read_text())
        self.assertFalse(manifest['application'])
        self.assertEqual(manifest['version'], '19.0.4.0.1')
        for dependency in ('sale', 'account', 'stock', 'sale_delivery_wizard', 'sale_order_extended_metrics'):
            self.assertIn(dependency, manifest['depends'])
        for name in manifest['data']:
            self.assertTrue((ROOT / name).is_file(), name)
        for bundle in manifest['assets'].values():
            for name in bundle:
                self.assertTrue((ROOT / name.split('/', 1)[1]).is_file(), name)
        self.assertEqual(len(list(ROOT.rglob('__manifest__.py'))), 1)

    def test_global_rules_cover_every_add_documentary_model(self):
        rules = E.parse(str(ROOT / 'security/add_rules.xml'))
        models = {e.get('ref') for e in rules.xpath('//field[@name="model_id"]')}
        for name in ('document', 'concept', 'tax', 'payment', 'application', 'relation', 'batch', 'item', 'audit', 'config'):
            self.assertIn('model_som_add_' + name, models)
        self.assertFalse(rules.xpath('//field[@name="groups"]'))
        for element in rules.xpath('//field[@name="domain_force"]'):
            for term in ('company_ids', 'user.company_ids.ids', 'user.add_company_ids.ids', 'user.share'):
                self.assertIn(term, element.text)

    def test_no_implicit_grants_no_vault_acl(self):
        with (ROOT / 'security/add/ir.model.access.csv').open() as stream:
            rows = list(csv.DictReader(stream))
        for row in rows:
            self.assertTrue(row['group_id/id'].startswith('group_add_'))
            self.assertNotEqual(row['model_id/id'], 'model_som_add_vault')
        groups = E.parse(str(ROOT / 'security/add_groups.xml'))
        self.assertFalse(groups.xpath('//field[@name="users" or @name="user_ids"]'))
        self.assertNotIn('base.group_user', ''.join(groups.xpath('//field/text()')))

    def test_acl_csv_loader_model_and_group_order(self):
        """Odoo chooses the CSV target model from its basename, not its headers."""
        manifest = ast.literal_eval((ROOT / '__manifest__.py').read_text())
        defined_groups, access_ids = set(), set()
        for name in manifest['data']:
            path = ROOT / name
            if path.suffix == '.xml':
                tree = E.parse(str(path))
                defined_groups.update(tree.xpath('//record[@model="res.groups"]/@id'))
            elif path.suffix == '.csv':
                with path.open() as stream:
                    reader = csv.DictReader(stream)
                    if 'perm_read' not in reader.fieldnames:
                        continue
                    self.assertEqual(path.stem, 'ir.model.access',
                                     'Un CSV de ACL debe cargar el modelo ir.model.access')
                    for row in reader:
                        self.assertNotIn(row['id'], access_ids)
                        access_ids.add(row['id'])
                        group = row['group_id/id']
                        if group.startswith('group_add_'):
                            self.assertIn(group, defined_groups,
                                          'Los grupos ADD deben cargarse antes de sus ACL')

    def test_menu_root_and_required_sections(self):
        views = E.parse(str(ROOT / 'views/add_views.xml'))
        root = views.xpath('//menuitem[@id="menu_add_root"]')[0]
        self.assertEqual(root.get('name'), 'ADD')
        self.assertIsNone(root.get('parent'))
        self.assertTrue(root.get('web_icon'))
        names = set(views.xpath('//menuitem[@parent="menu_add_root"]/@name'))
        self.assertTrue({'Tablero', 'Emitidos', 'Recibidos', 'Conceptos', 'Complementos de pago', 'Relaciones', 'Importaciones', 'Configuración'} <= names)

    def test_no_html_rendering_no_accounting_models(self):
        template = (ROOT / 'static/src/add/explorer.xml').read_text()
        self.assertNotIn('t-raw', template)
        js = (ROOT / 'static/src/add/explorer.js').read_text()
        for token in ('innerHTML', 'eval(', 'new Function', '<iframe'):
            self.assertNotIn(token, js)
        for path in list((ROOT / 'models').glob('add_*.py')) + list((ROOT / 'add_services').glob('*.py')):
            text = path.read_text()
            for model in ('account.move', 'account.payment', 'stock.move', 'sale.order', 'purchase.order', 'res.partner', 'product.product'):
                self.assertNotIn("env['" + model + "']", text, str(path))

    def test_xml_python_and_frontend_rpc_names(self):
        methods = set()
        for path in (ROOT / 'models').glob('add_*.py'):
            parsed = ast.parse(path.read_text(), filename=str(path))
            methods.update(n.name for n in ast.walk(parsed) if isinstance(n, ast.FunctionDef))
        for path in ROOT.rglob('*.xml'):
            E.parse(str(path))
        js = (ROOT / 'static/src/add/explorer.js').read_text()
        for method in re.findall(r'orm\.call\("som\.add\.[^"]+", "([^"]+)"', js):
            self.assertIn(method, methods)


if __name__ == '__main__':
    unittest.main()
