"""Importação local, transacional e repetível de pessoas e grupos do AppSheet.

Lê os valores salvos no XLSX sem modificar a planilha ou executar fórmulas.
O modo padrão é uma simulação; --apply exige o SHA256 revisado.
"""

import argparse
from contextlib import closing
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path, PurePosixPath
import sqlite3
import xml.etree.ElementTree as ET
from zipfile import ZipFile

import database as db

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
REQUIRED = {"Grupos": {"ID_Grupo", "Nome", "Fase", "DataInicio", "Publico", "Ativo"},
            "Pessoas": {"ID_Pessoa", "Nome", "Grupo", "Função", "Telefone", "Instagram", "Email",
                        "Acompanhador", "DataNascimento", "Genero", "Serviço", "Funcao_Serv", "EhComunidade", "Ativo"}}


def read_workbook(path, required=None):
    required = REQUIRED if required is None else required
    content = Path(path).read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    from io import BytesIO
    with ZipFile(BytesIO(content)) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        props = workbook.find("s:workbookPr", NS)
        epoch1904 = props is not None and props.get("date1904") in ("1", "true")
        relationships = {r.get("Id"): r.get("Target") for r in
                         ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))}
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ["".join(n.text or "" for n in item.findall(".//s:t", NS))
                       for item in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
        sheets = {}
        for sheet in workbook.findall("s:sheets/s:sheet", NS):
            name = sheet.get("name")
            if name not in required:
                continue
            target = relationships[sheet.get(REL)].replace("\\", "/")
            target = target.lstrip("/") if target.startswith("/") else str(PurePosixPath("xl") / target)
            root = ET.fromstring(archive.read(target))
            headers, rows = {}, []
            for row in root.findall("s:sheetData/s:row", NS):
                values = {}
                for cell in row.findall("s:c", NS):
                    column = "".join(c for c in cell.get("r") if c.isalpha())
                    node = cell.find("s:v", NS)
                    value = node.text or "" if node is not None else ""
                    kind = cell.get("t")
                    if kind == "s":
                        value = strings[int(value)]
                    elif kind == "inlineStr":
                        value = "".join(n.text or "" for n in cell.findall(".//s:t", NS))
                    if kind == "e" or cell.find("s:f", NS) is not None and node is None:
                        raise ValueError(f"{name}!{cell.get('r')}: fórmula sem valor válido salvo. Abra e salve a planilha no Excel.")
                    if value != "":
                        values[column] = value
                if not headers:
                    if values:
                        headers = {col: value.strip() for col, value in values.items()}
                        if len(set(headers.values())) != len(headers):
                            raise ValueError(f"Cabeçalhos duplicados em {name}.")
                    continue
                if values:
                    rows.append({"_row": int(row.get("r")),
                                 **{headers[col]: value for col, value in values.items() if col in headers}})
            if not required[name] <= set(headers.values()):
                raise ValueError(f"Cabeçalhos obrigatórios ausentes em {name}: {sorted(required[name] - set(headers.values()))}")
            sheets[name] = rows
        if set(sheets) != set(required):
            raise ValueError(f"A planilha precisa conter as abas: {', '.join(required)}.")
    return digest, epoch1904, sheets


def excel_date(value, epoch1904=False):
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        pass
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number != number.to_integral_value():
            raise ValueError("Data Excel inválida.")
        serial = int(number)
        if not epoch1904 and serial == 60:
            raise ValueError("29/02/1900 não é uma data válida.")
        base = date(1904, 1, 1) if epoch1904 else date(1899, 12, 30) if serial > 60 else date(1899, 12, 31)
        return (base + timedelta(days=serial)).isoformat()
    except (InvalidOperation, OverflowError):
        raise ValueError("Data Excel inválida.") from None


def phone(value):
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        number = Decimal(value)
    except InvalidOperation:
        return value  # Telefones já formatados permanecem como texto.
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        raise ValueError("Telefone numérico inválido.")
    return value if value.isdigit() else format(number, ".0f")


def boolean(value):
    if value in (None, ""):
        return None
    if str(value).strip() not in ("0", "1"):
        raise ValueError("Indicação deve ser 0 ou 1.")
    return int(value)


def _fingerprint(conn):
    values = {}
    for table in ("groups", "people"):
        values[table] = [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]
    return hashlib.sha256(json.dumps(values, sort_keys=True,
        default=lambda data: hashlib.sha256(data).hexdigest()).encode()).hexdigest()


def prepare(path, cev, *, phases=None, pastors_from_members=False, source=None, birth_dates=None):
    digest, epoch, sheets = read_workbook(path)
    source = source or f"CPaz:{cev}"
    report = {"arquivo": Path(path).name, "sha256": digest, "cev": cev, "origem": source,
              "escopo": ["Grupos", "Pessoas"], "avisos": [], "erros": [], "ignorados": [], "grupos": [], "pessoas": [],
              "pastores": [], "dados_originais": sheets, "pastores_dos_grupos": pastors_from_members,
              "decisoes": {"fases": phases or {}, "nascimentos": birth_dates or {}}}
    def warning(sheet, row, field, message):
        report["avisos"].append({"aba": sheet, "linha": row["_row"], "campo": field, "mensagem": message})
    def convert(sheet, row, field, function):
        try:
            return function(row.get(field))
        except ValueError as exc:
            report["erros"].append({"aba": sheet, "linha": row["_row"], "campo": field, "mensagem": str(exc)})
            return None
    valid = {}
    for sheet, key in (("Grupos", "ID_Grupo"), ("Pessoas", "ID_Pessoa")):
        valid[sheet] = {}
        for row in sheets[sheet]:
            identity, name = row.get(key, "").strip(), row.get("Nome", "").strip()
            if not identity and not name:
                report["ignorados"].append({"aba": sheet, "linha": row["_row"], "motivo": "Linha sem identificação e nome"})
                continue
            if not identity or not name or identity in valid[sheet]:
                report["erros"].append({"aba": sheet, "linha": row["_row"], "mensagem": "ID/nome ausente ou ID duplicado"})
                continue
            valid[sheet][identity] = row
    with db.connection() as conn:
        report["banco_revisado"] = _fingerprint(conn)
        mappings = {}
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='import_records'").fetchone():
            mappings = {(r["entity"], r["source_id"]): r["target_id"] for r in
                        conn.execute("SELECT * FROM import_records WHERE source=?", (source,))}
        for sheet, table, collection in (("Grupos", "groups", "grupos"), ("Pessoas", "people", "pessoas")):
            existing = [dict(r) for r in conn.execute(f"SELECT * FROM {table} WHERE cev=?", (cev,))]
            for identity, row in valid[sheet].items():
                name = row["Nome"].strip()
                target = mappings.get((table, identity))
                if target is not None:
                    found = conn.execute(f"SELECT * FROM {table} WHERE id=?", (target,)).fetchone()
                    if not found or found["cev"] != cev:
                        report["erros"].append({"aba": sheet, "linha": row["_row"], "mensagem": "Registro importado foi excluído ou mudou de CEv; revise o vínculo antes de reimportar"})
                        continue
                    report[collection].append({"origem_id": identity, "destino_id": target, "acao": "já importado"})
                    continue
                matches = [r for r in existing if r["nome"].strip().casefold() == name.casefold()]
                if len(matches) > 1 or table == "people" and matches:
                    report["erros"].append({"aba": sheet, "linha": row["_row"], "mensagem": f"Cadastro existente exige correspondência manual: {name}"})
                    continue
                old = matches[0] if matches else None
                data = {"cev": cev, "nome": name, "ativo": convert(sheet, row, "Ativo", boolean)}
                if data["ativo"] is None:
                    warning(sheet, row, "Ativo", "Situação ausente; preservada como não informada")
                if table == "groups":
                    data.update(fase=row.get("Fase", ""), publico=row.get("Publico", "").strip(),
                                data_inicio=convert(sheet, row, "DataInicio", lambda v: excel_date(v, epoch)))
                    if data["fase"] not in db.PHASES:
                        report["erros"].append({"aba": sheet, "linha": row["_row"], "mensagem": "Fase não aprovada"})
                    if old and old["fase"] != data["fase"]:
                        selected = (phases or {}).get(identity)
                        if selected not in (old["fase"], data["fase"]):
                            report["erros"].append({"aba": sheet, "linha": row["_row"], "mensagem": f"Decida a fase de {name}: {old['fase']} / {data['fase']}"})
                        else:
                            data["fase"] = selected
                else:
                    group = row.get("Grupo", "").strip()
                    if group and group not in valid["Grupos"]:
                        report["erros"].append({"aba": sheet, "linha": row["_row"], "mensagem": "Grupo referenciado ausente"})
                    data.update(categoria=row.get("Função", ""), contato=convert(sheet, row, "Telefone", phone) or "",
                                instagram=row.get("Instagram", "").strip(), email=row.get("Email", "").strip(),
                                genero=row.get("Genero", "").strip(), servico=row.get("Serviço", "").strip(),
                                funcao_servico=row.get("Funcao_Serv", "").strip(),
                                eh_comunidade=convert(sheet, row, "EhComunidade", boolean),
                                nascimento=convert(sheet, row, "DataNascimento", lambda v: excel_date(v, epoch)),
                                ministerio=row.get("Ministerio", "").strip(), acompanhador="")
                    if data["categoria"] not in db.CATEGORIES:
                        report["erros"].append({"aba": sheet, "linha": row["_row"], "mensagem": "Categoria não aprovada"})
                    if identity in (birth_dates or {}):
                        replacement = birth_dates[identity]
                        if replacement is not None:
                            db.validate_details(nascimento=replacement)
                        data["nascimento"] = replacement
                        warning(sheet, row, "DataNascimento", "Nascimento substituído por decisão explícita; valor original conservado no relatório")
                    if data["nascimento"] and data["nascimento"] > date.today().isoformat():
                        report["erros"].append({"aba": sheet, "linha": row["_row"], "mensagem": "Nascimento no futuro exige revisão"})
                    accompanier = row.get("Acompanhador", "").strip()
                    if accompanier in valid["Pessoas"]:
                        data["acompanhador"] = valid["Pessoas"][accompanier]["Nome"].strip()
                    elif accompanier:
                        warning(sheet, row, "Acompanhador", "Referência ausente na planilha; campo em branco e ID original conservado no relatório")
                image = row.get("Imagem" if table == "groups" else "Foto")
                if image:
                    warning(sheet, row, "Foto", f"Arquivo externo não incluído no XLSX: {image}")
                report[collection].append({"origem_id": identity, "destino_id": old["id"] if old else None,
                    "acao": "completar existente" if old else "inserir", "dados": data,
                    **({"grupo_origem": group} if table == "people" else {})})
        if pastors_from_members:
            for identity in valid["Grupos"]:
                candidates = [pid for pid, p in valid["Pessoas"].items()
                              if p.get("Grupo") == identity and p.get("Função") == "Pastor"]
                if len(candidates) == 1:
                    report["pastores"].append({"grupo_origem": identity, "pessoa_origem": candidates[0]})
                elif len(candidates) > 1:
                    warning("Grupos", valid["Grupos"][identity], "Pastor", "Mais de um pastor; responsável não alterado")
    return report


def apply(path, report):
    if report["erros"]:
        raise ValueError("A importação possui pendências. Revise os erros antes de aplicar.")
    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != report["sha256"]:
        raise ValueError("A planilha mudou depois da revisão.")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = db.DATABASE.parent / "backups" / f"portal-importacao-{stamp}.sqlite3"
    backup.parent.mkdir(parents=True, exist_ok=True)
    with db.connection() as conn:
        with closing(sqlite3.connect(backup)) as out:
            conn.backup(out)
        conn.execute("BEGIN IMMEDIATE")
        if _fingerprint(conn) != report["banco_revisado"]:
            raise ValueError("Os cadastros mudaram depois da revisão. Gere uma nova simulação.")
        conn.execute("""CREATE TABLE IF NOT EXISTS import_batches (
            id INTEGER PRIMARY KEY, source TEXT NOT NULL, sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL, report TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS import_records (
            source TEXT NOT NULL, entity TEXT NOT NULL CHECK(entity IN ('groups','people')),
            source_id TEXT NOT NULL, target_id INTEGER NOT NULL, batch_id INTEGER NOT NULL REFERENCES import_batches(id),
            PRIMARY KEY(source,entity,source_id), UNIQUE(source,entity,target_id))""")
        batch = conn.execute("INSERT INTO import_batches(source,sha256,created_at,report) VALUES(?,?,?,?)",
            (report["origem"], report["sha256"], datetime.now().isoformat(), "{}")).lastrowid
        targets = {}
        for collection, table in (("grupos", "groups"), ("pessoas", "people")):
            for item in report[collection]:
                target = item["destino_id"]
                if item["acao"] != "já importado":
                    data = dict(item["dados"])
                    if table == "people":
                        data["grupo_id"] = targets.get(("groups", item["grupo_origem"]))
                    keys = list(data)
                    if target is None:
                        target = conn.execute(f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                            tuple(data.values())).lastrowid
                    else:
                        conn.execute(f"UPDATE {table} SET {','.join(k+'=?' for k in keys)} WHERE id=?",
                            (*data.values(), target))
                    conn.execute("INSERT INTO import_records(source,entity,source_id,target_id,batch_id) VALUES(?,?,?,?,?)",
                        (report["origem"], table, item["origem_id"], target, batch))
                targets[(table, item["origem_id"])] = target
        # Não muda responsabilidades após reimportação, preservando edições feitas no portal.
        fresh_groups = {i["origem_id"] for i in report["grupos"] if i["acao"] != "já importado"}
        for link in report["pastores"]:
            if link["grupo_origem"] in fresh_groups:
                conn.execute("UPDATE groups SET pastor_id=? WHERE id=? AND pastor_id IS NULL",
                    (targets[("people", link["pessoa_origem"])], targets[("groups", link["grupo_origem"])]))
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("A importação produziu vínculos inválidos.")
        result = {**report, "importacao_executada": True, "backup": str(backup), "lote": batch,
                  "vinculos": [{"entidade": t, "origem_id": identity, "destino_id": target}
                               for (t, identity), target in targets.items()]}
        conn.execute("UPDATE import_batches SET report=? WHERE id=?",
            (json.dumps(result, ensure_ascii=False), batch))
    return result


def summary(report):
    return {"sha256": report["sha256"], "cev": report["cev"],
            **{kind: {action: sum(r["acao"] == action for r in report[kind])
                       for action in ("inserir", "completar existente", "já importado")}
               for kind in ("grupos", "pessoas")},
            "avisos": report["avisos"], "erros": report["erros"], "ignorados": report["ignorados"],
            "importacao_executada": report.get("importacao_executada", False), "backup": report.get("backup")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("arquivo", type=Path)
    parser.add_argument("--cev", required=True)
    parser.add_argument("--phase", action="append", default=[], help="ID_Grupo=Fase aprovada para conflito")
    parser.add_argument("--pastors-from-members", action="store_true")
    parser.add_argument("--blank-birth", action="append", default=[], help="ID_Pessoa aprovado para nascimento em branco")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-sha")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = prepare(args.arquivo, args.cev, phases=dict(item.split("=", 1) for item in args.phase),
                     pastors_from_members=args.pastors_from_members,
                     birth_dates={identity: None for identity in args.blank_birth})
    if args.apply:
        if args.expected_sha != report["sha256"]:
            parser.error("--apply exige --expected-sha com o SHA256 revisado da planilha.")
        report = apply(args.arquivo, report)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary(report), ensure_ascii=False, indent=2))
    if report["erros"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
