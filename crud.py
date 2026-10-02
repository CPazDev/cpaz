"""Edição e exclusão de registros com autorização verificada no servidor."""

from datetime import date, time
import json
import re
import sqlite3

import access
import database as db

LABELS = {
    "groups": "Grupos", "people": "Membros, pastores e núcleo", "meetings": "Encontros",
    "attendance": "Frequências", "followups": "Acompanhamentos", "notices": "Avisos",
    "events": "Eventos e retiros", "registrations": "Inscrições", "event_requests": "Pedidos extras",
    "request_orders": "Pedidos recebidos", "testimonials": "Testemunhos",
}
FIELDS = {
    "groups": {"cev", "nome", "pastor_id", "local", "dia_encontro", "horario", "fase", "foto", "foto_tipo",
               "data_inicio", "publico", "ativo", "neutro"},
    "people": {"cev", "nome", "categoria", "contato", "grupo_id", "instagram", "endereco", "nascimento",
               "foto", "foto_tipo", "acompanhador", "ministerio", "email", "genero", "servico",
               "funcao_servico", "eh_comunidade", "ativo"},
    "meetings": {"grupo_id", "data", "tema", "observacoes"},
    "attendance": {"encontro_id", "membro_id", "presenca"},
    "followups": {"grupo_id", "membro_id", "data", "acompanhador", "proximo_acompanhamento", "observacoes"},
    "notices": {"titulo", "texto", "destino", "cev", "grupo_id", "foto", "foto_tipo"},
    "events": {"titulo", "tipo", "inicio", "termino", "local", "descricao", "destino", "cev", "grupo_id",
               "capa", "capa_tipo", "cor", "whatsapp"},
    "registrations": {"respostas"},
    "event_requests": {"titulo", "descricao", "opcoes"},
    "request_orders": {"inscricao_id", "pedido_id", "opcao", "quantidade"},
    "testimonials": {"nome", "texto", "foto", "foto_tipo", "status"},
}


def _row(conn, table, record_id):
    if table not in LABELS:
        raise ValueError("Tipo de registro inválido.")
    result = conn.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
    if not result:
        raise ValueError("Registro não encontrado. Atualize a página.")
    return dict(result)


def record_scope(conn, table, row):
    if table == "groups":
        return row["cev"], row["id"]
    if table == "people":
        return row["cev"], row["grupo_id"]
    if table in ("meetings", "followups"):
        group = _row(conn, "groups", row["grupo_id"])
        return group["cev"], group["id"]
    if table == "attendance":
        return record_scope(conn, "meetings", _row(conn, "meetings", row["encontro_id"]))
    if table in ("notices", "events"):
        return row["cev"], row["grupo_id"]
    if table == "request_orders":
        request = _row(conn, "event_requests", row["pedido_id"])
        event = _row(conn, "events", request["evento_id"])
    else:
        event = _row(conn, "events", row["evento_id"])
    return record_scope(conn, "events", event)


def _allowed(conn, actor, table, row):
    if not actor:
        return False
    if table == "groups":
        return access.can_use_group(actor, row)
    if table == "people":
        return access.can_manage_cev(actor, row["cev"])
    if table in ("meetings", "followups", "attendance"):
        _, group_id = record_scope(conn, table, row)
        return access.can_use_group(actor, _row(conn, "groups", group_id))
    if table == "notices":
        return access.can_manage_publication(actor, row["destino"], row["cev"], row["grupo_id"])
    if table == "events":
        event = row
    elif table == "request_orders":
        request = _row(conn, "event_requests", row["pedido_id"])
        event = _row(conn, "events", request["evento_id"])
    else:
        event = _row(conn, "events", row["evento_id"])
    return access.can_manage_publication(actor, event["destino"], event["cev"], event["grupo_id"])


def create_notice(claims, titulo, texto, destino, cev=None, grupo_id=None, foto=None, foto_tipo=None):
    actor = access.profile(claims)
    if not access.can_manage_publication(actor, destino, cev, grupo_id):
        raise PermissionError("O destino do aviso está fora do seu acesso.")
    return db.save_notice(titulo, texto, destino, cev, grupo_id, foto, foto_tipo)


