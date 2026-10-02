"""Herança de avisos públicos e isolamento dos avisos privados de grupo."""
from io import BytesIO
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image
from streamlit.testing.v1 import AppTest

import access
import avisos
import database as db
import perfis
import portal


def claims(email):
    return {"is_logged_in": True, "email_verified": True, "email": email, "exp": time.time() + 3600}


class NoticesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="notices-tests-")
        self.addCleanup(temp.cleanup)
        patcher = patch.object(db, "DATABASE", Path(temp.name) / "db.sqlite3")
        patcher.start()
        self.addCleanup(patcher.stop)
        db.initialize()
        self.admin = claims("contatomaicondouglass@gmail.com")
        self.member = claims("membro@example.com")
        self.manager = claims("gestor@example.com")
        self.responsible = claims("responsavel@example.com")
        self.group = db.save_group("Itarema", "Grupo A", None, "", "", "Kerigma", ativo=1)
        self.other = db.save_group("Itarema", "Grupo B", None, "", "", "Kerigma", ativo=1)
        self.foreign = db.save_group("Paraipaba", "Grupo C", None, "", "", "Kerigma", ativo=1)
        self.person = db.save_person("Itarema", "Membro vinculado", "Membro", "", self.group,
                                     email=self.member["email"], ativo=1, eh_comunidade=0)
        self.general = db.save_notice("Geral para todos", "Texto geral", "Geral")
        self.cev = db.save_notice("Aviso de Itarema", "Texto do CEv", "CEv/Irradiação", "Itarema")
        self.foreign_cev = db.save_notice("Aviso de Paraipaba", "Texto de outro CEv", "CEv/Irradiação", "Paraipaba")
        photo = BytesIO()
        Image.new("RGB", (16, 16), "green").save(photo, format="PNG")
        self.private = db.save_notice("Privado A", "TEXTO_PRIVADO_A", "Grupo", "Itarema", self.group,
                                      photo.getvalue(), "PNG")
        self.other_private = db.save_notice("Privado B", "TEXTO_PRIVADO_B", "Grupo", "Itarema", self.other)
        self.foreign_private = db.save_notice("Privado C", "TEXTO_PRIVADO_C", "Grupo", "Paraipaba", self.foreign)
        access.grant(self.admin, self.manager["email"], "Gestor de CEv", "Itarema")
        access.grant(self.admin, self.responsible["email"], "Responsável de grupo", "Itarema", self.group)

    def ids(self, user, cev=None, group_id=None):
        rows = avisos.visible(user, cev, group_id)
        self.assertEqual(len(rows), len({r["id"] for r in rows}))
        return {r["id"] for r in rows}

    def test_public_hierarchy_never_returns_group_notices(self):
        self.assertEqual(self.ids({}), {self.general})
        self.assertEqual(self.ids({}, "Itarema"), {self.general, self.cev})
        for group in (self.group, self.other):
            self.assertEqual(self.ids({}, "Itarema", group), {self.general, self.cev})
        self.assertEqual(self.ids({}, "Paraipaba", self.foreign), {self.general, self.foreign_cev})

    def test_linked_member_without_management_sees_only_own_group(self):
        self.assertEqual(self.ids(self.member, "Itarema", self.group), {self.general, self.cev})
        perfis.self_link(self.member, self.person)
        self.assertIsNone(access.profile(self.member))
        self.assertEqual(self.ids(self.member, "Itarema", self.group), {self.general, self.cev, self.private})
        self.assertEqual(self.ids(self.member, "Itarema", self.other), {self.general, self.cev})
        self.assertEqual(self.ids(self.member, "Paraipaba", self.foreign), {self.general, self.foreign_cev})
        self.assertEqual(self.ids(self.member, "Itarema", self.foreign), {self.general, self.cev})

    def test_management_scope_and_responsible_without_personal_link(self):
        self.assertEqual(self.ids(self.admin, "Paraipaba", self.foreign),
                         {self.general, self.foreign_cev, self.foreign_private})
        self.assertEqual(self.ids(self.manager, "Itarema", self.other), {self.general, self.cev, self.other_private})
        self.assertEqual(self.ids(self.manager, "Paraipaba", self.foreign), {self.general, self.foreign_cev})
        self.assertEqual(self.ids(self.responsible, "Itarema", self.group), {self.general, self.cev, self.private})
        self.assertEqual(self.ids(self.responsible, "Itarema", self.other), {self.general, self.cev})

    def test_unlink_expiration_and_membership_change_remove_private_access(self):
        perfis.self_link(self.member, self.person)
        for invalid in ({}, dict(self.member, exp=0), dict(self.member, email_verified=False),
                        dict(claims("semvinculo@example.com"), grupo_id=self.group, membro_id=self.person)):
            self.assertEqual(self.ids(invalid, "Itarema", self.group), {self.general, self.cev})
        db.execute("UPDATE people SET grupo_id=? WHERE id=?", (self.other, self.person))
        self.assertEqual(self.ids(self.member, "Itarema", self.group), {self.general, self.cev})
        self.assertEqual(self.ids(self.member, "Itarema", self.other), {self.general, self.cev, self.other_private})
        perfis.self_link(self.member, None)
        self.assertEqual(self.ids(self.member, "Itarema", self.other), {self.general, self.cev})

    def app(self, user):
        patcher = patch.object(portal.st, "user", SimpleNamespace(to_dict=lambda: user))
        patcher.start()
        self.addCleanup(patcher.stop)
        content = patch.object(portal, "load_content", lambda: {"cevs": ["Itarema", "Paraipaba"],
                              "avisos": ["Aviso geral configurado"], "campos_pessoa_aprovados": True})
        content.start()
        self.addCleanup(content.stop)
        return AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20).run()

    def page(self, app, key):
        app._page_hash = app.session_state["pages"][key]._script_hash
        app.run()
        self.assertFalse(app.exception, [e.message for e in app.exception])

    def test_public_group_ui_emits_no_private_title_text_or_photo(self):
        app = self.app({})
        app.selectbox(key="cev_sidebar").select("Itarema").run()
        app.session_state["selected_group"] = self.group
        self.page(app, "group")
        self.assertIn("Geral para todos", [h.value for h in app.subheader])
        self.assertIn("Aviso de Itarema", [h.value for h in app.subheader])
        self.assertNotIn("Privado A", [h.value for h in app.subheader])
        self.assertNotIn("TEXTO_PRIVADO_A", " ".join(m.value for m in app.markdown))
        self.assertEqual(len(app.image), 0)
        self.assertTrue(any(m.value == "Aviso geral configurado" for m in app.markdown))

    def test_linked_member_sees_notices_in_personal_page_and_group_without_management(self):
        perfis.self_link(self.member, self.person)
        app = self.app(self.member)
        app.query_params["grupo_id"] = str(self.foreign)
        app.query_params["membro_id"] = str(self.foreign_private)
        self.page(app, "personal")
        headings = {h.value for h in app.subheader}
        self.assertTrue({"Meus avisos", "Geral para todos", "Aviso de Itarema", "Privado A"}.issubset(headings))
        self.assertFalse({"Privado B", "Privado C", "Aviso de Paraipaba"} & headings)
        self.assertEqual(len(app.image), 1)
        app.selectbox(key="cev_sidebar").select("Itarema").run()
        app.session_state["selected_group"] = self.group
        self.page(app, "group")
        self.assertIn("Privado A", [h.value for h in app.subheader])
        self.assertEqual(len(app.tabs), 0)
        self.assertEqual(len(app.dataframe), 0)
        perfis.self_link(self.member, None)
        app.run()
        self.assertNotIn("Privado A", [h.value for h in app.subheader])
        self.assertEqual(len(app.image), 0)


if __name__ == "__main__":
    unittest.main()
