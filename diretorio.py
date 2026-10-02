"""Listas e indicadores do CEv com categorias sem dupla contagem."""

import streamlit as st

import access
import edicao

MEMBERS = "Membros da obra"
COMMUNITY = "Comunidade"
LEADERS = "Pastores e núcleo"
GROUPS = "Grupos de oração"
ENGAGED = "Membros engajados"
NEUTRAL = "Vínculos neutros"
TABS = ("Ativos", "Inativos", "Revisão", NEUTRAL)


def people_category(person):
    if person["ativo"] == 0:
        return "Inativos"
    if person["ativo"] is None or person["eh_comunidade"] is None:
        return "Revisão"
    if person["eh_comunidade"] == 1:
        return COMMUNITY
    return MEMBERS if person["categoria"] == "Membro" else LEADERS


def group_category(group):
    if group["ativo"] is None:
        return "Revisão"
    if group["ativo"] == 0:
        return "Inativos"
    return NEUTRAL if group["neutro"] else "Ativos"


def summary(people, groups):
    categories = {key: [p for p in people if people_category(p) == key]
                  for key in (MEMBERS, COMMUNITY, LEADERS, "Inativos", "Revisão")}
    group_lists = {key: [g for g in groups if group_category(g) == key] for key in TABS}
    return categories, group_lists


def engaged(people):
    return [p for p in people if people_category(p) in (MEMBERS, LEADERS) and p["ministerio"].strip()]


def _open(key, tab, category="Todos"):
    st.session_state[key + "_tabs"] = tab
    st.session_state[key + "_filter"] = category
    st.session_state.pop(key + "_person", None)


def _tab_change(key):
    st.session_state[key + "_filter"] = "Todos"
    st.session_state.pop(key + "_person", None)


def _ui():
    import portal
    return portal


def person_list(people, key):
    ui = _ui()
    ui.records(people, {"nome": "Nome", "categoria": "Categoria", "contato": "Contato", "grupo": "Grupo",
                       "eh_comunidade": "Comunidade", "ativo": "Ativo"}, "Nenhuma pessoa nesta lista.")
    if not people:
        return
    labels = {p["id"]: f'{p["nome"]} (#{p["id"]})' for p in people}
    # Ao mudar de lista, uma seleção anterior não deve abrir outro cadastro.
    widget_key = key + "_person"
    if st.session_state.get(widget_key) not in labels:
        st.session_state.pop(widget_key, None)
    person_id = st.selectbox("Ver cadastro completo", list(labels), index=None,
                            format_func=labels.get, placeholder="Selecione uma pessoa", key=widget_key)
    if person_id is None:
        return
    person = next(p for p in people if p["id"] == person_id)
    with st.container(border=True):
        if person["foto"]:
            st.image(person["foto"], width=240)
        ui.records([person], {"nome": "Nome", "categoria": "Categoria", "contato": "Contato",
            "grupo": "Grupo", "instagram": "Instagram", "endereco": "Endereço",
            "nascimento": "Data de nascimento", "acompanhador": "Acompanhador", "ministerio": "Ministério",
            "email": "E-mail", "genero": "Gênero", "servico": "Serviço", "funcao_servico": "Função no serviço",
            "eh_comunidade": "Pertence à comunidade", "ativo": "Ativo"}, "")
        if access.can_manage_cev(access.profile(st.user.to_dict()), person["cev"]):
            edicao.link("people", "Editar esta pessoa", person["id"], key=key + "_edit_person")


def group_list(groups, key):
    ui = _ui()
    if not groups:
        st.info("Nenhum grupo nesta lista.")
    for group in groups:
        with st.container(border=True):
            photo, text = st.columns([1, 4])
            with photo:
                if group["foto"]:
                    st.image(group["foto"], width="stretch")
                else:
                    st.caption("Sem foto")
            with text:
                st.subheader(group["nome"])
                st.caption("Vínculo neutro · fora da contagem de grupos de oração" if group["neutro"] else
                           f'{group["fase"]} · {group["pastor"] or "Pastor não vinculado"}')
                st.write(f'Local: {group["local"] or "—"} · Dia: {group["dia_encontro"] or "—"} · Horário: {group["horario"] or "—"}')
                st.caption(f'Público: {group["publico"] or "—"} · Ativo: {ui.status_label(group["ativo"])} · Início: {ui.date_label(group["data_inicio"])}')
                if st.button("Abrir vínculo" if group["neutro"] else "Abrir grupo", key=f'{key}_open_group_{group["id"]}'):
                    st.session_state["selected_group"] = group["id"]
                    for picker in ("group_page_picker", "meeting_group", "attendance_group", "followup_group"):
                        st.session_state.pop(picker, None)
                    st.switch_page(st.session_state["pages"]["group"])
                edicao.link("groups", "Editar vínculo" if group["neutro"] else "Editar grupo", group["id"], key=f'{key}_edit_group_{group["id"]}')


