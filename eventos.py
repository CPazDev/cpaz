"""Páginas públicas de eventos e configuração das inscrições pelo organizador."""

from datetime import date
import base64
from html import escape
import json
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit, urlunsplit
import uuid

import streamlit as st

import access
import database as db
import crud
import edicao

FIELD_TYPES = ("Texto", "Texto longo", "Número", "Data", "Escolha", "Confirmação")


def ui():
    # Importação tardia evita dependência circular com a navegação do portal.
    import portal
    return portal


def editable(event):
    profile = access.profile(st.user.to_dict())
    return access.can_manage_publication(profile, event["destino"], event["cev"], event["grupo_id"])


def create():
    ui().header("Criar evento/retiro", "Monte uma página pública e configure a inscrição dos participantes.")
    target = ui().publication_target()
    if target is None:
        return
    with st.form("event_create", clear_on_submit=True):
        titulo = st.text_input("Título *")
        tipo = st.selectbox("Tipo *", ["Evento", "Retiro"], index=None, placeholder="Selecione o tipo")
        a, b = st.columns(2)
        inicio = a.date_input("Início", value=ui().today(), format="DD/MM/YYYY")
        termino = b.date_input("Término", value=None, format="DD/MM/YYYY")
        local = st.text_input("Local")
        descricao = st.text_area("Descrição")
        capa = st.file_uploader("Imagem de capa", type=["jpg", "jpeg", "png", "webp"])
        cor = st.color_picker("Cor principal", "#177d73")
        whatsapp = st.text_input("WhatsApp de contato", help="Use código do país, DDD e número. Exemplo de formato: 55 + DDD + número.")
        submit = st.form_submit_button("Criar página do evento", type="primary", disabled=target[0] is None)
    if submit:
        try:
            raw, kind = ui().photo_data(capa)
            phone = re.sub(r"\D", "", whatsapp)
            if whatsapp.strip() and not (10 <= len(phone) <= 15):
                raise ValueError("Informe o WhatsApp com código do país, DDD e número.")
            event_id = crud.create_event(st.user.to_dict(), titulo, tipo, inicio.isoformat(), termino.isoformat() if termino else None,
                                     local, descricao, *target, raw, kind, cor, phone)
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))
        else:
            st.session_state["selected_event"] = event_id
            ui().saved("Página criada. Configure os campos de inscrição em Gerenciar eventos.")


def cards(cev=None, group_id=None):
    events = db.publications("events", cev, group_id)
    if not events:
        st.info("Nenhum retiro ou evento publicado.")
    for event in events:
        with st.container(border=True):
            if event["capa"]:
                st.image(event["capa"], width="stretch")
            st.subheader(event["titulo"])
            st.caption(f'{event["tipo"]} · {ui().date_label(event["inicio"])} · {event["local"] or "Local a informar"}')
            if st.button("Ver página e inscrição", key=f'event_open_{event["id"]}'):
                st.session_state["selected_event"] = event["id"]
                st.switch_page(st.session_state["pages"]["event"])


def event_url(event_id):
    current = st.context.url
    if not current:
        return None
    parts = urlsplit(current)
    if parts.scheme not in ("http", "https"):
        return None
    return urlunsplit((parts.scheme, parts.netloc, "/evento", urlencode({"id": event_id}), ""))


def registration(event):
    fields = json.loads(event["campos"])
    if not fields:
        st.info("O organizador ainda não disponibilizou o formulário de inscrição.")
        return
    st.write("Preencha os campos definidos pelo organizador. * Obrigatório.")
    values = {}
    with st.form(f'register_{event["id"]}', clear_on_submit=True):
        for field in fields:
            label = field["label"] + (" *" if field["required"] else "")
            key = f'registration_{event["id"]}_{field["id"]}'
            kind = field["type"]
            if kind == "Texto":
                value = st.text_input(label, key=key)
            elif kind == "Texto longo":
                value = st.text_area(label, key=key)
            elif kind == "Número":
                value = st.number_input(label, value=None, step=1, key=key)
            elif kind == "Data":
                value = st.date_input(label, value=None, min_value=date(1900, 1, 1), max_value=date(2100, 12, 31),
                                      format="DD/MM/YYYY", key=key)
            elif kind == "Escolha":
                value = st.selectbox(label, field["options"], index=None, placeholder="Selecione uma opção", key=key)
            else:
                value = st.checkbox(label, key=key)
            values[field["id"]] = value
        submit = st.form_submit_button("Confirmar inscrição", type="primary")
    if submit:
        missing = [f["label"] for f in fields if f["required"] and (
            values[f["id"]] is None or values[f["id"]] is False
            or isinstance(values[f["id"]], str) and not values[f["id"]].strip())]
        if missing:
            st.error("Preencha os campos obrigatórios: " + ", ".join(missing))
            return
        responses = {}
        for field in fields:
            value = values[field["id"]]
            responses[field["id"]] = {"campo": field["label"], "valor": value.isoformat() if isinstance(value, date) else value}
        _, token = db.register(event["id"], responses)
        st.session_state[f'registration_token_{event["id"]}'] = token
        st.success("Inscrição recebida. Guarde seu código para acessar os pedidos deste evento.")
        st.code(token, language=None)


