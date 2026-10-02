# Publicar o CPaz

Repositório: https://github.com/CPazDev/cpaz — branch `main`, entrada `app.py`,
Python **3.14**. Código e modelos de configuração são versionados. Planilhas,
SQLite, fotos no banco, relatórios, backups e credenciais ficam fora do Git.

## Banco privado no Supabase

O portal usa PostgreSQL pelo **Session pooler** com TLS. Os dados ficam no schema
`portal`, sem habilitar esse schema na Data API. O aplicativo usa a conta limitada
`cpaz_app`; a conta administrativa do banco serve somente para a migração.

1. No projeto Supabase, abra **Connect → Session pooler** e copie a conexão.
2. Para configurá-la localmente sem editar arquivos:

   ```powershell
   .\.venv\Scripts\python.exe -m streamlit run configurar_supabase.py --server.address localhost --server.port 8503
   ```

   Abra http://localhost:8503, cole a conexão e informe a senha do banco.
   O assistente grava `.streamlit/secrets.supabase.toml`, ignorado pelo Git.
3. Conclua as importações locais e pause gravações no portal antes da migração.
   Execute primeiro a simulação e depois a aplicação:

   ```powershell
   .\.venv\Scripts\python.exe migrar_supabase.py --connection-file .streamlit/secrets.supabase.toml
   .\.venv\Scripts\python.exe migrar_supabase.py --connection-file .streamlit/secrets.supabase.toml --apply
   ```

A migração recusa um schema `portal` já ocupado. Transfere todas as tabelas,
fotos e vínculos, valida conteúdo e quantidades, mantém IDs e reinicia sequências.
Tudo é aplicado numa transação. A configuração limitada fica em
`.streamlit/secrets.supabase.runtime.toml`, também privada.

## GitHub e testes

```powershell
git remote add origin https://github.com/CPazDev/cpaz.git
git add .
git diff --cached --stat
git commit -m "Preparar portal para Supabase e Community Cloud"
git push -u origin main
```

Se `origin` já estiver configurado, não repita `remote add`. O GitHub Actions
valida o portal com bancos temporários e PostgreSQL isolado, sem dados de produção.

## Streamlit Community Cloud

Em https://share.streamlit.io, escolha **Create app**, repositório `CPazDev/cpaz`,
branch `main`, entrada `app.py` e Python **3.14** em **Advanced settings**.
Anote a URL real `https://SEU-APP.streamlit.app`.

Não fixe a porta em `.streamlit/config.toml`: o Community Cloud verifica o serviço
na porta 8501. No computador, use `--server.port 8502` no comando de execução.

Gere os Secrets privados preservando a integração Google já configurada:

```powershell
.\.venv\Scripts\python.exe configurar_nuvem.py --app-url https://SEU-APP.streamlit.app
```

Copie o conteúdo de `.streamlit/secrets.cloud.toml` somente para o campo
**Secrets** do Community Cloud. Esse arquivo contém a conexão limitada e as
credenciais Google; não publique nem cole na conversa. O segredo de cookie da
nuvem é separado e permanece estável. `database.require_remote = true` evita
iniciar com banco local vazio quando faltar a configuração.

No cliente OAuth Web da Google Auth Platform, adicione a URI
`https://SEU-APP.streamlit.app/oauth2callback`. Preserve também
`http://localhost:8502/oauth2callback`. A autorização de gestão continua no portal;
a publicação do site permite consultar as páginas públicas sem login.

Após publicar, valide login Google, indicadores, fotos, inscrições e escopos de
acesso. A configuração local atual permanece preservada.

## Referências oficiais

- [Implantar no Community Cloud](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)
- [Secrets](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)
- [Login Google](https://docs.streamlit.io/develop/tutorials/authentication/google)
- [Conexão PostgreSQL no Supabase](https://supabase.com/docs/guides/database/connecting-to-postgres)
