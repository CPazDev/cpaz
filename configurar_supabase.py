"""Assistente local de configuração; executar apenas em localhost."""
from pathlib import Path
import tomllib
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
    if not host.endswith('.pooler.supabase.com'):
        raise ValueError('Selecione Session pooler; a conexão direta IPv6 não funciona nesta publicação.')
    if address.username != 'postgres.'+PROJECT:
        raise ValueError('Copie a conexão do projeto CPaz indicado nesta página.')
    if address.port != 5432:
        raise ValueError('Selecione Session pooler, que usa a porta 5432.')
    if password:
        address=address.set(password=password)
    path=ROOT/'.streamlit/secrets.supabase.toml'
    if (not address.password or '[YOUR-PASSWORD]' in address.password) and path.is_file():
        previous=tomllib.loads(path.read_text(encoding='utf-8'))
        address=address.set(password=make_url(previous['database']['url']).password)
    if not address.password or '[YOUR-PASSWORD]' in address.password:
        raise ValueError('Informe a senha do banco no segundo campo.')
    address=address.set(drivername='postgresql+psycopg')
    updates={path:{'database':{'url':address.render_as_string(hide_password=False)}}}
    runtime_path=ROOT/'.streamlit/secrets.supabase.runtime.toml'
    if runtime_path.is_file():
        runtime=tomllib.loads(runtime_path.read_text(encoding='utf-8'))
        old=make_url(runtime['database']['url'])
        if old.username not in ('cpaz_app','cpaz_app.'+PROJECT):
            raise ValueError('A credencial limitada existente não corresponde ao aplicativo CPaz.')
        candidate=address.set(username='cpaz_app.'+PROJECT,password=old.password)
        from persistencia import connection
        with connection(candidate.render_as_string(hide_password=False)) as conn:
            version=conn.execute('SELECT versao FROM schema_meta WHERE id=1').fetchone()
            if not version or version[0]!=1:
                raise ValueError('O banco do pooler não contém a migração do portal.')
        runtime['database']={'url':candidate.render_as_string(hide_password=False),'require_remote':True}
        updates[runtime_path]=runtime
        for filename in ('secrets.toml','secrets.cloud.toml'):
            config_path=ROOT/'.streamlit'/filename
            if config_path.is_file():
                config=tomllib.loads(config_path.read_text(encoding='utf-8'))
                config['database']=runtime['database']
                updates[config_path]=config
    for config_path,config in updates.items():
        if config_path.exists():
            backup=config_path.with_name(config_path.stem+'.before-pooler.toml')
            if not backup.exists():backup.write_bytes(config_path.read_bytes())
        config_path.write_text(toml.dumps(config),encoding='utf-8')
    return runtime_path in updates


def main():
    st.set_page_config(page_title='Configurar banco CPaz',page_icon=str(ROOT/'logo.png'))
    st.title('Conectar o banco CPaz')
    st.write('Esta página funciona somente neste computador. Sua senha ficará no arquivo privado de configuração, fora do GitHub.')
    st.link_button('1. Abrir meu projeto no Supabase',f'https://supabase.com/dashboard/project/{PROJECT}')
    st.write('2. No Supabase, clique em **Connect**, escolha **Session pooler** e copie a conexão que começa com **postgresql://**.')
    st.write('3. Cole a conexão abaixo e informe a senha do banco definida ao criar o projeto.')
    if (ROOT/'.streamlit/secrets.supabase.toml').is_file():
        st.info('A senha já está salva. Pode deixar o campo Senha vazio para preservá-la. Se o banco já foi migrado, a conexão limitada e os Secrets da nuvem serão atualizados após a validação do pooler.')
    with st.form('connection',clear_on_submit=True):
        uri=st.text_input('Conexão PostgreSQL',placeholder='postgresql://postgres.…:[YOUR-PASSWORD]@…:5432/postgres')
        password=st.text_input('Senha do banco',type='password')
        submitted=st.form_submit_button('Salvar conexão privada',type='primary')
    if submitted:
        try:
            updated_runtime=prepare(uri,password)
        except Exception:
            st.error('Confira a conexão do projeto e a senha. Nenhuma senha será exibida.')
        else:
            st.success('Session pooler validado. Copie a configuração privada atualizada para Secrets no Streamlit.' if updated_runtime else 'Conexão salva. Volte à conversa e avise “salvei”.')
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
