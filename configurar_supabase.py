"""Assistente local de configuração; executar apenas em localhost."""
from pathlib import Path
import streamlit as st
import toml
from sqlalchemy.engine import make_url

PROJECT='irqqfgetbuexracsbrox'
ROOT=Path(__file__).parent


def prepare(uri, password):
    address=make_url(uri.strip())
    host=address.host or ''
    if address.drivername not in ('postgresql','postgresql+psycopg','postgres'):
        raise ValueError('Copie a conexão PostgreSQL, que começa com postgresql://.')
    if not (host.endswith('.pooler.supabase.com') or host==f'db.{PROJECT}.supabase.co'):
        raise ValueError('O endereço precisa ser o banco do Supabase.')
    if not ((address.username or '').endswith('.'+PROJECT) or host==f'db.{PROJECT}.supabase.co'):
        raise ValueError('Copie a conexão do projeto CPaz indicado nesta página.')
    if password:
        address=address.set(password=password)
    if not address.password or '[YOUR-PASSWORD]' in address.password:
        raise ValueError('Informe a senha do banco no segundo campo.')
    path=ROOT/'.streamlit/secrets.supabase.toml'
    path.write_text(toml.dumps({'database':{'url':address.set(drivername='postgresql+psycopg').render_as_string(hide_password=False)}}),encoding='utf-8')


def main():
    st.set_page_config(page_title='Configurar banco CPaz',page_icon=str(ROOT/'logo.png'))
    st.title('Conectar o banco CPaz')
    st.write('Esta página funciona somente neste computador. Sua senha ficará no arquivo privado de configuração, fora do GitHub.')
    st.link_button('1. Abrir meu projeto no Supabase',f'https://supabase.com/dashboard/project/{PROJECT}')
    st.write('2. No Supabase, clique em **Connect**, escolha **Session pooler** e copie a conexão que começa com **postgresql://**.')
    st.write('3. Cole a conexão abaixo e informe a senha do banco definida ao criar o projeto.')
    with st.form('connection',clear_on_submit=True):
        uri=st.text_input('Conexão PostgreSQL',placeholder='postgresql://postgres.…:[YOUR-PASSWORD]@…:5432/postgres')
        password=st.text_input('Senha do banco',type='password')
        submitted=st.form_submit_button('Salvar conexão privada',type='primary')
    if submitted:
        try:
            prepare(uri,password)
        except Exception:
            st.error('Confira a conexão do projeto e a senha. Nenhuma senha será exibida.')
        else:
            st.success('Conexão salva. Volte à conversa e avise “salvei”.')


if __name__=='__main__':main()
