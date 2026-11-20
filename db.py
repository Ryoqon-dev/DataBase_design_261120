"""Connection and installation for the local Oracle 10g XE database."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import struct
import subprocess

import pyodbc


ROOT = Path(__file__).resolve().parent
SETTINGS = ROOT / '.pantry-db.json'
SCHEMA = ROOT / 'sql' / 'schema.sql'
DEMO = ROOT / 'sql' / 'seed.sql'
APP_USER = 'PANTRY_APP'
ODBC_DRIVER = 'Oracle in XE'


class SetupError(Exception):
    """An installation problem with an actionable error message."""


def _read_settings():
    try:
        with SETTINGS.open(encoding='utf-8') as handle:
            settings = json.load(handle)
        if settings['user'] != APP_USER or not re.fullmatch(r'Pn[0-9a-f]{24}', settings['password']):
            raise ValueError
        return settings
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise SetupError('Oracle 접속 설정을 읽을 수 없습니다. 프로젝트의 setup.ps1을 확인하세요.') from error


def connect():
    if struct.calcsize('P') != 4 or ODBC_DRIVER not in pyodbc.drivers():
        raise SetupError('32비트 Python과 Oracle in XE ODBC 드라이버가 필요합니다. setup.ps1을 사용하세요.')
    settings = _read_settings()
    os.environ['NLS_LANG'] = 'AMERICAN_AMERICA.AL32UTF8'
    os.environ.setdefault('NLS_NUMERIC_CHARACTERS', '.,')
    connection = pyodbc.connect(
        f'DRIVER={{{ODBC_DRIVER}}};DBQ=//127.0.0.1:1521/XE;'
        f'UID={APP_USER};PWD={settings["password"]};',
        autocommit=False,
    )
    connection.setencoding(encoding='utf-8', ctype=pyodbc.SQL_CHAR)
    connection.setdecoding(pyodbc.SQL_CHAR, encoding='utf-8', ctype=pyodbc.SQL_CHAR)
    connection.setdecoding(pyodbc.SQL_WCHAR, encoding='utf-8', ctype=pyodbc.SQL_CHAR)
    return connection


@contextmanager
def transaction():
    connection = connect()
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _local_admin(sql):
    sqlplus = shutil.which('sqlplus')
    if not sqlplus:
        raise SetupError('PATH에서 Oracle XE의 sqlplus를 찾을 수 없습니다.')
    result = subprocess.run(
        [sqlplus, '-L', '-S', '/ as sysdba'],
        input=f'WHENEVER OSERROR EXIT FAILURE\nWHENEVER SQLERROR EXIT FAILURE\n{sql}\nEXIT SUCCESS\n'.encode('ascii'),
        capture_output=True,
        env={**os.environ, 'ORACLE_SID': 'XE', 'NLS_LANG': 'AMERICAN_AMERICA.AL32UTF8'},
    )
    output = result.stdout.decode('utf-8', errors='replace')
    error_codes = set(re.findall(r'\b(?:ORA|SP2)-\d+\b', output))
    if result.returncode or error_codes:
        if 'ORA-01920' in error_codes:
            raise SetupError('PANTRY_APP 계정이 이미 있습니다. 기존 계정을 유지했습니다. 원래 접속 설정이 필요합니다.')
        codes = ', '.join(sorted(error_codes)) or f'종료 코드 {result.returncode}'
        raise SetupError(f'로컬 Oracle 관리 접속 또는 계정 생성에 실패했습니다 ({codes}).')
    return output


def _create_account():
    if SETTINGS.exists():
        return
    status = _local_admin(
        "SET HEADING OFF FEEDBACK OFF PAGESIZE 0\n"
        "SELECT CASE WHEN COUNT(*) = 0 THEN 'PANTRY_USER_AVAILABLE' ELSE 'PANTRY_USER_EXISTS' END "
        f"FROM dba_users WHERE username = '{APP_USER}';"
    )
    if 'PANTRY_USER_AVAILABLE' not in status:
        raise SetupError('Oracle에 PANTRY_APP 계정이 있습니다. 이 프로젝트에 해당 계정의 원래 접속 설정이 필요합니다.')
    password = 'Pn' + secrets.token_hex(12)
    _local_admin(
        f'CREATE USER {APP_USER} IDENTIFIED BY "{password}" '
        'DEFAULT TABLESPACE USERS TEMPORARY TABLESPACE TEMP QUOTA 20M ON USERS;\n'
        f'GRANT CREATE SESSION, CREATE TABLE, CREATE SEQUENCE TO {APP_USER};'
    )
    pending = SETTINGS.with_suffix('.json.tmp')
    pending.write_text(json.dumps({'user': APP_USER, 'password': password}) + '\n', encoding='utf-8')
    pending.replace(SETTINGS)


def _script_statements(path):
    return [
        statement.strip()
        for statement in re.split(r'^/\s*$', path.read_text(encoding='utf-8'), flags=re.MULTILINE)
        if statement.strip()
    ]


@contextmanager
def _script_cursor(connection):
    # This driver accepts multilingual SQL text in wide mode and binds application text in UTF-8.
    connection.setencoding(encoding='utf-16le', ctype=pyodbc.SQL_WCHAR)
    try:
        yield connection.cursor()
    finally:
        connection.setencoding(encoding='utf-8', ctype=pyodbc.SQL_CHAR)


def install_schema():
    _create_account()
    with transaction() as connection, _script_cursor(connection) as cursor:
        existing = {
            (object_type.strip(), object_name.strip())
            for object_type, object_name in cursor.execute('SELECT object_type, object_name FROM user_objects')
        }
        for statement in _script_statements(SCHEMA):
            _, object_type, name, *_ = statement.split(maxsplit=3)
            if (object_type, name.upper()) not in existing:
                cursor.execute(statement)


def load_demo(connection):
    cursor = connection.cursor()
    cursor.execute('LOCK TABLE ingredient IN EXCLUSIVE MODE')
    if cursor.execute(
        'SELECT (SELECT COUNT(*) FROM ingredient) + (SELECT COUNT(*) FROM recipe) FROM dual',
    ).fetchval():
        raise SetupError('예시 데이터는 식재료와 레시피가 비어 있는 프로젝트 DB에 넣을 수 있습니다.')
    with _script_cursor(connection) as script:
        for statement in _script_statements(DEMO):
            script.execute(statement)
