"""Interface do C-Paz Missão Itapipoca. Os formulários usam os campos aprovados."""

from datetime import datetime
from io import BytesIO
from ipaddress import ip_address
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from PIL import Image, UnidentifiedImageError
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

import database as db
import access
import crud
import eventos
import edicao
import diretorio
import navegacao
import minha_conta
import avisos

ROOT = Path(__file__).resolve().parent


def today():
    return datetime.now(ZoneInfo("America/Sao_Paulo")).date()


def date_label(value):
    return datetime.strptime(value, "%Y-%m-%d").strftime("%d/%m/%Y") if value else "—"


def status_label(value):
    return "Não informado" if value is None else "Sim" if value else "Não"


def status_input(label):
    value = st.selectbox(label, ["Não informado", "Sim", "Não"])
    return {"Não informado": None, "Sim": 1, "Não": 0}[value]


def load_content():
    try:
        data = json.loads((ROOT / "conteudo.json").read_text(encoding="utf-8-sig"))
        cevs = list(dict.fromkeys(x.strip() for x in data.get("cevs", [])
                                  if isinstance(x, str) and x.strip()))
        notices = [x for x in data.get("avisos", []) if isinstance(x, str) and x.strip()]
        return {"cevs": cevs, "avisos": notices,
                "campos_pessoa_aprovados": data.get("campos_pessoa_aprovados", False)}
    except (OSError, ValueError, TypeError, AttributeError):
        st.error("Não foi possível ler conteudo.json. Verifique o arquivo de configuração.")
        return {"cevs": [], "avisos": [], "campos_pessoa_aprovados": False}


def auth_configuration():
    try:
        config = dict(st.secrets.get("auth", {}))
    except StreamlitSecretNotFoundError:
        return {}
    return config


def google_ready():
    config = auth_configuration()
    google = config.get("google", {})
    return bool(config.get("redirect_uri") and len(config.get("cookie_secret", "")) >= 32
                and google.get("client_id") and google.get("client_secret"))


def login_redirect_error(current_url, redirect_uri):
    """Detecta uma configuração local usada no login do site publicado."""
    if not current_url or not redirect_uri:
        return None
    current, target = urlsplit(current_url), urlsplit(redirect_uri)

    def local(hostname):
        hostname = (hostname or "").lower().rstrip(".")
        if hostname == "localhost" or hostname.endswith(".localhost"):
            return True
        try:
            address = ip_address(hostname)
            return address.is_loopback or address.is_unspecified
        except ValueError:
            return False

    if current.hostname and not local(current.hostname) and local(target.hostname):
        callback = urlunsplit((current.scheme, current.netloc, "/oauth2callback", "", ""))
        return ("O login Google está configurado com um endereço de retorno local. "
                "Em Settings → Secrets do Streamlit, altere redirect_uri na seção [auth] para "
                f"{callback} e salve.")
    return None


def canonical_login_url(current_url, redirect_uri):
    """Evita iniciar o OAuth num domínio diferente do endereço de retorno."""
    if not current_url or not redirect_uri or login_redirect_error(current_url, redirect_uri):
        return None
    current, target = urlsplit(current_url), urlsplit(redirect_uri)
    if target.scheme not in ("http", "https") or not target.hostname:
        return None
    def origin(parts):
        return (parts.scheme.lower(), (parts.hostname or "").lower(),
                parts.port or (443 if parts.scheme == "https" else 80))
    if origin(current) == origin(target):
        return None
    login_path = target.path.rsplit("/", 1)[0] + "/login"
    return urlunsplit((target.scheme, target.netloc, login_path, "", ""))


def authenticated():
    return bool(access.profile(st.user.to_dict()))


def visible_cevs():
    user = access.profile(st.user.to_dict())
    cevs = st.session_state["content"]["cevs"]
    if not user or user['nivel'] in ('Administrador','Serviço'):
        return cevs
    return [name for name in cevs if name == user['cev'] or access.can_manage_cev(user,name)]


def open_public_group(key):
    selected = st.session_state.get(key)
    if selected is not None:
        st.session_state["selected_group"] = selected
        st.session_state.pop("group_page_picker", None)
        st.switch_page(st.session_state["pages"]["group"])


def header(title, description):
    st.title(title)
    st.write(description)
    message = st.session_state.pop("success_message", None)
    if message:
        st.success(message)


def protected():
    if not authenticated():
        st.info("Entre no portal para acessar esta seção.")
        st.page_link(st.session_state["pages"]["login"], label="Ir para o login", icon=":material/login:")
        return False
    return True


def scope():
    if not protected():
        return None
    name = st.session_state.get("selected_cev")
    if name not in st.session_state["content"]["cevs"]:
        st.info("Selecione um CEv/Irradiação na barra lateral para continuar.")
        return None
    user = access.profile(st.user.to_dict())
    if user["cev"] != name and not access.can_manage_cev(user, name):
        st.info("Sua conta não possui acesso à gestão deste CEv/Irradiação.")
        return None
    st.caption(f"CEv/Irradiação: {name}")
    return name


