"""Páginas pessoais e definição do vínculo da conta com o membro."""

import streamlit as st

import access
import database as db
import perfis
import avisos


def _ui():
    import portal
    return portal


def _account():
    account = perfis.own(st.user.to_dict())
    if account is None:
        st.info("Entre com sua conta Google para acessar suas informações.")
        st.page_link(st.session_state["pages"]["login"], label="Entrar com Google", icon=":material/login:")
    return account


def profile_page():
    ui = _ui()
    ui.header("Meu perfil", "Escolha como seu nome aparece no portal.")
    account = _account()
    if account is None:
        return
    st.caption(account["email"])
    with st.form("my_profile"):
        nome = st.text_input("Nome", value=account["nome_formulario"], max_chars=120)
        apelido = st.text_input("Apelido", value=account["apelido"], max_chars=120,
                               help="Se preenchido, aparece no cartão do usuário.")
        submit = st.form_submit_button("Salvar meu perfil", type="primary")
    if submit:
        try:
            perfis.save_own(st.user.to_dict(), nome, apelido)
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))
        else:
            ui.saved("Seu perfil foi atualizado.")
    with st.container(border=True):
        st.subheader("Vínculo com a comunidade")
        if account["membro_id"]:
            st.write(account["membro_nome"])
            st.caption(f'{account["membro_cev"]} · {account["grupo"] or "Sem grupo/célula informado"}')
            st.page_link(st.session_state["pages"]["personal"], label="Ver minhas informações", icon=":material/person:")
        else:
            st.info("Vincule seu cadastro abaixo ou solicite o vínculo à gestão.")
    candidates = perfis.candidates(st.user.to_dict())
    if candidates:
        labels = {m["id"]: f'{m["nome"]} · {m["cev"]} · {m["grupo"] or "Sem grupo/célula"} (#{m["id"]})' for m in candidates}
        st.caption("São exibidos somente cadastros com o mesmo e-mail verificado da sua conta Google.")
        with st.form("self_member_link"):
            selected = st.selectbox("Meu cadastro de membro", list(labels), format_func=labels.get,
                                    index=list(labels).index(account["membro_id"]) if account["membro_id"] in labels else None,
                                    placeholder="Selecione seu cadastro")
            link = st.form_submit_button("Vincular meu cadastro", type="primary")
        if link:
            try:
                if selected is None:
                    raise ValueError("Selecione seu cadastro de membro.")
                perfis.self_link(st.user.to_dict(), selected)
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))
            else:
                ui.saved("Seu cadastro foi vinculado à sua conta.")
    elif not account["membro_id"]:
        st.caption("Para se vincular por aqui, seu cadastro de membro precisa ter o e-mail desta conta Google. A gestão também pode definir o vínculo.")
    if account["membro_id"] and st.button("Retirar meu vínculo", icon=":material/link_off:"):
        try:
            perfis.self_link(st.user.to_dict(), None)
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))
        else:
            ui.saved("Seu vínculo foi retirado.")


def personal_page():
    ui = _ui()
    ui.header("Minhas informações", "Seu cadastro e sua participação na comunidade.")
    if _account() is None:
        return
    data = perfis.personal(st.user.to_dict())
    if data is None:
        st.info("Sua conta ainda não está vinculada a um membro. Solicite o vínculo à gestão do seu CEv.")
        return
    member = data["membro"]
    with st.container(border=True):
        st.subheader(member["nome"])
        if member["foto"]:
            st.image(member["foto"], width=200)
        st.caption(f'{member["cev"]} · {member["categoria"]}')
        st.write("**Vínculo neutro:**" if member["neutro"] else "**Grupo/célula:**", member["grupo"] or "Não informado")
        st.write("**Ministério:**", member["ministerio"] or "Não informado")
        with st.expander("Meu cadastro completo"):
            ui.records([member], {"nome":"Nome", "categoria":"Categoria", "contato":"Contato", "grupo":"Grupo/célula",
                "instagram":"Instagram", "endereco":"Endereço", "nascimento":"Data de nascimento", "acompanhador":"Acompanhador",
                "ministerio":"Ministério", "email":"E-mail", "genero":"Gênero", "servico":"Serviço", "funcao_servico":"Função no serviço",
                "eh_comunidade":"Pertence à comunidade", "ativo":"Ativo"}, "")
    st.subheader("Meus avisos", icon=":material/campaign:")
    avisos.render(st.user.to_dict(), member["cev"], member["grupo_id"], st.session_state["content"]["avisos"])
    with st.container(border=True):
        st.subheader("Meus acompanhamentos", icon=":material/diversity_1:")
        left, right = st.columns(2)
        left.metric(f'Acompanhamentos em {data["ano"]}', data["acompanhamentos"]["no_ano"])
        right.metric("Último acompanhamento", ui.date_label(data["acompanhamentos"]["ultima_data"]))
    st.subheader("Minha frequência", icon=":material/fact_check:")
    ui.records(data["frequencia"], {"data":"Data", "tema":"Encontro", "grupo":"Grupo/célula", "presenca":"Presença"},
               "Nenhuma frequência registrada para você.")


def manage_links():
    user = access.profile(st.user.to_dict())
    if not access.can_manage_accounts(user):
        return
    ui = _ui()
    st.subheader("Vincular conta a um membro", icon=":material/link:")
    st.caption("O vínculo permite consultar as próprias informações e não concede acesso de gestão.")
    linked = perfis.links(st.user.to_dict())
    ui.records(linked, {"email":"Conta Google", "nome":"Nome da conta", "apelido":"Apelido", "membro":"Membro",
                        "cev":"CEv/Irradiação", "grupo":"Grupo/célula"}, "Nenhuma conta vinculada neste alcance.")
    cevs = st.session_state["content"]["cevs"] if user["nivel"] == "Administrador" else [user["cev"]]
    cev = st.selectbox("CEv do membro", cevs, index=None, key="profile_link_cev", placeholder="Selecione a unidade")
    members = db.people(cev) if cev else []
    options = {m["id"]: m["nome"] + f' (#{m["id"]})' + (" · Inativo" if m["ativo"] == 0 else "") for m in members}
    known = sorted({r["email"] for r in linked} | {r["email"] for r in access.account_grants(st.user.to_dict())})
    if user["nivel"] == "Administrador":
        known = sorted(set(known) | access.ADMIN_EMAILS | {r["email"] for r in db.query("SELECT email FROM access_requests")} |
                       {r["email"] for r in db.query("SELECT email FROM account_profiles")})
    chosen = st.selectbox("Conta conhecida", known, index=None, placeholder="Selecione ou informe o e-mail abaixo", key="profile_link_account")
    with st.form("member_account_link"):
        email = st.text_input("E-mail para vincular ao membro", value=chosen or "")
        selected = next((r["membro_id"] for r in linked if r["email"] == chosen), None)
        member_id = st.selectbox("Membro para vincular", list(options), index=list(options).index(selected) if selected in options else None,
                                format_func=options.get, placeholder="Selecione um membro")
        save = st.form_submit_button("Salvar vínculo", type="primary", disabled=not options)
        unlink = st.form_submit_button("Retirar vínculo")
    if save or unlink:
        try:
            if save and member_id is None:
                raise ValueError("Selecione o membro para vincular.")
            perfis.link(st.user.to_dict(), email, member_id if save else None)
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))
        else:
            ui.saved("Vínculo atualizado." if save else "Vínculo retirado.")
