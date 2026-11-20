"""Food inventory operations. The caller owns the database transaction."""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
import re

import pyodbc


UNITS = ('g', 'mL', '개')
STORAGE = ('실온', '냉장', '냉동')
MAX_INTEGER = 2_147_483_647
MAX_QUANTITY = Decimal('9999999.999')
QUANTITY_STEP = Decimal('0.001')


class ValidationError(ValueError):
    """An input or business rule prevented the requested operation."""


def _text(value, name, limit, *, optional=False):
    value = (value or '').strip()
    if not value and optional:
        return None
    if not value or len(value) > limit or '\0' in value:
        raise ValidationError(f'{name}은(는) 1~{limit}자로 입력해야 합니다.')
    return value


def _positive(value, name):
    if type(value) is not int or not 1 <= value <= MAX_INTEGER:
        raise ValidationError(f'{name}은(는) 1~{MAX_INTEGER:,} 사이의 정수여야 합니다.')
    return value


def _quantity(value, *, allow_zero):
    try:
        quantity = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValidationError('수량은 숫자로 입력해야 합니다.') from error
    minimum = Decimal(0) if allow_zero else QUANTITY_STEP
    if not quantity.is_finite() or not minimum <= quantity <= MAX_QUANTITY:
        raise ValidationError(f'수량은 {minimum}~{MAX_QUANTITY} 사이여야 합니다.')
    if quantity != quantity.quantize(QUANTITY_STEP):
        raise ValidationError('수량은 소수 셋째 자리까지 입력할 수 있습니다.')
    return quantity.quantize(QUANTITY_STEP)


def _next_id(cursor, sequence):
    # The sequence identifier is fixed by the application, and no user input enters this SQL.
    return int(cursor.execute(f'SELECT {sequence}.NEXTVAL FROM dual').fetchval())


def _recipe_items(items):
    result = {}
    for ingredient_id, quantity in items:
        _positive(ingredient_id, '식재료번호')
        if ingredient_id in result:
            raise ValidationError('한 레시피에 같은 식재료를 두 번 넣을 수 없습니다.')
        result[ingredient_id] = _quantity(quantity, allow_zero=False)
    if not result:
        raise ValidationError('레시피에는 식재료가 한 개 이상 필요합니다.')
    return result


@dataclass
class IngredientCheck:
    name: str
    unit: str
    required: Decimal
    available: Decimal
    shortage: Decimal
    unknown_batches: list[tuple[int, Decimal]] = field(default_factory=list)


@dataclass
class RecipeCheck:
    recipe_id: int
    name: str
    ingredients: dict[int, IngredientCheck] = field(default_factory=dict)

    @property
    def cookable(self):
        return bool(self.ingredients) and all(item.shortage == 0 for item in self.ingredients.values())


