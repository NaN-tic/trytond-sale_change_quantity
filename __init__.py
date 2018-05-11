# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond.pool import Pool
from . import sale


def register():
    Pool.register(
        sale.Sale,
        sale.SaleLine,
        sale.ChangeLineQuantityStart,
        module='sale_change_quantity', type_='model')
    Pool.register(
        sale.ChangeLineQuantity,
        module='sale_change_quantity', type_='wizard')
