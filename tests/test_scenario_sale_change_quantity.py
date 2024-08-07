import unittest
from decimal import Decimal

from proteus import Model, Wizard
from trytond.modules.account.tests.tools import (create_chart,
                                                 create_fiscalyear,
                                                 get_accounts)
from trytond.modules.account_invoice.tests.tools import (
    create_payment_term, set_fiscalyear_invoice_sequences)
from trytond.modules.company.tests.tools import create_company, get_company
from trytond.tests.test_tryton import drop_db
from trytond.tests.tools import activate_modules


class Test(unittest.TestCase):

    def setUp(self):
        drop_db()
        super().setUp()

    def tearDown(self):
        drop_db()
        super().tearDown()

    def test(self):

        # Imports

        # Install product_cost_plan Module
        config = activate_modules('sale_change_quantity')

        # Create company
        _ = create_company()
        company = get_company()

        # Create fiscal year
        fiscalyear = set_fiscalyear_invoice_sequences(
            create_fiscalyear(company))
        fiscalyear.click('create_period')

        # Create chart of accounts
        _ = create_chart(company)
        accounts = get_accounts(company)
        revenue = accounts['revenue']
        expense = accounts['expense']

        # Create parties
        Party = Model.get('party.party')
        supplier = Party(name='Supplier')
        supplier.save()
        customer = Party(name='Customer')
        customer.save()

        # Create category
        ProductCategory = Model.get('product.category')
        category = ProductCategory(name='Category')
        category.accounting = True
        category.account_expense = expense
        category.account_revenue = revenue
        category.save()

        # Create product
        ProductUom = Model.get('product.uom')
        unit, = ProductUom.find([('name', '=', 'Unit')])
        ProductTemplate = Model.get('product.template')
        Product = Model.get('product.product')
        product = Product()
        template = ProductTemplate()
        template.name = 'product'
        template.account_category = category
        template.default_uom = unit
        template.type = 'goods'
        template.salable = True
        template.list_price = Decimal('10')
        template.cost_price_method = 'fixed'
        template.save()
        product.template = template
        product.save()
        service = Product()
        template = ProductTemplate()
        template.name = 'service'
        template.default_uom = unit
        template.type = 'service'
        template.salable = True
        template.list_price = Decimal('30')
        template.cost_price_method = 'fixed'
        template.account_category = category
        template.save()
        service.template = template
        service.save()

        # Create payment term
        payment_term = create_payment_term()
        payment_term.save()

        # Create an Inventory
        Inventory = Model.get('stock.inventory')
        InventoryLine = Model.get('stock.inventory.line')
        Location = Model.get('stock.location')
        storage, = Location.find([
            ('code', '=', 'STO'),
        ])
        inventory = Inventory()
        inventory.location = storage
        inventory.save()
        inventory_line = InventoryLine(product=product, inventory=inventory)
        inventory_line.quantity = 100.0
        inventory_line.expected_quantity = 0.0
        inventory.save()
        inventory_line.save()
        Inventory.confirm([inventory.id], config.context)
        self.assertEqual(inventory.state, 'done')

        # Sale 5 products
        Sale = Model.get('sale.sale')
        sale = Sale()
        sale.party = customer
        sale.payment_term = payment_term
        sale.invoice_method = 'order'
        sale_line = sale.lines.new()
        sale_line.product = product
        sale_line.quantity = 5.0
        sale.click('quote')
        sale_line, = sale.lines
        sale_line.confirmed_quantity
        sale.click('confirm')
        sale_line, = sale.lines

        # Decrease quantity before processing
        change = Wizard('sale.change_line_quantity', [sale])
        change.form.line = sale_line
        change.form.new_quantity = 2.0
        change.execute('modify')
        sale.reload()
        sale_line, = sale.lines
        self.assertEqual(sale_line.quantity, 2.0)
        self.assertEqual(sale_line.confirmed_quantity, 5.0)
        sale.click('process')
        self.assertEqual(sale.state, 'processing')

        # Increase quantity and check shipments and invoices are updated
        change = Wizard('sale.change_line_quantity', [sale])
        change.form.line = sale_line
        change.form.new_quantity = 4.0
        change.execute('modify')
        sale.reload()
        sale_line, = sale.lines
        self.assertEqual(sale_line.confirmed_quantity, 5.0)
        self.assertEqual(sale_line.quantity, 4.0)
        shipment, = sale.shipments
        move, = shipment.outgoing_moves
        self.assertEqual(move.quantity, 4.0)
        invoice, = sale.invoices
        invoice_line, = invoice.lines
        self.assertEqual(invoice_line.quantity, 4.0)

        # Partially process the shipment
        for move in shipment.inventory_moves:

            move.quantity = 3.0
        shipment.click('assign_try')
        shipment.click('pick')
        shipment.click('pack')
        shipment.click('done')
        self.assertEqual(shipment.state, 'done')
        sale.reload()
        self.assertEqual(len(sale.shipments), 2)

        # Increase the quantity to 6 and check shipment and invoice are updated
        change = Wizard('sale.change_line_quantity', [sale])
        change.form.line = sale_line
        change.form.new_quantity = 6.0
        change.execute('modify')
        sale.reload()
        sale_line, = sale.lines
        self.assertEqual(sale_line.confirmed_quantity, 5.0)
        self.assertEqual(sale_line.quantity, 6.0)
        _, shipment, = sale.shipments
        move, = shipment.outgoing_moves
        self.assertEqual(move.quantity, 3.0)
        self.assertEqual(move.state, 'draft')
        invoice, = sale.invoices
        invoice_line, = invoice.lines
        self.assertEqual(invoice_line.quantity, 6.0)
