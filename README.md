# C-Paz Missão Itapipoca

Site em Streamlit baseado em `codigo.py` e nas definições aprovadas na conversa.

Para enviar o código ao GitHub e preparar a implantação no Streamlit Community
Cloud, consulte [DEPLOY.md](DEPLOY.md). O portal usa SQLite no computador e
PostgreSQL/Supabase na nuvem; a base e as credenciais ficam fora do repositório.

## Executar

No PowerShell, dentro desta pasta:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py --server.port 8502
```

Abra **http://localhost:8502**. A porta 8502 evita interromper o servidor que já
estava usando a porta 8501. O aplicativo permanece local até ser hospedado
em um servidor com suporte a Python/Streamlit.

## Funcionalidades

- Início com avisos gerais e seleção de CEv/Irradiação.
- Páginas de CEv e grupo com avisos públicos gerais e do CEv, além de retiros e eventos. Avisos de grupo são privados.
- Modos claro, escuro e automático pelo menu ⋮: **Light**, **Dark** ou **System**.
- Login Google integrado com `st.login`, `st.user` e `st.logout`.
- Autorização de contas e definição de nível de acesso dentro do portal.
- Menu lateral com gestão recolhível e perfil do usuário fixo no rodapé.
- Nome, apelido, vínculo com membro e consulta das próprias informações.
- Cadastros de grupo e membro/pastor/núcleo, com fotos.
- Edição de todos os cadastros e registros existentes em **Editar registros**.
- Exclusão mediante permissão explícita por CEv, grupo ou publicações gerais.
- Encontros, frequência e acompanhamentos vinculados ao grupo.
- Avisos com foto, destinados ao público geral, ao CEv ou ao grupo.
- Página própria de evento/retiro, capa, cor, WhatsApp e compartilhamento.
- Inscrição com campos definidos pelo organizador, sem campos iniciais presumidos.
- Pedidos extras com opções e quantidades, associados à inscrição.
- Testemunhos com foto opcional e aprovação antes da publicação.
- Gestão da página, inscrições, pedidos e testemunhos conforme o CEv ou grupo autorizado.
- Ministérios gerais e locais, participantes, encontros, frequência e acompanhamentos.
- Permissões acumuláveis de Coordenador/Núcleo, limitadas ao ministério autorizado.
- Dados e fotos gravados no SQLite local ou no PostgreSQL privado da nuvem.

## Campos aprovados

| Formulário | Campos |
| --- | --- |
| Grupo | Nome, CEv/Irradiação, pastor responsável, local, dia da semana, horário dos encontros, fase e foto |
| Pessoa | Nome, categoria, contato, grupo, Instagram, endereço, data de nascimento, foto, acompanhador e ministério |
| Encontro | Grupo, data, tema e observações |
| Frequência | Encontro, membro e presença |
| Acompanhamento | Membro, data, acompanhador, próximo acompanhamento e observações |
| Aviso | Título, texto, destino e foto |
| Evento/retiro | Título, tipo, início, término, local, descrição, destino, capa, cor principal e WhatsApp |
| Pedido extra | Título, descrição, opções e quantidade escolhida pelo inscrito |
| Testemunho | Nome, texto e foto opcional |

Fases: Kerigma, Filoteia, Metanoia, Martiria, Santidade e Permanente.
Categorias: Membro, Pastor e Núcleo. Presenças: Presente, Ausente e Liberado.
Ministérios também possuem cadastros e registros próprios. Um ministério geral
reúne os ministérios locais dos CEvs; avisos gerais chegam aos locais vinculados.
Participações atuais alimentam o campo Ministério, preservando preenchimentos
manuais na importação. Alterar esse texto não revoga participações ou acessos.

## Editar e excluir registros

### Perfil e informações pessoais

Após entrar com Google, abra **Meu perfil** no rodapé do menu lateral para editar
nome e apelido. O apelido, quando preenchido, aparece no cartão do usuário.
O nome do perfil não altera o nome no cadastro de membro.

O próprio usuário pode usar **Vincular meu cadastro** quando o e-mail cadastrado
no membro corresponde ao e-mail verificado da conta Google. O seletor mostra
somente esses cadastros. Para e-mail ausente ou diferente, um administrador ou
gestor do CEv pode definir o vínculo em **Gerenciar acessos → Vincular conta a um membro**.
Cada membro pode estar vinculado a uma única conta. Gestores ficam limitados ao
próprio CEv e não alteram contas administradoras ou vinculadas a outro CEv.

**Minhas informações** mostra somente o cadastro, grupo/célula, ministérios e
frequência do membro vinculado, além do número de acompanhamentos realizados
no ano e da data do último acompanhamento. Datas futuras não entram nesse resumo;
observações de encontros e acompanhamentos continuam restritas à gestão.
Os dados pessoais são resolvidos pelo e-mail Google verificado; IDs enviados
pela URL não selecionam outro membro. O grupo acompanha o cadastro atual.

O vínculo libera a consulta pessoal, sem conceder permissões de gestão.
Uma conta pode usar a área pessoal enquanto aguarda autorização de gestão.
O próprio usuário também pode retirar seu vínculo. Antes de excluir um membro
vinculado, é necessário revisar esse vínculo; a conta e seu nome/apelido são preservados.

### Cadastros e registros

Entre com sua conta Google e abra **Registros → Editar registros**. Selecione o tipo
e o registro, altere os campos e clique em **Salvar alterações**. Também há atalhos
nas páginas de grupo, CEv, encontros, frequência, acompanhamentos, avisos e eventos.
Fotos existentes são preservadas; você pode substituí-las ou removê-las no formulário.

Gestores editam os cadastros e publicações do seu CEv. Responsáveis de grupo editam
seu grupo, encontros, frequências, acompanhamentos, avisos e eventos desse grupo.
As publicações e seus registros seguem o destino autorizado da conta.
Históricos permanecem no grupo
em que foram registrados quando uma pessoa muda de grupo.

Nenhuma conta, incluindo administradores, recebe permissão de exclusão automaticamente.
As duas contas administradoras iniciais concedem e retiram essa permissão em
**Gerenciar acessos → Permissões de exclusão**. Escolha a conta e seu destino autorizado.
Para administradores, o destino pode ser um CEv, um grupo ou publicações gerais;
gestores ficam limitados ao seu CEv e responsáveis ao seu grupo. A permissão não
amplia o nível de acesso da conta e é retirada quando seu nível ou destino muda,
ou quando seu acesso é revogado.

No editor, abra **Excluir este registro**, confirme a seleção e clique em
**Excluir registro**. A exclusão é bloqueada enquanto houver registros vinculados:
um grupo com pessoas/encontros ou um evento com inscrições, por exemplo. O portal
mostra os vínculos a revisar e não apaga outros registros automaticamente.
A remoção de campos de inscrição também exige permissão de exclusão e preserva
as respostas já recebidas.

## Nomes de CEv/Irradiação

Edite a lista `cevs` em `conteudo.json` com os nomes determinados por você.
A lista já contém os nove nomes enviados pelo usuário. A lista `avisos`
permite textos gerais iniciais; avisos também podem ser publicados pela interface.

Avisos seguem a hierarquia **Geral → CEv → Grupo**. Gerais aparecem em todos os
CEvs e grupos; os do CEv aparecem também em todos os grupos dessa unidade.
Esses dois destinos são públicos. Avisos destinados a um grupo são privados:
apenas contas Google verificadas vinculadas a uma pessoa desse grupo ou a gestão
autorizada podem consultá-los. Uma conta vinculada recebe avisos gerais, do seu
CEv e do seu grupo na seção **Meus avisos** em **Minhas informações**, mesmo sem
permissão de gestão. Trocar de grupo ou retirar o vínculo atualiza esse acesso.

Novos avisos registram automaticamente a data e hora da publicação no fuso de
São Paulo. Editar um aviso preserva essa data; avisos antigos sem a informação
mostram **Data de publicação não registrada**.

Na página de um evento/retiro, **Compartilhar no app** permite à gestão publicar
um aviso com o título e um botão para abrir a página do evento. Os destinos
seguem as permissões existentes: administrador pode publicar em qualquer destino,
gestor no seu CEv e grupos, responsável no próprio grupo. O compartilhamento
segue a visibilidade de avisos gerais, do CEv e privados de grupo, inclusive em
**Minhas informações**. Se o evento for excluído, o aviso é preservado e o botão
de acesso ao evento é removido.
`campos_pessoa_aprovados` já está habilitado e não precisa ser alterado.

## Ativar o Google

O código está integrado, mas o login real depende de um cliente OAuth do
seu projeto Google. Não envie `client_secret` pelo chat.

1. Na [Google Auth Platform](https://console.cloud.google.com/auth/overview),
   configure o aplicativo e crie um cliente OAuth do tipo aplicação Web.
   Cadastre `http://localhost:8502/oauth2callback` como URI de redirecionamento.
   Durante os testes do Google, inclua as contas administradoras como usuários de teste.