def detail():
    raw_id = st.query_params.get("id") or st.session_state.get("selected_event")
    try:
        event_id = int(raw_id)
    except (ValueError, TypeError):
        st.info("Selecione um evento/retiro na página inicial, no CEv ou no grupo.")
        return
    matches = db.query("SELECT * FROM events WHERE id = ?", (event_id,))
    if not matches:
        st.info("Evento/retiro não encontrado.")
        return
    event = matches[0]
    if not access.can_view_publication(access.profile(st.user.to_dict()), event):
        st.info("Sua conta está limitada aos eventos do seu grupo.")
        return
    st.session_state["selected_event"] = event_id
    st.query_params["id"] = str(event_id)
    event_style(event)
    with st.container(key="event_shell"):
        event_header(event)
        with st.container(key="event_testimonials"):
            public_testimonials(event)
        st.html('<div id="event-registration" class="ev-section-label">PARTICIPE</div>')
        with st.container(key="event_signup"):
            tabs = st.tabs(["Inscrição", "Pedidos extras"])
            with tabs[0]:
                st.subheader("Inscrição")
                registration(event)
            with tabs[1]:
                public_requests(event)


def _luminance(channels):
    linear = [c / 255 / 12.92 if c / 255 <= .04045 else ((c / 255 + .055) / 1.055) ** 2.4 for c in channels]
    return sum(c * weight for c, weight in zip(linear, (.2126, .7152, .0722)))


def event_style(event):
    brand = event["cor"] if re.fullmatch(r"#[0-9a-fA-F]{6}", event["cor"]) else "#177d73"
    rgb = [int(brand[i:i + 2], 16) for i in (1, 3, 5)]
    luminance = _luminance(rgb)
    dark = [15, 23, 42]
    ink = "#ffffff" if 1.05 / (luminance + .05) >= (luminance + .05) / (_luminance(dark) + .05) else "#0f172a"
    end = [round(c * .76) if ink == "#ffffff" else round(c + (255 - c) * .2) for c in rgb]
    accent = list(rgb)
    while _luminance(accent) > .16:
        accent = [round(c * .85) for c in accent]
    as_hex = lambda channels: "#" + "".join(f"{c:02x}" for c in channels)
    ink_rgb = "255,255,255" if ink == "#ffffff" else "15,23,42"
    rgb_text = ",".join(str(c) for c in rgb)
    st.html(f'''<style>.st-key-event_shell {{
        --ev-brand:{brand};--ev-end:{as_hex(end)};--ev-ink:{ink};--ev-accent:{as_hex(accent)};
        --ev-soft:rgba({rgb_text},.065);--ev-border:rgba({rgb_text},.16);
        --ev-line:rgba({ink_rgb},.28);--ev-faint:rgba({ink_rgb},.045);
    }}</style>''')
    css = Path(__file__).with_name("eventos.css").read_text(encoding="utf-8")
    st.html("<style>" + css + "</style>")


def _icon(kind):
    return f'<span class="ev-icon ev-icon-{kind}" aria-hidden="true"></span>'


def _image_url(raw, kind):
    mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(kind, "image/png")
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def _period(event):
    months = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro")
    start = date.fromisoformat(event["inicio"])
    end = date.fromisoformat(event["termino"]) if event["termino"] else start
    if start == end:
        return f"{start.day} de {months[start.month - 1]} de {start.year}"
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.day} a {end.day} de {months[end.month - 1]} de {end.year}"
    return f"{start.day} de {months[start.month - 1]} de {start.year} a {end.day} de {months[end.month - 1]} de {end.year}"


