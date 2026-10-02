"""Avisos em hierarquia, com consulta privada aos avisos de grupo."""

import streamlit as st
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import access
import database as db


def publication_label(value):
    if not value:
        return "Data de publicação não registrada"
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.astimezone(ZoneInfo("America/Sao_Paulo"))
    return timestamp.strftime("Publicado em %d/%m/%Y às %H:%M")


def visible(claims, cev=None, group_id=None, ministry_id=None):
    clauses = ["n.destino='Geral'"]
    params = []
    import ministerios, servicos
    actor = access.profile(claims)
    root_filter = ''
    if ministry_id is not None:
        selected = ministerios.ministry(ministry_id)
        if selected:
            root_filter = ' AND m.id=?'
            params.append(selected['parent_id'] or selected['id'])
    clauses.append("(n.destino='Ministério' AND n.ministerio_id IN (SELECT m.id FROM ministries m WHERE m.cev IS NULL"+root_filter+'))')
    email = access.verified_email(claims)
    linked_ids = {r['ministerio_id'] for r in db.query('''SELECT v.ministerio_id FROM ministry_members v
        JOIN account_profiles p ON p.membro_id=v.pessoa_id WHERE p.email=? AND (v.ativo IS NULL OR v.ativo=1)''', (email,))} if email else set()
    for m in db.query('SELECT * FROM ministries WHERE cev IS NOT NULL'):
        if (ministry_id is None or m['id'] == ministry_id) and (cev is None or m['cev'] == cev) and (m['id'] in linked_ids or servicos.can_manage(actor, m)):
            clauses.append("(n.destino='Ministério' AND n.ministerio_id=?)")
            params.append(m['id'])
    groups = db.query("SELECT * FROM groups WHERE id=?", (group_id,)) if group_id is not None else []
    group = groups[0] if groups and (cev is None or groups[0]["cev"] == cev) else None
    if group and cev is None:
        cev = group["cev"]
    if cev is not None:
        clauses.append("(n.destino='CEv/Irradiação' AND n.cev=?)")
        params.append(cev)
    if group:
        actor = access.profile(claims)
        email = access.verified_email(claims)
        linked = bool(email and db.query("""SELECT 1 FROM account_profiles a JOIN people p ON p.id=a.membro_id
            WHERE a.email=? AND p.cev=? AND (p.grupo_id=? OR p.id=?)""",
            (email, group["cev"], group["id"], group["pastor_id"])))
        if access.can_use_group(actor, group) or linked:
            clauses.append("(n.destino='Grupo' AND n.cev=? AND n.grupo_id=?)")
            params.extend((group["cev"], group["id"]))
    return db.query("SELECT n.*,g.nome AS grupo,m.nome AS ministerio FROM notices n LEFT JOIN groups g ON g.id=n.grupo_id LEFT JOIN ministries m ON m.id=n.ministerio_id WHERE "
                    + " OR ".join(clauses) + " ORDER BY n.publicado_em IS NULL, n.publicado_em DESC, n.id DESC", params)


def _select_page(key, page):
    st.session_state[key] = page


def render(claims, cev=None, group_id=None, configured=(), ministry_id=None):
    notices = visible(claims, cev, group_id, ministry_id)
    configured = list(configured)
    total = len(notices) + len(configured)
    if not total:
        st.info("Nenhum aviso publicado.")
        return
    page_count = (total + 2) // 3
    path = urlsplit(st.context.url or "").path
    email = access.verified_email(claims) or "public"
    page_key = f"notice_page_{path!r}_{email}_{cev!r}_{group_id}_{ministry_id}"
    page = max(0, min(st.session_state.get(page_key, 0), page_count - 1))
    st.session_state[page_key] = page
    start, end = page * 3, (page + 1) * 3
    for notice in notices[start:end]:
        with st.container(border=True):
            st.subheader(notice["titulo"])
            if notice["destino"] == "Geral":
                st.caption("Geral · Público")
            elif notice["destino"] == "CEv/Irradiação":
                st.caption(f'{notice["cev"]} · Público')
            elif notice['destino'] == 'Ministério':
                st.caption(f'{notice["ministerio"]} · {notice["cev"] or "Geral · Público"}')
            else:
                st.caption(f'{notice["grupo"]} · Aviso restrito ao grupo')
            st.caption(publication_label(notice["publicado_em"]))
            if notice["foto"]:
                st.image(notice["foto"], width="stretch")
            st.write(notice["texto"])
            if notice["evento_id"] is not None and st.button("Ver evento/retiro", icon=":material/event:",
                                                           key=f'notice_event_open_{notice["id"]}'):
                st.session_state["selected_event"] = notice["evento_id"]
                st.switch_page(st.session_state["pages"]["event"])
    for text in configured[max(0, start - len(notices)):max(0, end - len(notices))]:
        with st.container(border=True):
            st.caption("Geral · Público")
            st.caption("Data de publicação não registrada")
            st.write(text)
    if page_count > 1:
        previous, indicator, following = st.columns([1, 2, 1])
        previous.button("Anterior", icon=":material/chevron_left:", width="stretch",
                        disabled=page == 0, key=page_key + "_previous",
                        on_click=_select_page, args=(page_key, page - 1))
        indicator.caption(f"Página {page + 1} de {page_count} · {total} avisos")
        following.button("Próxima", icon=":material/chevron_right:", width="stretch",
                         disabled=page == page_count - 1, key=page_key + "_next",
                         on_click=_select_page, args=(page_key, page + 1))
