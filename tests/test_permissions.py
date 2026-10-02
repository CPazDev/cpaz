"""Delegação por CEv e gestão de publicações limitada ao grupo autorizado."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
import access
import crud
import database as db
import diretorio
import portal


def claims(email):
    return {"is_logged_in": True, "email_verified": True, "email": email, "exp": time.time() + 3600}


class PermissionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="scoped-permissions-")
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(db, "DATABASE", Path(self.temp.name) / "db.sqlite3")
        patcher.start()
        self.addCleanup(patcher.stop)
        db.initialize()
        self.admin = claims("contatomaicondouglass@gmail.com")
        self.manager = claims("gestor@example.com")
        self.responsible = claims("grupo@example.com")
        self.group = db.save_group("Itarema", "Meu grupo", None, "", "", "Kerigma", ativo=1)
        self.other = db.save_group("Itarema", "Outro grupo", None, "", "", "Kerigma", ativo=1)
        self.foreign = db.save_group("Paraipaba", "Grupo de outro CEv", None, "", "", "Kerigma", ativo=1)
        access.grant(self.admin, self.manager["email"], "Gestor de CEv", "Itarema")
        access.grant(self.admin, self.responsible["email"], "Responsável de grupo", "Itarema", self.group)
        access.grant(self.admin, "outrocev@example.com", "Gestor de CEv", "Paraipaba")
        access.grant(self.admin, "admin@example.com", "Administrador")

    def app(self, user):
        patcher = patch.object(portal.st, "user", SimpleNamespace(to_dict=lambda: user))
        patcher.start()
        self.addCleanup(patcher.stop)
        content = patch.object(portal, "load_content", lambda: {"cevs": ["Itarema", "Paraipaba"], "avisos": [], "campos_pessoa_aprovados": True})
        content.start()
        self.addCleanup(content.stop)
        return AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20).run()

    def page(self, app, key):
        app._page_hash = app.session_state["pages"][key]._script_hash
        app.run()
        self.assertFalse(app.exception, [r.message for r in app.exception])

    def event(self, group, cev="Itarema", owner="admin@example.com"):
        return db.save_event("Evento", "Retiro", "2026-11-01", None, "", "", "Grupo", cev, group,
                             None, None, "#177d73", "", owner)

    def test_manager_delegates_same_level_and_below_in_own_cev(self):
        access.grant(self.manager, "colega@example.com", "Gestor de CEv", "Itarema")
        access.grant(self.manager, "responsavel@example.com", "Responsável de grupo", "Itarema", self.other)
        self.assertEqual(access.profile(claims("colega@example.com"))["cev"], "Itarema")
        self.assertEqual(access.profile(claims("responsavel@example.com"))["grupo_id"], self.other)
        self.assertNotIn("outrocev@example.com", [r["email"] for r in access.account_grants(self.manager)])
        access.set_deletion_permission(self.admin, "responsavel@example.com", True, "Itarema", self.other)
        access.revoke(self.manager, "responsavel@example.com")
        self.assertFalse(db.query("SELECT * FROM access_grants WHERE email='responsavel@example.com'"))
        self.assertFalse(db.query("SELECT * FROM deletion_permissions WHERE email='responsavel@example.com'"))

    def test_delegation_rejects_cross_cev_escalation_and_account_takeover(self):
        for level, cev, group in (("Administrador", None, None), ("Gestor de CEv", "Paraipaba", None),
                                  ("Responsável de grupo", "Paraipaba", self.foreign)):
            with self.assertRaises(PermissionError):
                access.grant(self.manager, "novo@example.com", level, cev, group)
        with self.assertRaises(ValueError):
            access.grant(self.manager, "novo@example.com", "Responsável de grupo", "Itarema", self.foreign)
        for email in ("outrocev@example.com", "admin@example.com"):
            before = db.query("SELECT * FROM access_grants WHERE email=?", (email,))
            with self.assertRaises(PermissionError):
                access.grant(self.manager, email, "Gestor de CEv", "Itarema")
            with self.assertRaises(PermissionError):
                access.revoke(self.manager, email)
            self.assertEqual(db.query("SELECT * FROM access_grants WHERE email=?", (email,)), before)
        with self.assertRaises(PermissionError):
            access.grant(self.responsible, "novo@example.com", "Responsável de grupo", "Itarema", self.group)
        with self.assertRaises(PermissionError):
            access.set_deletion_permission(self.manager, self.responsible["email"], True, "Itarema", self.group)
        self.assertFalse(access.can_delete(self.responsible, "Itarema", self.group))

    def test_concurrent_managers_cannot_take_over_each_others_new_account(self):
        gate = Barrier(2)
        def grant(user, cev):
            gate.wait(timeout=5)
            try:
                access.grant(user, "compartilhado@example.com", "Gestor de CEv", cev)
                return cev
            except PermissionError:
                return None
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(grant, self.manager, "Itarema")
            second = executor.submit(grant, claims("outrocev@example.com"), "Paraipaba")
            results = [first.result(timeout=15), second.result(timeout=15)]
        successful = [r for r in results if r is not None]
        self.assertEqual(len(successful), 1)
        self.assertEqual(access.profile(claims("compartilhado@example.com"))["cev"], successful[0])

    def test_role_is_rechecked_inside_delegation_transaction(self):
        stale = access.profile(self.manager)
        access.grant(self.admin, self.manager["email"], "Responsável de grupo", "Itarema", self.group)
        with patch.object(access, "profile", return_value=stale):
            with self.assertRaises(PermissionError):
                access.grant(self.manager, "novo@example.com", "Gestor de CEv", "Itarema")
            with self.assertRaises(PermissionError):
                access.revoke(self.manager, self.responsible["email"])
        self.assertFalse(db.query("SELECT * FROM access_grants WHERE email='novo@example.com'"))
        self.assertTrue(db.query("SELECT * FROM access_grants WHERE email=?", (self.responsible["email"],)))

    def test_group_editor_and_publication_create_are_enforced_by_server(self):
        crud.update(self.responsible, "groups", self.group, {"local": "Local atualizado"})
        for target in (self.other, self.foreign):
            with self.assertRaises(PermissionError):
                crud.update(self.responsible, "groups", target, {"local": "Proibido"})
        with self.assertRaises(PermissionError):
            crud.update(self.responsible, "groups", self.group, {"cev": "Paraipaba"})
        pastor = db.save_person("Itarema", "Pastor de outro grupo", "Pastor", "", self.other)
        with self.assertRaises(PermissionError):
            crud.update(self.responsible, "groups", self.group, {"pastor_id": pastor})
        own = crud.create_notice(self.responsible, "Aviso autorizado", "Texto", "Grupo", "Itarema", self.group)
        self.assertEqual(db.query("SELECT grupo_id FROM notices WHERE id=?", (own,))[0]["grupo_id"], self.group)
        for destination, cev, group in (("Geral", None, None), ("CEv/Irradiação", "Itarema", None),
                                        ("Grupo", "Itarema", self.other), ("Grupo", "Paraipaba", self.foreign)):
            with self.assertRaises(PermissionError):
                crud.create_notice(self.responsible, "Proibido", "", destination, cev, group)
            with self.assertRaises(PermissionError):
                crud.create_event(self.responsible, "Proibido", "Retiro", "2026-11-01", None, "", "",
                                  destination, cev, group, None, None, "#177d73", "")
        event = crud.create_event(self.responsible, "Permitido", "Retiro", "2026-11-01", None, "", "",
                                  "Grupo", "Itarema", self.group, None, None, "#177d73", "")
        self.assertEqual(db.query("SELECT organizador FROM events WHERE id=?", (event,))[0]["organizador"], self.responsible["email"])

    def test_event_management_covers_cev_and_group_scope_regardless_of_organizer(self):
        own, other, foreign = self.event(self.group), self.event(self.other), self.event(self.foreign, "Paraipaba")
        crud.update(self.manager, "events", other, {"titulo": "Gerenciado pelo CEv"})
        crud.update(self.responsible, "events", own, {"titulo": "Gerenciado pelo grupo"})
        self.assertEqual({r["id"] for r in crud.list_records(self.manager, "events")}, {own, other})
        self.assertEqual({r["id"] for r in crud.list_records(self.responsible, "events")}, {own})
        for event in (other, foreign):
            with self.assertRaises(PermissionError):
                crud.update(self.responsible, "events", event, {"titulo": "Proibido"})
        with self.assertRaises(PermissionError):
            crud.update(self.responsible, "events", own, {"grupo_id": self.other})
        crud.update_fields(self.responsible, own, [{"id": "nome", "label": "Nome", "type": "Texto", "required": False, "options": []}])
        with self.assertRaises(PermissionError):
            crud.update_fields(self.responsible, own, [])

    def test_manager_access_ui_does_not_show_other_cev_or_admin_roles(self):
        app = self.app(self.manager)
        self.assertEqual(app.selectbox(key="cev_sidebar").options, ["Itarema"])
        self.page(app, "access")
        levels = next(w for w in app.selectbox if w.label == "Nível de acesso")
        self.assertEqual(levels.options, ["Gestor de CEv", "Responsável de grupo"])
        levels.select("Responsável de grupo").run()
        group = next(w for w in app.selectbox if w.label == "Grupo autorizado")
        self.assertNotIn("Grupo de outro CEv", group.options)
        group.select(self.other)
        next(w for w in app.text_input if w.label == "E-mail da conta Google").set_value("autorizado@example.com")
        next(b for b in app.button if b.label == "Salvar autorização").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(access.profile(claims("autorizado@example.com"))["grupo_id"], self.other)
        emails = set(app.dataframe[0].value["E-mail"])
        self.assertNotIn("outrocev@example.com", emails)
        self.assertNotIn("admin@example.com", emails)
        self.assertFalse(any(b.label == "Conceder permissão de exclusão" for b in app.button))

    def test_group_ui_shows_only_own_publications_and_fixed_create_destination(self):
        db.save_notice("Aviso do meu grupo", "", "Grupo", "Itarema", self.group)
        db.save_notice("Aviso de outro grupo", "", "Grupo", "Itarema", self.other)
        db.save_notice("Aviso de CEv", "", "CEv/Irradiação", "Itarema")
        foreign = self.event(self.other)
        app = self.app(self.responsible)
        self.assertEqual(app.selectbox(key="cev_sidebar").options, ["Itarema"])
        self.assertIn("Aviso do meu grupo", [w.value for w in app.subheader])
        self.assertIn("Aviso de CEv", [w.value for w in app.subheader])
        self.page(app, "group")
        picker = app.selectbox(key="group_page_picker")
        self.assertEqual(picker.value, self.group)
        self.assertFalse(any("Outro grupo" in option for option in picker.options))
        self.assertTrue(any(b.label == "Editar grupo" for b in app.button))
        self.page(app, "notice")
        self.assertFalse(any(w.label == "Destino" for w in app.selectbox))
        next(w for w in app.text_input if w.label == "Título *").set_value("Aviso novo")
        next(b for b in app.button if b.label == "Publicar aviso").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(db.query("SELECT grupo_id FROM notices WHERE titulo='Aviso novo'")[0]["grupo_id"], self.group)
        self.page(app, "event_create")
        next(w for w in app.text_input if w.label == "Título *").set_value("Retiro do grupo")
        next(w for w in app.selectbox if w.label == "Tipo *").select("Retiro")
        next(b for b in app.button if b.label == "Criar página do evento").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(db.query("SELECT grupo_id FROM events WHERE titulo='Retiro do grupo'")[0]["grupo_id"], self.group)
        app.query_params["id"] = str(foreign)
        self.page(app, "event")
        self.assertTrue(any("limitada" in message.value for message in app.info))
        self.assertFalse(app.tabs)

    def test_engaged_includes_noncommunity_pastors_and_nucleus_and_clicks_to_same_list(self):
        included = set()
        for category in db.CATEGORIES:
            included.add(db.save_person("Itarema", category + " engajado", category, "", self.group,
                         ativo=1, eh_comunidade=0, ministerio="Música; Liturgia"))
        for active, community, ministry in ((1, 1, "Música"), (0, 0, "Música"), (None, 0, "Música"), (1, None, "Música"), (1, 0, "  ")):
            db.save_person("Itarema", "Fora do indicador", "Pastor", "", self.group,
                           ativo=active, eh_comunidade=community, ministerio=ministry)
        self.assertEqual({p["id"] for p in diretorio.engaged(db.people("Itarema"))}, included)
        app = self.app(self.manager)
        self.page(app, "cev")
        app.button(key="directory_Itarema_indicator_Membros engajados").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(set(app.dataframe[0].value["Nome"]), {c + " engajado" for c in db.CATEGORIES})

    def test_dropdown_opens_public_group_without_button_list(self):
        app = self.app({})
        app.selectbox(key="cev_sidebar").select("Itarema").run()
        self.page(app, "cev")
        picker = next(w for w in app.selectbox if w.label == "Grupo para consultar")
        self.assertEqual(set(picker.options), {"Meu grupo", "Outro grupo"})
        self.assertFalse(any(b.label in ("Meu grupo", "Outro grupo") for b in app.button))
        picker.select(self.other).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["selected_group"], self.other)
        self.assertEqual(app.selectbox(key="group_page_picker").value, self.other)


if __name__ == "__main__":
    unittest.main()
