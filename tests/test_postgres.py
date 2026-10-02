"""Portabilidade SQL e integração com PostgreSQL isolado do CI."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sqlite3
from contextlib import closing
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable
import database as db
from database import remote_settings
import migrar_supabase
import persistencia


class PortabilityTests(unittest.TestCase):
    def test_parameters_keep_literals_and_quotes(self):
        sql,values=persistencia.bind_sql("SELECT '?' AS literal, ? AS value, 'it''s ?' AS quoted",(7,))
        self.assertEqual(values,{'p0':7});self.assertEqual(sql.count(':p0'),1)
    def test_connection_diagnostics_never_include_credentials(self):
        for message,expected in (('password authentication failed secret-password','Autenticação recusada'),
                                 ('Tenant or user not found secret-password','não reconheceu'),
                                 ('connection timeout secret-password','tempo limite'),
                                 ('could not translate host name secret-password','DNS'),
                                 ('unexpected postgresql://user:secret-password@example.com/db','não classificada')):
            result=persistencia.connection_error(Exception(message))
            self.assertIn(expected,result);self.assertNotIn('secret-password',result);self.assertNotIn('example.com',result)
    def test_metadata_preserves_constraints_and_binary(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(db,'DATABASE',Path(tmp)/'db.sqlite3'):
            db.initialize()
            with closing(sqlite3.connect(db.DATABASE)) as conn:
                conn.row_factory=sqlite3.Row;meta=migrar_supabase.metadata(conn)
            for table in meta.tables.values():
                self.assertIn('CREATE TABLE portal.',str(CreateTable(table).compile(dialect=postgresql.dialect())))
            people=meta.tables['portal.people']
            self.assertIsInstance(people.c.foto.type,sa.LargeBinary)
            self.assertTrue(any(isinstance(c,sa.CheckConstraint) for c in people.constraints))
            notices=meta.tables['portal.notices']
            self.assertTrue(any(f.ondelete=='SET NULL' for f in notices.foreign_key_constraints))
    def test_cloud_does_not_fall_back_to_empty_sqlite(self):
        with patch.dict(os.environ,{'CPAZ_REQUIRE_REMOTE':'true','CPAZ_DATABASE_URL':''}), patch('streamlit.secrets',{'database':{}}):
            with self.assertRaises(RuntimeError):remote_settings()


@unittest.skipUnless(os.environ.get('CPAZ_TEST_DATABASE_URL'),'PostgreSQL isolado não configurado neste ambiente.')
class PostgreSQLTests(unittest.TestCase):
    def test_roundtrip_transactions_ids_and_dependencies(self):
        url=os.environ['CPAZ_TEST_DATABASE_URL'];parsed=make_url(url)
        self.assertIn(parsed.host,('localhost','127.0.0.1'));self.assertEqual(parsed.database,'cpaz_test')
        with tempfile.TemporaryDirectory() as tmp,patch.object(db,'DATABASE',Path(tmp)/'db.sqlite3'):
            db.initialize()
            with closing(sqlite3.connect(db.DATABASE)) as source:
                source.row_factory=sqlite3.Row;meta=migrar_supabase.metadata(source)
        engine=persistencia.engine(url)
        with engine.begin() as target:
            target.execute(sa.text('DROP SCHEMA IF EXISTS portal CASCADE'))
            target.execute(sa.text('CREATE SCHEMA portal'));meta.create_all(target)
        with persistencia.connection(url) as conn:
            group=conn.execute("INSERT INTO groups(cev,nome,fase) VALUES(?,?,?)",('Itarema','Grupo','Kerigma')).lastrowid
            person=conn.execute("INSERT INTO people(cev,nome,categoria,grupo_id,foto) VALUES(?,?,?,?,?)",('Itarema','Pessoa','Membro',group,b'photo')).lastrowid
            conn.execute('UPDATE groups SET pastor_id=? WHERE id=?',(person,group))
            row=conn.execute('SELECT id,nome,foto FROM people WHERE id=?',(person,)).fetchone()
            self.assertEqual(dict(row),{'id':person,'nome':'Pessoa','foto':b'photo'});self.assertEqual(row[0],person)
            conn.execute('INSERT INTO account_profiles(email,nome) VALUES(?,?) ON CONFLICT(email) DO UPDATE SET nome=excluded.nome',('p@example.com','Pessoa'))
            self.assertTrue(list(conn.execute('PRAGMA foreign_key_list(groups)')))
        with self.assertRaises(sqlite3.IntegrityError):
            with persistencia.connection(url) as conn:
                conn.execute('BEGIN IMMEDIATE');conn.execute('INSERT INTO people(cev,nome,categoria) VALUES(?,?,?)',('Itarema','Inválido','Outro'))
        with persistencia.connection(url) as conn:self.assertEqual(conn.execute('SELECT count(*) FROM people').fetchone()[0],1)