def event_header(event):
    destination = event["cev"] or "C-Paz"
    if event["destino"] == "Grupo":
        groups = db.query("SELECT nome FROM groups WHERE id=?", (event["grupo_id"],))
        destination = groups[0]["nome"] if groups else destination
    actions = []
    if json.loads(event["campos"]):
        actions.append('<a class="ev-action primary" href="#event-registration">Quero me inscrever ' + _icon("arrow") + '</a>')
    if event["whatsapp"] and re.fullmatch(r"\d{10,15}", event["whatsapp"]):
        actions.append(f'<a class="ev-action secondary" href="https://wa.me/{event["whatsapp"]}" target="_blank" rel="noopener noreferrer">' + _icon("chat") + 'Falar com a organização</a>')
    cover = ''
    if event["capa"]:
        cover = f'<figure class="ev-cover"><img src="{_image_url(event["capa"], event["capa_tipo"])}" alt="Capa de {escape(event["titulo"], quote=True)}"></figure>'
    description = f'<p class="ev-hero-description">{escape(event["descricao"])}</p>' if event["descricao"] else ''
    st.html(f'''<p class="ev-breadcrumb">C-Paz / Eventos e retiros</p>
        <section class="ev-hero {"has-cover" if cover else ""}" aria-label="Sobre o evento">
            <div class="ev-hero-grid"><div class="ev-hero-content">
                <div class="ev-tags"><span class="ev-tag">{escape(event["tipo"])}</span><span class="ev-tag">{escape(destination)}</span></div>
                <h1>{escape(event["titulo"])}</h1>{description}
                {('<div class="ev-actions">' + ''.join(actions) + '</div>') if actions else ''}
            </div>{cover}</div>
        </section>''')
    st.html(f'''<div class="ev-details">
        <div class="ev-detail"><span class="ev-detail-icon">{_icon("calendar")}</span><div><small>Quando</small><strong><time datetime="{escape(event["inicio"])}">{_period(event)}</time></strong></div></div>
        <div class="ev-detail"><span class="ev-detail-icon">{_icon("pin")}</span><div><small>Onde</small><strong>{escape(event["local"] or "Local a informar")}</strong></div></div>
    </div>''')
    url = event_url(event["id"])
    if url:
        share = "https://wa.me/?" + urlencode({"text": event["titulo"] + " " + url})
        st.html(f'<div class="ev-share-row"><a class="ev-share" href="{escape(share, quote=True)}" target="_blank" rel="noopener noreferrer">{_icon("share")}Compartilhar evento</a></div>')


def public_requests(event):
    st.subheader("Pedidos extras")
    requests = db.query("SELECT * FROM event_requests WHERE evento_id = ? ORDER BY id", (event["id"],))
    if not requests:
        st.info("A organização ainda não disponibilizou pedidos extras.")
        return
    token_key = f'registration_token_{event["id"]}'
    token = st.session_state.get(token_key, "")
    registration_data = db.find_registration(event["id"], token)
    if not registration_data:
        st.write("Inscreva-se no evento ou informe o código de sua inscrição para fazer pedidos.")
        with st.form(f'recover_registration_{event["id"]}'):
            code = st.text_input("Código de inscrição", type="password")
            recover = st.form_submit_button("Acessar meus pedidos")
        if recover:
            if db.find_registration(event["id"], code):
                st.session_state[token_key] = code.strip()
                st.rerun()
            else:
                st.error("Código de inscrição inválido para este evento.")
        return
    for request in requests:
        st.write(f'**{request["titulo"]}**')
        st.write(request["descricao"])
        options = json.loads(request["opcoes"])
        with st.form(f'order_{request["id"]}'):
            option = st.selectbox("Opção", options, index=None, placeholder="Selecione uma opção") if options else ""
            quantity = st.number_input("Quantidade", min_value=1, step=1, value=None)
            submit = st.form_submit_button("Salvar pedido")
        if submit:
            try:
                db.save_order(event["id"], token, request["id"], option, quantity)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.success("Pedido salvo. Salvar a mesma opção novamente atualiza a quantidade.")
    st.subheader("Meus pedidos")
    ui().records(db.order_rows(event["id"], registration_data["id"]),
                 {"pedido": "Pedido", "opcao": "Opção", "quantidade": "Quantidade"}, "Nenhum pedido enviado.")