def cev(name, people, groups):
    key = "directory_" + name
    categories, group_lists = summary(people, groups)
    st.html("""<style>
        .st-key-cev_indicators button {min-height:112px; border:1px solid #dce7e3;
            border-radius:16px; background:#fff; padding:18px; justify-content:flex-start;}
        .st-key-cev_indicators button:hover {border-color:#177d73; background:#f3faf7;}
        .st-key-cev_indicators button p {text-align:left; color:#183b56; font-size:.95rem;}
        .st-key-cev_indicators button strong {display:block; font-size:2rem; line-height:1.3; margin-bottom:6px;}
        </style>""")
    with st.container(key="cev_indicators"):
        for column, label, count in zip(st.columns(5), (MEMBERS, COMMUNITY, LEADERS, ENGAGED, GROUPS),
                (len(categories[MEMBERS]), len(categories[COMMUNITY]), len(categories[LEADERS]), len(engaged(people)), len(group_lists["Ativos"]))):
            with column:
                st.button(f"**{count}** {label}", key=key + "_indicator_" + label, width="stretch",
                          on_click=_open, args=(key, "Ativos", label), help="Abrir a lista correspondente")
    st.caption("Somente registros ativos. Pastores e núcleo da comunidade aparecem apenas em Comunidade. Engajados: pessoas da obra com ministério cadastrado.")
    with st.container(horizontal=True):
        for tab in ("Inativos", "Revisão", NEUTRAL):
            count = len(categories.get(tab, [])) + len(group_lists[tab])
            st.button(f"{tab} · {count}", key=key + "_shortcut_" + tab,
                      on_click=_open, args=(key, tab))
    a, b = st.columns(2)
    a.page_link(st.session_state["pages"]["group_create"], label="Cadastrar grupo", icon=":material/add_circle:")
    b.page_link(st.session_state["pages"]["person_create"], label="Cadastrar membro/pastor", icon=":material/person_add:")
    st.subheader("Cadastros do CEv")
    tabs = st.tabs(TABS, key=key + "_tabs", on_change=_tab_change, args=(key,))
    for tab, container in zip(TABS, tabs):
        if not container.open:
            continue
        with container:
            if tab == NEUTRAL:
                st.info("Vínculos para pessoas sem grupo de oração. Não entram no indicador de grupos.")
                group_list(group_lists[NEUTRAL], key + "_" + tab)
                continue
            options = ["Todos", MEMBERS, COMMUNITY, LEADERS, ENGAGED, GROUPS] if tab == "Ativos" else ["Todos", "Pessoas", GROUPS]
            if st.session_state.get(key + "_filter") not in options:
                st.session_state[key + "_filter"] = "Todos"
            selected = st.selectbox("Exibir", options, key=key + "_filter")
            if tab == "Revisão":
                st.info("Confirme Ativo e, para pessoas, Pertence à comunidade. Estes cadastros não entram nos indicadores.")
            selected_people = ([p for p in people if people_category(p) in (MEMBERS, COMMUNITY, LEADERS)]
                               if tab == "Ativos" else categories[tab])
            if selected in (MEMBERS, COMMUNITY, LEADERS):
                selected_people = categories[selected]
            elif selected == ENGAGED:
                selected_people = engaged(people)
            selected_groups = group_lists[tab]
            if selected in ("Todos", GROUPS):
                st.subheader(f"Grupos · {len(selected_groups)}")
                group_list(selected_groups, key + "_" + tab)
            if selected != GROUPS:
                st.subheader(f"{selected if selected in (MEMBERS, COMMUNITY, LEADERS, ENGAGED) else 'Pessoas'} · {len(selected_people)}")
                person_list(selected_people, key)


def group_people(people, key):
    categories, _ = summary(people, [])
    tabs = st.tabs(TABS[:3], key=key + "_tabs", on_change="rerun")
    for tab, container in zip(TABS[:3], tabs):
        if container.open:
            with container:
                rows = ([p for p in people if people_category(p) in (MEMBERS, COMMUNITY, LEADERS)]
                        if tab == "Ativos" else categories[tab])
                if tab == "Revisão":
                    st.info("Situação ou pertencimento à comunidade não informado.")
                person_list(rows, key + "_" + tab)