def group_picker(cev, key, private=True):
    groups = db.groups(cev)
    user = access.profile(st.user.to_dict())
    if private or user and user["nivel"] == "Responsável de grupo":
        groups = [g for g in groups if access.can_use_group(user, g)]
    remembered = st.session_state.get("selected_group")
    # Um grupo arquivado pode ser aberto pela gestão, mas não aparece no seletor comum.
    groups = [g for g in groups if (g["ativo"] == 1 and not g["neutro"]) or
              (g["id"] == remembered and access.can_use_group(user, g))]
    if not groups:
        st.info("Nenhum grupo ativo disponível. A gestão pode consultar Inativos e Revisão no CEv.")
        if access.can_manage_cev(access.profile(st.user.to_dict()), cev):
            st.page_link(st.session_state["pages"]["group_create"], label="Cadastrar grupo")
        return None
    labels = {g["id"]: f'{g["nome"]} (#{g["id"]})' +
              (" · Inativo" if g["ativo"] == 0 else " · Revisão" if g["ativo"] is None else "") +
              (" · Vínculo neutro" if g["neutro"] else "") for g in groups}
    remembered = st.session_state.get("selected_group")
    ids = list(labels)
    chosen = st.selectbox("Grupo", ids, index=ids.index(remembered) if remembered in ids else None,
                          format_func=labels.get, placeholder="Selecione um grupo", key=key)
    if chosen is None:
        return None
    st.session_state["selected_group"] = chosen
    return next(g for g in groups if g["id"] == chosen)


def records(rows, columns, empty_message):
    if not rows:
        st.info(empty_message)
        return
    data = []
    for row in rows:
        display = {}
        for key, label in columns.items():
            value = row.get(key)
            if key in ("data", "proximo_acompanhamento", "nascimento", "data_inicio"):
                value = date_label(value)
            elif key in ("ativo", "eh_comunidade"):
                value = status_label(value)
            display[label] = value if value is not None else "—"
        data.append(display)
    st.dataframe(data, hide_index=True, width="stretch")


def saved(message):
    st.session_state["success_message"] = message
    st.rerun()


def photo_data(upload):
    if upload is None:
        return None, None
    if upload.size > 5 * 1024 * 1024:
        raise ValueError("A foto deve ter no máximo 5 MB.")
    raw = upload.getvalue()
    try:
        with Image.open(BytesIO(raw)) as image:
            kind = image.format
            if kind not in ("JPEG", "PNG", "WEBP"):
                raise ValueError("Formato de imagem não permitido.")
            image.verify()
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("Envie uma imagem JPG, PNG ou WebP válida.") from exc
    return raw, kind


def home():
    pages = st.session_state["pages"]
    content = st.session_state["content"]
    st.caption("C-Paz · GRUPOS E COMUNIDADE")
    st.title("Um lugar para conectar sua comunidade.")
    st.write("Acesse seu CEv/Irradiação e acompanhe a vida do seu grupo.")
    st.page_link(pages["login"], label="Acesso à gestão", icon=":material/login:")
    st.divider()
    left, right = st.columns([1.4, 1], gap="large")
    with left:
        st.subheader("Avisos", icon=":material/campaign:")
        public_content()
    with right:
        with st.container(border=True):
            st.subheader("Seu CEv/Irradiação", icon=":material/location_on:")
            st.write("Selecione a unidade que deseja acessar.")
            if content["cevs"]:
                chosen = st.selectbox("CEv/Irradiação", visible_cevs(), index=None,
                                      placeholder="Selecione uma unidade", key="home_cev")
                if st.button("Acessar CEv", type="primary", disabled=chosen is None, width="stretch"):
                    st.session_state["selected_cev"] = chosen
                    st.session_state.pop("cev_sidebar", None)
                    st.session_state.pop("selected_group", None)
                    st.switch_page(pages["cev"])
            else:
                st.info("Os nomes de CEv/Irradiação ainda não foram informados.")


