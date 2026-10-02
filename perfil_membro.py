"""Cartões de perfil com os campos já aprovados do cadastro de pessoas."""

from html import escape

import streamlit as st

import access
import database as db


def load(claims, person_id):
    actor = access.profile(claims)
    if not actor or not isinstance(person_id, int) or isinstance(person_id, bool):
        raise PermissionError("Sua conta não pode consultar este perfil.")
    rows = db.query("""SELECT p.*,g.nome AS grupo FROM people p LEFT JOIN groups g ON g.id=p.grupo_id
        WHERE p.id=?""", (person_id,))
    if not rows or not access.can_view_person(actor, rows[0]):
        raise PermissionError("Sua conta não pode consultar este perfil.")
    return rows[0]


def _text(value):
    return escape(str(value)) if value is not None and str(value).strip() else "—"


def _card(title, fields):
    items = "".join(f'<div class="member-field"><dt>{escape(label)}</dt><dd>{_text(value)}</dd></div>'
                    for label, value in fields)
    return f'<section class="member-card"><h4>{escape(title)}</h4><dl>{items}</dl></section>'


def render(person_id, claims):
    import portal as ui

    try:
        person = load(claims, person_id)
    except PermissionError as exc:
        st.info(str(exc))
        return
    st.html("""<style>
        .member-profile {color:inherit; font-family:inherit;}
        .member-hero {padding:24px; border-radius:20px;
            border:1px solid color-mix(in srgb,currentColor 16%,transparent);
            background:linear-gradient(130deg,color-mix(in srgb,currentColor 7%,transparent),transparent);}
        .member-hero h3 {margin:12px 0 8px; font-size:1.65rem; overflow-wrap:anywhere;}
        .member-hero p {margin:8px 0 0; overflow-wrap:anywhere;}
        .member-kicker {font-size:.75rem; letter-spacing:.1em; text-transform:uppercase; opacity:.7;}
        .member-badges {display:flex; flex-wrap:wrap; gap:8px;}
        .member-badges span {font-size:.8rem; padding:5px 11px; border-radius:999px;
            background:color-mix(in srgb,currentColor 8%,transparent);}
        .member-avatar {display:grid; place-items:center; width:96px; height:96px;
            border-radius:24px; background:color-mix(in srgb,currentColor 9%,transparent); font-size:2rem;}
        .member-deck {display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; margin-top:16px;}
        .member-card {padding:22px; border:1px solid color-mix(in srgb,currentColor 16%,transparent); border-radius:18px;}
        .member-card h4 {font-size:1.05rem; margin:0 0 18px;}
        .member-card dl {margin:0; display:grid; gap:16px;}
        .member-field dt {font-size:.8rem; opacity:.7; margin-bottom:3px;}
        .member-field dd {margin:0; font-size:.95rem; white-space:pre-wrap; overflow-wrap:anywhere;}
        @media(max-width:640px) {.member-deck {grid-template-columns:1fr;} .member-hero,.member-card {padding:18px;}}
    </style>""")
    st.subheader("Perfil do membro", icon=":material/account_circle:")
    with st.container(border=False, key="member_profile"):
        initials = "".join(word[0] for word in (person.get("nome") or "?").split()[:2]).upper()
        badges = (person.get("categoria"),
                  "Comunidade" if person.get("eh_comunidade") == 1 else
                  "Obra" if person.get("eh_comunidade") == 0 else "Comunidade não informada",
                  "Ativo" if person.get("ativo") == 1 else "Inativo" if person.get("ativo") == 0 else "Situação não informada")
        if person.get("foto"):
            st.image(person["foto"], width=160)
            avatar = ""
        else:
            avatar = f'<div class="member-avatar" aria-label="Foto não cadastrada">{escape(initials)}</div>'
        badge_html = "".join(f"<span>{_text(badge)}</span>" for badge in badges)
        hero = (f'<div class="member-hero"><span class="member-kicker">Cadastro da comunidade</span>{avatar}'
                f'<h3>{_text(person.get("nome"))}</h3><div class="member-badges">{badge_html}</div>'
                f'<p>{_text(person.get("grupo") or "Sem grupo vinculado")} · {_text(person.get("cev"))}</p></div>')
        cards = [
            _card("Contato", [("Telefone / contato", person.get("contato")), ("E-mail", person.get("email")),
                              ("Instagram", person.get("instagram")), ("Endereço", person.get("endereco"))]),
            _card("Informações pessoais", [("Data de nascimento", ui.date_label(person.get("nascimento"))),
                                            ("Gênero", person.get("genero")),
                                            ("Pertence à comunidade", ui.status_label(person.get("eh_comunidade"))),
                                            ("Ativo", ui.status_label(person.get("ativo")))]),
            _card("Vida no grupo", [("Grupo / célula", person.get("grupo")), ("CEv / Irradiação", person.get("cev")),
                                    ("Categoria", person.get("categoria")), ("Acompanhador", person.get("acompanhador"))]),
            _card("Ministérios e serviço", [("Ministério", person.get("ministerio")), ("Serviço", person.get("servico")),
                                           ("Função no serviço", person.get("funcao_servico"))]),
        ]
        st.html(f'<article class="member-profile">{hero}<div class="member-deck">{"".join(cards)}</div></article>')
