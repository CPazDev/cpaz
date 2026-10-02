"""Identidade própria, vínculo de membro e isolamento de informações pessoais."""
from datetime import date
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import access
import crud
import database as db
import perfis
import portal


def claims(email, **extra):
    return {"is_logged_in": True, "email_verified": True, "email": email,
            "exp": time.time() + 3600, "name": "Pessoa da conta", **extra}


class ProfileTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="profile-tests-")
        self.addCleanup(temp.cleanup)
        patcher = patch.object(db, "DATABASE", Path(temp.name) / "db.sqlite3")
        patcher.start()
        self.addCleanup(patcher.stop)
        db.initialize()
        self.admin = claims("contatomaicondouglass@gmail.com")
        self.manager = claims("gestor@example.com")
        self.alice = claims("alice@example.com")
        self.bob = claims("bob@example.com")
        self.group = db.save_group("Itarema", "Grupo A", None, "", "", "Kerigma", ativo=1)
        self.other = db.save_group("Paraipaba", "Grupo B", None, "", "", "Filoteia", ativo=1)
        self.member = db.save_person("Itarema", "Alice", "Membro", "Contato de Alice", self.group,
                                    email=" ALICE@EXAMPLE.COM ", ativo=1, eh_comunidade=0, ministerio="Música; Liturgia")
        self.foreign = db.save_person("Paraipaba", "Bob", "Núcleo", "Contato de Bob", self.other,
                                     email="bob@example.com", ativo=1, eh_comunidade=0)
        access.grant(self.admin, self.manager["email"], "Gestor de CEv", "Itarema")

    def app(self, user):
        patcher = patch.object(portal.st, "user", SimpleNamespace(to_dict=lambda: user))
        patcher.start()
        self.addCleanup(patcher.stop)
        content = patch.object(portal, "load_content", lambda: {"cevs":["Itarema", "Paraipaba"], "avisos":[], "campos_pessoa_aprovados":True})
        content.start()
        self.addCleanup(content.stop)
        return AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20).run()

    def page(self, app, key):
        app._page_hash = app.session_state["pages"][key]._script_hash
        app.run()
        self.assertFalse(app.exception, [e.message for e in app.exception])

    def test_self_edit_and_link_use_only_verified_identity(self):
        perfis.self_link(self.alice, self.member)
        perfis.save_own(self.alice, "Alice da conta", "Ali")
        profile = perfis.own(claims(" ALICE@EXAMPLE.COM "))
        self.assertEqual(profile["nome_exibido"], "Ali")
        self.assertEqual(profile["membro_id"], self.member)
        self.assertEqual(profile["grupo"], "Grupo A")
        self.assertIsNone(access.profile(self.alice))  # Não concede acesso de gestão.
        self.assertEqual([r["id"] for r in perfis.candidates(self.alice)], [self.member])
        with self.assertRaises(PermissionError):
            perfis.self_link(self.alice, self.foreign)
        self.assertIsNone(perfis.personal(self.bob))
        perfis.self_link(self.alice, None)
        self.assertIsNone(perfis.personal(self.alice))
        self.assertEqual(perfis.own(self.alice)["apelido"], "Ali")
        self.assertTrue(db.query("SELECT 1 FROM people WHERE id=?", (self.member,)))

    def test_invalid_identity_cannot_read_edit_or_link(self):
        perfis.self_link(self.alice, self.member)
        for invalid in ({}, dict(self.alice, email_verified=False), dict(self.alice, exp=0)):
            self.assertIsNone(perfis.own(invalid))
            self.assertIsNone(perfis.personal(invalid))
            self.assertEqual(perfis.candidates(invalid), [])
            with self.assertRaises(PermissionError):
                perfis.save_own(invalid, "Nome", "Apelido")
            with self.assertRaises(PermissionError):
                perfis.self_link(invalid, self.member)
        with self.assertRaises(ValueError):
            perfis.save_own(self.alice, "", "")

    def test_manager_scope_and_unique_member_link_preserve_profile(self):
        perfis.save_own(self.alice, "Alice da conta", "Ali")
        perfis.link(self.manager, self.alice["email"], self.member)
        self.assertEqual(perfis.own(self.alice)["apelido"], "Ali")
        with self.assertRaises(PermissionError):
            perfis.link(self.manager, self.bob["email"], self.foreign)
        perfis.link(self.admin, self.bob["email"], self.foreign)
        with self.assertRaises(PermissionError):
            perfis.link(self.manager, self.bob["email"], self.member)
        with self.assertRaises(PermissionError):
            perfis.link(self.manager, self.bob["email"], None)
        with self.assertRaises(ValueError):
            perfis.link(self.admin, "outra@example.com", self.member)
        self.assertEqual(perfis.own(self.alice)["membro_id"], self.member)
        self.assertEqual([r["email"] for r in perfis.links(self.manager)], [self.alice["email"]])
        self.assertEqual(len(perfis.links(self.admin)), 2)
        self.assertEqual(perfis.links(self.alice), [])

    def test_manager_cannot_link_admin_accounts_or_foreign_grants(self):
        access.grant(self.admin, "gestor-b@example.com", "Gestor de CEv", "Paraipaba")
        for email in (self.admin["email"], "gestor-b@example.com"):
            with self.assertRaises(PermissionError):
                perfis.link(self.manager, email, self.member)
        access.revoke(self.admin, self.manager["email"])
        with self.assertRaises(PermissionError):
            perfis.link(self.manager, self.alice["email"], self.member)

    def test_own_data_counts_completed_followups_and_omits_private_notes(self):
        perfis.self_link(self.alice, self.member)
        meeting = db.execute("INSERT INTO meetings(grupo_id,data,tema,observacoes) VALUES(?,?,?,?)",
                             (self.group, "2026-09-30", "Tema do encontro", "NOTAS_PRIVADAS_ENCONTRO"))
        db.save_attendance(meeting, self.member, "Liberado")
        for day in ("2025-12-20", "2026-01-10", "2026-09-30", "2026-11-15", "2027-01-01"):
            db.save_followup(self.group, self.member, day, "Acompanhador", None, "NOTAS_PRIVADAS_ACOMPANHAMENTO")
        db.save_followup(self.other, self.foreign, "2026-10-01", "Alice", None, "OUTRAS_NOTAS_PRIVADAS")
        data = perfis.personal(self.alice, today=date(2026, 10, 2))
        self.assertEqual(data["membro"]["id"], self.member)
        self.assertEqual(data["acompanhamentos"], {"no_ano":2, "ultima_data":"2026-09-30"})
        self.assertEqual(data["frequencia"][0]["presenca"], "Liberado")
        self.assertNotIn("NOTAS_PRIVADAS", json.dumps(data))
        db.execute("UPDATE people SET grupo_id=? WHERE id=?", (db.save_group("Itarema", "Novo grupo", None, "", "", "Metanoia"), self.member))
        self.assertEqual(perfis.own(self.alice)["grupo"], "Novo grupo")

    def test_linked_member_delete_requires_review_of_profile_link(self):
        perfis.self_link(self.alice, self.member)
        access.set_deletion_permission(self.admin, self.admin["email"], True, "Itarema")
        allowed, dependencies = crud.deletion_info(self.admin, "people", self.member)
        self.assertTrue(allowed)
        self.assertEqual(dependencies["Perfis de usuário"], 1)
        with self.assertRaises(ValueError):
            crud.delete(self.admin, "people", self.member)
        perfis.self_link(self.alice, None)
        crud.delete(self.admin, "people", self.member)
        self.assertIsNone(perfis.own(self.alice)["membro_id"])

    def test_self_service_ui_and_personal_page_ignore_member_id_in_url(self):
        app = self.app(self.alice)
        self.page(app, "profile")
        next(w for w in app.text_input if w.label == "Nome").set_value("Alice da conta")
        next(w for w in app.text_input if w.label == "Apelido").set_value("Ali")
        next(b for b in app.button if b.label == "Salvar meu perfil").click().run()
        self.assertEqual(perfis.own(self.alice)["apelido"], "Ali")
        next(w for w in app.selectbox if w.label == "Meu cadastro de membro").select(self.member)
        next(b for b in app.button if b.label == "Vincular meu cadastro").click().run()
        self.assertFalse(app.exception)
        app.query_params["membro_id"] = str(self.foreign)
        self.page(app, "personal")
        self.assertIn("Alice", [w.value for w in app.subheader])
        self.assertNotIn("Bob", [w.value for w in app.subheader])
        self.assertTrue(any(w.label == "Último acompanhamento" and w.value == "—" for w in app.metric))
        self.assertFalse(any(w.label == "Cadastrar grupo" for w in app.button))