def login():
    header("Entrar no portal", "Acesse seu perfil e os recursos autorizados da comunidade.")
    if authenticated():
        st.success("Você já está conectado.")
        st.page_link(st.session_state["pages"]["cev"], label="Acessar CEv/Irradiação")
        st.page_link(st.session_state["pages"]["profile"], label="Meu perfil")
        return
    if st.user.to_dict().get("is_logged_in"):
        if access.verified_email(st.user.to_dict()):
            st.info("Seu perfil pessoal está disponível. O acesso de gestão depende de autorização.")
            st.page_link(st.session_state["pages"]["profile"], label="Meu perfil")
        else:
            st.error("Sua sessão expirou ou a conta não possui e-mail verificado.")
        st.button("Sair e trocar de conta", on_click=st.logout)
        return
    with st.container(width=460):
        st.write("Use sua conta Google para acessar seu perfil. Recursos de gestão dependem de autorização.")
        redirect_uri = auth_configuration().get("redirect_uri")
        error = login_redirect_error(st.context.url, redirect_uri)
        ready = google_ready() and not error
        canonical = canonical_login_url(st.context.url, redirect_uri) if ready else None
        if canonical:
            st.link_button("Entrar com Google", canonical, type="primary", width="stretch")
        else:
            st.button("Entrar com Google", on_click=st.login, args=["google"],
                      type="primary", width="stretch", disabled=not ready)
        if error:
            st.error(error)
        elif not ready:
            st.info("O acesso com Google ainda está em configuração.")


def cev():
    header("CEv/Irradiação", "Avisos, retiros e eventos da sua comunidade.")
    name = st.session_state.get("selected_cev")
    if name not in st.session_state["content"]["cevs"]:
        st.info("Selecione um CEv/Irradiação na barra lateral para consultar avisos e eventos.")
        return
    st.caption(name)
    user = access.profile(st.user.to_dict())
    if user and user["nivel"] == "Responsável de grupo" and not access.can_manage_cev(user, name):
        if user["cev"] != name:
            st.info("Sua conta está limitada ao CEv e grupo autorizados.")
            return
        st.subheader("Avisos e eventos do seu grupo")
        st.session_state["selected_group"] = user["grupo_id"]
        st.page_link(st.session_state["pages"]["group"], label="Abrir meu grupo", icon=":material/groups:")
        public_content(cev=name, group_id=user["grupo_id"])
        return
    public_content(cev=name)
    groups = db.groups(name)
    if not access.can_manage_cev(user, name):
        st.subheader("Avisos e eventos dos grupos")
        groups = [g for g in groups if g["ativo"] == 1 and not g["neutro"]]
        if not groups:
            st.info("Nenhum grupo disponível.")
        labels = {g["id"]: g["nome"] for g in groups}
        picker = "public_groups_" + name
        st.selectbox("Grupo para consultar", list(labels), index=None, format_func=labels.get,
                     placeholder="Selecione um grupo", key=picker, disabled=not labels,
                     on_change=open_public_group, args=(picker,))
        return
    st.divider()
    st.subheader("Gestão do CEv")
    people = db.people(name)
    diretorio.cev(name, people, groups)


def group():
    header("Grupo", "Avisos, retiros e eventos do grupo.")
    name = st.session_state.get("selected_cev")
    if name not in st.session_state["content"]["cevs"]:
        st.info("Selecione um CEv/Irradiação para consultar os grupos.")
        return
    g = group_picker(name, "group_page_picker", private=False)
    if not g:
        return
    st.subheader(g["nome"])
    if g["neutro"]:
        st.info("Vínculo neutro para pessoas sem grupo de oração. Não entra na contagem de grupos.")
    public_content(cev=name, group_id=g["id"])
    if not access.can_use_group(access.profile(st.user.to_dict()), g):
        return
    st.divider()
    st.subheader("Gestão do grupo")
    st.caption(f'Fase: {g["fase"]} · Pastor: {g["pastor"] or "—"}')
    if g["foto"]:
        st.image(g["foto"], width=360)
    st.write(f'Local: {g["local"] or "—"} · Dia: {g["dia_encontro"] or "—"} · Horário: {g["horario"] or "—"}')
    st.caption(f'Público: {g["publico"] or "—"} · Ativo: {status_label(g["ativo"])} · Início: {g["data_inicio"] or "—"}')
    if access.can_use_group(access.profile(st.user.to_dict()), g):
        edicao.link("groups", "Editar grupo", g["id"])
    columns = st.columns(3)
    for col, key, label, icon in zip(columns, ["meeting", "attendance", "followup"],
                                    ["Registrar encontro", "Registrar frequência", "Registrar acompanhamento"],
                                    [":material/event:", ":material/checklist:", ":material/diversity_1:"]):
        with col:
            st.page_link(st.session_state["pages"][key], label=label, icon=icon)
    st.divider()
    tabs = st.tabs(["Pessoas", "Encontros", "Frequência", "Acompanhamentos"])
    with tabs[0]:
        diretorio.group_people(db.group_people(g["id"]), f'group_people_{g["id"]}')
    with tabs[1]:
        records(db.meetings(g["id"]), {"data": "Data", "tema": "Tema", "observacoes": "Observações"},
                "Nenhum encontro registrado.")
    with tabs[2]:
        records(db.attendance(g["id"]), {"data": "Data", "tema": "Encontro", "membro": "Membro", "presenca": "Presença"},
                "Nenhuma frequência registrada.")
    with tabs[3]:
        records(db.followups(g["id"]), {"membro": "Membro", "data": "Data", "acompanhador": "Acompanhador",
                "proximo_acompanhamento": "Próximo acompanhamento", "observacoes": "Observações"},
                "Nenhum acompanhamento registrado.")


