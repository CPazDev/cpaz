"""Prepara Secrets privados da nuvem sem exibir credenciais."""
import argparse
from pathlib import Path
import secrets
import tomllib
from urllib.parse import urlsplit
import toml


def prepare(app_url, database_file, root=None):
    root=Path(root or Path(__file__).parent)
    parts=urlsplit(app_url)
    if parts.scheme!='https' or not parts.hostname or not parts.hostname.endswith('.streamlit.app'):
        raise ValueError('Informe a URL HTTPS real do aplicativo Streamlit.')
    source=tomllib.loads((root/'.streamlit/secrets.toml').read_text(encoding='utf-8'))
    database=tomllib.loads(Path(database_file).read_text(encoding='utf-8'))['database']['url']
    if not database.startswith(('postgresql://','postgresql+psycopg://')):
        raise ValueError('Configure a conexão PostgreSQL em [database].url no arquivo privado.')
    destination=root/'.streamlit/secrets.cloud.toml'
    previous=tomllib.loads(destination.read_text(encoding='utf-8')) if destination.exists() else {}
    source['auth']['redirect_uri']=f'https://{parts.hostname}/oauth2callback'
    source['auth']['cookie_secret']=previous.get('auth',{}).get('cookie_secret') or secrets.token_urlsafe(48)
    source['database']={'url':database,'require_remote':True}
    destination.write_text(toml.dumps(source),encoding='utf-8')
    return destination


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-url',required=True)
    parser.add_argument('--database-file',default=str(Path(__file__).parent/'.streamlit/secrets.supabase.runtime.toml'))
    args=parser.parse_args()
    try:
        print('Secrets privados preparados em '+str(prepare(args.app_url,args.database_file))+'. Valores não exibidos.')
    except (ValueError,KeyError,FileNotFoundError):
        print('Confira a URL do aplicativo e o arquivo privado da conexão PostgreSQL.')
        raise SystemExit(1)