def share_event(claims, event_id, destino, cev=None, grupo_id=None):
    if not isinstance(event_id, int) or isinstance(event_id, bool):
        raise ValueError("Selecione um evento/retiro válido.")
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        actor = access._locked_actor(conn, claims)
        if not access.can_manage_publication(actor, destino, cev, grupo_id):
            raise PermissionError("O destino do compartilhamento está fora do seu acesso.")
        event = _row(conn, "events", event_id)
        if not access.can_view_publication(actor, event):
            raise PermissionError("Sua conta não pode compartilhar este evento/retiro.")
        target = {"destino": destino, "cev": cev, "grupo_id": grupo_id}
        _target(conn, target)
        return conn.execute("""INSERT INTO notices (titulo,texto,destino,cev,grupo_id,publicado_em,evento_id)
            VALUES (?,?,?,?,?,?,?)""", (event["titulo"], "", target["destino"], target["cev"], target["grupo_id"],
                                       db.publication_time(), event_id)).lastrowid


def create_event(claims, titulo, tipo, inicio, termino, local, descricao, destino, cev, grupo_id,
                 capa, capa_tipo, cor, whatsapp):
    actor = access.profile(claims)
    if not access.can_manage_publication(actor, destino, cev, grupo_id):
        raise PermissionError("O destino do evento está fora do seu acesso.")
    return db.save_event(titulo, tipo, inicio, termino, local, descricao, destino, cev, grupo_id,
                         capa, capa_tipo, cor, whatsapp, actor["email"])


def list_records(claims, table, cev=None):
    actor = access.profile(claims)
    if table not in LABELS:
        raise ValueError("Tipo de registro inválido.")
    if not actor:
        return []
    with db.connection() as conn:
        rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id DESC")]
        return [r for r in rows if _allowed(conn, actor, table, r)
                and (cev is None or record_scope(conn, table, r)[0] == cev)]


def _date(value, label, required=False):
    if not value and not required:
        return
    try:
        date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError(f"Informe uma data válida em {label}.") from None


def _membership(conn, group_id, member_id):
    group = _row(conn, "groups", group_id)
    person = _row(conn, "people", member_id)
    if person["cev"] != group["cev"] or (person["grupo_id"] != group_id and group["pastor_id"] != member_id):
        raise ValueError("Selecione uma pessoa vinculada ao grupo.")


def _target(conn, row):
    if row["destino"] == "Geral":
        row["cev"], row["grupo_id"] = None, None
    elif row["destino"] not in ("CEv/Irradiação", "Grupo") or not row["cev"]:
        raise ValueError("Selecione o destino da publicação.")
    elif row["destino"] == "Grupo":
        if _row(conn, "groups", row["grupo_id"])["cev"] != row["cev"]:
            raise ValueError("O grupo de destino deve pertencer ao CEv selecionado.")
    else:
        row["grupo_id"] = None


