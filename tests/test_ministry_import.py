"""Importação dos ministérios sem substituir edição manual ou recriar pessoas."""
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
import importar_ministerios as imp


def workbook(path, ministries, links, people):
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    datasets = {"Ministerios": ministries, "MinisterioPessoa": links, "Pessoas": people}
    book = ET.Element("workbook", xmlns=ns, attrib={"xmlns:r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"})
    sheets = ET.SubElement(book, "sheets")
    rels = ET.Element("Relationships")
    with ZipFile(path, "w") as archive:
        for index, (name, rows) in enumerate(datasets.items(), 1):
            ET.SubElement(sheets, "sheet", name=name, attrib={"r:id": f"r{index}"})
            ET.SubElement(rels, "Relationship", Id=f"r{index}", Target=f"worksheets/sheet{index}.xml")
            root = ET.Element("worksheet", xmlns=ns)
            data = ET.SubElement(root, "sheetData")
            headers = sorted(imp.HEADERS[name])
            for number, row in enumerate([dict(zip(headers, headers)), *rows], 1):
                target = ET.SubElement(data, "row", r=str(number))
                for col, header in enumerate(headers):
                    if row.get(header) is not None:
                        cell = ET.SubElement(target, "c", r=f"{chr(65 + col)}{number}", t="inlineStr")
                        ET.SubElement(ET.SubElement(cell, "is"), "t").text = str(row[header])
            archive.writestr(f"xl/worksheets/sheet{index}.xml", ET.tostring(root))
        archive.writestr("xl/workbook.xml", ET.tostring(book))
        archive.writestr("xl/_rels/workbook.xml.rels", ET.tostring(rels))


class MinistryImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ministry-import-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        patcher = patch.object(db, "DATABASE", self.path / "db.sqlite3")
        patcher.start()
        self.addCleanup(patcher.stop)
        db.initialize()
        self.person = db.save_person("Paraipaba", "Pessoa importada", "Núcleo", "", None, ativo=1, eh_comunidade=0)
        self.other = db.save_person("Paraipaba", "Outra pessoa", "Membro", "", None, ativo=1, eh_comunidade=1)
        with db.connection() as conn:
            conn.execute("CREATE TABLE import_batches(id INTEGER PRIMARY KEY,source TEXT,sha256 TEXT,created_at TEXT,report TEXT)")
            conn.execute("CREATE TABLE import_records(source TEXT,entity TEXT,source_id TEXT,target_id INTEGER)")
            for source, target in (("p1", self.person), ("p2", self.other), ("deleted", 999)):
                conn.execute("INSERT INTO import_records VALUES('CPaz:Paraipaba','people',?,?)", (source, target))
        self.people = [{"ID_Pessoa": "p1", "Nome": "Pessoa importada"}, {"ID_Pessoa": "p2", "Nome": "Outra pessoa"},
                       {"ID_Pessoa": "deleted", "Nome": "Pessoa excluída"}]
        self.ministries = [{"ID_Ministerio": "m1", "Nome": "Música", "Ativo": "1"},
                           {"ID_Ministerio": "m2", "Nome": "Liturgia", "Ativo": "1"},
                           {"ID_Ministerio": "m3", "Nome": "Inativo", "Ativo": "0"}]
        self.links = [{"ID_MinisterioPessoa": "1", "Pessoa": "p1", "Ministerio": "m1"},
                      {"ID_MinisterioPessoa": "2", "Pessoa": "p1", "Ministerio": "m2", "Ativo": "1"},
                      {"ID_MinisterioPessoa": "3", "Pessoa": "p1", "Ministerio": "m2", "Ativo": "1"},
                      {"ID_MinisterioPessoa": "4", "Pessoa": "p1", "Ministerio": "m3", "Ativo": "1"},
                      {"ID_MinisterioPessoa": "5", "Pessoa": "p2", "Ministerio": "m1", "Ativo": "1"},
                      {"ID_MinisterioPessoa": "6", "Pessoa": "p2", "Ministerio": "m2", "Ativo": "0"},
                      {"ID_MinisterioPessoa": "7", "Pessoa": "deleted", "Ministerio": "m1"},
                      {"ID_MinisterioPessoa": "8", "Pessoa": "missing", "Ministerio": "m1"}]
        self.book = self.path / "test.xlsx"
        self.write()

    def write(self):
        workbook(self.book, self.ministries, self.links, self.people)

    def test_multiple_ministries_deduplicated_and_inactive_links_excluded(self):
        report = imp.prepare(self.book, "Paraipaba")
        self.assertFalse(report["erros"])
        self.assertEqual(len(report["alteracoes"]), 2)
        before = db.people("Paraipaba")
        self.assertTrue(all(p["ministerio"] == "" for p in before))
        result = imp.apply(self.book, report)
        by_id = {p["id"]: p for p in db.people("Paraipaba")}
        self.assertEqual(by_id[self.person]["ministerio"], "Liturgia; Música")
        self.assertEqual(by_id[self.other]["ministerio"], "Música")
        self.assertEqual(result["membros_engajados"], 1)
        for previous in before:
            self.assertEqual({k: v for k, v in previous.items() if k != "ministerio"},
                             {k: v for k, v in by_id[previous["id"]].items() if k != "ministerio"})
        self.assertEqual(len(by_id), 2)
        self.assertEqual(imp.prepare(self.book, "Paraipaba")["alteracoes"], [])
        self.assertTrue(any("excluído" in warning["motivo"] for warning in report["avisos"]))
        with closing(sqlite3.connect(result["backup"])) as conn:
            self.assertEqual(conn.execute("SELECT ministerio FROM people WHERE id=?", (self.person,)).fetchone()[0], "")
        audit = json.loads(db.query("SELECT report FROM import_batches")[0]["report"])
        self.assertNotIn("Ativo", audit["dados_originais"]["MinisterioPessoa"][0])

    def test_manual_and_renamed_records_are_preserved(self):
        db.execute("UPDATE people SET ministerio='Ministério cadastrado' WHERE id=?", (self.person,))
        db.execute("UPDATE people SET nome='Nome alterado' WHERE id=?", (self.other,))
        report = imp.prepare(self.book, "Paraipaba")
        self.assertEqual(report["alteracoes"], [])
        self.assertTrue(any("manualmente" in warning["motivo"] for warning in report["avisos"]))
        self.assertTrue(any("Nome" in warning["motivo"] for warning in report["avisos"]))

    def test_changed_database_and_source_require_new_review(self):
        report = imp.prepare(self.book, "Paraipaba")
        db.execute("UPDATE people SET ministerio='Alterado' WHERE id=?", (self.person,))
        with self.assertRaisesRegex(ValueError, "cadastros mudaram"):
            imp.apply(self.book, report)
        report = imp.prepare(self.book, "Paraipaba")
        self.ministries[0]["Nome"] = "Novo nome"
        self.write()
        with self.assertRaisesRegex(ValueError, "planilha mudou"):
            imp.apply(self.book, report)

    def test_failure_rolls_back_earlier_updates(self):
        report = imp.prepare(self.book, "Paraipaba")
        report["alteracoes"][1]["pessoa_id"] = 10000
        with self.assertRaises(ValueError):
            imp.apply(self.book, report)
        self.assertTrue(all(p["ministerio"] == "" for p in db.people("Paraipaba")))
        self.assertEqual(db.query("SELECT * FROM import_batches"), [])


if __name__ == "__main__":
    unittest.main()