def group_create():
    header("Cadastro de grupo", "Crie um grupo vinculado ao seu CEv/Irradiação.")
    name = scope()
    if not name:
        return
    if not access.can_manage_cev(access.profile(st.user.to_dict()), name):
        st.info("Seu nível de acesso permite registrar dados do grupo, mas não cadastrar grupos.")
        return
    pastors = {p["id"]: f'{p["nome"]} (#{p["id"]})' for p in db.people(name) if p["categoria"] == "Pastor"}
    st.caption("* Obrigatório. A foto pode ser JPG, PNG ou WebP, de até 5 MB.")
    if not pastors:
        st.info("Cadastre um pastor para vinculá-lo ao grupo. É possível cadastrar um pastor sem grupo.")
    with st.form("group_form", clear_on_submit=True):
        nome = st.text_input("Nome *")
        st.text_input("CEv/Irradiação", value=name, disabled=True)
        pastor_id = st.selectbox("Pastor responsável", list(pastors), index=None, format_func=pastors.get,
                                 placeholder="Selecione um pastor", disabled=not pastors)
        local = st.text_input("Local")
        dia_encontro = st.selectbox("Dia do encontro", db.WEEKDAYS, index=None,
                                    placeholder="Selecione o dia da semana")
        horario = st.time_input("Horário dos encontros", value=None)
        fase = st.selectbox("Fase do grupo *", db.PHASES, index=None, placeholder="Selecione a fase")
        data_inicio = st.date_input("Data de início", value=None, format="DD/MM/YYYY")
        publico = st.text_input("Público")
        ativo = status_input("Ativo")
        neutro = st.checkbox("Vínculo neutro (não contar como grupo de oração)")
        foto = st.file_uploader("Foto do grupo", type=["jpg", "jpeg", "png", "webp"])
        submit = st.form_submit_button("Cadastrar grupo", type="primary")
    if submit:
        try:
            image_bytes, image_type = photo_data(foto)
            group_id = db.save_group(name, nome, pastor_id, local,
                                     horario.strftime("%H:%M") if horario else "", fase, image_bytes, image_type,
                                     dia_encontro=dia_encontro, data_inicio=data_inicio.isoformat() if data_inicio else None,
                                     publico=publico, ativo=ativo, neutro=neutro)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state["selected_group"] = group_id
            saved("Grupo cadastrado.")


def person_create():
    header("Cadastro de membro/pastor", "Cadastre uma pessoa na comunidade.")
    if not st.session_state["content"]["campos_pessoa_aprovados"]:
        st.info("Os campos deste cadastro estão aguardando sua aprovação.")
        return
    name = scope()
    if not name:
        return
    if not access.can_manage_cev(access.profile(st.user.to_dict()), name):
        st.info("O cadastro de pessoas é exclusivo do gestor de CEv e dos administradores.")
        return
    groups = {g["id"]: f'{g["nome"]} (#{g["id"]})' +
              (" · Vínculo neutro" if g["neutro"] else "") for g in db.groups(name)}
    st.caption("* Obrigatório. O grupo pode ser vinculado no cadastro; pastores podem ser responsáveis por grupos.")
    with st.form("person_form", clear_on_submit=True):
        nome = st.text_input("Nome *")
        categoria = st.selectbox("Categoria *", db.CATEGORIES, index=None,
                                 placeholder="Selecione a categoria")
        contato = st.text_input("Contato")
        grupo_id = st.selectbox("Grupo", list(groups), index=None, format_func=groups.get,
                                placeholder="Selecione um grupo", disabled=not groups)
        instagram = st.text_input("Instagram")
        endereco = st.text_area("Endereço")
        nascimento = st.date_input("Data de nascimento", value=None, max_value=today(),
                                   min_value=today().replace(year=today().year - 120, month=1, day=1), format="DD/MM/YYYY")
        foto = st.file_uploader("Foto", type=["jpg", "jpeg", "png", "webp"])
        acompanhador = st.text_input("Acompanhador")
        ministerio = st.text_input("Ministério")
        email = st.text_input("E-mail")
        genero = st.text_input("Gênero")
        servico = st.text_input("Serviço")
        funcao_servico = st.text_input("Função no serviço")
        eh_comunidade = status_input("Pertence à comunidade")
        ativo = status_input("Ativo")
        submit = st.form_submit_button("Cadastrar pessoa", type="primary")
    if submit:
        try:
            image_bytes, image_type = photo_data(foto)
            db.save_person(name, nome, categoria, contato, grupo_id, instagram, endereco,
                           nascimento.isoformat() if nascimento else None, image_bytes, image_type,
                           acompanhador, ministerio, email=email, genero=genero, servico=servico,
                           funcao_servico=funcao_servico, eh_comunidade=eh_comunidade, ativo=ativo)
        except ValueError as exc:
            st.error(str(exc))
        else:
            saved("Pessoa cadastrada.")


