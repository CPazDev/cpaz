"""Data de publicação, migração e compartilhamento de eventos como avisos."""
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from streamlit.testing.v1 import AppTest

import access
import avisos
import crud
import database as db
import perfis
import portal


def claims(email):
    return {"is_logged_in": True, "email_verified": True, "email": email, "exp": time.time() + 3600}


class NoticeSharingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="notice-sharing-")
        self.addCleanup(temp.cleanup)
        patcher = patch.object(db, "DATABASE", Path(temp.name) / "db.sqlite3")
        patcher.start()
        self.addCleanup(patcher.stop)
        db.initialize()
        self.admin = claims("contatomaicondouglass@gmail.com")
        self.manager = claims("gestor@example.com")
        self.responsible = claims("responsavel@example.com")
        self.member = claims("membro@example.com")
        self.group = db.save_group("Itarema", "Grupo A", None, "", "", "Kerigma", ativo=1)
        self.other = db.save_group("Itarema", "Grupo B", None, "", "", "Kerigma", ativo=1)
        self.foreign = db.save_group("Paraipaba", "Grupo C", None, "", "", "Kerigma", ativo=1)
        self.person = db.save_person("Itarema", "Membro vinculado", "Membro", "", self.group,
                                     email=self.member["email"], ativo=1, eh_comunidade=0)
        perfis.self_link(self.member, self.person)
        access.grant(self.admin, self.manager["email"], "Gestor de CEv", "Itarema")
        access.grant(self.admin, self.responsible["email"], "Responsável de grupo", "Itarema", self.group)
        self.event = db.save_event("Retiro de teste", "Retiro", "2026-11-01", None, "Local", "Descrição",
                                   "Geral", None, None, None, None, "#177d73", "", self.admin["email"])
        self.group_event = db.save_event("Evento do grupo", "Evento", "2026-11-02", None, "", "",
                                         "Grupo", "Itarema", self.group, None, None, "#177d73", "", self.admin["email"])

    def test_publication_timestamp_uses_sao_paulo_and_is_preserved_on_edit(self):
        before = datetime.now(ZoneInfo("America/Sao_Paulo")) - timedelta(seconds=1)
        notice_id = crud.create_notice(self.admin, "Aviso datado", "Texto", "Geral")
        original = db.query("SELECT publicado_em FROM notices WHERE id=?", (notice_id,))[0]["publicado_em"]
        stamp = datetime.fromisoformat(original)
        self.assertGreaterEqual(stamp, before)
        self.assertLessEqual(stamp, datetime.now(ZoneInfo("America/Sao_Paulo")))
        self.assertEqual(stamp.utcoffset(), timedelta(hours=-3))
        self.assertIn(stamp.strftime("%d/%m/%Y"), avisos.publication_label(original))
        self.assertEqual(avisos.publication_label("2026-10-02T18:00:00+00:00"), "Publicado em 02/10/2026 às 15:00")
        crud.update(self.admin, "notices", notice_id, {"texto": "Texto editado"})
        self.assertEqual(db.query("SELECT publicado_em FROM notices WHERE id=?", (notice_id,))[0]["publicado_em"], original)
        with self.assertRaises(ValueError):
            crud.update(self.admin, "notices", notice_id, {"publicado_em": "2020-01-01"})

    def test_migration_keeps_legacy_dates_unknown_and_existing_notice_content(self):
        with db.connection() as conn:
            conn.execute("DROP TABLE notices")
            conn.execute("""CREATE TABLE notices (id INTEGER PRIMARY KEY,titulo TEXT NOT NULL,texto TEXT NOT NULL DEFAULT '',
                destino TEXT NOT NULL,cev TEXT,grupo_id INTEGER REFERENCES groups(id),foto BLOB,foto_tipo TEXT)""")
            conn.execute("INSERT INTO notices(id,titulo,texto,destino) VALUES(99,'Aviso antigo','Texto original','Geral')")
        db.initialize()
        db.initialize()
        row = db.query("SELECT * FROM notices WHERE id=99")[0]
        self.assertEqual(row["texto"], "Texto original")
        self.assertIsNone(row["publicado_em"])
        self.assertIsNone(row["evento_id"])
        self.assertEqual(avisos.publication_label(row["publicado_em"]), "Data de publicação não registrada")
        new_id = db.save_notice("Novo aviso", "", "Geral")
        self.assertTrue(db.query("SELECT publicado_em FROM notices WHERE id=?", (new_id,))[0]["publicado_em"])

    def test_share_permissions_are_scoped_and_source_must_be_visible(self):
        crud.share_event(self.admin, self.event, "Geral")
        notice = crud.share_event(self.manager, self.event, "Grupo", "Itarema", self.other)
        self.assertEqual(db.query("SELECT evento_id FROM notices WHERE id=?", (notice,))[0]["evento_id"], self.event)
        for actor, event, destination, cev, group in (
            (self.manager, self.event, "Geral", None, None),
            (self.manager, self.event, "Grupo", "Paraipaba", self.foreign),
            (self.responsible, self.group_event, "Grupo", "Itarema", self.other),
            (self.member, self.event, "Grupo", "Itarema", self.group),
            ({}, self.event, "Geral", None, None),
        ):
            with self.assertRaises(PermissionError):
                crud.share_event(actor, event, destination, cev, group)
        own = crud.share_event(self.responsible, self.group_event, "Grupo", "Itarema", self.group)
        self.assertTrue(db.query("SELECT publicado_em FROM notices WHERE id=?", (own,))[0]["publicado_em"])

    def test_private_share_reaches_only_target_group_and_allows_recipients_to_open_event(self):
        source = db.query("SELECT * FROM events WHERE id=?", (self.event,))[0]
        responsible = access.profile(self.responsible)
        self.assertFalse(access.can_view_publication(responsible, source))
        foreign_share = crud.share_event(self.manager, self.event, "Grupo", "Itarema", self.other)
        self.assertFalse(access.can_view_publication(responsible, source))
        own_share = crud.share_event(self.manager, self.event, "Grupo", "Itarema", self.group)
        self.assertTrue(access.can_view_publication(responsible, source))
        self.assertEqual({n["id"] for n in avisos.visible(self.member, "Itarema", self.group)}, {own_share})
        self.assertFalse(avisos.visible({}, "Itarema", self.group))
        self.assertFalse(avisos.visible(self.member, "Itarema", self.other))
        self.assertNotEqual(own_share, foreign_share)

    def test_deleting_event_keeps_notice_and_removes_button_reference(self):
        notice = crud.share_event(self.admin, self.event, "Geral")
        access.set_deletion_permission(self.admin, self.admin["email"], True)
        crud.delete(self.admin, "events", self.event)
        row = db.query("SELECT * FROM notices WHERE id=?", (notice,))[0]
        self.assertEqual(row["titulo"], "Retiro de teste")
        self.assertIsNone(row["evento_id"])
        self.assertTrue(row["publicado_em"])
        self.assertFalse(db.query("PRAGMA foreign_key_check"))

    def app(self, user):
        patcher = patch.object(portal.st, "user", SimpleNamespace(to_dict=lambda: user))
        patcher.start()
        self.addCleanup(patcher.stop)
        content = patch.object(portal, "load_content", lambda: {"cevs": ["Itarema", "Paraipaba"],
                                                               "avisos": [], "campos_pessoa_aprovados": True})
        content.start()
        self.addCleanup(content.stop)
        return AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20).run()

    def page(self, app, key):
        app._page_hash = app.session_state["pages"][key]._script_hash
        app.run()
        self.assertFalse(app.exception, [e.message for e in app.exception])

    def test_ui_share_then_open_from_personal_notices(self):
        app = self.app(self.manager)
        app.session_state["selected_event"] = self.event
        self.page(app, "event")
        prefix = f'event_share_{self.event}'
        app.selectbox(key=prefix + "_destino").select("Grupo").run()
        app.selectbox(key=prefix + "_cev").select("Itarema").run()
        app.selectbox(key=prefix + "_grupo").select(self.group).run()
        next(b for b in app.button if b.label == "Publicar compartilhamento").click().run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        notice = db.query("SELECT * FROM notices")[0]
        self.assertEqual(notice["evento_id"], self.event)
        self.assertEqual(notice["grupo_id"], self.group)
        member_app = self.app(self.member)
        self.page(member_app, "personal")
        self.assertIn("Retiro de teste", [h.value for h in member_app.subheader])
        self.assertTrue(any(c.value.startswith("Publicado em ") for c in member_app.caption))
        member_app.button(key=f'notice_event_open_{notice["id"]}').click().run()
        self.assertFalse(member_app.exception, [e.message for e in member_app.exception])
        self.assertEqual(member_app.session_state["selected_event"], self.event)
        self.assertEqual(member_app.query_params["id"], [str(self.event)])
        self.assertFalse(any(b.label == "Publicar compartilhamento" for b in member_app.button))


if __name__ == "__main__":
    unittest.main()