2. Execute o configurador local:

   ```powershell
   .\.venv\Scripts\python.exe configurar_google.py
   ```

   Informe Client ID e Client secret. O segredo é solicitado sem eco na tela.
   O script grava `.streamlit/secrets.toml` e gera um segredo de cookie.
   Também é possível copiar `.streamlit/secrets.example.toml` para `secrets.toml`
   e preencher os valores localmente.
3. Reinicie o Streamlit. O botão **Entrar com Google** será habilitado.

Se baixar o JSON do cliente OAuth Web, salve-o nesta pasta como
`credenciais-google.json` e importe sem copiar os segredos:

```powershell
.\.venv\Scripts\python.exe configurar_google.py --arquivo credenciais-google.json
```

O importador confere a URI de redirecionamento antes de alterar a configuração.
O arquivo de credenciais também está excluído do Git.

Essas instruções seguem o [tutorial oficial do Streamlit para autenticação Google](https://docs.streamlit.io/develop/tutorials/authentication/google).
Se hospedar o site, ajuste a URI no Google e no `secrets.toml` para a URL do
servidor com `/oauth2callback` ao final. Mantenha um `cookie_secret` estável.
Os segredos e o banco de dados estão excluídos do Git.

## Permissões

As contas `contatomaicondouglass@gmail.com` e `projetocpaz@gmail.com` são as
administradoras iniciais. Outras contas Google verificadas ficam aguardando
autorização e aparecem em **Gerenciar acessos**. Um administrador pode definir
ou alterar seu nível de acesso e revogar contas adicionais pela plataforma.
Gestores também podem conceder e revogar acesso de gestor ou responsável de grupo
no próprio CEv, informando o e-mail em **Gerenciar acessos**. Não podem alterar
administradores nem contas de outro CEv. A concessão de exclusão continua restrita
às duas contas administradoras iniciais.

| Perfil | Permissões |
| --- | --- |
| Público, sem login | Consulta avisos gerais e de CEv, retiros e eventos; usa inscrições públicas e pedidos de sua própria inscrição; envia testemunhos para moderação |
| Administrador | Gestão completa e autorização de contas |
| Gestor de CEv | Cadastros, grupos, registros e publicações do seu CEv; concede acesso de gestor ou responsável de grupo no próprio CEv |
| Responsável de grupo | Consulta o próprio grupo e os avisos públicos gerais e do CEv; edita o grupo e seus registros; cria e gerencia avisos, eventos e retiros destinados ao próprio grupo |

Cadastros de pessoas e registros internos não aparecem ao público.
Inscrições e pedidos recebidos são visíveis somente na gestão do evento.
Testemunhos aparecem publicamente após aprovação pela gestão autorizada do evento.
Guarde o código fornecido na inscrição para acessar seus pedidos extras.
O banco armazena o hash desse código, não o código original.

## Importação de Paraipaba

Na gestão do CEv, os indicadores abrem suas listas filtradas. Somente registros
ativos e com pertencimento à comunidade informado entram nas contagens de pessoas:
**Membros da obra** são da categoria Membro e não pertencem à comunidade;
**Comunidade** reúne todos os que pertencem à comunidade, inclusive pastor e núcleo;
**Pastores e núcleo** reúne essas categorias, incluindo as pessoas da comunidade.
Pastores e núcleos da comunidade entram nos dois indicadores; os indicadores não
devem ser somados como um total de pessoas. **Membros engajados** é um
indicador adicional: pessoas ativas que não pertencem à comunidade e possuem
ministério preenchido, incluindo Membro, Pastor e Núcleo. O indicador abre a lista
correspondente. Grupos contam somente quando ativos e não são vínculos neutros.

A lista **Pastores e núcleo** mostra a mesma quantidade do indicador e a divisão
entre obra e comunidade.

Selecione uma linha da lista de pessoas (ou use o seletor) para abrir o perfil
em cartões, com foto e os dados do cadastro. A consulta é restrita aos
administradores, gestores do respectivo CEv e responsáveis de grupo autorizados
cuja conta esteja vinculada a um Pastor/Núcleo do mesmo grupo. O vínculo pessoal
sozinho não concede acesso de gestão. A edição continua sujeita às permissões
existentes, e exclusão depende de concessão explícita.

CAL e C. Vida de Paraipaba são **vínculos neutros**: mantêm as pessoas vinculadas,
mas não contam como grupos de oração. A gestão os encontra na aba **Vínculos neutros**.
A marcação **Vínculo neutro (não contar como grupo de oração)** pode ser editada
no cadastro e permanece válida mesmo quando o nome muda. Vínculos neutros
inativos continuam na aba Inativos.

As abas **Inativos** e **Revisão** conservam o acesso aos cadastros e à edição,
conforme as permissões da conta. Revisão reúne situações ou pertencimentos ausentes.
Grupos inativos ficam fora da consulta pública e dos seletores comuns; a gestão
pode abri-los pela lista de inativos para consultar seus registros.

A primeira etapa importa somente **pessoas e grupos**, incluindo grupos inativos.
Os campos adicionais aprovados estão disponíveis no cadastro e na edição:
e-mail, gênero, serviço, função no serviço, pertence à comunidade e ativo para
pessoas; data de início, público e ativo para grupos. Situações ausentes ficam
como **Não informado**.

`importar_planilha.py` lê o XLSX sem alterar a planilha. O modo padrão apenas
simula. Com o portal inicializado, execute, por exemplo:

```powershell
.\.venv\Scripts\python.exe importar_planilha.py 'CPaz - Paraipaba.xlsx' --cev Paraipaba --report dados/importacoes/simulacao.json
```

Conflitos de fase exigem `--phase ID_Grupo=Fase`. `--pastors-from-members` vincula
o único pastor de cada grupo, quando essa regra tiver sido aprovada.
`--blank-birth ID_Pessoa` deixa um nascimento em branco por decisão explícita,
conservando o valor original no relatório. Para gravar, acrescente `--apply`
e `--expected-sha` com o SHA256 exibido na simulação, mantendo as decisões aprovadas.

A gravação cria um backup em `dados/backups` e aplica todo o lote em uma transação.
Os IDs de origem ficam vinculados aos IDs do portal. Repetir a importação não
duplica registros nem sobrescreve edições posteriores. Correspondências entre
pessoas já cadastradas exigem revisão manual. Referências de fotos externas
e acompanhadores ausentes são registradas para revisão, sem inventar valores.

O relatório desta etapa está em `dados/importacoes/paraipaba-pessoas-grupos.json`.
Os vínculos com ministérios ativos foram importados para o campo **Ministério**,
preservando todos os nomes quando há mais de um. Foram atualizadas 94 pessoas;
o resultado inicial é de 76 engajados. Um vínculo com pessoa ausente foi separado
para revisão. O relatório está em `dados/importacoes/paraipaba-ministerios.json`.
O importador `importar_ministerios.py` usa os IDs da importação anterior, preserva
preenchimentos manuais e não recria pessoas excluídas. Seu modo padrão simula:

```powershell
.\.venv\Scripts\python.exe importar_ministerios.py 'CPaz - Paraipaba.xlsx' --cev Paraipaba --report dados/importacoes/simulacao-ministerios.json
```

Para gravar, use `--apply --expected-sha SHA256_DA_SIMULACAO`. A gravação cria backup
e aplica o lote em uma transação. Situações de vínculo ausentes na origem ficam
documentadas no relatório. Permissões da planilha são preservadas para revisão,
sem conceder acesso automaticamente.

## Importar toda a missão

`importar_missao.py` lê os nove arquivos `CPaz - CEv.xlsx`, simula em uma cópia e
registra pendências em `dados/importacoes`. Importa pessoas, grupos, ministérios,
participações, históricos de grupo e ministério, avisos e vínculos mensais.
As abas Registro CB e FrequenciaCB são excluídas. Campos extras ficam no arquivo
de origem arquivado, sem criar novos campos no portal. Referências inválidas e
presenças conflitantes ficam para revisão; não são inventados vínculos.

```powershell
.\.venv\Scripts\python.exe importar_missao.py
.\.venv\Scripts\python.exe importar_missao.py --apply
```

A aplicação cria backup, verifica alterações concorrentes e aplica o lote em uma
transação. Repetir preserva correções manuais e não recria pessoas excluídas.

Em **Acessos de ministério**, uma conta pode receber vários vínculos. Coordenador
ou Núcleo local gerencia somente seu ministério; o geral gerencia também os locais
vinculados. Coordenadores delegam dentro do alcance autorizado; Núcleo não delega.
Somente Coordenador de Pastoreio possui gestão pastoral ampla: no CEv local ou
em toda a missão quando geral. Exclusão exige concessão explícita dos dois
administradores iniciais. Avisos locais de ministério são privados; gerais são públicos.

## Verificar

```powershell
.\.venv\Scripts\python.exe verificar.py
```

O verificador desativa a conexão de produção durante os testes, que usam bancos
temporários sem inserir dados no portal real. Use esse comando mesmo quando
o computador já estiver conectado ao Supabase.
Cobrem cadastro pela interface, autorização, frequência, acompanhamentos,
fotos, consulta pública, inscrições, isolamento de pedidos, moderação, migração,
auto-vínculo pelo e-mail verificado e isolamento da consulta pessoal.
O fluxo OAuth real deve ser testado após configurar as credenciais Google.

Na nuvem, configure o PostgreSQL conforme [DEPLOY.md](DEPLOY.md). A configuração
`require_remote = true` impede iniciar com um SQLite vazio se faltar a conexão.
Guarde os backups privados para preservar cadastros e fotos.
