"""Formulários de edição com os mesmos campos dos cadastros existentes."""

from datetime import date, time
import hashlib
import json

import streamlit as st

import access
import crud
import database as db

EMPTY = "__nao_informado__"


def ui():
    import portal
    return portal


def link(table, label="Editar registros", record_id=None, key=None):
    if st.button(label, key=key or f"editor_link_{table}_{record_id}"):
        st.session_state["crud_kind"] = table
        st.session_state.pop(f"crud_record_{table}", None)
        if record_id is not None:
            st.session_state[f"crud_record_{table}"] = record_id
        st.switch_page(st.session_state["pages"]["edit"])


def _get(table, record_id):
    rows = db.query(f"SELECT * FROM {table} WHERE id=?", (record_id,))
    return rows[0] if rows else {}


def _label(table, row):
    if table == "attendance":
        encounter = _get("meetings", row["encontro_id"])
        text = f'{_get("people", row["membro_id"]).get("nome", "Pessoa")} · {encounter.get("data", "")}'
    elif table == "followups":
        text = f'{_get("people", row["membro_id"]).get("nome", "Pessoa")} · {row["data"]}'
    elif table == "meetings":
        text = f'{row["data"]} · {row["tema"] or "Sem tema"}'
    elif table == "registrations":
        text = " · ".join(str(v["valor"]) for v in list(json.loads(row["respostas"]).values())[:2]) or "Inscrição"
    elif table == "request_orders":
        text = f'{_get("event_requests", row["pedido_id"]).get("titulo", "Pedido")} · Inscrição #{row["inscricao_id"]}'
    else:
        text = row.get("nome") or row.get("titulo") or "Registro"
    if table in ("registrations", "event_requests", "testimonials"):
        text += " · " + _get("events", row["evento_id"]).get("titulo", "Evento")
    elif table in ("meetings", "followups"):
        text += " · " + _get("groups", row["grupo_id"]).get("nome", "Grupo")
    if row.get("cev"):
        text += " · " + row["cev"]
    return f'{text} (#{row["id"]})'


def _select(label, choices, current, key, empty="Selecione", nullable=False):
    options = list(choices)
    if nullable:
        options.insert(0, EMPTY)
        if current is None:
            current = EMPTY
    def display(value):
        return "Não informado" if value == EMPTY else choices.get(value, str(value)) if isinstance(choices, dict) else str(value)
    selected = st.selectbox(label, options, index=options.index(current) if current in options else None,
                        format_func=display,
                        placeholder=empty, key=key, disabled=not options)
    return None if selected == EMPTY else selected


def _groups(actor, cev=None):
    return {g["id"]: g["nome"] + (" · Vínculo neutro" if g["neutro"] else "") for g in db.query("SELECT * FROM groups ORDER BY nome,id")
            if (cev is None or g["cev"] == cev) and access.can_use_group(actor, g)}


def _people(group_id, current=None):
    choices = {p["id"]: p["nome"] for p in db.group_people(group_id)} if group_id else {}
    if current is not None and current not in choices:
        row = _get("people", current)
        if row:
            choices[current] = row["nome"] + " (vínculo histórico)"
    return choices


def _date(label, value, key, birth=False):
    kwargs = {"min_value": date(1900, 1, 1), "max_value": ui().today() if birth else date(2100, 12, 31)}
    value = date.fromisoformat(value) if value else None
    if value and value < kwargs["min_value"]:
        kwargs["min_value"] = value
    return st.date_input(label, value=value, format="DD/MM/YYYY", key=key, **kwargs)


def _photo(row, column, key):
    if row[column]:
        st.image(row[column], width=220)
    upload = st.file_uploader("Substituir foto" if column == "foto" else "Substituir imagem de capa",
                              type=["jpg", "jpeg", "png", "webp"], key=key + "_upload")
    remove = st.checkbox("Remover foto atual" if column == "foto" else "Remover capa atual", key=key + "_remove")
    return upload, remove


