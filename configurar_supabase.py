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
    if host.endswith('.pooler.supabase.com') and address.port != 5432:
        raise ValueError('Selecione Session pooler, que usa a porta 5432.')
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
    if (ROOT/'.streamlit/secrets.supabase.runtime.toml').is_file():
        st.divider()
        st.header('Configuração do Streamlit Community Cloud')
        st.write('Após o código estar no GitHub, crie o aplicativo com repositório **CPazDev/cpaz**, branch **main**, arquivo **app.py** e Python **3.14**. Informe abaixo a URL escolhida no painel.')
        st.link_button('Abrir Streamlit Community Cloud', 'https://share.streamlit.io')
        with st.form('cloud'):
            app_url=st.text_input('URL do aplicativo',placeholder='https://seu-aplicativo.streamlit.app')
            cloud_submitted=st.form_submit_button('Preparar configuração da nuvem')
        if cloud_submitted:
            from configurar_nuvem import prepare as prepare_cloud
            try:
                prepare_cloud(app_url,ROOT/'.streamlit/secrets.supabase.runtime.toml',ROOT)
            except Exception:
                st.error('Confira a URL HTTPS do aplicativo terminado em .streamlit.app.')
            else:
                st.success('Configuração privada preparada. Copie somente para o campo Secrets do painel do Streamlit.')
                st.session_state['cloud_ready']=True
        if (ROOT/'.streamlit/secrets.cloud.toml').is_file():
            import tomllib
            cloud_path=ROOT/'.streamlit/secrets.cloud.toml'
            cloud=tomllib.loads(cloud_path.read_text(encoding='utf-8'))
            callback=cloud['auth']['redirect_uri']
            st.write('No cliente OAuth Web do Google, adicione esta URI de redirecionamento e preserve a URI local já existente:')
            st.code(callback,language=None)
            st.link_button('Abrir clientes OAuth do Google','https://console.cloud.google.com/auth/clients')
            with st.expander('Configuração privada para copiar em Secrets'):
                st.write('Este conteúdo é privado. Copie apenas para o painel do seu aplicativo.')
                if st.button('Mostrar configuração privada neste computador'):
                    st.text_area('Secrets',value=cloud_path.read_text(encoding='utf-8'),height=330)


if __name__=='__main__':main()