def meeting():
    header("Encontro", "Registre um encontro do grupo.")
    name = scope()
    if not name:
        return
    g = group_picker(name, "meeting_group")
    if not g:
        return
    with st.form("meeting_form", clear_on_submit=True):
        data = st.date_input("Data", value=today(), format="DD/MM/YYYY")
        tema = st.text_input("Tema")
        observacoes = st.text_area("Observações")
        submit = st.form_submit_button("Salvar encontro", type="primary")
    if submit:
        db.execute("INSERT INTO meetings (grupo_id,data,tema,observacoes) VALUES (?,?,?,?)",
                   (g["id"], data.isoformat(), tema.strip(), observacoes.strip()))
        saved("Encontro registrado.")
    st.subheader("Encontros registrados")
    records(db.meetings(g["id"]), {"data": "Data", "tema": "Tema", "observacoes": "Observações"},
            "Nenhum encontro registrado.")
    edicao.link("meetings", "Editar encontros")


def attendance():
    header("Frequência", "Registre a participação em um encontro.")
    name = scope()
    if not name:
        return
    g = group_picker(name, "attendance_group")
    if not g:
        return
    meetings = {e["id"]: f'{date_label(e["data"])} · {e["tema"] or "Sem tema"} (#{e["id"]})'
                for e in db.meetings(g["id"])}
    members = {p["id"]: f'{p["nome"]} (#{p["id"]})' for p in db.group_people(g["id"])}
    if not meetings or not members:
        st.info("O grupo precisa de um encontro e de uma pessoa vinculada para registrar frequência.")
        return
    st.caption("Cada pessoa tem uma frequência por encontro. Salvar novamente atualiza o registro.")
    with st.form("attendance_form", clear_on_submit=True):
        encontro_id = st.selectbox("Encontro *", list(meetings), index=None, format_func=meetings.get,
                                   placeholder="Selecione o encontro")
        membro_id = st.selectbox("Membro *", list(members), index=None, format_func=members.get,
                                 placeholder="Selecione o membro")
        presenca = st.selectbox("Presença *", db.PRESENCES, index=None, placeholder="Selecione a presença")
        submit = st.form_submit_button("Salvar frequência", type="primary")
    if submit:
        if encontro_id is None or membro_id is None or presenca is None:
            st.error("Selecione o encontro, o membro e a presença.")
        else:
            try:
                db.save_attendance(encontro_id, membro_id, presenca)
            except ValueError as exc:
                st.error(str(exc))
            else:
                saved("Frequência registrada.")
    st.subheader("Frequências registradas")
    records(db.attendance(g["id"]), {"data": "Data", "tema": "Encontro", "membro": "Membro", "presenca": "Presença"},
            "Nenhuma frequência registrada.")
    edicao.link("attendance", "Editar frequências")


def followup():
    header("Acompanhamento", "Registre o acompanhamento das pessoas do grupo.")
    name = scope()
    if not name:
        return
    g = group_picker(name, "followup_group")
    if not g:
        return
    members = {p["id"]: f'{p["nome"]} (#{p["id"]})' for p in db.group_people(g["id"])}
    if not members:
        st.info("Cadastre uma pessoa neste grupo para registrar um acompanhamento.")
        return
    with st.form("followup_form", clear_on_submit=True):
        membro_id = st.selectbox("Membro *", list(members), index=None, format_func=members.get,
                                 placeholder="Selecione o membro")
        data = st.date_input("Data", value=today(), format="DD/MM/YYYY")
        acompanhador = st.text_input("Acompanhador *")
        proximo = st.date_input("Próximo acompanhamento", value=None, format="DD/MM/YYYY")
        observacoes = st.text_area("Observações")
        submit = st.form_submit_button("Salvar acompanhamento", type="primary")
    if submit:
        if membro_id is None:
            st.error("Selecione o membro.")
        else:
            try:
                db.save_followup(g["id"], membro_id, data.isoformat(), acompanhador,
                                 proximo.isoformat() if proximo else None, observacoes)
            except ValueError as exc:
                st.error(str(exc))
            else:
                saved("Acompanhamento registrado.")
    st.subheader("Acompanhamentos registrados")
    records(db.followups(g["id"]), {"membro": "Membro", "data": "Data", "acompanhador": "Acompanhador",
            "proximo_acompanhamento": "Próximo acompanhamento", "observacoes": "Observações"},
            "Nenhum acompanhamento registrado.")
    edicao.link("followups", "Editar acompanhamentos")


