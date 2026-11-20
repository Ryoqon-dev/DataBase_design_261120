from datetime import date, datetime, timedelta
from decimal import Decimal
import unittest
from uuid import uuid4

import pyodbc

from db import connect, transaction
from pantry import Pantry, ValidationError


class OracleIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.connection = connect()
        self.addCleanup(self.connection.close)
        self.addCleanup(self.connection.rollback)
        self.app = Pantry(self.connection)
        self.label = uuid4().hex[:10]
        self.today = date(2026, 11, 20)

    def ingredient(self, name='김치', unit='g', category='채소'):
        return self.app.add_ingredient(f'{name}-{self.label}', category, unit)

    def recipe(self, items, *, base=1, name='목살 김치볶음밥', instructions='재료를 볶는다.', recipe_id=None):
        return self.app.save_recipe(name, base, instructions, 15, items, recipe_id=recipe_id)

    def stock(self, ingredient_id, qty, *, days=None):
        expiry = None if days is None else self.today + timedelta(days=days)
        return self.app.add_inventory(ingredient_id, qty, '냉장', expiry, registered_on=self.today)

    def test_ingredient_search_category_and_duplicate(self):
        iid = self.ingredient()
        rows = self.app.ingredients(f'치-{self.label}', '채소')
        self.assertEqual([tuple(row) for row in rows], [(iid, f'김치-{self.label}', '채소', 'g')])
        self.assertEqual(self.app.ingredients(self.label, '육류'), [])
        with self.assertRaisesRegex(ValidationError, '이미 등록'):
            self.ingredient()
        for bad_unit in ('kg', 'ml'):
            with self.assertRaises(ValidationError):
                self.app.change_unit(iid, bad_unit)

    def test_unit_changes_only_for_unreferenced_ingredient(self):
        unused = self.ingredient('계란')
        in_stock = self.ingredient('김치')
        in_recipe = self.ingredient('마늘')
        self.app.change_unit(unused, '개')
        self.assertEqual(self.app.ingredients(f'계란-{self.label}')[0][3], '개')
        self.stock(in_stock, 0)
        self.recipe([(in_recipe, 3)])
        for ingredient in (in_stock, in_recipe):
            with self.assertRaisesRegex(ValidationError, '사용 중'):
                self.app.change_unit(ingredient, 'mL')

    def test_stock_belongs_to_batches_and_round_trips_exact_quantity(self):
        iid = self.ingredient()
        first = self.stock(iid, '1234567.123', days=3)
        second = self.stock(iid, 20)
        lots = {int(row[0]): row for row in self.app.inventory() if row[0] in (first, second)}
        self.assertEqual(lots[first][2], Decimal('1234567.123'))
        self.assertEqual(lots[first][5].date(), self.today)
        self.assertEqual(lots[second][2], 20)
        self.assertIsNone(lots[second][6])
        self.app.update_quantity(first, 0)
        self.assertEqual([row[0] for row in self.app.inventory() if row[0] in (first, second)], [second])

    def test_expiring_inventory_includes_today_and_day_three(self):
        iid = self.ingredient()
        ids = {self.stock(iid, 1, days=day): day for day in (-1, 0, 3, 4, None)}
        ids[self.stock(iid, 0, days=1)] = 1
        current = [ids[row[0]] for row in self.app.inventory(as_of=self.today, expiring=True) if row[0] in ids]
        self.assertEqual(current, [0, 3])

    def test_invalid_quantity_and_storage(self):
        iid = self.ingredient()
        for value in ('NaN', -1, '1.0001', '10000000'):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.stock(iid, value)
        with self.assertRaises(ValidationError):
            self.app.add_inventory(iid, 1, '냉장고')

    def test_recipe_creation_edit_and_unicode_instructions(self):
        kimchi = self.ingredient()
        rice = self.ingredient('밥')
        instructions = '가' * 499 + '🥑' + '나' * 1500
        rid = self.recipe([(kimchi, '100.125'), (rice, 200)], instructions=instructions)
        name, servings, minutes, returned, items = self.app.recipe_detail(rid)
        self.assertEqual((name, servings, minutes, returned), ('목살 김치볶음밥', 1, 15, instructions))
        self.assertEqual({row[0]: row[2] for row in items}, {kimchi: Decimal('100.125'), rice: Decimal(200)})
        self.recipe([(rice, 10)], name='밥 요리', base=2, instructions='🥑' * 2000, recipe_id=rid)
        edited = self.app.recipe_detail(rid)
        self.assertEqual(edited[:4], ('밥 요리', 2, 15, '🥑' * 2000))
        self.assertEqual([(row[0], row[2]) for row in edited[4]], [(rice, 10)])

    def test_empty_and_duplicate_recipe_items_are_rejected(self):
        iid = self.ingredient()
        rid = self.recipe([(iid, 1)])
        for items in ([], [(iid, 1), (iid, 2)], [(iid, 0)], [(iid, '0.0001')]):
            with self.subTest(items=items), self.assertRaises(ValidationError):
                self.recipe(items, recipe_id=rid)
        self.assertEqual(self.app.recipe_detail(rid)[4][0][2], 1)
        with self.assertRaises(ValidationError):
            self.recipe([(iid, 1)], base=0)

    def test_ppt_forecast_and_unknown_batches(self):
        kimchi = self.ingredient('김치')
        pork = self.ingredient('목살', category='육류')
        rice = self.ingredient('밥', category='곡류')
        self.stock(kimchi, 40, days=0)
        unknown_a = self.stock(kimchi, 12)
        unknown_b = self.stock(kimchi, 8)
        self.stock(kimchi, 50, days=-1)
        self.stock(kimchi, 0)
        self.stock(pork, 150, days=1)
        self.stock(rice, 200, days=7)
        rid = self.recipe([(kimchi, 100), (pork, 120), (rice, 200)])
        [one] = self.app.check_recipes(1, recipe_id=rid, as_of=self.today)
        [two] = self.app.check_recipes(2, recipe_id=rid, as_of=self.today)
        self.assertFalse(one.cookable)
        self.assertEqual(one.ingredients[kimchi].available, 60)
        self.assertEqual(one.ingredients[kimchi].shortage, 40)
        self.assertEqual(one.ingredients[kimchi].unknown_batches, [(unknown_a, 12), (unknown_b, 8)])
        self.assertEqual(one.ingredients[pork].shortage, 0)
        self.assertEqual(one.ingredients[rice].shortage, 0)
        self.assertEqual(two.ingredients[kimchi].shortage, 140)
        self.stock(kimchi, 40, days=5)
        cookable = {row.recipe_id for row in self.app.check_recipes(1, as_of=self.today) if row.cookable}
        self.assertIn(rid, cookable)

    def test_fractional_scale_and_no_stock(self):
        iid = self.ingredient()
        rid = self.recipe([(iid, 1)], base=3)
        [check] = self.app.check_recipes(1, recipe_id=rid, as_of=self.today)
        self.assertEqual(check.ingredients[iid].available, 0)
        self.assertEqual(check.ingredients[iid].shortage, Decimal(1) / 3)
        lot = self.stock(iid, '0.333')
        [check] = self.app.check_recipes(1, recipe_id=rid, as_of=self.today)
        self.assertFalse(check.cookable)
        self.app.update_quantity(lot, '0.334')
        [check] = self.app.check_recipes(1, recipe_id=rid, as_of=self.today)
        self.assertTrue(check.cookable)

    def test_history_filters_by_inclusive_dates_and_recipe_without_changing_stock(self):
        iid = self.ingredient()
        lot = self.stock(iid, 100)
        first_recipe = self.recipe([(iid, 1)])
        other_recipe = self.recipe([(iid, 1)], name='다른 요리')
        morning = datetime.combine(self.today, datetime.min.time())
        night = morning.replace(hour=23, minute=59, second=59)
        earliest = self.app.add_cook_log(first_recipe, morning, 2)
        latest = self.app.add_cook_log(first_recipe, night, 1, '맛있게 먹었다.')
        self.app.add_cook_log(other_recipe, morning, 1)
        self.app.add_cook_log(first_recipe, night + timedelta(seconds=1), 1)
        rows = self.app.cook_logs(start=self.today, end=self.today, recipe_id=first_recipe)
        self.assertEqual([(row[0], row[3], row[4], row[5]) for row in rows], [
            (latest, night, 1, '맛있게 먹었다.'), (earliest, morning, 2, None),
        ])
        self.assertEqual(next(row[2] for row in self.app.inventory() if row[0] == lot), 100)
        self.app.update_quantity(lot, 60)
        self.assertEqual(next(row[2] for row in self.app.inventory() if row[0] == lot), 60)
        with self.assertRaises(ValidationError):
            self.app.cook_logs(start=self.today + timedelta(days=1), end=self.today)

    def test_oracle_enforces_foreign_key_quantity_and_composite_key(self):
        iid = self.ingredient()
        rid = self.recipe([(iid, 1)])
        lot = self.stock(iid, 1)
        cursor = self.connection.cursor()
        invalid_sql = [
            ('UPDATE inventory SET ingredient_id = 0 WHERE inventory_id = ?', [lot]),
            ('UPDATE inventory SET remaining_qty = -1 WHERE inventory_id = ?', [lot]),
            ('INSERT INTO recipe_ingredient VALUES (?, ?, 1)', [rid, iid]),
            ('UPDATE recipe_ingredient SET required_qty = 0 WHERE recipe_id = ?', [rid]),
        ]
        for sql, params in invalid_sql:
            with self.subTest(sql=sql), self.assertRaises(pyodbc.IntegrityError):
                cursor.execute(sql, params)

    def test_failed_transaction_leaves_no_partial_recipe_or_ingredient(self):
        name = f'롤백-{self.label}'
        with self.assertRaises(ValidationError), transaction() as connection:
            app = Pantry(connection)
            iid = app.add_ingredient(name, '검증', 'g')
            app.save_recipe(name, 1, '볶기', 1, [(iid, 1)])
            app.save_recipe(name, 1, '볶기', 1, [])
        self.assertEqual(self.app.ingredients(name), [])
        self.assertFalse(any(row[1] == name for row in self.app.recipes()))