def _target(actor, row, key):
    import servicos
    ministries = {m['id']: m for m in db.query('SELECT * FROM ministries ORDER BY nome') if servicos.can_manage(actor, m)}
    if row['destino'] == 'Ministério':
        chosen = _select('Ministério de destino', {i: m['nome']+' · '+(m['cev'] or 'Geral') for i,m in ministries.items()},
                         row.get('ministerio_id'), key+'_ministry')
        return {'destino': 'Ministério', 'ministerio_id': chosen, 'cev': ministries[chosen]['cev'] if chosen else None, 'grupo_id': None}
    if actor["nivel"] == "Responsável de grupo":
        st.caption("Destino: seu grupo autorizado.")
        return {"destino": "Grupo", "cev": actor["cev"], "grupo_id": actor["grupo_id"]}
    destinations = ["Geral", "CEv/Irradiação", "Grupo"] if actor["nivel"] == "Administrador" else ["CEv/Irradiação", "Grupo"]
    target = _select("Destino", destinations, row["destino"], key + "_destino")
    cev, group_id = None, None
    if target in ("CEv/Irradiação", "Grupo"):
        cevs = ui().load_content()["cevs"] if actor["nivel"] == "Administrador" else [actor["cev"]]
        cev = _select("CEv/Irradiação de destino", cevs, row["cev"], key + "_cev")
        if target == "Grupo":
            group_id = _select("Grupo de destino", _groups(actor, cev) if cev else {}, row["grupo_id"], key + "_grupo")
    return {"destino": target, "cev": cev, "grupo_id": group_id}


def _context(table, actor, row, key):
    """Seleções relacionadas ficam fora do formulário para atualizar as opções."""
    values = {}
    if table in ("groups", "people"):
        cevs = ui().load_content()["cevs"] if actor["nivel"] == "Administrador" else [actor["cev"]]
        values["cev"] = _select("CEv/Irradiação do cadastro", cevs, row["cev"], key + "_cev")
    elif table in ("meetings", "followups", "attendance"):
        old_group = row["grupo_id"] if table != "attendance" else _get("meetings", row["encontro_id"])["grupo_id"]
        group_id = _select("Grupo do registro", _groups(actor), old_group, key + "_grupo")
        if table == "attendance":
            meetings = {m["id"]: f'{m["data"]} · {m["tema"] or "Sem tema"}' for m in db.meetings(group_id)} if group_id else {}
            values["encontro_id"] = _select("Encontro", meetings, row["encontro_id"], key + "_encontro")
        else:
            values["grupo_id"] = group_id
        values["_group_id"] = group_id
    elif table in ("notices", "events"):
        values.update(_target(actor, row, key))
    elif table == "request_orders":
        event_id = _get("event_requests", row["pedido_id"])["evento_id"]
        requests = {r["id"]: r["titulo"] for r in db.query("SELECT * FROM event_requests WHERE evento_id=?", (event_id,))}
        values["pedido_id"] = _select("Pedido extra", requests, row["pedido_id"], key + "_pedido")
    return values


def _responses(row, key):
    previous = json.loads(row["respostas"])
    fields = json.loads(_get("events", row["evento_id"])["campos"])
    specs = {field["id"]: field for field in fields}
    ids = list(dict.fromkeys([*previous, *specs]))
    responses = {}
    for field_id in ids:
        old = previous.get(field_id, {})
        spec = specs.get(field_id, {})
        label = old.get("campo", spec.get("label", "Campo"))
        value = old.get("valor")
        kind = spec.get("type") or ("Confirmação" if isinstance(value, bool) else "Número" if isinstance(value, (int, float)) else "Texto")
        widget_key = key + "_answer_" + field_id
        compatible = value in (None, "")
        if kind in ("Texto", "Texto longo", "Escolha"):
            compatible = compatible or isinstance(value, str)
        elif kind == "Número":
            compatible = compatible or isinstance(value, (int, float)) and not isinstance(value, bool)
        elif kind == "Confirmação":
            compatible = isinstance(value, bool) or value is None
        elif kind == "Data":
            try:
                if value:
                    date.fromisoformat(value)
                compatible = True
            except (ValueError, TypeError):
                compatible = False
        if not compatible:
            st.caption(f"O formato de {label} foi alterado depois desta inscrição. Resposta anterior: {value}")
            if not st.checkbox(f"Atualizar o formato de {label}", key=widget_key + "_convert"):
                responses[field_id] = dict(old)
                continue
            value = None
        if kind == "Confirmação":
            answer = st.checkbox(label, value=bool(value), key=widget_key)
        elif kind == "Número":
            answer = st.number_input(label, value=value if value != "" else None, key=widget_key)
        elif kind == "Data":
            chosen = _date(label, value, widget_key)
            answer = chosen.isoformat() if chosen else None
        elif kind == "Escolha":
            options = list(spec.get("options", []))
            if value and value not in options:
                options.append(value)
            answer = _select(label, options, value, widget_key)
        elif kind == "Texto longo":
            answer = st.text_area(label, value=value or "", key=widget_key)
        else:
            answer = st.text_input(label, value=value or "", key=widget_key)
        responses[field_id] = {"campo": label, "valor": answer}
    return responses