def _validate(conn, table, old, row, actor=None):
    required = {"groups": ("nome",), "people": ("nome",), "followups": ("acompanhador",),
                "notices": ("titulo",), "events": ("titulo",), "event_requests": ("titulo",),
                "testimonials": ("nome", "texto")}.get(table, ())
    if any(not isinstance(row[key], str) or not row[key].strip() for key in required):
        raise ValueError("Preencha os campos obrigatórios.")
    if table == "groups":
        db.validate_details(data_inicio=row["data_inicio"], ativo=row["ativo"])
        db.validate_neutral(row["neutro"])
        if row["fase"] not in db.PHASES or row["dia_encontro"] not in ("", *db.WEEKDAYS):
            raise ValueError("Selecione a fase e um dia da semana válido.")
        if row["horario"]:
            try:
                time.fromisoformat(row["horario"])
            except (ValueError, TypeError):
                raise ValueError("Informe um horário válido.") from None
        if row["pastor_id"] is not None:
            pastor = _row(conn, "people", row["pastor_id"])
            if pastor["cev"] != row["cev"] or pastor["categoria"] != "Pastor":
                raise ValueError("Selecione um pastor deste CEv.")
            if actor and actor["nivel"] == "Responsável de grupo" and (
                    pastor["grupo_id"] != old["id"] and pastor["id"] != old["pastor_id"]):
                raise PermissionError("Selecione um pastor vinculado ao seu grupo.")
        if row["cev"] != old["cev"] and any(dependencies(conn, table, old["id"]).values()):
            raise ValueError("Não é possível mudar o CEv de um grupo com vínculos. Revise os registros vinculados primeiro.")
    elif table == "people":
        db.validate_details(nascimento=row["nascimento"], ativo=row["ativo"], eh_comunidade=row["eh_comunidade"])
        if row["categoria"] not in db.CATEGORIES:
            raise ValueError("Selecione uma categoria válida.")
        _date(row["nascimento"], "Data de nascimento")
        if row["nascimento"] and row["nascimento"] > date.today().isoformat():
            raise ValueError("A data de nascimento não pode estar no futuro.")
        if row["grupo_id"] is not None and _row(conn, "groups", row["grupo_id"])["cev"] != row["cev"]:
            raise ValueError("Selecione um grupo deste CEv.")
        pastors = conn.execute("SELECT cev FROM groups WHERE pastor_id=?", (row["id"],)).fetchall()
        if pastors and (row["categoria"] != "Pastor" or any(g["cev"] != row["cev"] for g in pastors)):
            raise ValueError("Esta pessoa é pastor responsável. Atualize o responsável dos grupos antes de alterar a categoria ou o CEv.")
        if row["cev"] != old["cev"] and (conn.execute("SELECT 1 FROM attendance WHERE membro_id=?", (row["id"],)).fetchone()
                or conn.execute("SELECT 1 FROM followups WHERE membro_id=?", (row["id"],)).fetchone()):
            raise ValueError("Não é possível mudar o CEv de uma pessoa com histórico vinculado.")
    elif table in ("meetings", "followups"):
        _row(conn, "groups", row["grupo_id"])
        _date(row["data"], "Data", True)
        if table == "meetings" and row["grupo_id"] != old["grupo_id"] and conn.execute(
                "SELECT 1 FROM attendance WHERE encontro_id=?", (row["id"],)).fetchone():
            raise ValueError("Este encontro possui frequências. Revise os vínculos antes de mudar o grupo.")
        if table == "followups":
            if (row["grupo_id"], row["membro_id"]) != (old["grupo_id"], old["membro_id"]):
                _membership(conn, row["grupo_id"], row["membro_id"])
            _date(row["proximo_acompanhamento"], "Próximo acompanhamento")
            if row["proximo_acompanhamento"] and row["proximo_acompanhamento"] < row["data"]:
                raise ValueError("O próximo acompanhamento não pode ser anterior à data registrada.")
    elif table == "attendance":
        if row["presenca"] not in db.PRESENCES:
            raise ValueError("Selecione uma presença válida.")
        if (row["encontro_id"], row["membro_id"]) != (old["encontro_id"], old["membro_id"]):
            meeting = _row(conn, "meetings", row["encontro_id"])
            _membership(conn, meeting["grupo_id"], row["membro_id"])
    elif table in ("notices", "events"):
        _target(conn, row)
        if table == "events":
            if row["tipo"] not in ("Evento", "Retiro"):
                raise ValueError("Selecione um tipo válido.")
            _date(row["inicio"], "Início", True)
            _date(row["termino"], "Término")
            if row["termino"] and row["termino"] < row["inicio"]:
                raise ValueError("A data de término não pode ser anterior ao início.")
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", row["cor"]):
                raise ValueError("Informe uma cor válida.")
            row["whatsapp"] = re.sub(r"\D", "", row["whatsapp"])
            if row["whatsapp"] and not 10 <= len(row["whatsapp"]) <= 15:
                raise ValueError("Informe o WhatsApp com código do país, DDD e número.")
    elif table == "registrations":
        responses = row["respostas"]
        previous = json.loads(old["respostas"])
        fields = json.loads(_row(conn, "events", row["evento_id"])["campos"])
        specs = {f["id"]: f for f in fields}
        if not isinstance(responses, dict) or set(responses) != set(previous) | set(specs):
            raise ValueError("Use os campos definidos para esta inscrição.")
        for key, answer in responses.items():
            spec = specs.get(key)
            label = previous[key]["campo"] if key in previous else spec["label"]
            if not isinstance(answer, dict) or set(answer) != {"campo", "valor"} or answer["campo"] != label:
                raise ValueError("O nome dos campos da inscrição deve ser preservado.")
            value = answer["valor"]
            if spec:
                if spec["required"] and (value is None or value is False or isinstance(value, str) and not value.strip()):
                    raise ValueError(f"Preencha o campo obrigatório: {label}.")
                kind = spec["type"]
                # Respostas históricas conservam o valor original quando o tipo
                # do campo foi alterado depois da inscrição.
                if value not in (None, "") and value != previous.get(key, {}).get("valor"):
                    if kind in ("Texto", "Texto longo") and not isinstance(value, str):
                        raise ValueError(f"Informe um texto em {label}.")
                    if kind == "Número" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
                        raise ValueError(f"Informe um número em {label}.")
                    if kind == "Data":
                        _date(value, label, True)
                    if kind == "Escolha" and value not in spec["options"] and value != previous.get(key, {}).get("valor"):
                        raise ValueError(f"Selecione uma opção válida em {label}.")
                    if kind == "Confirmação" and not isinstance(value, bool):
                        raise ValueError(f"Informe uma confirmação em {label}.")
        row["respostas"] = json.dumps(responses, ensure_ascii=False, allow_nan=False)
    elif table == "event_requests":
        options = row["opcoes"]
        if not isinstance(options, list) or any(not isinstance(o, str) or not o.strip() for o in options):
            raise ValueError("Informe opções válidas.")
        options = list(dict.fromkeys(o.strip() for o in options))
        used = [r[0] for r in conn.execute("SELECT DISTINCT opcao FROM request_orders WHERE pedido_id=?", (row["id"],))]
        if any(option not in options if options else option != "" for option in used):
            raise ValueError("Não é possível remover opções que já possuem pedidos recebidos.")
        row["opcoes"] = json.dumps(options, ensure_ascii=False)
    elif table == "request_orders":
        request = _row(conn, "event_requests", row["pedido_id"])
        registration = _row(conn, "registrations", row["inscricao_id"])
        original = _row(conn, "event_requests", old["pedido_id"])
        if request["evento_id"] != original["evento_id"] or registration["evento_id"] != request["evento_id"]:
            raise ValueError("O pedido e a inscrição devem pertencer ao mesmo evento original.")
        options = json.loads(request["opcoes"])
        if row["opcao"] not in (options or [""]):
            raise ValueError("Selecione uma opção válida para este pedido.")
        if isinstance(row["quantidade"], bool) or not isinstance(row["quantidade"], int) or row["quantidade"] < 1:
            raise ValueError("Informe uma quantidade maior que zero.")
    elif table == "testimonials" and row["status"] not in ("Pendente", "Aprovado", "Rejeitado"):
        raise ValueError("Selecione um status válido.")