def manage_access():
    header("Gerenciar acessos", "Autorize contas e defina os níveis de acesso ao portal.")
    user = access.profile(st.user.to_dict())
    if not access.can_manage_accounts(user):
        st.info("Esta seção é exclusiva dos administradores e gestores de CEv.")
        return
    admin = user["nivel"] == "Administrador"
    requests = db.query("SELECT * FROM access_requests ORDER BY nome, email") if admin else []
    if admin:
        st.subheader("Administradores iniciais")
        for email in sorted(access.ADMIN_EMAILS):
            st.write(email)
        st.subheader("Contas aguardando autorização")
        records(requests, {"nome": "Nome da conta Google", "email": "E-mail"},
                "Nenhuma solicitação de acesso pendente.")
    else:
        st.caption(f'Autorize gestores e responsáveis de grupo de {user["cev"]}, informando o e-mail da conta Google.')
    st.subheader("Autorizar conta ou alterar acesso")
    requested_email = st.selectbox("Solicitação pendente", [r["email"] for r in requests], index=None,
                                   placeholder="Selecione uma solicitação ou informe o e-mail abaixo") if admin else None
    level = st.selectbox("Nível de acesso", access.delegable_levels(user), index=None, placeholder="Selecione o nível")
    selected_cev, group_id = None, None
    if level in ("Gestor de CEv", "Responsável de grupo"):
        if admin:
            selected_cev = st.selectbox("CEv/Irradiação autorizado", st.session_state["content"]["cevs"],
                                        index=None, placeholder="Selecione o CEv/Irradiação")
        else:
            selected_cev = user["cev"]
            st.caption(f"CEv/Irradiação autorizado: {selected_cev}")
        if level == "Responsável de grupo" and selected_cev:
            groups = {g["id"]: g["nome"] for g in db.groups(selected_cev)}
            group_id = st.selectbox("Grupo autorizado", list(groups), index=None,
                                    format_func=groups.get, placeholder="Selecione o grupo")
    with st.form("access_form"):
        email = st.text_input("E-mail da conta Google", value=requested_email or "")
        submit = st.form_submit_button("Salvar autorização", type="primary", disabled=level is None)
    if submit:
        try:
            access.grant(st.user.to_dict(), email, level, selected_cev, group_id)
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))
        else:
            saved("Autorização salva.")
    st.subheader("Contas autorizadas")
    grants = access.account_grants(st.user.to_dict())
    records(grants, {"email": "E-mail", "nivel": "Nível", "cev": "CEv/Irradiação", "grupo": "Grupo"},
            "Nenhuma conta adicional autorizada.")
    if grants:
        with st.form("revoke_access"):
            email_to_revoke = st.selectbox("Conta para revogar acesso", [g["email"] for g in grants], index=None)
            revoke_submit = st.form_submit_button("Revogar acesso")
        if revoke_submit:
            if email_to_revoke is None:
                st.error("Selecione a conta para revogar o acesso.")
            else:
                try:
                    access.revoke(st.user.to_dict(), email_to_revoke)
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
                else:
                    saved("Acesso revogado.")
    if admin:
        deletion_permissions(user, grants)
    minha_conta.manage_links()


def deletion_permissions(user, grants):
    st.subheader("Permissões de exclusão")
    rows = db.query("""SELECT p.*,g.nome AS grupo FROM deletion_permissions p
        LEFT JOIN groups g ON g.id=p.grupo_id ORDER BY p.email,p.escopo""")
    records(rows, {"email": "Conta", "cev": "CEv/Irradiação", "grupo": "Grupo", "escopo": "Destino autorizado"},
            "Nenhuma conta tem permissão de exclusão.")
    if user["email"] not in access.ADMIN_EMAILS:
        st.caption("As permissões de exclusão são concedidas somente pelos administradores iniciais.")
        return
    st.caption("A permissão não amplia o nível de acesso. Cada concessão cobre somente o destino selecionado.")
    accounts = sorted(set(access.ADMIN_EMAILS) | {g["email"] for g in grants})
    email = st.selectbox("Conta para permissão de exclusão", accounts, index=None,
                        placeholder="Selecione a conta", key="delete_permission_account")
    if email is None:
        return
    target = {"nivel": "Administrador"} if email in access.ADMIN_EMAILS else next(g for g in grants if g["email"] == email)
    cev, group_id = None, None
    ready = False
    if target["nivel"] == "Administrador":
        destination = st.selectbox("Limite da permissão de exclusão", ["Publicações gerais", "CEv/Irradiação", "Grupo"], index=None)
        if destination == "Publicações gerais":
            ready = True
        elif destination in ("CEv/Irradiação", "Grupo"):
            cev = st.selectbox("CEv para exclusão", st.session_state["content"]["cevs"], index=None)
            ready = bool(cev)
            if destination == "Grupo" and cev:
                groups = {g["id"]: g["nome"] for g in db.groups(cev)}
                group_id = st.selectbox("Grupo para exclusão", list(groups), index=None, format_func=groups.get)
                ready = group_id is not None
    elif target["nivel"] == "Gestor de CEv":
        cev, ready = target["cev"], True
        st.write(f"Destino: {cev}")
    else:
        cev, group_id, ready = target["cev"], target["grupo_id"], True
        group = db.query("SELECT nome FROM groups WHERE id=?", (group_id,))
        st.write(f'Destino: {cev} · {group[0]["nome"] if group else "Grupo indisponível"}')
    if ready:
        with st.form("deletion_permission_form"):
            grant = st.form_submit_button("Conceder permissão de exclusão")
            revoke = st.form_submit_button("Retirar permissão de exclusão")
        if grant or revoke:
            try:
                access.set_deletion_permission(st.user.to_dict(), email, grant, cev, group_id)
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))
            else:
                saved("Permissão de exclusão concedida." if grant else "Permissão de exclusão retirada.")


