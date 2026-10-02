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
            for uri in (self.uri.replace(configurar_supabase.PROJECT,'other'),self.uri.replace(':5432',':6543')):
                with self.assertRaises(ValueError):configurar_supabase.prepare(uri,'test')
        self.assertFalse((self.root/'.streamlit/secrets.supabase.toml').exists())
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
