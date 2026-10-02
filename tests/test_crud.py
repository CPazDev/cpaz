"""Verificações de autorização, preservação de vínculos e edição de registros."""

from pathlib import Path
from io import BytesIO
import json
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from PIL import Image

import access
import crud
import database as db
import edicao
import portal


def claims(email):
    return {"is_logged_in": True, "email_verified": True, "email": email,
            "exp": time.time() + 3600, "name": "Teste"}


class CrudTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="crud-tests-")
        self.addCleanup(self.temp.cleanup)
        self.db_patch = patch.object(db, "DATABASE", Path(self.temp.name) / "crud.sqlite3")
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        db.initialize()
        self.admin = claims("contatomaicondouglass@gmail.com")
        self.manager = claims("gestor@example.com")
        self.responsible = claims("grupo@example.com")
        self.pastor = db.save_person("CEv A", "Pastor A", "Pastor", "", None)
        photo = BytesIO()
        Image.new("RGB", (10, 10), "green").save(photo, format="PNG")
        self.photo = photo.getvalue()
        self.group = db.save_group("CEv A", "Grupo A", self.pastor, "Local", "19:00", "Kerigma", foto=self.photo, foto_tipo="PNG")
        self.other_group = db.save_group("CEv A", "Outro grupo A", None, "", "", "Metanoia")
        self.foreign_group = db.save_group("CEv B", "Grupo B", None, "", "", "Permanente")
        self.person = db.save_person("CEv A", "Pessoa A", "Membro", "Contato", self.group)
        self.meeting = db.execute("INSERT INTO meetings(grupo_id,data,tema,observacoes) VALUES(?,?,?,?)",
                                  (self.group, "2026-10-02", "Tema", ""))
        self.other_meeting = db.execute("INSERT INTO meetings(grupo_id,data,tema) VALUES(?,?,?)",
                                        (self.other_group, "2026-10-03", "Outro"))
        db.save_attendance(self.meeting, self.person, "Presente")
        self.attendance = db.attendance(self.group)[0]["id"]
        self.followup = db.save_followup(self.group, self.person, "2026-10-02", "Pessoa responsável", None, "")
        access.grant(self.admin, self.manager["email"], "Gestor de CEv", "CEv A")
        access.grant(self.admin, self.responsible["email"], "Responsável de grupo", "CEv A", self.group)

    def row(self, table, record_id):
        return db.query(f"SELECT * FROM {table} WHERE id=?", (record_id,))[0]

    def event_fixture(self, cev=None, group_id=None, owner=None):
        destination = "Grupo" if group_id else "CEv/Irradiação" if cev else "Geral"
        event = db.save_event("Retiro", "Retiro", "2026-10-02", None, "Local", "Descrição", destination,
                              cev, group_id, None, None, "#177d73", "", owner or self.admin["email"])
        fields = [{"id": "campo", "label": "Nome aprovado", "type": "Texto", "required": True, "options": []}]
        crud.update_fields(self.admin, event, fields)
        registration, token = db.register(event, {"campo": {"campo": "Nome aprovado", "valor": "Inscrito"}})
        request = db.execute("INSERT INTO event_requests(evento_id,titulo,opcoes) VALUES(?,?,?)",
                             (event, "Camisa", json.dumps(["P", "M"])))
        db.save_order(event, token, request, "M", 2)
        order = db.order_rows(event)[0]["id"]
        testimony = db.save_testimonial(event, "Autor", "Testemunho")
        return event, registration, request, order, testimony

    def app(self, user):
        user_patch = patch.object(portal.st, "user", SimpleNamespace(to_dict=lambda: user))
        content_patch = patch.object(portal, "load_content", lambda: {
            "cevs": ["CEv A", "CEv B"], "avisos": [], "campos_pessoa_aprovados": True})
        user_patch.start()
        content_patch.start()
        self.addCleanup(user_patch.stop)
        self.addCleanup(content_patch.stop)
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20).run()
        app._page_hash = app.session_state["pages"]["edit"]._script_hash
        app.run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        return app

    def choose(self, app, table, record_id):
        app.selectbox(key="crud_kind").select(table).run()
        app.selectbox(key=f"crud_record_{table}").select(record_id).run()
        self.assertFalse(app.exception, [e.message for e in app.exception])

    def test_edit_scope_and_reassignment_are_enforced_by_server(self):
        crud.update(self.responsible, "meetings", self.meeting, {"tema": "Tema corrigido"})
        self.assertEqual(self.row("meetings", self.meeting)["tema"], "Tema corrigido")
        for table, record_id, changes in (("meetings", self.other_meeting, {"tema": "Proibido"}),
                                          ("people", self.person, {"nome": "Proibido"}),
                                          ("groups", self.other_group, {"nome": "Proibido"})):
            with self.assertRaises(PermissionError):
                crud.update(self.responsible, table, record_id, changes)
        crud.update(self.responsible, "groups", self.group, {"nome": "Meu grupo atualizado"})
        self.assertEqual(self.row("groups", self.group)["nome"], "Meu grupo atualizado")
        with self.assertRaises(PermissionError):
            crud.update(self.responsible, "followups", self.followup, {"grupo_id": self.other_group, "membro_id": self.pastor})
        with self.assertRaises(PermissionError):
            crud.update(self.manager, "groups", self.foreign_group, {"nome": "Proibido"})
        with self.assertRaises(PermissionError):
            crud.update(self.manager, "people", self.pastor, {"cev": "CEv B"})
        self.assertEqual(crud.list_records({}, "people"), [])
        with self.assertRaises(PermissionError):
            crud.update({}, "people", self.person, {"nome": "Proibido"})

    def test_deletion_is_opt_in_scoped_and_revocable(self):
        self.assertFalse(access.can_delete(self.admin, "CEv A", self.group))
        with self.assertRaises(PermissionError):
            crud.delete(self.admin, "attendance", self.attendance)
        access.set_deletion_permission(self.admin, self.manager["email"], True, "CEv A", self.group)
        self.assertTrue(access.can_delete(self.manager, "CEv A", self.group))
        self.assertFalse(access.can_delete(self.manager, "CEv A", self.other_group))
        self.assertFalse(access.can_delete(self.manager, "CEv B", self.foreign_group))
        with self.assertRaises(PermissionError):
            crud.delete(self.manager, "meetings", self.other_meeting)
        access.set_deletion_permission(self.admin, self.manager["email"], False, "CEv A", self.group)
        with self.assertRaises(PermissionError):
            crud.delete(self.manager, "attendance", self.attendance)
        access.set_deletion_permission(self.admin, self.responsible["email"], True, "CEv A", self.group)
        with self.assertRaises(ValueError):
            access.set_deletion_permission(self.admin, self.responsible["email"], True, "CEv A", self.other_group)
        crud.delete(self.responsible, "attendance", self.attendance)
        self.assertFalse(db.query("SELECT 1 FROM attendance WHERE id=?", (self.attendance,)))

    def test_only_initial_admins_delegate_and_role_changes_remove_permissions(self):
        extra_admin = claims("admin@example.com")
        access.grant(self.admin, extra_admin["email"], "Administrador")
        with self.assertRaises(PermissionError):
            access.set_deletion_permission(extra_admin, self.manager["email"], True, "CEv A")
        access.set_deletion_permission(self.admin, self.manager["email"], True, "CEv A")
        access.grant(self.admin, self.manager["email"], "Gestor de CEv", "CEv B")
        self.assertFalse(db.query("SELECT 1 FROM deletion_permissions WHERE email=?", (self.manager["email"],)))
        access.grant(self.admin, self.manager["email"], "Gestor de CEv", "CEv A")
        self.assertFalse(access.can_delete(self.manager, "CEv A", self.group))
        access.set_deletion_permission(self.admin, self.manager["email"], True, "CEv A")
        access.revoke(self.admin, self.manager["email"])
        self.assertFalse(db.query("SELECT 1 FROM deletion_permissions WHERE email=?", (self.manager["email"],)))

    def test_dependencies_block_deletion_without_erasing_history(self):
        access.set_deletion_permission(self.admin, self.admin["email"], True, "CEv A")
        for table, record_id in (("groups", self.group), ("people", self.person), ("meetings", self.meeting)):
            with self.assertRaises(ValueError):
                crud.delete(self.admin, table, record_id)
            self.assertTrue(self.row(table, record_id))
        crud.delete(self.admin, "attendance", self.attendance)
        crud.delete(self.admin, "meetings", self.meeting)
        self.assertTrue(self.row("followups", self.followup))
        with db.connection() as conn:
            self.assertFalse(conn.execute("PRAGMA foreign_key_check").fetchall())

    def test_edit_preserves_photos_and_relationship_integrity(self):
        crud.update(self.manager, "groups", self.group, {"nome": "Grupo atualizado", "dia_encontro": "Terça-feira"})
        row = self.row("groups", self.group)
        self.assertEqual(row["foto"], self.photo)
        self.assertEqual(row["dia_encontro"], "Terça-feira")
        with self.assertRaises(ValueError):
            crud.update(self.admin, "people", self.pastor, {"categoria": "Membro"})
        with self.assertRaises(ValueError):
            crud.update(self.admin, "meetings", self.meeting, {"grupo_id": self.other_group})
        with self.assertRaises(ValueError):
            crud.update(self.admin, "people", self.person, {"grupo_id": self.foreign_group})
        crud.update(self.manager, "people", self.person, {"nome": "Pessoa atualizada", "grupo_id": self.other_group})
        # Frequências e acompanhamentos mantêm o grupo do histórico.
        crud.update(self.responsible, "attendance", self.attendance, {"presenca": "Liberado"})
        crud.update(self.responsible, "followups", self.followup, {"observacoes": "Histórico preservado"})
        self.assertEqual(self.row("attendance", self.attendance)["presenca"], "Liberado")

    def test_event_children_edit_and_delete_with_original_scope(self):
        event, registration, request, order, testimony = self.event_fixture()
        notice = db.save_notice("Aviso", "Texto", "Geral")
        crud.update(self.admin, "notices", notice, {"titulo": "Aviso corrigido"})
        crud.update(self.admin, "events", event, {"titulo": "Retiro corrigido"})
        crud.update(self.admin, "registrations", registration, {"respostas": {
            "campo": {"campo": "Nome aprovado", "valor": "Inscrito corrigido"}}})
        crud.update(self.admin, "event_requests", request, {"titulo": "Camisa atualizada"})
        crud.update(self.admin, "request_orders", order, {"quantidade": 3})
        crud.update(self.admin, "testimonials", testimony, {"texto": "Texto corrigido", "status": "Aprovado"})
        self.assertEqual(self.row("request_orders", order)["quantidade"], 3)
        with self.assertRaises(ValueError):
            crud.update(self.admin, "event_requests", request, {"opcoes": ["P"]})
        foreign = self.event_fixture()[1]
        with self.assertRaises(ValueError):
            crud.update(self.admin, "request_orders", order, {"inscricao_id": foreign})
        access.set_deletion_permission(self.admin, self.admin["email"], True)
        with self.assertRaises(ValueError):
            crud.delete(self.admin, "events", event)
        with self.assertRaises(ValueError):
            crud.delete(self.admin, "registrations", registration)
        for table, record_id in (("request_orders", order), ("event_requests", request),
                                  ("registrations", registration), ("testimonials", testimony), ("events", event), ("notices", notice)):
            crud.delete(self.admin, table, record_id)
            self.assertFalse(db.query(f"SELECT 1 FROM {table} WHERE id=?", (record_id,)))

    def test_event_organizer_boundary_and_field_removal_permission(self):
        event = self.event_fixture("CEv A", self.group, self.manager["email"])[0]
        foreign = self.event_fixture("CEv B", self.foreign_group)[0]
        self.assertEqual([e["id"] for e in crud.list_records(self.manager, "events")], [event])
        with self.assertRaises(PermissionError):
            crud.update(self.manager, "events", foreign, {"titulo": "Proibido"})
        with self.assertRaises(PermissionError):
            crud.update_fields(self.manager, event, [])
        fields = json.loads(self.row("events", event)["campos"])
        fields[0]["label"] = "Campo renomeado"
        crud.update_fields(self.manager, event, fields)
        original_answer = db.query("SELECT respostas FROM registrations WHERE evento_id=?", (event,))[0]
        self.assertEqual(json.loads(original_answer["respostas"])["campo"]["campo"], "Nome aprovado")
        access.set_deletion_permission(self.admin, self.manager["email"], True, "CEv A", self.group)
        crud.update_fields(self.manager, event, [])
        self.assertTrue(db.query("SELECT 1 FROM registrations WHERE evento_id=?", (event,)))

    def test_editor_forms_for_all_entities(self):
        event, registration, request, order, testimony = self.event_fixture()
        notice = db.save_notice("Aviso", "Texto", "Geral")
        app = self.app(self.admin)
        records = (("groups", self.group), ("people", self.person), ("meetings", self.meeting),
                   ("attendance", self.attendance), ("followups", self.followup), ("notices", notice),
                   ("events", event), ("registrations", registration), ("event_requests", request),
                   ("request_orders", order), ("testimonials", testimony))
        for table, record_id in records:
            with self.subTest(table=table):
                self.choose(app, table, record_id)
                next(b for b in app.button if b.label == "Salvar alterações").click().run()
                self.assertFalse(app.exception, [e.message for e in app.exception])
                self.assertFalse(app.error, [e.value for e in app.error])
                self.assertFalse(any(b.label == "Excluir registro" for b in app.button))

    def test_ui_edit_and_explicit_delete_confirmation(self):
        db.execute("DELETE FROM meetings WHERE id=?", (self.other_meeting,))
        app = self.app(self.admin)
        self.choose(app, "groups", self.other_group)
        next(w for w in app.text_input if w.label == "Nome *").set_value("Nome corrigido na tela")
        next(b for b in app.button if b.label == "Salvar alterações").click().run()
        self.assertEqual(self.row("groups", self.other_group)["nome"], "Nome corrigido na tela")
        access.set_deletion_permission(self.admin, self.admin["email"], True, "CEv A", self.other_group)
        app.run()
        next(b for b in app.button if b.label == "Excluir registro").click().run()
        self.assertTrue(self.row("groups", self.other_group))
        next(w for w in app.checkbox if w.label == "Confirmo a exclusão deste registro").check()
        next(b for b in app.button if b.label == "Excluir registro").click().run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        self.assertFalse(db.query("SELECT 1 FROM groups WHERE id=?", (self.other_group,)))
        self.assertIsNone(app.selectbox(key="crud_record_groups").value)

    def test_changed_registration_field_types_preserve_historical_answers(self):
        event, registration, _, _, _ = self.event_fixture()
        fields = [{"id": "campo", "label": "Número aprovado", "type": "Número", "required": True, "options": []},
                  {"id": "data", "label": "Data aprovada", "type": "Data", "required": False, "options": []},
                  {"id": "escolha", "label": "Opção aprovada", "type": "Escolha", "required": False, "options": ["A", "B"]},
                  {"id": "aceite", "label": "Aceite aprovado", "type": "Confirmação", "required": False, "options": []}]
        crud.update_fields(self.admin, event, fields)
        app = self.app(self.admin)
        self.choose(app, "registrations", registration)
        next(b for b in app.button if b.label == "Salvar alterações").click().run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        self.assertFalse(app.error, [e.value for e in app.error])
        responses = json.loads(self.row("registrations", registration)["respostas"])
        self.assertEqual(responses["campo"]["valor"], "Inscrito")
        self.assertEqual(responses["campo"]["campo"], "Nome aprovado")
        next(w for w in app.checkbox if w.label == "Atualizar o formato de Nome aprovado").check().run()
        next(w for w in app.number_input if w.label == "Nome aprovado").set_value(42)
        next(w for w in app.date_input if w.label == "Data aprovada").set_value("2026-10-02")
        next(w for w in app.selectbox if w.label == "Opção aprovada").select("B")
        next(w for w in app.checkbox if w.label == "Aceite aprovado").check()
        next(b for b in app.button if b.label == "Salvar alterações").click().run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        self.assertFalse(app.error, [e.value for e in app.error])
        responses = json.loads(self.row("registrations", registration)["respostas"])
        self.assertEqual(responses["campo"]["valor"], 42)
        self.assertEqual(responses["data"]["valor"], "2026-10-02")
        self.assertEqual(responses["escolha"]["valor"], "B")
        self.assertIs(responses["aceite"]["valor"], True)

    def test_editor_checks_revoked_permission_at_delete_time(self):
        access.set_deletion_permission(self.admin, self.admin["email"], True, "CEv A", self.group)
        app = self.app(self.admin)
        self.choose(app, "attendance", self.attendance)
        next(w for w in app.checkbox if w.label == "Confirmo a exclusão deste registro").check()
        access.set_deletion_permission(self.admin, self.admin["email"], False, "CEv A", self.group)
        next(b for b in app.button if b.label == "Excluir registro").click().run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        self.assertTrue(self.row("attendance", self.attendance))
        self.assertFalse(any(b.label == "Excluir registro" for b in app.button))

    def test_access_page_grants_scoped_permission(self):
        app = self.app(self.admin)
        app._page_hash = app.session_state["pages"]["access"]._script_hash
        app.run()
        app.selectbox(key="delete_permission_account").select(self.responsible["email"]).run()
        next(b for b in app.button if b.label == "Conceder permissão de exclusão").click().run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        self.assertTrue(access.can_delete(self.responsible, "CEv A", self.group))
        self.assertFalse(access.can_delete(self.responsible, "CEv A", self.other_group))
        next(b for b in app.button if b.label == "Retirar permissão de exclusão").click().run()
        self.assertFalse(access.can_delete(self.responsible, "CEv A", self.group))

    def test_editor_can_remove_optional_links_and_photo(self):
        app = self.app(self.admin)
        self.choose(app, "people", self.person)
        next(w for w in app.selectbox if w.label == "Grupo").select(edicao.EMPTY)
        next(b for b in app.button if b.label == "Salvar alterações").click().run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        self.assertIsNone(self.row("people", self.person)["grupo_id"])
        self.assertTrue(self.row("followups", self.followup))
        self.choose(app, "groups", self.group)
        next(w for w in app.selectbox if w.label == "Pastor responsável").select(edicao.EMPTY)
        next(w for w in app.checkbox if w.label == "Remover foto atual").check()
        next(b for b in app.button if b.label == "Salvar alterações").click().run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        self.assertFalse(app.error, [e.value for e in app.error])
        row = self.row("groups", self.group)
        self.assertIsNone(row["pastor_id"])
        self.assertIsNone(row["foto"])


if __name__ == "__main__":
    unittest.main()