def publication_target(key_prefix=None):
    user = access.profile(st.user.to_dict())
    if not user:
        st.info("Entre com uma conta autorizada para publicar.")
        return None
    if user["nivel"] == "Responsável de grupo" and not user.get('servicos'):
        groups = db.query("SELECT nome FROM groups WHERE id=? AND cev=?", (user["grupo_id"], user["cev"]))
        if not groups:
            st.info("O grupo autorizado não está disponível.")
            return None
        st.caption(f'Destino: {user["cev"]} · {groups[0]["nome"]}')
        return "Grupo", user["cev"], user["grupo_id"]
    admin = user["nivel"] == "Administrador"
    import servicos
    ministries = [m for m in db.query('SELECT * FROM ministries ORDER BY nome,cev') if servicos.can_manage(user, m)]
    options = ["Geral", "CEv/Irradiação", "Grupo"] if admin else ["CEv/Irradiação", "Grupo"] if user['nivel'] == 'Gestor de CEv' else ['Grupo'] if user['nivel']=='Responsável de grupo' else []
    if ministries:
        options.append('Ministério')
    destino = st.selectbox("Destino", options,
                           index=None, placeholder="Selecione o destino",
                           key=key_prefix + "_destino" if key_prefix else None)
    cev, group_id = None, None
    if destino == 'Ministério':
        labels = {m['id']: m['nome']+' · '+(m['cev'] or 'Geral') for m in ministries}
        chosen = st.selectbox('Ministério de destino', list(labels), format_func=labels.get, index=None,
                              key=(key_prefix or 'publication')+'_ministry')
        class Target(tuple):
            pass
        target = Target((destino, next((m['cev'] for m in ministries if m['id'] == chosen), None), None))
        target.ministerio_id = chosen
        return target
    if destino in ("CEv/Irradiação", "Grupo"):
        cevs = st.session_state["content"]["cevs"]
        options = cevs if admin else [user["cev"]] if user["cev"] in cevs else []
        cev = st.selectbox("CEv/Irradiação de destino", options, index=None, placeholder="Selecione o CEv/Irradiação",
                           key=key_prefix + "_cev" if key_prefix else None)
        if destino == "Grupo" and cev:
            groups = {g["id"]: g["nome"] for g in db.groups(cev) if access.can_use_group(user,g)}
            group_id = st.selectbox("Grupo de destino", list(groups), index=None,
                                   format_func=groups.get, placeholder="Selecione o grupo",
                                   key=key_prefix + "_grupo" if key_prefix else None)
    return destino, cev, group_id


def notice_create():
    header("Publicar aviso", "Crie avisos gerais, do CEv/Irradiação ou de um grupo.")
    target = publication_target()
    if target is None:
        return
    edicao.link("notices", "Editar avisos")
    with st.form("notice_form", clear_on_submit=True):
        titulo = st.text_input("Título *")
        texto = st.text_area("Texto")
        foto = st.file_uploader("Foto do aviso", type=["jpg", "jpeg", "png", "webp"])
        submit = st.form_submit_button("Publicar aviso", type="primary", disabled=target[0] is None)
    if submit:
        try:
            raw, kind = photo_data(foto)
            crud.create_notice(st.user.to_dict(), titulo, texto, *target, raw, kind,
                               ministerio_id=getattr(target, 'ministerio_id', None))
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))
        else:
            saved("Aviso publicado.")


def public_content(cev=None, group_id=None):
    """A consulta pública apresenta apenas as publicações autorizadas."""
    user = access.profile(st.user.to_dict())
    if user and user["nivel"] == "Responsável de grupo":
        cev, group_id = user["cev"], user["grupo_id"]
    avisos.render(st.user.to_dict(), cev, group_id, st.session_state["content"]["avisos"])
    st.subheader("Retiros e eventos", icon=":material/event:")
    eventos.cards(cev, group_id)


