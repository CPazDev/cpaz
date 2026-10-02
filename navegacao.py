"""Barra lateral com navegação recolhível e perfil no rodapé."""

from html import escape
from pathlib import Path
from base64 import b64encode

import streamlit as st

import access
import database as db
import perfis


def _logo(class_name):
    data = b64encode(Path(__file__).with_name("logo.png").read_bytes()).decode("ascii")
    return f'<img class="{class_name}" src="data:image/png;base64,{data}" alt="C-Paz">'


def context(profile):
    import portal
    cevs = portal.visible_cevs()
    if not cevs:
        st.caption("CEv/Irradiação: nomes a definir")
        return
    current = st.session_state.get("selected_cev")
    if profile and profile["nivel"] in ('Gestor de CEv', 'Responsável de grupo') and not any(access.can_manage_cev(profile,name) and name != profile['cev'] for name in cevs) and current != profile["cev"]:
        current = profile["cev"]
        st.session_state["selected_cev"] = current
        st.session_state.pop("cev_sidebar", None)
        st.session_state.pop("selected_group", None)
    if profile and profile["nivel"] == "Responsável de grupo" and not access.can_manage_cev(profile, profile['cev']):
        st.session_state["selected_group"] = profile["grupo_id"]
    index = cevs.index(current) if current in cevs else None
    name = st.selectbox("CEv/Irradiação", cevs, index=index,
                        placeholder="Selecione uma unidade", key="cev_sidebar")
    if name != current:
        st.session_state["selected_cev"] = name
        st.session_state.pop("selected_group", None)
        for key in ("group_page_picker", "meeting_group", "attendance_group", "followup_group"):
            st.session_state.pop(key, None)


def footer(pages, profile):
    account = perfis.own(st.user.to_dict())
    if not account:
        st.html('<div class="sidebar-profile">' + _logo("sidebar-avatar-logo") +
                '<div class="sidebar-person"><strong>Bem-vindo</strong><small>Tudo em um só lugar.</small></div></div>')
        st.page_link(pages["login"], label="Acessar minha conta", icon=":material/login:")
    else:
        name = account["nome_exibido"]
        initials = "".join(word[0] for word in name.split()[:2]).upper() or "CP"
        level = profile["nivel"] if profile else "Minha conta"
        if profile and profile.get('servicos'):
            bindings=profile['servicos']
            first=bindings[0]
            level=f'{first["papel"]} · {first["nome"]} · {first["cev"] or "Geral"}'
            if len(bindings)>1:
                level+=f' +{len(bindings)-1}'
        group = account["grupo"]
        prefix = "Vínculo" if account["neutro"] else "Meu grupo"
        if not group and profile and profile["grupo_id"] is not None:
            rows = db.query("SELECT nome FROM groups WHERE id=?", (profile["grupo_id"],))
            group = rows[0]["nome"] if rows else None
            prefix = "Grupo autorizado"
        detail = f"{prefix}: {group}" if group else account["membro_cev"] or "Membro ainda não vinculado"
        st.html(f'<div class="sidebar-profile"><div class="sidebar-avatar">{escape(initials)}</div>'
                f'<div class="sidebar-person"><strong title="{escape(name, quote=True)}">{escape(name)}</strong>'
                f'<small title="{escape(account["email"], quote=True)}">{escape(account["email"])}</small></div></div>'
                f'<div class="sidebar-context"><span class="sidebar-role">{escape(level)}</span>'
                f'<span class="sidebar-group">{escape(detail)}</span></div>')
        left, right = st.columns([1.4, 1], gap="small")
        left.page_link(pages["profile"], label="Meu perfil", icon=":material/manage_accounts:")
        right.button("Sair", icon=":material/logout:", on_click=st.logout, width="stretch")
    st.html('<div class="sidebar-theme-hint">Claro ou escuro no menu ⋮</div>')


def render(pages, sections, profile):
    css = Path(__file__).with_name("sidebar.css").read_text(encoding="utf-8")
    st.html("<style>" + css + "</style>")
    with st.sidebar, st.container(key="sidebar_shell"):
        with st.container(key="sidebar_header"):
            st.html('<div class="sidebar-brand">' + _logo("sidebar-logo") + '<div>'
                    '<div class="portal-brand">C-Paz</div><div class="portal-subtitle">Tudo em um só lugar.</div></div></div>')
            context(profile)
        with st.container(key="sidebar_menu"):
            st.html('<div class="sidebar-section">Explorar</div>')
            for key in ("home", "cev", "group", "event"):
                st.page_link(pages[key])
            if "Minha área" in sections:
                st.html('<div class="sidebar-section">Minha área</div>')
                st.page_link(pages["personal"])
            managed = {name: section for name, section in sections.items()
                       if name in ("Cadastros", "Publicações", "Registros", "Administração", 'Ministérios')}
            if managed:
                st.html('<div class="sidebar-section">Gestão</div>')
                for name, section in managed.items():
                    with st.expander(name):
                        for page in section:
                            st.page_link(page)
        with st.container(key="sidebar_account"):
            footer(pages, profile)
