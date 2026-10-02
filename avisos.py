"""Avisos em hierarquia, com consulta privada aos avisos de grupo."""

import streamlit as st

import access
import database as db


def visible(claims, cev=None, group_id=None):
    clauses = ["n.destino='Geral'"]
    params = []
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
    return db.query("SELECT n.*,g.nome AS grupo FROM notices n LEFT JOIN groups g ON g.id=n.grupo_id WHERE "
                    + " OR ".join(clauses) + " ORDER BY n.id DESC", params)


def render(claims, cev=None, group_id=None, configured=()):
    notices = visible(claims, cev, group_id)
    for notice in notices:
        with st.container(border=True):
            st.subheader(notice["titulo"])
            if notice["destino"] == "Geral":
                st.caption("Geral · Público")
            elif notice["destino"] == "CEv/Irradiação":
                st.caption(f'{notice["cev"]} · Público')
            else:
                st.caption(f'{notice["grupo"]} · Aviso restrito ao grupo')
            if notice["foto"]:
                st.image(notice["foto"], width="stretch")
            st.write(notice["texto"])
    for text in configured:
        with st.container(border=True):
            st.caption("Geral · Público")
            st.write(text)
    if not notices and not configured:
        st.info("Nenhum aviso publicado.")