def public_testimonials(event):
    st.html('<p class="ev-section-label">RELATOS DA COMUNIDADE</p>')
    st.header("Testemunhos")
    approved = db.query("SELECT * FROM testimonials WHERE evento_id = ? AND status = 'Aprovado' ORDER BY id DESC",
                        (event["id"],))
    for start in range(0, len(approved), 2):
        batch = approved[start:start + 2]
        columns = st.columns(len(batch))
        for column, testimony in zip(columns, batch):
            with column, st.container(key=f'testimony_card_{testimony["id"]}'):
                st.html('<div class="ev-quote" aria-hidden="true">“</div>')
                st.write(testimony["texto"])
                if testimony["foto"]:
                    avatar = f'<img src="{_image_url(testimony["foto"], testimony["foto_tipo"])}" alt="Foto de {escape(testimony["nome"], quote=True)}">'
                else:
                    initials = "".join(word[0] for word in testimony["nome"].split()[:2]) or "—"
                    avatar = f'<span class="ev-avatar" aria-hidden="true">{escape(initials.upper())}</span>'
                st.html(f'<div class="ev-author">{avatar}<span>{escape(testimony["nome"])}</span></div>')
    if not approved:
        st.html('<div class="ev-empty">Nenhum testemunho publicado. Compartilhe sua experiência com a comunidade.</div>')
    with st.expander("Compartilhe seu testemunho"):
        st.write("Seu testemunho será publicado após a aprovação da organização.")
        with st.form(f'testimony_{event["id"]}', clear_on_submit=True):
            nome = st.text_input("Nome *")
            texto = st.text_area("Testemunho *")
            foto = st.file_uploader("Foto (opcional)", type=["jpg", "jpeg", "png", "webp"])
            submit = st.form_submit_button("Enviar testemunho")
    if submit:
        try:
            raw, kind = ui().photo_data(foto)
            db.save_testimonial(event["id"], nome, texto, raw, kind)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.success("Testemunho recebido e enviado para aprovação.")


def manage():
    ui().header("Gerenciar eventos", "Configure as inscrições e acompanhe seus eventos/retiros.")
    user = access.profile(st.user.to_dict())
    if not user:
        st.info("Esta seção é exclusiva dos organizadores e administradores.")
        return
    events = db.query("SELECT * FROM events ORDER BY id DESC")
    events = [event for event in events if editable(event)]
    if not events:
        st.info("Nenhum evento disponível para gerenciar.")
        st.page_link(st.session_state["pages"]["event_create"], label="Criar evento/retiro")
        return
    labels = {e["id"]: f'{e["titulo"]} (#{e["id"]})' for e in events}
    event_id = st.selectbox("Evento/retiro", list(labels), index=None, format_func=labels.get,
                            placeholder="Selecione um evento")
    if event_id is None:
        return
    event = next(e for e in events if e["id"] == event_id)
    tabs = st.tabs(["Página e identidade", "Formulário de inscrição", "Inscrições", "Pedidos extras", "Testemunhos"])
    with tabs[0]:
        edit_page(event)
    with tabs[1]:
        manage_fields(event)
    with tabs[2]:
        manage_registrations(event)
    with tabs[3]:
        manage_requests(event)
    with tabs[4]:
        moderate_testimonials(event)