def update(claims, table, record_id, changes):
    actor = access.profile(claims)
    if table not in FIELDS or not changes or not set(changes) <= FIELDS[table]:
        raise ValueError("Campos de edição inválidos.")
    try:
        with db.connection() as conn:
            old = _row(conn, table, record_id)
            if not _allowed(conn, actor, table, old):
                raise PermissionError("Sua conta não pode editar este registro.")
            row = {**old, **changes}
            if not _allowed(conn, actor, table, row):
                raise PermissionError("O novo destino está fora do seu nível de acesso.")
            for key in changes:
                if isinstance(row[key], str) and key not in ("respostas", "opcoes"):
                    row[key] = row[key].strip()
            # Os campos JSON são tratados como estruturas na validação.
            if table == "event_requests" and "opcoes" not in changes:
                row["opcoes"] = json.loads(old["opcoes"])
            _validate(conn, table, old, row, actor)
            if not _allowed(conn, actor, table, row):
                raise PermissionError("O novo destino está fora do seu nível de acesso.")
            keys = list(changes)
            if table in ("notices", "events"):
                keys = list(dict.fromkeys([*keys, "cev", "grupo_id"]))
            conn.execute(f"UPDATE {table} SET " + ",".join(f"{k}=?" for k in keys) + " WHERE id=?",
                         (*[row[k] for k in keys], record_id))
    except sqlite3.IntegrityError:
        raise ValueError("A alteração criaria um vínculo inválido ou um registro duplicado.") from None