def main():
    st.set_page_config(page_title="C-Paz", page_icon=str(ROOT / "logo.png"), layout="wide",
                       initial_sidebar_state="auto")
    db.initialize()
    content = load_content()
    st.session_state["content"] = content
    profile = access.profile(st.user.to_dict())
    st.html("""<style>
        .stMainBlockContainer {max-width: 1160px; padding-top: 5rem; padding-bottom: 3rem;}
        h1 {letter-spacing: -0.035em;} h3 {letter-spacing: -0.02em;}
        [data-testid="stSidebar"] {border-right: 1px solid color-mix(in srgb, currentColor 15%, transparent);}
        [data-testid="stVerticalBlockBorderWrapper"] {border-radius: 16px;}
        .portal-brand {font-size: 1.4rem; font-weight: 750; color: inherit; margin-bottom: .25rem;}
        .portal-subtitle {color: color-mix(in srgb, currentColor 75%, transparent); font-size: .85rem; margin-bottom: 1.5rem;}
        @media(max-width:640px) {.stMainBlockContainer {padding-top:4rem;}}
        </style>""")
    pages = {
        "ministries": st.Page(__import__('ministerios_ui').page, title="Ministérios", icon=":material/diversity_3:", url_path="ministerios"),
        "service_access": st.Page(__import__('ministerios_ui').access_page, title="Acessos de serviços", icon=":material/key:", url_path="acessos-servicos"),
        "home": st.Page(home, title="Início", icon=":material/home:", default=True),
        "login": st.Page(login, title="Login", icon=":material/login:", url_path="login"),
        "cev": st.Page(cev, title="CEv/Irradiação", icon=":material/location_on:", url_path="cev"),
        "group": st.Page(group, title="Grupo", icon=":material/groups:", url_path="grupo"),
        "group_create": st.Page(group_create, title="Cadastrar grupo", icon=":material/add_circle:", url_path="cadastro-grupo"),
        "person_create": st.Page(person_create, title="Cadastrar membro/pastor", icon=":material/person_add:", url_path="cadastro-pessoa"),
        "meeting": st.Page(meeting, title="Encontro", icon=":material/event:", url_path="encontro"),
        "attendance": st.Page(attendance, title="Frequência", icon=":material/checklist:", url_path="frequencia"),
        "followup": st.Page(followup, title="Acompanhamento", icon=":material/diversity_1:", url_path="acompanhamento"),
        "edit": st.Page(edicao.page, title="Editar registros", icon=":material/edit:", url_path="editar-registros"),
        "access": st.Page(manage_access, title="Gerenciar acessos", icon=":material/admin_panel_settings:",
                          url_path="acessos"),
        "notice": st.Page(notice_create, title="Publicar aviso", icon=":material/campaign:", url_path="avisos"),
        "event_create": st.Page(eventos.create, title="Criar evento/retiro", icon=":material/event_available:", url_path="criar-evento"),
        "event_manage": st.Page(eventos.manage, title="Gerenciar eventos", icon=":material/settings:", url_path="gerenciar-eventos"),
        "event": st.Page(eventos.detail, title="Evento/retiro", icon=":material/celebration:", url_path="evento"),
        "profile": st.Page(minha_conta.profile_page, title="Meu perfil", icon=":material/manage_accounts:", url_path="meu-perfil"),
        "personal": st.Page(minha_conta.personal_page, title="Minhas informações", icon=":material/person:", url_path="minhas-informacoes"),
    }
    st.session_state["pages"] = pages
    sections = {"Portal": [pages["home"], pages["login"]],
                "Comunidade": [pages["cev"], pages["group"]],
                "Eventos": [pages["event"]]}
    profile = access.profile(st.user.to_dict())
    if access.verified_email(st.user.to_dict()):
        sections["Minha área"] = [pages["profile"], pages["personal"]]
    if profile:
        if profile["nivel"] in ("Administrador", "Gestor de CEv"):
            sections["Cadastros"] = [pages["group_create"], pages["person_create"]]
        sections["Publicações"] = [pages["notice"], pages["event_create"], pages["event_manage"]]
        sections["Registros"] = [pages["meeting"], pages["attendance"], pages["followup"], pages["edit"]]
        sections['Ministérios'] = [pages['ministries'], pages['service_access']]
        if any(access.can_manage_cev(profile, name) for name in st.session_state['content']['cevs']):
            sections['Cadastros'] = [pages['group_create'], pages['person_create']]
    elif access.verified_email(st.user.to_dict()):
        sections['Ministérios'] = [pages['ministries']]
    if access.can_manage_accounts(profile):
        sections["Administração"] = [pages["access"]]
    nav = st.navigation(sections, position="hidden")
    navegacao.render(pages, sections, profile)
    nav.run()