def manage_fields(event):
    event_id = event["id"]
    st.subheader("Campos de inscrição")
    fields = json.loads(event["campos"])
    if fields:
        ui().records(fields, {"label": "Campo", "type": "Tipo", "required": "Obrigatório"}, "")
    else:
        st.info("Nenhum campo definido. O formulário público será aberto quando você adicionar os campos.")
    field_type = st.selectbox("Tipo do novo campo", FIELD_TYPES)
    with st.form(f'add_field_{event_id}', clear_on_submit=True):
        label = st.text_input("Nome do campo")
        required = st.checkbox("Obrigatório")
        options_text = st.text_area("Opções (uma por linha)") if field_type == "Escolha" else ""
        add = st.form_submit_button("Adicionar campo", type="primary")
    if add:
        options = list(dict.fromkeys(x.strip() for x in options_text.splitlines() if x.strip()))
        if not label.strip():
            st.error("Informe o nome do campo.")
        elif field_type == "Escolha" and not options:
            st.error("Informe as opções de escolha.")
        elif any(f["label"].casefold() == label.strip().casefold() for f in fields):
            st.error("Este formulário já tem um campo com esse nome.")
        else:
            fields.append({"id": uuid.uuid4().hex, "label": label.strip(), "type": field_type,
                           "required": required, "options": options})
            try:
                crud.update_fields(st.user.to_dict(), event_id, fields)
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))
            else:
                ui().saved("Campo adicionado à inscrição.")
    if fields:
        field_ids = {f["id"]: f["label"] for f in fields}
        edit_id = st.selectbox("Campo para editar", list(field_ids), index=None, format_func=field_ids.get,
                               key=f"edit_field_picker_{event_id}")
        if edit_id:
            selected = next(f for f in fields if f["id"] == edit_id)
            with st.form(f'edit_field_{event_id}_{edit_id}'):
                new_label = st.text_input("Nome do campo", selected["label"])
                new_type = st.selectbox("Tipo do campo", FIELD_TYPES, index=FIELD_TYPES.index(selected["type"]))
                new_required = st.checkbox("Obrigatório", value=selected["required"])
                new_options = st.text_area("Opções (uma por linha)", "\n".join(selected["options"]))
                save_field = st.form_submit_button("Salvar campo")
            if save_field:
                changed = {"id": edit_id, "label": new_label.strip(), "type": new_type, "required": new_required,
                           "options": list(dict.fromkeys(o.strip() for o in new_options.splitlines() if o.strip())) if new_type == "Escolha" else []}
                try:
                    crud.update_fields(st.user.to_dict(), event_id, [changed if f["id"] == edit_id else f for f in fields])
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
                else:
                    ui().saved("Campo atualizado. As respostas recebidas foram preservadas.")
        allowed, _ = crud.deletion_info(st.user.to_dict(), "events", event_id)
        if not allowed:
            st.caption("Remover campos exige permissão de exclusão no destino do evento.")
            return
        with st.form(f'remove_field_{event_id}'):
            field_ids = {f["id"]: f["label"] for f in fields}
            remove_id = st.selectbox("Campo para remover", list(field_ids), index=None, format_func=field_ids.get)
            confirmed = st.checkbox("Confirmo a remoção deste campo")
            remove = st.form_submit_button("Remover campo")
        if remove:
            if remove_id is None:
                st.error("Selecione o campo para remover.")
            elif not confirmed:
                st.error("Confirme a remoção do campo selecionado.")
            else:
                fields = [f for f in fields if f["id"] != remove_id]
                try:
                    crud.update_fields(st.user.to_dict(), event_id, fields)
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
                else:
                    ui().saved("Campo removido do formulário. As inscrições já recebidas foram preservadas.")


def manage_registrations(event):
    event_id = event["id"]
    st.subheader("Inscrições recebidas")
    edicao.link("registrations", "Editar inscrições")
    registrations = db.query("SELECT id,respostas FROM registrations WHERE evento_id = ? ORDER BY id DESC", (event_id,))
    display = []
    for registered in registrations:
        row = {"Inscrição": registered["id"]}
        row.update({value["campo"]: value["valor"] for value in json.loads(registered["respostas"]).values()})
        display.append(row)
    if display:
        st.dataframe(display, width="stretch", hide_index=True)
    else:
        st.info("Nenhuma inscrição recebida.")


def edit_page(event):
    st.subheader("Página e identidade visual")
    edicao.link("events", "Editar destino e demais dados do evento", event["id"])
    if event["capa"]:
        st.image(event["capa"], width=300)
    with st.form(f'edit_event_{event["id"]}'):
        titulo = st.text_input("Título", event["titulo"])
        tipo = st.selectbox("Tipo", ["Evento", "Retiro"], index=["Evento", "Retiro"].index(event["tipo"]))
        inicio = st.date_input("Início", value=date.fromisoformat(event["inicio"]), format="DD/MM/YYYY")
        termino = st.date_input("Término", value=date.fromisoformat(event["termino"]) if event["termino"] else None,
                                format="DD/MM/YYYY")
        local = st.text_input("Local", event["local"])
        descricao = st.text_area("Descrição", event["descricao"])
        capa = st.file_uploader("Substituir imagem de capa", type=["jpg", "jpeg", "png", "webp"])
        cor = st.color_picker("Cor principal", event["cor"])
        whatsapp = st.text_input("WhatsApp de contato", event["whatsapp"])
        save = st.form_submit_button("Salvar página", type="primary")
    if save:
        phone = re.sub(r"\D", "", whatsapp)
        if not titulo.strip():
            st.error("Informe o título.")
        elif termino and termino < inicio:
            st.error("A data de término não pode ser anterior à data de início.")
        elif whatsapp.strip() and not 10 <= len(phone) <= 15:
            st.error("Informe o WhatsApp com código do país, DDD e número.")
        else:
            try:
                raw, kind = ui().photo_data(capa) if capa else (event["capa"], event["capa_tipo"])
            except ValueError as exc:
                st.error(str(exc))
            else:
                try:
                    crud.update(st.user.to_dict(), "events", event["id"],
                                {"titulo": titulo, "tipo": tipo, "inicio": inicio.isoformat(),
                                 "termino": termino.isoformat() if termino else None, "local": local,
                                 "descricao": descricao, "capa": raw, "capa_tipo": kind, "cor": cor, "whatsapp": phone})
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
                else:
                    ui().saved("Página do evento atualizada.")
    url = event_url(event["id"])
    if url:
        st.link_button("Abrir página pública", url)


