"""Terminal interface for the pantry application."""

import argparse
from datetime import date, datetime
from decimal import Decimal
import re

import pyodbc

from db import SetupError, install_schema, load_demo, transaction
from pantry import Pantry, STORAGE, UNITS, ValidationError


def run(operation, *args, **kwargs):
    with transaction() as connection:
        return operation(Pantry(connection), *args, **kwargs)


def ask(label):
    return input(f'{label} > ').strip()


def number(label, *, optional=False):
    text = ask(label)
    if optional and not text:
        return None
    try:
        return int(text)
    except ValueError as error:
        raise ValidationError('정수로 입력해 주세요.') from error


def day(label):
    text = ask(label)
    if not text:
        return None
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
        raise ValidationError('날짜는 YYYY-MM-DD 형식으로 입력해 주세요.')
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise ValidationError('날짜는 YYYY-MM-DD 형식으로 입력해 주세요.') from error


def cooking_time():
    text = ask('조리 일시 YYYY-MM-DD HH:MM 또는 HH:MM:SS [Enter: 현재]')
    if not text:
        return datetime.now().replace(microsecond=0)
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?', text):
        raise ValidationError('조리 일시는 YYYY-MM-DD HH:MM 또는 HH:MM:SS로 입력해 주세요.')
    try:
        return datetime.fromisoformat(text)
    except ValueError as error:
        raise ValidationError('유효한 조리 날짜와 시간을 입력해 주세요.') from error


def amount(value):
    if 0 < value < Decimal('0.000001'):
        return '<0.000001'
    return f'{value:.6f}'.rstrip('0').rstrip('.')


def display_ingredients(rows):
    if not rows:
        print('조회된 식재료가 없습니다.')
    for iid, name, category, unit in rows:
        print(f'[{iid}] {name} / {category} / {unit}')


def all_ingredients():
    rows = run(Pantry.ingredients)
    if not rows:
        raise ValidationError('식재료를 먼저 등록해 주세요.')
    display_ingredients(rows)


def add_ingredient():
    ingredient_id = run(
        Pantry.add_ingredient, ask('식재료명'), ask('분류'), ask(f'기준 단위 ({", ".join(UNITS)})'),
    )
    print(f'식재료를 등록했습니다. 식재료번호 {ingredient_id}')


def find_ingredients():
    display_ingredients(run(Pantry.ingredients, ask('이름 일부 [Enter: 전체]'), ask('분류 [Enter: 전체]')))


def change_unit():
    all_ingredients()
    run(Pantry.change_unit, number('식재료번호'), ask(f'새 기준 단위 ({", ".join(UNITS)})'))
    print('기준 단위를 저장했습니다.')


def add_inventory():
    all_ingredients()
    inventory_id = run(
        Pantry.add_inventory, number('식재료번호'), ask('남은 양'),
        ask(f'보관 장소 ({", ".join(STORAGE)})'), day('소비기한 YYYY-MM-DD [Enter: 미입력]'),
    )
    print(f'보유 식재료를 등록했습니다. 보유번호 {inventory_id}')


def show_inventory(*, expiring=False):
    rows = run(Pantry.inventory, expiring=expiring)
    if not rows:
        print('조회된 보유 식재료가 없습니다.')
    for iid, name, qty, unit, storage, registered, expiry in rows:
        expiry_label = expiry.date().isoformat() if expiry else '미입력'
        print(f'[{iid}] {name} {amount(qty)}{unit} / {storage} / 등록 {registered.date()} / 소비기한 {expiry_label}')


def update_quantity():
    show_inventory()
    run(Pantry.update_quantity, number('보유번호'), ask('수정 후 남은 양'))
    print('해당 묶음의 남은 양을 저장했습니다.')


def show_expiring():
    show_inventory(expiring=True)


def show_recipes():
    rows = run(Pantry.recipes)
    if not rows:
        print('등록된 레시피가 없습니다.')
    for rid, name, base, minutes in rows:
        print(f'[{rid}] {name} / 기준 {base}인분 / {minutes}분')
    return bool(rows)


def instructions_input():
    print('조리 방법을 입력하고 마지막 줄에 마침표 하나(.)를 입력하세요.')
    lines = []
    while (line := input()) != '.':
        lines.append(line)
    return '\n'.join(lines)


def save_recipe(recipe_id=None):
    all_ingredients()
    name = ask('요리명')
    servings = number('기준 인분')
    minutes = number('조리 시간(분)')
    method = instructions_input()
    items = []
    while (ingredient_id := number('레시피에 넣을 식재료번호 [Enter: 완료]', optional=True)) is not None:
        items.append((ingredient_id, ask('기준 인분에 필요한 양')))
    saved_id = run(Pantry.save_recipe, name, servings, method, minutes, items, recipe_id=recipe_id)
    print(f'레시피를 저장했습니다. 레시피번호 {saved_id}')


def view_recipe(recipe_id=None):
    if recipe_id is None:
        if not show_recipes():
            return
        recipe_id = number('레시피번호')
    name, base, minutes, method, items = run(Pantry.recipe_detail, recipe_id)
    print(f'[{recipe_id}] {name} / 기준 {base}인분 / {minutes}분')
    for ingredient_id, ingredient, qty, unit in items:
        print(f'  [{ingredient_id}] {ingredient} {amount(qty)}{unit}')
    print(method)