def dependencies(conn, table, record_id):
    related = {}
    for child in (*LABELS, "access_grants", "account_profiles"):
        for fk in conn.execute(f"PRAGMA foreign_key_list({child})"):
            if fk["table"] == table and fk["on_delete"] != "SET NULL":
                count = conn.execute(f'SELECT count(*) FROM {child} WHERE "{fk["from"]}"=?', (record_id,)).fetchone()[0]
                if count:
                    label = LABELS.get(child, "Perfis de usuário" if child == "account_profiles" else "Contas autorizadas")
                    related[label] = related.get(label, 0) + count
    return related


def deletion_info(claims, table, record_id):
    actor = access.profile(claims)
    with db.connection() as conn:
        row = _row(conn, table, record_id)
        if not _allowed(conn, actor, table, row):
            return False, {}
        cev, group_id = record_scope(conn, table, row)
        return access.can_delete(claims, cev, group_id), dependencies(conn, table, record_id)


def delete(claims, table, record_id):
    actor = access.profile(claims)
    try:
        with db.connection() as conn:
            row = _row(conn, table, record_id)
            if not _allowed(conn, actor, table, row):
                raise PermissionError("Sua conta não pode excluir este registro.")
            cev, group_id = record_scope(conn, table, row)
            if not access.can_delete(claims, cev, group_id):
                raise PermissionError("Sua conta não tem permissão de exclusão neste CEv ou grupo.")
            related = dependencies(conn, table, record_id)
            if related:
                raise ValueError("Revise os registros vinculados antes de excluir: " + ", ".join(f"{k}: {v}" for k, v in related.items()))
            if table == "groups":
                conn.execute("DELETE FROM deletion_permissions WHERE grupo_id=?", (record_id,))
            conn.execute(f"DELETE FROM {table} WHERE id=?", (record_id,))
    except sqlite3.IntegrityError:
        raise ValueError("Existem registros vinculados. Revise os vínculos antes de excluir.") from None


def update_fields(claims, event_id, fields):
    actor = access.profile(claims)
    with db.connection() as conn:
        event = _row(conn, "events", event_id)
        if not _allowed(conn, actor, "events", event):
            raise PermissionError("Sua conta não pode alterar este formulário.")
        previous = json.loads(event["campos"])
        if not isinstance(fields, list) or any(not isinstance(field, dict) for field in fields):
            raise ValueError("Informe campos válidos para a inscrição.")
        ids, labels = set(), set()
        for field in fields:
            if set(field) != {"id", "label", "type", "required", "options"}:
                raise ValueError("Informe os dados completos do campo.")
            if not isinstance(field["id"], str) or not field["id"] or field["id"] in ids:
                raise ValueError("Cada campo deve ter uma identificação única.")
            if not isinstance(field["label"], str) or not field["label"].strip() or field["label"].strip().casefold() in labels:
                raise ValueError("Informe um nome único para cada campo.")
            if field["type"] not in ("Texto", "Texto longo", "Número", "Data", "Escolha", "Confirmação") or not isinstance(field["required"], bool):
                raise ValueError("Selecione um tipo e a obrigatoriedade do campo.")
            if not isinstance(field["options"], list) or any(not isinstance(o, str) or not o.strip() for o in field["options"]):
                raise ValueError("Informe opções válidas.")
            if field["type"] == "Escolha" and not field["options"]:
                raise ValueError("Informe as opções de escolha.")
            ids.add(field["id"])
            labels.add(field["label"].strip().casefold())
        if {field["id"] for field in previous} - ids:
            cev, group_id = record_scope(conn, "events", event)
            if not access.can_delete(claims, cev, group_id):
                raise PermissionError("A remoção de campos exige permissão de exclusão no destino do evento.")
        conn.execute("UPDATE events SET campos=? WHERE id=?", (json.dumps(fields, ensure_ascii=False), event_id))
