# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond import backend
from trytond.model import ModelView, fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval
from trytond.transaction import Transaction
from trytond.wizard import Button, StateTransition, StateView, Wizard
from trytond.i18n import gettext
from trytond.exceptions import UserError

__all__ = ['Sale', 'SaleLine', 'ChangeLineQuantityStart', 'ChangeLineQuantity']


class Sale(metaclass=PoolMeta):
    __name__ = 'sale.sale'

    @classmethod
    def confirm(cls, sales):
        pool = Pool()
        SaleLine = pool.get('sale.line')

        to_write = []
        for sale in sales:
            for line in sale.lines:
                if line.type == 'line':
                    to_write += [[line], {
                            'confirmed_quantity': line.quantity,
                            }]
        if to_write:
            SaleLine.write(*to_write)
        return super(Sale, cls).confirm(sales)


class SaleLine(metaclass=PoolMeta):
    __name__ = 'sale.line'

    confirmed_quantity = fields.Float('Confirmed Quantity',
        digits=(16, Eval('unit_digits', 2)), readonly=True,
        states={
            'invisible': ((Eval('type') != 'line')
                | (Eval('quantity') == Eval('confirmed_quantity'))
                | ~Eval('_parent_sale', {}).get('state',
                    '').in_(['confirmed', 'processing', 'done'])),
            },
        depends=['type', 'quantity', 'unit_digits'])

    @classmethod
    def __register__(cls, module_name):
        pool = Pool()
        Sale = pool.get('sale.sale')
        TableHandler = backend.get('TableHandler')
        cursor = Transaction().connection.cursor()
        sql_table = cls.__table__()
        sale = Sale.__table__()

        table = TableHandler(cls, module_name)
        copy_qty = not table.column_exist('confirmed_quantity')

        super(SaleLine, cls).__register__(module_name)

        # Initialize data on module installation
        if copy_qty:
            # Don't use update from as SQLite doesn't support it
            query = sql_table.update(
                    columns=[sql_table.confirmed_quantity],
                    values=[sql_table.quantity],
                    where=(sql_table.sale.in_(sale.select(sale.id,
                            where=sale.state.in_(
                                ['confirmed', 'processing', 'done']))))
                    )
            cursor.execute(*query)

    @classmethod
    def copy(cls, lines, default=None):
        if default is None:
            default = {}
        else:
            default = default.copy()
        default['confirmed_quantity'] = None
        return super(SaleLine, cls).copy(lines, default=default)


class ChangeLineQuantityStart(ModelView):
    'Change Line Quantity - Start'
    __name__ = 'sale.change_line_quantity.start'

    sale = fields.Many2One('sale.sale', 'Sale', readonly=True)
    line = fields.Many2One('sale.line', 'Sale Line', required=True,
        domain=[
            ('sale', '=', Eval('sale')),
            # ('invoice_lines.invoice.state', '=', 'draft'),
            # ('moves.state', '=', 'draft'),
            ],
        depends=['sale'])
    current_quantity = fields.Float('Current Quantity',
        digits=(16, Eval('unit_digits', 2)), readonly=True,
        depends=['unit_digits'])
    new_quantity = fields.Float('New Quantity',
        digits=(16, Eval('unit_digits', 2)), required=True,
        domain=[
            ('new_quantity', '!=', Eval('current_quantity')),
            ('new_quantity', '>=', Eval('minimal_quantity')),
            ],
        depends=['unit_digits', 'current_quantity', 'minimal_quantity'])
    minimal_quantity = fields.Float('Minimal Quantity',
        digits=(16, Eval('unit_digits', 2)), readonly=True,
        depends=['unit_digits'])
    unit = fields.Many2One('product.uom', 'Unit', readonly=True)
    unit_digits = fields.Integer('Unit Digits', readonly=True)

    @fields.depends('line')
    def on_change_with_current_quantity(self):
        return self.line.quantity if self.line else None

    @fields.depends('line')
    def on_change_with_minimal_quantity(self):
        pool = Pool()
        Uom = pool.get('product.uom')

        invoiced_quantity = 0
        for iline in (self.line.invoice_lines if self.line else []):
            if (iline.invoice.state in ('validated', 'posted', 'paid')
                    or (iline.invoice.state == 'cancel'
                        and iline.invoice.sale_exception_state == 'ignored')):
                invoiced_quantity += Uom.compute_qty(iline.unit,
                    iline.quantity, self.line.unit)

        shipped_quantity = 0
        for move in (self.line.moves if self.line else []):
            if (move.state in ('assigned', 'done')
                    or (move.state == 'cancel'
                        and move.sale_exception_state == 'ignored')):
                shipped_quantity += Uom.compute_qty(move.uom, move.quantity,
                    self.line.unit)

        return max(invoiced_quantity, shipped_quantity)

    @fields.depends('line')
    def on_change_with_unit(self):
        if self.line:
            return self.line.unit.id

    @fields.depends('line')
    def on_change_with_unit_digits(self):
        if self.line:
            return self.line.unit.digits


