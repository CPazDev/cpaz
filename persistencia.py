"""Conexão PostgreSQL mantendo a interface das operações locais existentes."""
from contextlib import contextmanager
import re
import sqlite3
from functools import lru_cache


def bind_sql(sql, params):
    """Converte parâmetros posicionais, respeitando literais SQL."""
    out, quoted, index = [], None, 0
    i = 0
    while i < len(sql):
        c = sql[i]
        if quoted:
            out.append(c)
            if c == quoted:
                if i + 1 < len(sql) and sql[i + 1] == quoted:
                    i += 1
                    out.append(sql[i])
                else:
                    quoted = None
        elif c in ("'", '"'):
            quoted = c
            out.append(c)
        elif c == '?':
            out.append(f':p{index}')
            index += 1
        else:
            out.append(c)
        i += 1
    if index != len(params):
        raise ValueError('Quantidade de parâmetros SQL inválida.')
    return ''.join(out), {f'p{i}': value for i, value in enumerate(params)}


class Row:
    def __init__(self, values):
        self.values = dict(values)
    def keys(self):
        return self.values.keys()
    def __getitem__(self, key):
        return list(self.values.values())[key] if isinstance(key, int) else self.values[key]
    def __iter__(self):
        return iter(self.values.values())


class Cursor:
    def __init__(self, rows=(), lastrowid=None, rowcount=0):
        self.rows = iter(Row(row) for row in rows)
        self.lastrowid, self.rowcount = lastrowid, rowcount
    def fetchone(self):
        return next(self.rows, None)
    def fetchall(self):
        return list(self.rows)
    def __iter__(self):
        return self.rows


@lru_cache(maxsize=2)
def engine(url):
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    address = make_url(url)
    if address.drivername not in ('postgres', 'postgresql', 'postgresql+psycopg'):
        raise RuntimeError('O banco remoto precisa ser PostgreSQL.')
    address = address.set(drivername='postgresql+psycopg')
    sslmode = address.query.get('sslmode','require') if address.host in ('localhost','127.0.0.1') else 'require'
    return create_engine(address, pool_size=3, max_overflow=2, pool_pre_ping=True,
                         hide_parameters=True, connect_args={'sslmode': sslmode, 'connect_timeout': 15})


class Connection:
    def __init__(self, raw):
        self.raw, self.locked = raw, False
    def lock(self):
        if not self.locked:
            from sqlalchemy import text
            self.raw.execute(text('SELECT pg_advisory_xact_lock(1942263011)'))
            self.locked = True
    def execute(self, sql, params=()):
        from sqlalchemy import text, inspect
        from sqlalchemy.exc import IntegrityError
        if sql.strip().upper() == 'BEGIN IMMEDIATE':
            self.lock()
            return Cursor()
        pragma = re.fullmatch(r'\s*PRAGMA foreign_key_list\((\w+)\)\s*', sql, re.I)
        if pragma:
            rows = []
            for fk in inspect(self.raw).get_foreign_keys(pragma[1], schema='portal'):
                for source, target in zip(fk['constrained_columns'], fk['referred_columns']):
                    rows.append({'table': fk['referred_table'], 'from': source, 'to': target,
                                 'on_delete': fk.get('options', {}).get('ondelete', 'NO ACTION').upper()})
            return Cursor(rows)
        command = sql.lstrip().split(None, 1)[0].upper()
        inserted = re.match(r'\s*INSERT\s+INTO\s+(\w+)', sql, re.I)
        returning = False
        if command not in ('SELECT', 'WITH', 'EXPLAIN', 'SHOW'):
            self.lock()
        if inserted and 'RETURNING' not in sql.upper():
            columns = inspect(self.raw).get_pk_constraint(inserted[1], schema='portal')['constrained_columns']
            if columns == ['id']:
                sql = sql.rstrip().rstrip(';') + ' RETURNING id'
                returning = True
        sql, bindings = bind_sql(sql, params)
        try:
            result = self.raw.execute(text(sql), bindings)
            rows = list(result.mappings()) if result.returns_rows else []
            return Cursor(rows, rows[0]['id'] if returning and rows else None, result.rowcount)
        except IntegrityError:
            raise sqlite3.IntegrityError('Vínculo inválido ou registro duplicado.') from None


@contextmanager
def connection(url):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    try:
        raw = engine(url).connect()
    except Exception as error:
        raise RuntimeError(connection_error(error)) from None
    try:
        with raw.begin():
            raw.execute(text('SET LOCAL search_path TO portal'))
            yield Connection(raw)
    except IntegrityError:
        raise sqlite3.IntegrityError('Vínculo inválido ou registro duplicado.') from None
    finally:
        raw.close()


def connection_error(error):
    """Classifica falhas sem registrar a URL, a senha ou o erro original."""
    original=getattr(error,'orig',error)
    code=getattr(original,'sqlstate',None)
    message=str(original).lower()
    reason='Falha de conexão não classificada; confira a configuração privada do banco.'
    if code=='28P01' or any(term in message for term in ('password authentication failed','wrong password','sasl authentication failed')):
        reason='Autenticação recusada pelo banco. Confira a senha na configuração privada.'
    elif 'tenant or user not found' in message:
        reason='O pooler não reconheceu o usuário ou projeto. Confira a conexão Session pooler.'
    elif code=='53300' or any(term in message for term in ('max client connections','too many clients','maxclientsinsessionmode','max clients reached')):
        reason='O pooler atingiu o limite de conexões. Feche instâncias ociosas e tente novamente.'
    elif 'could not translate host name' in message or 'name or service not known' in message or 'nodename nor servname' in message:
        reason='Não foi possível resolver o endereço do banco (DNS).'
    elif 'timeout' in message or 'timed out' in message:
        reason='A conexão com o banco excedeu o tempo limite. Confira acesso à rede e Session pooler.'
    elif 'connection refused' in message:
        reason='O servidor recusou a conexão. Confira o endereço e a porta do Session pooler.'
    elif 'network is unreachable' in message or 'no route to host' in message:
        reason='A rede do servidor não consegue alcançar o banco. Use o endereço IPv4 do Session pooler.'
    elif 'circuit breaker' in message or 'upstream database' in message:
        reason='O pooler não conseguiu conectar ao banco de origem.'
    elif 'no pg_hba.conf entry' in message:
        reason='O banco recusou a origem da conexão. Confira as restrições de rede do projeto.'
    elif 'server closed the connection' in message:
        reason='O servidor encerrou a conexão antes da autenticação.'
    elif 'ssl' in message or 'certificate' in message:
        reason='Falha na conexão TLS com o banco.'
    elif type(original).__name__ in ('ArgumentError','ValueError'):
        reason='Formato inválido da conexão PostgreSQL em database.url.'
    elif isinstance(original,ImportError):
        reason='A dependência PostgreSQL não foi instalada.'
    elif message=='o banco remoto precisa ser postgresql.':
        reason='O protocolo em database.url não é PostgreSQL.'
    # Somente classe e código SQL padronizado; nunca o texto da exceção externa.
    category=type(original).__name__
    category=category if re.fullmatch(r'[A-Za-z_]{1,60}',category) else 'Erro'
    safe_code=code if isinstance(code,str) and re.fullmatch(r'[A-Z0-9]{5}',code) else 'ausente'
    return 'Não foi possível conectar ao PostgreSQL. '+reason+f' [Tipo: {category}; SQLSTATE: {safe_code}]'