def edit_recipe():
    if show_recipes():
        recipe_id = number('수정할 레시피번호')
        view_recipe(recipe_id)
        print('바뀐 레시피와 전체 재료 목록을 입력하세요.')
        save_recipe(recipe_id)


def check_recipe():
    if not show_recipes():
        return
    recipe_id = number('확인할 레시피번호')
    servings = number('목표 인분')
    [result] = run(Pantry.check_recipes, servings, recipe_id=recipe_id)
    status = '조리 가능' if result.cookable else '재료 부족'
    print(f'{result.name} {servings}인분 / {status}')
    for item in result.ingredients.values():
        print(f'  {item.name} / 필요 {amount(item.required)}{item.unit} / '
              f'보유 {amount(item.available)}{item.unit} / 부족 {amount(item.shortage)}{item.unit}')
        for batch, quantity in item.unknown_batches:
            print(f'    보유번호 {batch} {amount(quantity)}{item.unit} / 소비기한 미입력')


def show_cookable():
    servings = number('목표 인분')
    results = [result for result in run(Pantry.check_recipes, servings) if result.cookable]
    if not results:
        print(f'{servings}인분을 만들 수 있는 레시피가 없습니다.')
    for result in results:
        print(f'[{result.recipe_id}] {result.name} / {servings}인분 조리 가능')


def add_cook_log():
    if not show_recipes():
        return
    log_id = run(
        Pantry.add_cook_log, number('레시피번호'), cooking_time(), number('조리 인분'), ask('메모 [Enter: 생략]'),
    )
    print(f'조리 기록을 저장했습니다. 기록번호 {log_id}')
    print('실제로 사용한 양은 메뉴 6에서 보유 묶음별로 반영하세요.')


def show_cook_logs():
    rows = run(
        Pantry.cook_logs, start=day('시작일 YYYY-MM-DD [Enter: 제한 없음]'),
        end=day('종료일 YYYY-MM-DD [Enter: 제한 없음]'), recipe_id=number('레시피번호 [Enter: 전체]', optional=True),
    )
    if not rows:
        print('조회된 조리 기록이 없습니다.')
    for log_id, recipe_id, name, when, servings, memo in rows:
        print(f'[{log_id}] {when:%Y-%m-%d %H:%M:%S} / {name}(레시피 {recipe_id}) / {servings}인분'
              + (f' / {memo}' if memo else ''))


MENU = {
    '1': ('식재료 등록', add_ingredient),
    '2': ('식재료 검색·분류 조회', find_ingredients),
    '3': ('식재료 기준 단위 수정', change_unit),
    '4': ('보유 식재료 등록', add_inventory),
    '5': ('현재 보유 식재료 조회', show_inventory),
    '6': ('보유 식재료 남은 양 수정', update_quantity),
    '7': ('소비기한 임박 조회', show_expiring),
    '8': ('레시피 등록', save_recipe),
    '9': ('레시피 상세 조회', view_recipe),
    '10': ('레시피 수정', edit_recipe),
    '11': ('레시피별 조리 가능 여부·부족량 확인', check_recipe),
    '12': ('조리 가능한 레시피 목록', show_cookable),
    '13': ('조리 기록 저장', add_cook_log),
    '14': ('조리 기록 조회', show_cook_logs),
}


def database_message(error):
    details = '\n'.join(str(arg) for arg in error.args).split('\0', 1)[0]
    code = re.search(r'(?:ORA|TNS)-\d+: [^\r\n]+', details)
    if code:
        return f'Oracle 처리 중 오류가 발생했습니다. {code.group(0)}'
    return 'Oracle에 접속하거나 요청을 처리할 수 없습니다. Oracle XE 서비스와 setup.ps1 실행 상태를 확인하세요.'


def terminal():
    with transaction() as connection:
        connection.cursor().execute('SELECT 1 FROM ingredient WHERE 1 = 0')
    print('자취생 식재료 재고 및 레시피 관리')
    menu = '\n'.join(f'{key:>2}. {label}' for key, (label, _) in MENU.items()) + '\n 0. 종료'
    print(menu)
    while (selection := ask('\n메뉴 번호 (? 목록)')) != '0':
        if selection == '?':
            print(menu)
            continue
        if selection not in MENU:
            print('표시된 메뉴 번호를 입력해 주세요.')
            continue
        try:
            MENU[selection][1]()
        except ValidationError as error:
            print(error)
        except pyodbc.Error as error:
            print(database_message(error))


def main():
    parser = argparse.ArgumentParser(description='자취생 식재료 재고 및 레시피 관리')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--init', action='store_true', help='로컬 Oracle 계정과 테이블 준비')
    mode.add_argument('--seed', action='store_true', help='빈 프로젝트 DB에 PPT 예시 데이터 입력')
    args = parser.parse_args()
    try:
        if args.init:
            install_schema()
            print('Oracle 프로젝트 스키마를 준비했습니다.')
        elif args.seed:
            with transaction() as connection:
                load_demo(connection)
            print('PPT의 목살 김치볶음밥 예시를 입력했습니다.')
        else:
            terminal()
    except (EOFError, KeyboardInterrupt):
        print('\n종료했습니다.')
    except SetupError as error:
        parser.exit(1, f'{error}\n')
    except pyodbc.Error as error:
        parser.exit(1, f'{database_message(error)}\n')


if __name__ == '__main__':
    main()