def edit_form(claims, actor, table, row):
    revision = hashlib.sha256(json.dumps(row, sort_keys=True, default=lambda value: hashlib.sha256(value).hexdigest()).encode()).hexdigest()[:10]
    key = f'edit_{table}_{row["id"]}_{revision}'
    context = _context(table, actor, row, key)
    image = None
    with st.form(key):
        values = {k: v for k, v in context.items() if not k.startswith("_")}
        text_fields = {
            "groups": [("nome", "Nome *"), ("local", "Local"), ("publico", "Público")],
            "people": [("nome", "Nome *"), ("contato", "Contato"), ("instagram", "Instagram"),
                       ("acompanhador", "Acompanhador"), ("ministerio", "Ministério"),
                       ("email", "E-mail"), ("genero", "Gênero"), ("servico", "Serviço"),
                       ("funcao_servico", "Função no serviço")],
            "meetings": [("tema", "Tema")], "followups": [("acompanhador", "Acompanhador *")],
            "notices": [("titulo", "Título *")],
            "events": [("titulo", "Título *"), ("local", "Local"), ("whatsapp", "WhatsApp de contato")],
            "event_requests": [("titulo", "Título *")], "testimonials": [("nome", "Nome *")],
        }
        for column, label in text_fields.get(table, []):
            values[column] = st.text_input(label, row[column], key=key + "_" + column)
        area_fields = {"people": ("endereco", "Endereço"), "meetings": ("observacoes", "Observações"),
                       "followups": ("observacoes", "Observações"), "notices": ("texto", "Texto"),
                       "events": ("descricao", "Descrição"), "event_requests": ("descricao", "Descrição"),
                       "testimonials": ("texto", "Testemunho *")}
        if table in area_fields:
            column, label = area_fields[table]
            values[column] = st.text_area(label, row[column], key=key + "_" + column)
        if table == "groups":
            values["neutro"] = st.checkbox("Vínculo neutro (não contar como grupo de oração)",
                                           value=bool(row["neutro"]), key=key + "_neutral")
            start = _date("Data de início", row["data_inicio"], key + "_start")
            values["data_inicio"] = start.isoformat() if start else None
            available = db.group_people(row["id"]) if actor["nivel"] == "Responsável de grupo" else db.people(values["cev"]) if values["cev"] else []
            pastors = {p["id"]: p["nome"] for p in available if p["categoria"] == "Pastor"}
            values["pastor_id"] = _select("Pastor responsável", pastors, row["pastor_id"], key + "_pastor", nullable=True)
            values["fase"] = _select("Fase do grupo *", db.PHASES, row["fase"], key + "_fase")
            values["dia_encontro"] = _select("Dia do encontro", db.WEEKDAYS, row["dia_encontro"] or None, key + "_dia", nullable=True) or ""
            hour = st.time_input("Horário dos encontros", value=time.fromisoformat(row["horario"]) if row["horario"] else None,
                                 key=key + "_hora")
            values["horario"] = hour.strftime("%H:%M") if hour else ""
        elif table == "people":
            values["categoria"] = _select("Categoria *", db.CATEGORIES, row["categoria"], key + "_categoria")
            values["grupo_id"] = _select("Grupo", _groups(actor, values["cev"]) if values["cev"] else {}, row["grupo_id"], key + "_grupo", nullable=True)
            birth = _date("Data de nascimento", row["nascimento"], key + "_birth", birth=True)
            values["nascimento"] = birth.isoformat() if birth else None
            values["eh_comunidade"] = _select("Pertence à comunidade", {1: "Sim", 0: "Não"},
                                                row["eh_comunidade"], key + "_community", nullable=True)
        elif table == "attendance":
            current = row["membro_id"] if values["encontro_id"] == row["encontro_id"] else None
            values["membro_id"] = _select("Membro *", _people(context["_group_id"], current), row["membro_id"], key + "_membro")
            values["presenca"] = _select("Presença *", db.PRESENCES, row["presenca"], key + "_presenca")
        elif table == "followups":
            current = row["membro_id"] if values["grupo_id"] == row["grupo_id"] else None
            values["membro_id"] = _select("Membro *", _people(context["_group_id"], current), row["membro_id"], key + "_membro")
            next_date = _date("Próximo acompanhamento", row["proximo_acompanhamento"], key + "_proximo")
            values["proximo_acompanhamento"] = next_date.isoformat() if next_date else None
        if table in ("meetings", "followups"):
            chosen = _date("Data", row["data"], key + "_data")
            values["data"] = chosen.isoformat() if chosen else None
        if table in ("groups", "people"):
            values["ativo"] = _select("Ativo", {1: "Sim", 0: "Não"}, row["ativo"], key + "_active", nullable=True)
        if table in ("groups", "people", "notices", "testimonials"):
            image = ("foto", *_photo(row, "foto", key + "_foto"))
        elif table == "events":
            values["tipo"] = _select("Tipo", ["Evento", "Retiro"], row["tipo"], key + "_tipo")
            for column, label in (("inicio", "Início"), ("termino", "Término")):
                chosen = _date(label, row[column], key + "_" + column)
                values[column] = chosen.isoformat() if chosen else None
            values["cor"] = st.color_picker("Cor principal", row["cor"], key=key + "_cor")
            image = ("capa", *_photo(row, "capa", key + "_capa"))
        elif table == "registrations":
            values["respostas"] = _responses(row, key)
        elif table == "event_requests":
            options = st.text_area("Opções (uma por linha)", "\n".join(json.loads(row["opcoes"])), key=key + "_options")
            values["opcoes"] = [option.strip() for option in options.splitlines() if option.strip()]
        elif table == "request_orders":
            request = _get("event_requests", values["pedido_id"])
            registrations = {r["id"]: _label("registrations", r) for r in db.query(
                "SELECT * FROM registrations WHERE evento_id=?", (request.get("evento_id"),))}
            values["inscricao_id"] = _select("Inscrição", registrations, row["inscricao_id"], key + "_inscricao")
            options = json.loads(request.get("opcoes", "[]"))
            values["opcao"] = _select("Opção", options, row["opcao"], key + "_opcao") if options else ""
            values["quantidade"] = st.number_input("Quantidade", min_value=1, value=row["quantidade"], step=1, key=key + "_qty")
        elif table == "testimonials":
            values["status"] = _select("Publicação", ["Pendente", "Aprovado", "Rejeitado"], row["status"], key + "_status")
        save = st.form_submit_button("Salvar alterações", type="primary")
    if save:
        try:
            if image:
                column, upload, remove = image
                if upload and remove:
                    raise ValueError("Escolha substituir ou remover a imagem.")
                if upload or remove:
                    raw, kind = ui().photo_data(upload) if upload else (None, None)
                    values.update({column: raw, column + "_tipo": kind})
            crud.update(claims, table, row["id"], values)
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))
        else:
            ui().saved("Alterações salvas.")


