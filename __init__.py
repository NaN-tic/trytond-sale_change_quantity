# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond.pool import Pool
from .sale import *


def register():
    Pool.register(
        Sale,
        SaleLine,
        ChangeLineQuantityStart,
        module='sale_change_quantity', type_='model')
    Pool.register(
        ChangeLineQuantity,
        module='sale_change_quantity', type_='wizard')