class ChangeLineQuantity(Wizard):
    'Change Line Quantity'
    __name__ = 'sale.change_line_quantity'

    start = StateView('sale.change_line_quantity.start',
        'sale_change_quantity.change_line_quantity_start_view_form', [
            Button('Cancel', 'end', 'tryton-cancel'),
            Button('Modify', 'modify', 'tryton-ok', default=True),
            ])
    modify = StateTransition()

    def default_start(self, fields):
        Sale = Pool().get('sale.sale')
        sale = Sale(Transaction().context['active_id'])
        if (sale.state not in ('confirmed', 'processing')
                or sale.invoice_state not in ('none', 'waiting')
                or sale.shipment_state not in ('none', 'waiting')):
            raise UserError(gettext('sale_change_quantity.invalid_sale_state',
                    sale=sale.rec_name))
        return {
            'sale': sale.id,
            }

    def transition_modify(self):
        pool = Pool()
        Sale = pool.get('sale.sale')

        line = self.start.line

        if line.quantity == self.start.new_quantity:
            return

        line.quantity = self.start.new_quantity
        line.save()

        if line.sale.state == 'processing':
            self.update_move()
            self.update_invoice_line()

            Sale.process([self.start.line.sale])
        return 'end'

    def update_move(self):
        pool = Pool()
        Uom = pool.get('product.uom')
        Move = pool.get('stock.move')
        ShipmentOut = pool.get('stock.shipment.out')
        ShipmentOutReturn = pool.get('stock.shipment.out.return')

        line = self.start.line
        quantity = self.start.new_quantity

        for move in line.moves:
            if (move.state in ('assigned', 'done')
                    or (move.state == 'cancel'
                        and move.sale_exception_state == 'ignored')):
                quantity -= Uom.compute_qty(move.uom, move.quantity, line.unit)
        if quantity < 0:
            raise UserError(
                gettext('sale_change_quantity.quantity_already_delivered'))

        if line.sale.shipment_method != 'order':
            return

        updateable_moves = self.get_updateable_moves()
        shipments_out = list(set([m.shipment.id for m in updateable_moves
                    if isinstance(m.shipment, ShipmentOut)]))
        shipments_out_return = list(set([m.shipment.id
                    for m in updateable_moves
                    if isinstance(m.shipment, ShipmentOutReturn)]))

        if updateable_moves and quantity < line.unit.rounding:
            Move.delete(updateable_moves)
        else:
            move = updateable_moves.pop(0)
            move.quantity = Uom.compute_qty(line.unit, quantity, move.uom)
            move.save()
            if updateable_moves:
                Move.delete(updateable_moves)
                # Reload move because its shipment moves has changed
                move = Move(move.id)
            if isinstance(move.shipment, ShipmentOut):
                ShipmentOut.wait([move.shipment])

        if shipments_out:
            shipments_out = [s for s in ShipmentOut.browse(shipments_out)
                if not s.outgoing_moves]
            if shipments_out:
                ShipmentOut.delete(shipments_out)
        if shipments_out_return:
            shipments_out_return = [s
                for s in ShipmentOutReturn.browse(shipments_out)
                if not s.incoming_moves]
            if shipments_out_return:
                ShipmentOutReturn.delete(shipments_out_return)

    def get_updateable_moves(self):
        moves = sorted(
            [m for m in self.start.line.moves if m.state == 'draft'],
            key=self._move_key)
        if not moves:
            raise UserError(gettext('sale_change_quantity.no_updateable_move'))
        return moves

    def _move_key(self, move):
        return -move.quantity

    def update_invoice_line(self):
        pool = Pool()
        Invoice = pool.get('account.invoice')
        InvoiceLine = pool.get('account.invoice.line')
        Uom = pool.get('product.uom')

        line = self.start.line
        quantity = self.start.new_quantity

        for iline in line.invoice_lines:
            if iline.invoice and iline.invoice.state != 'draft':
                quantity -= Uom.compute_qty(iline.unit, iline.quantity,
                    line.unit)
        if quantity < 0:
            raise UserError(
                gettext('sale_change_quantity.quantity_already_invoiced'))

        if line.sale.invoice_method != 'order':
            return

        updateable_lines = self.get_updateable_invoice_lines()
        invoices = list(set(l.invoice.id for l in updateable_lines
            if l.invoice))

        if quantity >= line.unit.rounding:
            invoice_line = updateable_lines.pop(0)
            invoice_line.quantity = Uom.compute_qty(line.unit, quantity,
                invoice_line.unit)
            invoice_line.save()
        if updateable_lines:
            with Transaction().set_context(
                    allow_remove_sale_invoice_lines=True):
                InvoiceLine.delete(updateable_lines)

        if invoices:
            invoices = [i for i in Invoice.browse(invoices) if i.lines]
            if invoices:
                with Transaction().set_context(
                        allow_remove_sale_invoice_lines=True):
                    Invoice.delete(invoices)

    def get_updateable_invoice_lines(self):
        invoice_lines = sorted(
            [l for l in self.start.line.invoice_lines
                if not l.invoice or l.invoice.state == 'draft'],
            key=self._invoice_line_key)
        if not invoice_lines:
            raise UserError(gettext('sale_change_quantity.no_updateable_line'))
        return invoice_lines

    def _invoice_line_key(self, invoice_line):
        return invoice_line.id