def delete_form(claims, table, row):
    allowed, related = crud.deletion_info(claims, table, row["id"])
    if not allowed:
        st.caption("Exclusão indisponível: sua conta precisa de permissão para este destino.")
        return
    with st.expander("Excluir este registro"):
        if related:
            st.write("Antes de excluir, revise os registros vinculados:")
            for label, count in related.items():
                st.write(f"{label}: {count}")
            return
        st.write(f'Você excluirá {_label(table, row)}. Esta operação não apaga outros registros.')
        with st.form(f'delete_{table}_{row["id"]}'):
            confirmed = st.checkbox("Confirmo a exclusão deste registro")
            delete = st.form_submit_button("Excluir registro")
        if delete:
            if not confirmed:
                st.error("Confirme a exclusão do registro selecionado.")
            else:
                try:
                    crud.delete(claims, table, row["id"])
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
                else:
                    st.session_state.pop(f"crud_record_{table}", None)
                    if table == "groups" and st.session_state.get("selected_group") == row["id"]:
                        st.session_state.pop("selected_group", None)
                    ui().saved("Registro excluído.")


def page():
    ui().header("Editar registros", "Consulte e atualize os registros autorizados para sua conta.")
    if not ui().protected():
        return
    claims = st.user.to_dict()
    actor = access.profile(claims)
    choices = dict(crud.LABELS) if actor["nivel"] in ("Administrador", "Gestor de CEv") else {
        k: crud.LABELS[k] for k in crud.LABELS if k != "people"}
    if st.session_state.get("crud_kind") not in choices:
        st.session_state.pop("crud_kind", None)
    table = st.selectbox("Tipo de registro", list(choices), format_func=choices.get, key="crud_kind")
    rows = crud.list_records(claims, table)
    if not rows:
        st.info("Nenhum registro disponível para editar.")
        return
    labels = {row["id"]: _label(table, row) for row in rows}
    st.dataframe([{"Registro": label} for label in labels.values()], hide_index=True, width="stretch")
    record_id = st.selectbox("Registro para editar", list(labels), format_func=labels.get, index=None,
                            placeholder="Selecione um registro", key=f"crud_record_{table}")
    if record_id is None:
        return
    row = next(r for r in rows if r["id"] == record_id)
    st.subheader(labels[record_id])
    edit_form(claims, actor, table, row)
    delete_form(claims, table, row)
