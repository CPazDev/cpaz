# Publicar no GitHub e no Streamlit Community Cloud

O projeto está preparado para versionar o código. O arquivo de entrada é `app.py`,
a branch é `main` e a versão de Python usada na validação local é **3.14**.
O repositório não inclui a base de dados local nem as credenciais Google.

## Enviar ao GitHub

Crie um repositório vazio no GitHub, sem adicionar README, licença ou `.gitignore`
na criação. No PowerShell, dentro da pasta do projeto:

```powershell
git status
git remote add origin https://github.com/SEU-USUARIO/SEU-REPOSITORIO.git
git push -u origin main
```

Substitua o endereço pelo do seu repositório. O primeiro commit local já contém
os arquivos preparados. Para alterações futuras:

```powershell
git add .
git diff --cached --stat
git commit -m "Descreva a alteração"
git push
```

`dados/`, planilhas, backups, logs, ambiente virtual e credenciais são ignorados.
O arquivo `.streamlit/config.toml`, os modelos de secrets e `requirements.txt`
devem estar no Git. O GitHub Actions executa os testes em Linux quando houver
push ou pull request; ele não usa o banco local nem credenciais de produção.

## Criar o aplicativo na nuvem

Em [share.streamlit.io](https://share.streamlit.io), selecione **Create app** e
informe seu repositório, branch **main** e arquivo **app.py**. Escolha o subdomínio
do aplicativo. Em **Advanced settings**, selecione Python **3.14**.
As dependências serão instaladas a partir de `requirements.txt` na raiz.
A porta 8502 é usada localmente; a plataforma define a porta do servidor hospedado.

## Login Google no site publicado

1. No cliente OAuth Web da [Google Auth Platform](https://console.cloud.google.com/auth/clients),
   adicione `https://SEU-APP.streamlit.app/oauth2callback` às URIs de redirecionamento.
   Preserve também `http://localhost:8502/oauth2callback` para continuar usando o portal local.
2. Abra `.streamlit/secrets.cloud.example.toml` e use sua estrutura em
   **Advanced settings → Secrets**, ou **Settings → Secrets** após criar o app.
   Preencha `client_id` e `client_secret` com os valores do cliente Google.
   Substitua `SEU-APP` pela URL real; não use localhost nos secrets da nuvem.
3. Gere um segredo de cookie separado para a nuvem e mantenha-o estável:

   ```powershell
   .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

   Cole o resultado em `cookie_secret` somente no painel de Secrets.
4. Enquanto o Google estiver em modo de teste, inclua os e-mails autorizados
   como usuários de teste. Para permitir login de novas contas, configure a
   publicação do cliente Google. A autorização de gestão permanece no portal.

Não grave credenciais no GitHub. O arquivo `.streamlit/secrets.toml` local
permanece com sua configuração atual e não deve ser enviado.

## Banco de dados antes do uso definitivo

O código atual usa **SQLite local** em `dados/portal.sqlite3`. O Community Cloud
não garante persistência do armazenamento local. Reinícios e recriações podem
perder cadastros, fotos, permissões, eventos e inscrições gravados nesse arquivo.

Um clone do repositório inicia com banco vazio; as pessoas e grupos importados
no computador não são enviados automaticamente. As duas contas administradoras
iniciais continuam reconhecidas pelo código. Não publique a planilha ou o SQLite
no Git para transferir os dados.

Para uso definitivo, a etapa pendente é adaptar a persistência para um banco
externo e migrar a base atual por um canal privado, incluindo fotos, permissões,
perfis de usuário e vínculos com membros.
As credenciais desse serviço também deverão ficar em Secrets. Definir apenas
`CEV_DATABASE` muda o caminho do arquivo local; não conecta a um banco remoto.
O projeto ainda não possui adaptador de banco remoto.

Até essa etapa, uma implantação no Community Cloud serve para testar a interface,
sem cadastrar dados que precisem ser preservados. A base local continua no computador.

## Referências oficiais

- [Deploy e versão do Python](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)
- [Dependências](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies)
- [Secrets no Community Cloud](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)
- [Autenticação Google](https://docs.streamlit.io/develop/tutorials/authentication/google)
- [Conexões e persistência local](https://docs.streamlit.io/develop/concepts/connections/connecting-to-data)
