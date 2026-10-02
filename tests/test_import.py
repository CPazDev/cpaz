"""Verificação da importação sem tocar no banco ou na planilha de produção."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from zipfile import ZipFile

import database as db
import importar_planilha as imp


def fixture(path, groups, people, *, epoch1904=False):
    """Arquivo mínimo de teste com valores salvos do Excel e uma fórmula cacheada."""
    ns = imp.NS["s"]
    def sheet(headers, rows):
        root = ET.Element(f"{{{ns}}}worksheet")
        data = ET.SubElement(root, f"{{{ns}}}sheetData")
        for number, values in enumerate([dict(zip(headers, headers)), *rows], 1):
            row = ET.SubElement(data, f"{{{ns}}}row", r=str(number))
            for index, header in enumerate(headers):
                value = values.get(header)
                if value is None:
                    continue
                cell = ET.SubElement(row, f"{{{ns}}}c", r=f"{chr(65 + index)}{number}")
                if isinstance(value, tuple):
                    ET.SubElement(cell, f"{{{ns}}}f").text = value[1]
                    if value[0] is not None:
                        ET.SubElement(cell, f"{{{ns}}}v").text = value[0]
                else:
                    cell.set("t", "inlineStr")
                    ET.SubElement(ET.SubElement(cell, f"{{{ns}}}is"), f"{{{ns}}}t").text = str(value)
        return ET.tostring(root, encoding="utf-8")
    with ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", f'''<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><workbookPr date1904="{int(epoch1904)}"/><sheets><sheet name="Grupos" r:id="r1"/><sheet name="Pessoas" r:id="r2"/></sheets></workbook>''')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/><Relationship Id="r2" Target="worksheets/sheet2.xml"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", sheet(sorted(imp.REQUIRED["Grupos"] | {"Imagem"}), groups))
        archive.writestr("xl/worksheets/sheet2.xml", sheet(sorted(imp.REQUIRED["Pessoas"] | {"Ministerio"}), people))


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="portal-import-tests-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.db_patch = patch.object(db, "DATABASE", self.path / "test.sqlite3")
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        db.initialize()
        self.group = db.save_group("Paraipaba", "Kadosh", None, "CEv", "16:00", "Metanoia", dia_encontro="Sábado")
        self.manual = db.save_person("Paraipaba", "Gabriel", "Pastor", "Manual", self.group)
        self.groups = [{"ID_Grupo": "g1", "Nome": "Kadosh", "Fase": "Filoteia", "DataInicio": "46144", "Publico": "Jovens", "Ativo": "1"},
                       {"ID_Grupo": "g2", "Nome": "Inativo", "Fase": "Kerigma", "Ativo": "0"}]
        self.people = [{"ID_Pessoa": "p1", "Nome": "Pastor completo", "Grupo": "g1", "Função": "Pastor",
                        "Telefone": "8.5991983052E10", "Acompanhador": "p2", "EhComunidade": "1", "Ativo": "1",
                        "DataNascimento": "38721", "Email": "p@example.com", "Genero": "Masculino",
                        "Serviço": "Fraternidade", "Funcao_Serv": "Coordenador"},
                       {"ID_Pessoa": "p2", "Nome": "Pessoa acompanhadora", "Grupo": "g2", "Função": "Núcleo", "Ativo": "0"},
                       {"EhComunidade": "0"}]
        self.book = self.path / "test.xlsx"
        fixture(self.book, self.groups, self.people)

    def prepare(self, **options):
        return imp.prepare(self.book, "Paraipaba", phases={"g1": "Filoteia"}, pastors_from_members=True, **options)

    def test_conversion_and_epochs(self):
        self.assertEqual(imp.phone("8.5991983052E10"), "85991983052")
        self.assertEqual(imp.phone("08599999999"), "08599999999")
        self.assertEqual(imp.phone("(85) 99999-9999"), "(85) 99999-9999")
        self.assertEqual(imp.excel_date("46144"), "2026-05-02")
        self.assertEqual(imp.excel_date("0", True), "1904-01-01")
        self.assertEqual(imp.excel_date("59"), "1900-02-28")
        with self.assertRaises(ValueError):
            imp.excel_date("60")
        for bad in ("0.5", "nan", "-1"):
            with self.assertRaises(ValueError):
                imp.excel_date(bad)
        self.assertIsNone(imp.boolean(None))
        with self.assertRaises(ValueError):
            imp.boolean("2")

    def test_dry_run_merges_then_reimport_preserves_edits(self):
        report = self.prepare()
        self.assertFalse(report["erros"])
        self.assertEqual(len(db.people("Paraipaba")), 1)
        self.assertEqual(len(report["ignorados"]), 1)
        result = imp.apply(self.book, report)
        self.assertEqual(len(db.people("Paraipaba")), 3)
        self.assertEqual(len(db.groups("Paraipaba")), 2)
        group = db.query("SELECT * FROM groups WHERE id=?", (self.group,))[0]
        self.assertEqual((group["fase"], group["local"], group["horario"], group["dia_encontro"]),
                         ("Filoteia", "CEv", "16:00", "Sábado"))
        self.assertEqual(group["publico"], "Jovens")
        imported = db.query("SELECT * FROM people WHERE nome='Pastor completo'")[0]
        self.assertEqual(group["pastor_id"], imported["id"])
        self.assertEqual(imported["acompanhador"], "Pessoa acompanhadora")
        self.assertEqual(imported["contato"], "85991983052")
        self.assertEqual(imported["email"], "p@example.com")
        inactive = next(g for g in db.groups("Paraipaba") if g["nome"] == "Inativo")
        self.assertEqual(inactive["ativo"], 0)
        self.assertIsNone(next(p for p in db.people("Paraipaba") if p["nome"] == "Pessoa acompanhadora")["eh_comunidade"])
        db.execute("UPDATE groups SET fase='Santidade',pastor_id=NULL WHERE id=?", (self.group,))
        db.execute("UPDATE people SET contato='Atualizado' WHERE id=?", (imported["id"],))
        repeat = self.prepare()
        self.assertTrue(all(r["acao"] == "já importado" for kind in ("grupos", "pessoas") for r in repeat[kind]))
        imp.apply(self.book, repeat)
        self.assertEqual(len(db.people("Paraipaba")), 3)
        updated = db.query("SELECT * FROM groups WHERE id=?", (self.group,))[0]
        self.assertEqual(updated["fase"], "Santidade")
        self.assertIsNone(updated["pastor_id"])
        self.assertEqual(db.query("SELECT contato FROM people WHERE id=?", (imported["id"],))[0]["contato"], "Atualizado")
        with closing(sqlite3.connect(result["backup"])) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM people").fetchone()[0], 1)
        self.assertEqual(db.query("SELECT count(*) AS n FROM meetings")[0]["n"], 0)
        self.assertEqual(db.query("SELECT count(*) AS n FROM access_grants")[0]["n"], 0)

    def test_conflicts_and_missing_references_are_not_guessed(self):
        self.assertTrue(imp.prepare(self.book, "Paraipaba")["erros"])
        self.people[0]["Acompanhador"] = "missing"
        fixture(self.book, self.groups, self.people)
        report = self.prepare()
        self.assertEqual(report["pessoas"][0]["dados"]["acompanhador"], "")
        self.assertTrue(any(a["campo"] == "Acompanhador" for a in report["avisos"]))
        self.people[0]["Grupo"] = "missing"
        fixture(self.book, self.groups, self.people)
        with self.assertRaises(ValueError):
            imp.apply(self.book, self.prepare())
        self.assertEqual(len(db.people("Paraipaba")), 1)

    def test_future_birth_requires_explicit_decision_and_keeps_raw_value(self):
        self.people[0]["DataNascimento"] = "2099-01-01"
        fixture(self.book, self.groups, self.people)
        self.assertTrue(self.prepare()["erros"])
        report = self.prepare(birth_dates={"p1": None})
        self.assertFalse(report["erros"])
        self.assertIsNone(report["pessoas"][0]["dados"]["nascimento"])
        result = imp.apply(self.book, report)
        audit = json.loads(db.query("SELECT report FROM import_batches WHERE id=?", (result["lote"],))[0]["report"])
        self.assertEqual(audit["dados_originais"]["Pessoas"][0]["DataNascimento"], "2099-01-01")

    def test_changed_source_or_database_requires_new_preview(self):
        report = self.prepare()
        db.execute("UPDATE people SET contato='Mudou' WHERE id=?", (self.manual,))
        with self.assertRaisesRegex(ValueError, "cadastros mudaram"):
            imp.apply(self.book, report)
        report = self.prepare()
        self.people[0]["Nome"] = "Outro nome"
        fixture(self.book, self.groups, self.people)
        with self.assertRaisesRegex(ValueError, "planilha mudou"):
            imp.apply(self.book, report)

    def test_failure_rolls_back_every_insert_and_audit_mapping(self):
        report = self.prepare()
        report["pessoas"][1]["dados"]["categoria"] = "Inválida"
        with self.assertRaises(sqlite3.IntegrityError):
            imp.apply(self.book, report)
        self.assertEqual(len(db.groups("Paraipaba")), 1)
        self.assertEqual(len(db.people("Paraipaba")), 1)
        self.assertEqual(db.groups("Paraipaba")[0]["fase"], "Metanoia")
        self.assertFalse(db.query("SELECT 1 FROM sqlite_master WHERE name='import_records'"))

    def test_cached_formulas_and_workbook_epoch(self):
        self.groups[0]["DataInicio"] = ("46144", "DATE(2026,5,2)")
        fixture(self.book, self.groups, self.people)
        self.assertEqual(self.prepare()["grupos"][0]["dados"]["data_inicio"], "2026-05-02")
        self.groups[0]["DataInicio"] = (None, "DATE(2026,5,2)")
        fixture(self.book, self.groups, self.people)
        with self.assertRaisesRegex(ValueError, "fórmula sem valor"):
            self.prepare()
        self.groups[0]["DataInicio"] = "0"
        fixture(self.book, self.groups, self.people, epoch1904=True)
        self.assertEqual(self.prepare()["grupos"][0]["dados"]["data_inicio"], "1904-01-01")

    def test_duplicate_source_ids_and_existing_people_block_automatic_merge(self):
        self.people[1]["ID_Pessoa"] = "p1"
        fixture(self.book, self.groups, self.people)
        self.assertTrue(self.prepare()["erros"])
        self.people[1]["ID_Pessoa"] = "p2"
        self.people[0]["Nome"] = "Gabriel"
        fixture(self.book, self.groups, self.people)
        self.assertTrue(any("correspondência manual" in e["mensagem"] for e in self.prepare()["erros"]))

    def test_approved_fields_validate_in_crud_and_creation(self):
        import crud
        import time
        actor = {"is_logged_in": True, "email_verified": True, "email": "contatomaicondouglass@gmail.com", "exp": time.time() + 3600}
        crud.update(actor, "people", self.manual, {"email": "novo@example.com", "genero": "Masculino",
                    "servico": "Música", "funcao_servico": "Membro", "eh_comunidade": 1, "ativo": 0})
        crud.update(actor, "groups", self.group, {"data_inicio": "2026-05-02", "publico": "Jovens", "ativo": 0})
        self.assertEqual(db.people("Paraipaba")[0]["ativo"], 0)
        for invalid in (2, "1", 0.5):
            with self.assertRaises(ValueError):
                crud.update(actor, "people", self.manual, {"ativo": invalid})
        with self.assertRaises(ValueError):
            db.save_group("Paraipaba", "Grupo", None, "", "", "Kerigma", data_inicio="invalid")


if __name__ == "__main__":
    unittest.main()