class Pantry:
    def __init__(self, connection):
        self.connection = connection

    def add_ingredient(self, name, category, unit):
        name = _text(name, '식재료명', 50)
        category = _text(category, '분류', 20)
        if unit not in UNITS:
            raise ValidationError('기준 단위는 g, mL, 개 중 하나여야 합니다.')
        cursor = self.connection.cursor()
        ingredient_id = _next_id(cursor, 'ingredient_seq')
        try:
            cursor.execute(
                'INSERT INTO ingredient (ingredient_id, name, category, unit) VALUES (?, ?, ?, ?)',
                ingredient_id, name, category, unit,
            )
        except pyodbc.IntegrityError as error:
            if re.search(r'ORA-00001:.*\bUQ_INGREDIENT_NAME\b', str(error), flags=re.IGNORECASE):
                raise ValidationError('같은 이름의 식재료가 이미 등록되어 있습니다.') from error
            raise
        return ingredient_id

    def ingredients(self, name='', category=''):
        cursor = self.connection.cursor()
        sql = 'SELECT ingredient_id, name, category, unit FROM ingredient WHERE 1 = 1'
        params = []
        if name:
            sql += ' AND INSTR(LOWER(name), LOWER(?)) > 0'
            params.append(name)
        if category:
            sql += ' AND category = ?'
            params.append(category)
        return cursor.execute(sql + ' ORDER BY name, ingredient_id', params).fetchall()

    def _lock_ingredient(self, cursor, ingredient_id):
        _positive(ingredient_id, '식재료번호')
        row = cursor.execute(
            'SELECT unit FROM ingredient WHERE ingredient_id = ? FOR UPDATE', ingredient_id,
        ).fetchone()
        if row is None:
            raise ValidationError(f'식재료 {ingredient_id}번을 찾을 수 없습니다.')
        return row[0]

    def change_unit(self, ingredient_id, unit):
        if unit not in UNITS:
            raise ValidationError('기준 단위는 g, mL, 개 중 하나여야 합니다.')
        cursor = self.connection.cursor()
        if self._lock_ingredient(cursor, ingredient_id) == unit:
            return
        referenced = cursor.execute(
            '''SELECT 1 FROM dual WHERE EXISTS (SELECT 1 FROM inventory WHERE ingredient_id = ?)
                   OR EXISTS (SELECT 1 FROM recipe_ingredient WHERE ingredient_id = ?)''',
            ingredient_id, ingredient_id,
        ).fetchone()
        if referenced:
            raise ValidationError('재고나 레시피에서 사용 중인 식재료의 단위는 변경할 수 없습니다.')
        cursor.execute('UPDATE ingredient SET unit = ? WHERE ingredient_id = ?', unit, ingredient_id)

    def add_inventory(self, ingredient_id, quantity, storage, expiry=None, *, registered_on=None):
        quantity = _quantity(quantity, allow_zero=True)
        if storage not in STORAGE:
            raise ValidationError('보관 장소는 실온, 냉장, 냉동 중 하나여야 합니다.')
        cursor = self.connection.cursor()
        self._lock_ingredient(cursor, ingredient_id)
        inventory_id = _next_id(cursor, 'inventory_seq')
        cursor.execute(
            '''INSERT INTO inventory (inventory_id, ingredient_id, registered_on, remaining_qty, storage, expiry_date)
               VALUES (?, ?, ?, ?, ?, ?)''',
            inventory_id, ingredient_id, registered_on or date.today(), quantity, storage, expiry,
        )
        return inventory_id

    def update_quantity(self, inventory_id, quantity):
        _positive(inventory_id, '보유번호')
        quantity = _quantity(quantity, allow_zero=True)
        cursor = self.connection.cursor()
        cursor.execute('UPDATE inventory SET remaining_qty = ? WHERE inventory_id = ?', quantity, inventory_id)
        if not cursor.rowcount:
            raise ValidationError(f'보유 식재료 {inventory_id}번을 찾을 수 없습니다.')

    def inventory(self, *, expiring=False, as_of=None):
        cursor = self.connection.cursor()
        sql = '''SELECT s.inventory_id, i.name, s.remaining_qty, i.unit, s.storage, s.registered_on, s.expiry_date
                 FROM inventory s JOIN ingredient i ON i.ingredient_id = s.ingredient_id
                 WHERE s.remaining_qty > 0'''
        params = []
        if expiring:
            today = as_of or date.today()
            sql += ' AND s.expiry_date BETWEEN ? AND ?'
            params = [today, today + timedelta(days=3)]
        return cursor.execute(sql + ' ORDER BY s.expiry_date NULLS LAST, s.inventory_id', params).fetchall()

    def _lock_recipe(self, cursor, recipe_id):
        _positive(recipe_id, '레시피번호')
        row = cursor.execute(
            '''SELECT name, base_servings, cooking_minutes, DBMS_LOB.GETLENGTH(instructions)
               FROM recipe WHERE recipe_id = ? FOR UPDATE''', recipe_id,
        ).fetchone()
        if row is None:
            raise ValidationError(f'레시피 {recipe_id}번을 찾을 수 없습니다.')
        return row

    def save_recipe(self, name, base_servings, instructions, cooking_minutes, items, *, recipe_id=None):
        name = _text(name, '요리명', 80)
        instructions = _text(instructions, '조리 방법', 2000)
        _positive(base_servings, '기준 인분')
        _positive(cooking_minutes, '조리 시간')
        items = _recipe_items(items)
        cursor = self.connection.cursor()
        if recipe_id is not None:
            self._lock_recipe(cursor, recipe_id)
        # Take the same parent locks as change_unit, in a consistent order.
        for ingredient_id in sorted(items):
            self._lock_ingredient(cursor, ingredient_id)
        if recipe_id is None:
            recipe_id = _next_id(cursor, 'recipe_seq')
            cursor.execute(
                '''INSERT INTO recipe (name, base_servings, instructions, cooking_minutes, recipe_id)
                   VALUES (?, ?, ?, ?, ?)''', name, base_servings, instructions, cooking_minutes, recipe_id,
            )
        else:
            cursor.execute(
                '''UPDATE recipe SET name = ?, base_servings = ?, instructions = ?, cooking_minutes = ?
                   WHERE recipe_id = ?''', name, base_servings, instructions, cooking_minutes, recipe_id,
            )
            cursor.execute('DELETE FROM recipe_ingredient WHERE recipe_id = ?', recipe_id)
        cursor.executemany(
            'INSERT INTO recipe_ingredient (recipe_id, ingredient_id, required_qty) VALUES (?, ?, ?)',
            [(recipe_id, ingredient_id, quantity) for ingredient_id, quantity in items.items()],
        )
        return recipe_id

    def recipes(self):
        return self.connection.cursor().execute(
            'SELECT recipe_id, name, base_servings, cooking_minutes FROM recipe ORDER BY recipe_id',
        ).fetchall()

    def recipe_detail(self, recipe_id):
        cursor = self.connection.cursor()
        name, base_servings, cooking_minutes, length = self._lock_recipe(cursor, recipe_id)
        # The XE 10g ODBC driver truncates some Unicode CLOBs on a direct fetch.
        # DBMS_LOB offsets are UTF-16 code units, including both halves of a surrogate pair.
        chunks = []
        offset = 1
        while offset <= length:
            chunk = cursor.execute(
                'SELECT DBMS_LOB.SUBSTR(instructions, 500, ?) FROM recipe WHERE recipe_id = ?',
                offset, recipe_id,
            ).fetchval()
            if not chunk:
                raise ValidationError('Oracle에 저장된 조리 방법을 끝까지 읽을 수 없습니다.')
            chunks.append(chunk)
            offset += len(chunk.encode('utf-16-le')) // 2
        items = cursor.execute(
            '''SELECT i.ingredient_id, i.name, ri.required_qty, i.unit FROM recipe_ingredient ri
               JOIN ingredient i ON i.ingredient_id = ri.ingredient_id
               WHERE ri.recipe_id = ? ORDER BY i.ingredient_id''', recipe_id,
        ).fetchall()
        return name, int(base_servings), int(cooking_minutes), ''.join(chunks), items

    def check_recipes(self, servings, *, recipe_id=None, as_of=None):
        _positive(servings, '목표 인분')
        if recipe_id is not None:
            _positive(recipe_id, '레시피번호')
        sql = '''WITH eligible AS (
                     SELECT inventory_id, ingredient_id, remaining_qty, expiry_date
                     FROM inventory WHERE remaining_qty > 0 AND (expiry_date >= ? OR expiry_date IS NULL)
                 ), totals AS (
                     SELECT ingredient_id, SUM(remaining_qty) AS available_qty FROM eligible GROUP BY ingredient_id
                 )
                 SELECT r.recipe_id, r.name, r.base_servings, i.ingredient_id, i.name, i.unit, ri.required_qty,
                        CAST(NVL(t.available_qty, 0) AS NUMBER(28,3)), u.inventory_id, u.remaining_qty
                 FROM recipe r JOIN recipe_ingredient ri ON ri.recipe_id = r.recipe_id
                 JOIN ingredient i ON i.ingredient_id = ri.ingredient_id
                 LEFT JOIN totals t ON t.ingredient_id = i.ingredient_id
                 LEFT JOIN eligible u ON u.ingredient_id = i.ingredient_id AND u.expiry_date IS NULL'''
        params = [as_of or date.today()]
        if recipe_id is not None:
            sql += ' WHERE r.recipe_id = ?'
            params.append(recipe_id)
        cursor = self.connection.cursor()
        rows = cursor.execute(sql + ' ORDER BY r.recipe_id, i.ingredient_id, u.inventory_id', params)
        results = {}
        for row in rows:
            rid, name, base, iid, ingredient, unit, base_quantity, available, batch, batch_quantity = row
            rid, iid = int(rid), int(iid)
            if rid not in results:
                results[rid] = RecipeCheck(rid, name)
            ingredients = results[rid].ingredients
            if iid not in ingredients:
                # Compare the scaled quantities before dividing, so a repeating decimal
                # cannot change whether the stock is sufficient.
                scaled = base_quantity * servings
                short_scaled = max(Decimal(0), scaled - available * base)
                ingredients[iid] = IngredientCheck(ingredient, unit, scaled / base, available, short_scaled / base)
            if batch is not None:
                ingredients[iid].unknown_batches.append((int(batch), batch_quantity))
        if recipe_id is not None and recipe_id not in results:
            raise ValidationError(f'레시피 {recipe_id}번을 찾을 수 없습니다.')
        return list(results.values())

    def add_cook_log(self, recipe_id, cooked_at, servings, memo=None):
        _positive(recipe_id, '레시피번호')
        _positive(servings, '조리 인분')
        memo = _text(memo, '메모', 300, optional=True)
        cursor = self.connection.cursor()
        if not cursor.execute('SELECT 1 FROM recipe WHERE recipe_id = ?', recipe_id).fetchone():
            raise ValidationError(f'레시피 {recipe_id}번을 찾을 수 없습니다.')
        log_id = _next_id(cursor, 'cook_log_seq')
        cursor.execute(
            'INSERT INTO cook_log (log_id, recipe_id, cooked_at, servings, memo) VALUES (?, ?, ?, ?, ?)',
            log_id, recipe_id, cooked_at.replace(microsecond=0), servings, memo,
        )
        return log_id

    def cook_logs(self, *, start=None, end=None, recipe_id=None):
        if start and end and start > end:
            raise ValidationError('시작일은 종료일 이전이거나 같아야 합니다.')
        params = []
        sql = '''SELECT l.log_id, l.recipe_id, r.name, l.cooked_at, l.servings, l.memo
                 FROM cook_log l JOIN recipe r ON r.recipe_id = l.recipe_id WHERE 1 = 1'''
        if start:
            sql += ' AND l.cooked_at >= ?'
            params.append(datetime.combine(start, time.min))
        if end:
            sql += ' AND l.cooked_at <= ?'
            params.append(datetime.combine(end, time(23, 59, 59)))
        if recipe_id is not None:
            _positive(recipe_id, '레시피번호')
            sql += ' AND l.recipe_id = ?'
            params.append(recipe_id)
        return self.connection.cursor().execute(sql + ' ORDER BY l.cooked_at DESC, l.log_id DESC', params).fetchall()
