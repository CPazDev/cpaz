"""Migração privada SQLite → schema portal no Supabase.

Sem --apply, valida a configuração e apresenta somente quantidades.
A credencial de migração deve estar em CPAZ_MIGRATION_URL, nunca na linha de comando.
"""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from contextlib import closing

import sqlalchemy as sa
from sqlalchemy.schema import CreateTable, CreateIndex, AddConstraint, sort_tables_and_constraints
from sqlalchemy.dialects import postgresql
from persistencia import engine


def checks(sql):
    import re
    result=[]
    for match in re.finditer(r'\bCHECK\s*\(',sql,re.I):
        i=match.end();start=i;depth=1;quote=False
        while depth and i<len(sql):
            c=sql[i]
            if c=="'":
                if quote and i+1<len(sql) and sql[i+1]=="'":i+=2;continue
                quote=not quote
            elif not quote:
                depth+=int(c=='(')-int(c==')')
            i+=1
        if depth:raise ValueError('CHECK inválido na origem.')
        result.append(sql[start:i-1])
    return result


def metadata(conn):
    meta=sa.MetaData(schema='portal')
    schemas={r['name']:r['sql'] for r in conn.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    for name,sql in schemas.items():
        columns=[]
        info=list(conn.execute(f'PRAGMA table_info({name})'))
        keys=[r['name'] for r in sorted(info,key=lambda r:r['pk']) if r['pk']]
        for r in info:
            kind=sa.LargeBinary() if r['type']=='BLOB' else sa.BigInteger() if 'INT' in r['type'] else sa.Text()
            identity=[sa.Identity()] if r['name']=='id' and keys==['id'] else []
            default=sa.text(r['dflt_value']) if r['dflt_value'] is not None else None
            columns.append(sa.Column(r['name'],kind,*identity,nullable=not bool(r['notnull'] or r['pk']),server_default=default))
        constraints=[sa.PrimaryKeyConstraint(*keys)] if keys else []
        for i,condition in enumerate(checks(sql)):
            constraints.append(sa.CheckConstraint(condition,name=f'ck_{name}_{i}'))
        for i,fk in enumerate(conn.execute(f'PRAGMA foreign_key_list({name})')):
            constraints.append(sa.ForeignKeyConstraint([fk['from']],[f"portal.{fk['table']}.{fk['to']}"],
                name=f'fk_{name}_{i}',ondelete=fk['on_delete'],deferrable=True,initially='DEFERRED'))
        table=sa.Table(name,meta,*columns,*constraints)
        for idx in conn.execute(f'PRAGMA index_list({name})'):
            if not idx['unique'] or idx['origin']=='pk':continue
            names=[r['name'] for r in conn.execute(f'PRAGMA index_info("{idx["name"]}")')]
            where=None
            if idx['partial']:
                idx_sql=conn.execute('SELECT sql FROM sqlite_master WHERE name=?',(idx['name'],)).fetchone()[0]
                where=sa.text(idx_sql.split(' WHERE ',1)[1])
            sa.Index('uq_'+name+'_'+str(idx['seq']),*[table.c[n] for n in names],unique=True,postgresql_where=where)
    return meta


def fingerprint(rows):
    normalized=sorted(json.dumps(dict(r),sort_keys=True,default=lambda v:hashlib.sha256(bytes(v)).hexdigest(),ensure_ascii=False) for r in rows)
    return hashlib.sha256('\n'.join(normalized).encode()).hexdigest()


def migrate(path, url, *, apply=False, ddl_path=None):
    if not url:raise ValueError('Configure CPAZ_MIGRATION_URL de forma privada.')
    path=Path(path)
    from sqlalchemy.engine import make_url
    address=make_url(url)
    project='irqqfgetbuexracsbrox'
    if not ((address.username or '').endswith('.'+project) or address.host==f'db.{project}.supabase.co'):
        raise ValueError('A conexão não corresponde ao projeto Supabase autorizado.')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as source:
        source.row_factory=sqlite3.Row
        source.execute('BEGIN')
        if source.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or source.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('A origem falhou na verificação de integridade.')
        meta=metadata(source)
        counts={table.name:source.execute(f'SELECT COUNT(*) FROM {table.name}').fetchone()[0] for table in meta.tables.values()}
        if ddl_path:
            statements=['CREATE SCHEMA IF NOT EXISTS portal;']
            for table,fks in sort_tables_and_constraints(meta.tables.values()):
                if table is None:
                    statements.extend(str(AddConstraint(fk).compile(dialect=postgresql.dialect()))+';' for fk in fks)
                else:
                    statements.append(str(CreateTable(table,include_foreign_key_constraints=fks).compile(dialect=postgresql.dialect()))+';')
                    statements.extend(str(CreateIndex(index).compile(dialect=postgresql.dialect()))+';' for index in table.indexes)
            Path(ddl_path).write_text('\n\n'.join(statements),encoding='utf-8')
        with engine(url).begin() as target:
            target.execute(sa.text('SELECT pg_advisory_xact_lock(1942263011)'))
            existing=sa.inspect(target).get_table_names(schema='portal') if sa.inspect(target).has_schema('portal') else []
            if existing:raise ValueError('O schema portal já contém tabelas. Nenhuma tabela será sobrescrita.')
            if apply:
                target.execute(sa.text('CREATE SCHEMA IF NOT EXISTS portal'))
                meta.create_all(target)
                target.execute(sa.text('SET CONSTRAINTS ALL DEFERRED'))
                for table in meta.tables.values():
                    rows=[dict(r) for r in source.execute(f'SELECT * FROM {table.name}')]
                    for offset in range(0,len(rows),250):
                        target.execute(table.insert(),rows[offset:offset+250])
                    actual=list(target.execute(sa.select(table)).mappings())
                    if len(actual)!=counts[table.name] or fingerprint(actual)!=fingerprint(rows):
                        raise RuntimeError(f'Dados divergentes em {table.name}; migração revertida.')
                    if 'id' in table.c and list(table.primary_key.columns.keys())==['id']:
                        maximum=max((r['id'] for r in rows),default=0)
                        target.execute(sa.text("SELECT setval(pg_get_serial_sequence(:table,'id'),:value,:used)"),
                            {'table':'portal.'+table.name,'value':max(1,maximum),'used':maximum>0})
                target.execute(sa.text('SET CONSTRAINTS ALL IMMEDIATE'))
                # Credencial limitada da aplicação: não conceder DDL nem expor pela Data API.
                runtime=Path(__file__).parent/'.streamlit/secrets.supabase.runtime.toml'
                import secrets,tomllib,toml
                password=secrets.token_urlsafe(48)
                if runtime.is_file():
                    stored=tomllib.loads(runtime.read_text(encoding='utf-8'))
                    password=make_url(stored['database']['url']).password
                exists=target.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname='cpaz_app'")).scalar()
                if exists and not runtime.is_file():
                    raise ValueError('A conta cpaz_app já existe sem configuração local. Migração cancelada para preservar a credencial.')
                if not exists:
                    target.execute(sa.text("SELECT set_config('cpaz.runtime_password',:password,true)"),{'password':password})
                    target.execute(sa.text("""DO $$ BEGIN EXECUTE format('CREATE ROLE cpaz_app LOGIN PASSWORD %L',
                        current_setting('cpaz.runtime_password')); END $$"""))
                target.execute(sa.text('GRANT USAGE ON SCHEMA portal TO cpaz_app'))
                target.execute(sa.text('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA portal TO cpaz_app'))
                target.execute(sa.text('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA portal TO cpaz_app'))
                runtime_url=address.set(drivername='postgresql+psycopg',username='cpaz_app.'+project if address.username.endswith('.'+project) else 'cpaz_app',password=password)
                runtime.write_text(toml.dumps({'database':{'url':runtime_url.render_as_string(hide_password=False),'require_remote':True}}),encoding='utf-8')
        return {'aplicado':apply,'tabelas':counts,'verificacao':'Integridade e comparação por conteúdo' if apply else 'Simulação sem alterações'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',default='dados/portal.sqlite3')
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--ddl')
    parser.add_argument('--connection-file')
    args=parser.parse_args()
    try:
        url=os.environ.get('CPAZ_MIGRATION_URL','')
        if args.connection_file:
            import tomllib
            url=tomllib.loads(Path(args.connection_file).read_text(encoding='utf-8'))['database']['url']
        print(json.dumps(migrate(args.source,url,apply=args.apply,ddl_path=args.ddl),ensure_ascii=True))
    except Exception as error:
        # Erros externos podem conter URL de conexão; nunca imprimir sua representação.
        if isinstance(error,(ValueError,RuntimeError)):
            print(str(error))
        else:
            print('Falha na conexão ou migração. Confira a configuração privada; nenhuma senha será exibida.')
        raise SystemExit(1)
