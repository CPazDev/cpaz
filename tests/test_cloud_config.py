from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch
from sqlalchemy.engine import make_url
import toml
import configurar_nuvem
import configurar_supabase


class CloudConfigurationTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);(self.root/'.streamlit').mkdir()
        self.uri='postgresql://postgres.'+configurar_supabase.PROJECT+':[YOUR-PASSWORD]@aws-0-test.pooler.supabase.com:5432/postgres'
    def test_password_encoded_and_private_file(self):
        with patch.object(configurar_supabase,'ROOT',self.root):
            configurar_supabase.prepare(self.uri,'a@b:%/ senha')
        data=tomllib.loads((self.root/'.streamlit/secrets.supabase.toml').read_text(encoding='utf-8'))
        self.assertEqual(make_url(data['database']['url']).password,'a@b:%/ senha')
    def test_wrong_project_and_transaction_pooler_rejected(self):
        with patch.object(configurar_supabase,'ROOT',self.root):
            for uri in (self.uri.replace(configurar_supabase.PROJECT,'other'),self.uri.replace(':5432',':6543'),
                        'postgresql://postgres:fake@db.'+configurar_supabase.PROJECT+'.supabase.co:5432/postgres'):
                with self.assertRaises(ValueError):configurar_supabase.prepare(uri,'test')
        self.assertFalse((self.root/'.streamlit/secrets.supabase.toml').exists())
    def test_pooler_update_preserves_credentials_google_and_existing_data(self):
        base=self.root/'.streamlit'
        files={
            'secrets.supabase.toml':{'database':{'url':'postgresql://postgres:admin-fake@db.'+configurar_supabase.PROJECT+'.supabase.co:5432/postgres'}},
            'secrets.supabase.runtime.toml':{'database':{'url':'postgresql://cpaz_app:limited-fake@db.'+configurar_supabase.PROJECT+'.supabase.co:5432/postgres','require_remote':True}},
            'secrets.toml':{'auth':{'cookie_secret':'local-cookie','redirect_uri':'http://localhost:8502/oauth2callback'}},
            'secrets.cloud.toml':{'auth':{'cookie_secret':'cloud-cookie','redirect_uri':'https://cpaz-test.streamlit.app/oauth2callback'}},
        }
        for name,data in files.items():(base/name).write_text(toml.dumps(data),encoding='utf-8')
        with patch.object(configurar_supabase,'ROOT',self.root),patch('persistencia.connection') as connection:
            conn=connection.return_value.__enter__.return_value
            conn.execute.return_value.fetchone.return_value=(1,)
            self.assertTrue(configurar_supabase.prepare(self.uri,''))
            candidate=make_url(connection.call_args.args[0])
            self.assertEqual(candidate.password,'limited-fake');self.assertEqual(candidate.username,'cpaz_app.'+configurar_supabase.PROJECT)
            self.assertTrue(candidate.host.endswith('.pooler.supabase.com'))
            conn.execute.assert_called_once_with('SELECT versao FROM schema_meta WHERE id=1')
        for name in ('secrets.toml','secrets.cloud.toml'):
            config=tomllib.loads((base/name).read_text(encoding='utf-8'))
            self.assertEqual(config['auth'],files[name]['auth'])
            self.assertEqual(make_url(config['database']['url']).password,'limited-fake')
        self.assertEqual(make_url(tomllib.loads((base/'secrets.supabase.toml').read_text(encoding='utf-8'))['database']['url']).password,'admin-fake')
    def test_cloud_preserves_google_and_stable_separate_cookie(self):
        local={'auth':{'client_id':'test-id','client_secret':'test-secret','cookie_secret':'local-cookie',
                       'redirect_uri':'http://localhost:8502/oauth2callback','server_metadata_url':'https://example.com/test'}}
        local_file=self.root/'.streamlit/secrets.toml';local_file.write_text(toml.dumps(local),encoding='utf-8')
        database_file=self.root/'.streamlit/secrets.supabase.runtime.toml'
        database_file.write_text(toml.dumps({'database':{'url':self.uri.replace('[YOUR-PASSWORD]','fake')}}),encoding='utf-8')
        destination=configurar_nuvem.prepare('https://cpaz-test.streamlit.app',database_file,self.root)
        first=tomllib.loads(destination.read_text(encoding='utf-8'))
        self.assertTrue(first['database']['require_remote']);self.assertEqual(first['auth']['client_secret'],'test-secret')
        self.assertEqual(first['auth']['redirect_uri'],'https://cpaz-test.streamlit.app/oauth2callback')
        self.assertNotEqual(first['auth']['cookie_secret'],local['auth']['cookie_secret'])
        configurar_nuvem.prepare('https://cpaz-test.streamlit.app',database_file,self.root)
        self.assertEqual(tomllib.loads(destination.read_text(encoding='utf-8'))['auth']['cookie_secret'],first['auth']['cookie_secret'])
        self.assertEqual(tomllib.loads(local_file.read_text(encoding='utf-8')),local)
