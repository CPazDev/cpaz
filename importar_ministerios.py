"""Preenche o campo Ministério usando os vínculos existentes na planilha.

Não recria pessoas excluídas nem substitui ministérios preenchidos manualmente.
Conserva os vínculos e situações de origem no relatório privado da importação.
"""

import argparse
from collections import defaultdict
from contextlib import closing
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import database as db
import diretorio
from importar_planilha import boolean, read_workbook

HEADERS = {"Ministerios": {"ID_Ministerio", "Nome", "Ativo"},
           "MinisterioPessoa": {"ID_MinisterioPessoa", "Ministerio", "Pessoa", "Ativo"},
           "Pessoas": {"ID_Pessoa", "Nome"}}


def _snapshot(conn):
    rows = [tuple(r) for r in conn.execute("SELECT id,cev,nome,ministerio FROM people ORDER BY id")]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()


def prepare(path, cev):
    digest, _, sheets = read_workbook(path, HEADERS)
    report = {"arquivo": Path(path).name, "sha256": digest, "cev": cev, "dados_originais": sheets,
              "alteracoes": [], "avisos": [], "erros": [], "ja_preenchidos": 0,
              "vinculos_sem_situacao": 0, "importacao_executada": False}
    ministries = {}
    for row in sheets["Ministerios"]:
        identity = row.get("ID_Ministerio", "").strip()
        if not identity or not row.get("Nome", "").strip() or identity in ministries:
            report["erros"].append("Ministério sem identificação/nome ou com ID duplicado.")
            continue
        try:
            active = boolean(row.get("Ativo"))
        except ValueError as exc:
            report["erros"].append(str(exc))
            continue
        ministries[identity] = {"nome": row["Nome"].strip(), "ativo": active}
    source_people = {r["ID_Pessoa"].strip(): r for r in sheets["Pessoas"] if r.get("ID_Pessoa")}
    names = defaultdict(set)
    for row in sheets["MinisterioPessoa"]:
        person, ministry = row.get("Pessoa", "").strip(), row.get("Ministerio", "").strip()
        if ministry not in ministries or person not in source_people:
            report["avisos"].append({"linha": row["_row"], "motivo": "Pessoa ou ministério ausente na origem; vínculo não importado"})
            continue
        try:
            active = boolean(row.get("Ativo"))
        except ValueError as exc:
            report["erros"].append(f"Linha {row['_row']}: {exc}")
            continue
        if active is None:
            report["vinculos_sem_situacao"] += 1
        # A existência da ligação informa o ministério da pessoa. Não inventa
        # um status para vínculos sem situação, que permanecem assim no relatório.
        if active != 0 and ministries[ministry]["ativo"] == 1:
            names[person].add(ministries[ministry]["nome"])
    report["ministerios_ativos"] = sum(m["ativo"] == 1 for m in ministries.values())
    with db.connection() as conn:
        report["banco_revisado"] = _snapshot(conn)
        maps = {r["source_id"]: r["target_id"] for r in conn.execute(
            "SELECT * FROM import_records WHERE source=? AND entity='people'", (f"CPaz:{cev}",))}
        for source_id, ministry_names in names.items():
            identity = maps.get(source_id)
            row = conn.execute("SELECT * FROM people WHERE id=?", (identity,)).fetchone() if identity is not None else None
            if not row or row["cev"] != cev:
                report["avisos"].append({"pessoa_origem": source_id, "motivo": "Cadastro excluído ou sem correspondência; não foi recriado"})
                continue
            if row["nome"].strip().casefold() != source_people[source_id]["Nome"].strip().casefold():
                report["avisos"].append({"pessoa_origem": source_id, "motivo": "Nome do cadastro alterado; correspondência requer revisão"})
                continue
            value = "; ".join(sorted(ministry_names, key=str.casefold))
            if row["ministerio"].strip() == value:
                report["ja_preenchidos"] += 1
            elif row["ministerio"].strip():
                report["avisos"].append({"pessoa_origem": source_id, "motivo": "Ministério já preenchido manualmente; valor preservado"})
            else:
                report["alteracoes"].append({"pessoa_origem": source_id, "pessoa_id": identity,
                    "nome": row["nome"], "anterior": row["ministerio"], "ministerio": value,
                    "ministerios": sorted(ministry_names, key=str.casefold)})
    return report


def apply(path, report):
    if report["erros"]:
        raise ValueError("Revise os erros da simulação antes de importar.")
    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != report["sha256"]:
        raise ValueError("A planilha mudou depois da revisão.")
    stamp = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y%m%d-%H%M%S-%f")
    backup = db.DATABASE.parent / "backups" / f"portal-antes-ministerios-{stamp}.sqlite3"
    backup.parent.mkdir(parents=True, exist_ok=True)
    with db.connection() as conn:
        with closing(sqlite3.connect(backup)) as out:
            conn.backup(out)
        conn.execute("BEGIN IMMEDIATE")
        if _snapshot(conn) != report["banco_revisado"]:
            raise ValueError("Os cadastros mudaram depois da revisão. Gere uma nova simulação.")
        for change in report["alteracoes"]:
            cursor = conn.execute("UPDATE people SET ministerio=? WHERE id=? AND cev=? AND nome=? AND ministerio=?",
                (change["ministerio"], change["pessoa_id"], report["cev"], change["nome"], change["anterior"]))
            if cursor.rowcount != 1:
                raise ValueError("Uma correspondência mudou. Nenhuma alteração foi aplicada.")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Vínculos inválidos detectados; importação desfeita.")
        result = {**report, "importacao_executada": True, "backup": str(backup)}
        conn.execute("INSERT INTO import_batches(source,sha256,created_at,report) VALUES(?,?,?,?)",
            (f"CPaz:{report['cev']}:Ministerios", report["sha256"], datetime.now(ZoneInfo("America/Sao_Paulo")).isoformat(),
             json.dumps(result, ensure_ascii=False)))
    result["membros_engajados"] = len(diretorio.engaged(db.people(report["cev"])))
    return result


def summary(report):
    return {key: report.get(key) for key in ("sha256", "cev", "ministerios_ativos", "vinculos_sem_situacao",
        "ja_preenchidos", "membros_engajados", "avisos", "erros", "importacao_executada", "backup")} | {
        "pessoas_para_preencher": len(report["alteracoes"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("arquivo", type=Path)
    parser.add_argument("--cev", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-sha")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = prepare(args.arquivo, args.cev)
    if args.apply:
        if args.expected_sha != report["sha256"]:
            parser.error("Informe o SHA256 revisado em --expected-sha.")
        report = apply(args.arquivo, report)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary(report), ensure_ascii=False, indent=2))
    if report["erros"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
