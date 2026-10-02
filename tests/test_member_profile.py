"""Perfil em cartões e autorização por gestão ou liderança vinculada ao grupo."""
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
import database as db
import perfis
import perfil_membro
import portal


def claims(email):
    return {"is_logged_in": True, "email_verified": True, "email": email, "exp": time.time() + 3600}


class MemberProfileTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="member-profile-")
        self.addCleanup(temp.cleanup)
        patcher = patch.object(db, "DATABASE", Path(temp.name) / "db.sqlite3")
        patcher.start()
        self.addCleanup(patcher.stop)
        db.initialize()
        self.admin = claims("contatomaicondouglass@gmail.com")
        self.manager = claims("gestor@example.com")
        self.leader = claims("pastor@example.com")
        self.group = db.save_group("Itarema", "Grupo A", None, "", "", "Kerigma", ativo=1)
        self.other = db.save_group("Itarema", "Grupo B", None, "", "", "Kerigma", ativo=1)
        self.foreign = db.save_group("Paraipaba", "Grupo C", None, "", "", "Kerigma", ativo=1)
        self.member = db.save_person("Itarema", "Nome <script>TESTE</script>", "Membro", "Contato aprovado", self.group,
                                     ativo=1, eh_comunidade=0, endereco="Rua de teste", nascimento="2000-03-02",
                                     ministerio="Música", acompanhador="Acompanhador de teste")
        self.pastor = db.save_person("Itarema", "Pastor de teste", "Pastor", "", self.group,
                                     email=self.leader["email"], ativo=1, eh_comunidade=1)
        self.outside = db.save_person("Itarema", "Outro grupo", "Membro", "", self.other, ativo=1, eh_comunidade=0)
        self.foreign_member = db.save_person("Paraipaba", "Outro CEv", "Membro", "", self.foreign, ativo=1, eh_comunidade=0)
        access.grant(self.admin, self.manager["email"], "Gestor de CEv", "Itarema")
        access.grant(self.admin, self.leader["email"], "Responsável de grupo", "Itarema", self.group)

    def test_manager_and_admin_scope(self):
        self.assertEqual(perfil_membro.load(self.manager, self.member)["id"], self.member)
        self.assertEqual(perfil_membro.load(self.manager, self.outside)["id"], self.outside)
        self.assertEqual(perfil_membro.load(self.admin, self.foreign_member)["id"], self.foreign_member)
        with self.assertRaises(PermissionError):
            perfil_membro.load(self.manager, self.foreign_member)

    def test_group_requires_both_authorization_and_linked_pastor_or_nucleus(self):
        with self.assertRaises(PermissionError):
            perfil_membro.load(self.leader, self.member)
        perfis.self_link(self.leader, self.pastor)
        for category in ("Pastor", "Núcleo"):
            db.execute("UPDATE people SET categoria=? WHERE id=?", (category, self.pastor))
            self.assertEqual(perfil_membro.load(self.leader, self.member)["id"], self.member)
        for person_id in (self.outside, self.foreign_member):
            with self.assertRaises(PermissionError):
                perfil_membro.load(self.leader, person_id)
        db.execute("UPDATE people SET categoria='Membro' WHERE id=?", (self.pastor,))
        with self.assertRaises(PermissionError):
            perfil_membro.load(self.leader, self.member)
        db.execute("UPDATE people SET categoria='Pastor',grupo_id=? WHERE id=?", (self.other, self.pastor))
        with self.assertRaises(PermissionError):
            perfil_membro.load(self.leader, self.member)
        db.execute("UPDATE groups SET pastor_id=? WHERE id=?", (self.pastor, self.group))
        self.assertEqual(perfil_membro.load(self.leader, self.member)["id"], self.member)

    def test_link_alone_and_expired_claims_do_not_grant_access(self):
        perfis.self_link(self.leader, self.pastor)
        access.revoke(self.admin, self.leader["email"])
        with self.assertRaises(PermissionError):
            perfil_membro.load(self.leader, self.member)
        for invalid in ({}, dict(self.admin, exp=0), dict(self.admin, email_verified=False)):
            with self.assertRaises(PermissionError):
                perfil_membro.load(invalid, self.member)

    def app(self, user):
        patcher = patch.object(portal.st, "user", SimpleNamespace(to_dict=lambda: user))
        patcher.start()
        self.addCleanup(patcher.stop)
        return AppTest.from_string(f"import streamlit as st\nimport perfil_membro\nperfil_membro.render({self.member}, st.user.to_dict())").run()

    def test_profile_cards_show_approved_fields_and_escape_user_text(self):
        app = self.app(self.manager)
        self.assertFalse(app.exception, [e.message for e in app.exception])
        body = "".join(element.proto.body for element in app.get("html"))
        for expected in ("Contato aprovado", "Rua de teste", "02/03/2000", "Acompanhador de teste", "Música", "Grupo A"):
            self.assertIn(expected, body)
        self.assertIn("&lt;script&gt;TESTE&lt;/script&gt;", body)
        self.assertNotIn("<script>TESTE</script>", body)
        self.assertEqual(len(app.dataframe), 0)
        self.assertIn("Perfil do membro", [h.value for h in app.subheader])
        photo = BytesIO()
        Image.new("RGB", (32, 32), "teal").save(photo, format="PNG")
        db.execute("UPDATE people SET foto=?,foto_tipo='PNG' WHERE id=?", (photo.getvalue(), self.member))
        app.run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        self.assertEqual(len(app.image), 1)

    def test_unauthorized_render_does_not_emit_profile_data(self):
        app = self.app(self.leader)
        self.assertFalse(app.exception)
        self.assertEqual(len(app.get("html")), 0)
        self.assertFalse(app.subheader)
        self.assertTrue(any("não pode consultar" in item.value for item in app.info))


if __name__ == "__main__":
    unittest.main()
