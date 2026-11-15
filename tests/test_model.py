import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('model', Path(__file__).parents[1] / 'custom_components/household_shopping/model.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class ReceiptTests(unittest.TestCase):
    def test_existing_list_preserves_uid_store_quantity_requester(self):
        item = m.parse_item({'uid':'original', 'summary':'[Albert Heijn] 2x Milk (Alex)'})
        self.assertEqual((item['store'],item['quantity'],item['item'],item['requester'],item['uid']), ('Albert Heijn',2,'Milk','Alex','original'))
        self.assertIsNone(m.parse_item({'uid':'unassigned','summary':'Milk'}))

    def test_people_stay_separate_when_item_is_grouped(self):
        items=[m.parse_item({'uid':'a','summary':'[Action] 2x Tape (Alex)'}),m.parse_item({'uid':'b','summary':'[Action] 3x tape (Sam)'})]
        self.assertEqual(m.receipt_lines(items),'[ ] 5x Tape (2 Alex, 3 Sam)')

    def test_loyalty_formats(self):
        self.assertEqual(m.loyalty_block('_abc')['data'],'abc')
        self.assertEqual(m.loyalty_block('qr:abc')['type'],'qr')
        self.assertEqual(m.loyalty_block('123456789012')['code_type'],'ean13')
        self.assertEqual(m.loyalty_block('AB123')['code_type'],'code128')
        self.assertEqual(m.loyalty_block('image:cards/ah.png')['image_path'],'cards/ah.png')
        self.assertIsNone(m.loyalty_block('None'))

    def test_currency_avoids_float_rounding_and_invalid_amounts(self):
        self.assertEqual(m.cents('12,34'),1234)
        self.assertIsNone(m.cents(''))
        for value in ('NaN','Infinity','-1','1.001'):
            with self.assertRaises(ValueError):m.cents(value)

if __name__=='__main__':unittest.main()