def manage_requests(event):
    st.subheader("Pedidos extras do evento")
    st.write("Crie pedidos conforme a necessidade do evento. As opções são definidas por você.")
    edicao.link("event_requests", "Editar pedidos extras")
    with st.form(f'create_request_{event["id"]}', clear_on_submit=True):
        titulo = st.text_input("Título do pedido *")
        descricao = st.text_area("Descrição do pedido")
        options_text = st.text_area("Opções (uma por linha)", help="Deixe vazio quando o pedido não precisar de opções.")
        create_request = st.form_submit_button("Adicionar pedido", type="primary")
    if create_request:
        if not titulo.strip():
            st.error("Informe o título do pedido.")
        else:
            options = list(dict.fromkeys(x.strip() for x in options_text.splitlines() if x.strip()))
            db.execute("INSERT INTO event_requests (evento_id,titulo,descricao,opcoes) VALUES (?,?,?,?)",
                       (event["id"], titulo.strip(), descricao.strip(), json.dumps(options, ensure_ascii=False)))
            ui().saved("Pedido disponibilizado aos inscritos.")
    requests = db.query("SELECT * FROM event_requests WHERE evento_id = ? ORDER BY id", (event["id"],))
    for request in requests:
        st.write(f'**{request["titulo"]}** — {request["descricao"]}')
        options = json.loads(request["opcoes"])
        if options:
            st.caption("Opções: " + ", ".join(options))
    orders = db.order_rows(event["id"])
    st.subheader("Pedidos recebidos")
    edicao.link("request_orders", "Editar pedidos recebidos")
    ui().records(orders, {"inscricao_id": "Inscrição", "pedido": "Pedido", "opcao": "Opção", "quantidade": "Quantidade"},
                 "Nenhum pedido recebido.")
    if orders:
        totals = {}
        for order in orders:
            key = (order["pedido"], order["opcao"])
            totals[key] = totals.get(key, 0) + order["quantidade"]
        st.write("Totais por pedido e opção")
        st.dataframe([{"Pedido": key[0], "Opção": key[1], "Quantidade": qty} for key, qty in totals.items()],
                     hide_index=True, width="stretch")


def moderate_testimonials(event):
    st.subheader("Aprovar testemunhos")
    edicao.link("testimonials", "Editar testemunhos")
    testimonies = db.query("SELECT * FROM testimonials WHERE evento_id = ? ORDER BY id DESC", (event["id"],))
    if not testimonies:
        st.info("Nenhum testemunho recebido.")
    for testimony in testimonies:
        with st.container(border=True):
            st.write(f'**{testimony["nome"]}** · {testimony["status"]}')
            st.write(testimony["texto"])
            if testimony["foto"]:
                st.image(testimony["foto"], width=220)
            left, right = st.columns(2)
            if left.button("Aprovar", key=f'approve_testimony_{testimony["id"]}'):
                try:
                    crud.update(st.user.to_dict(), "testimonials", testimony["id"], {"status": "Aprovado"})
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
                else:
                    ui().saved("Testemunho aprovado e publicado.")
            if right.button("Rejeitar", key=f'reject_testimony_{testimony["id"]}'):
                try:
                    crud.update(st.user.to_dict(), "testimonials", testimony["id"], {"status": "Rejeitado"})
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
                else:
                    ui().saved("Testemunho rejeitado.")
