"""Verificações de persistência, permissões e fluxos do portal."""

from io import BytesIO
import json
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
import portal
import eventos


def claims(email):
    return {"is_logged_in": True, "email": email, "email_verified": True,
            "exp": time.time() + 3600, "name": "Conta de teste"}


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="portal-tests-")
        self.database_patch = patch.object(db, "DATABASE", Path(self.temp.name) / "test.sqlite3")
        self.database_patch.start()
        db.initialize()
        self.admin = claims("contatomaicondouglass@gmail.com")
        self.other_admin = claims("projetocpaz@gmail.com")

    def tearDown(self):
        self.database_patch.stop()
        self.temp.cleanup()

    def fixture(self):
        pastor = db.save_person("CEv de teste A", "Pastor de teste", "Pastor", "", None)
        group = db.save_group("CEv de teste A", "Grupo de teste", pastor, "Sala de teste", "19:00", "Kerigma")
        member = db.save_person("CEv de teste A", "Pessoa de teste", "Núcleo", "Contato privado", group,
                                "@teste", "Endereço privado", "2000-01-01", acompanhador="Pessoa A", ministerio="Teste")
        meeting = db.execute("INSERT INTO meetings (grupo_id,data,tema,observacoes) VALUES (?,?,?,?)",
                             (group, "2026-10-02", "Tema privado", "Observação privada"))
        return group, member, meeting

    def test_initial_admins_and_invalid_identity(self):
        self.assertEqual(access.profile(self.admin)["nivel"], "Administrador")
        self.assertEqual(access.profile(self.other_admin)["nivel"], "Administrador")
        invalid = dict(self.admin, email_verified=False)
        self.assertIsNone(access.profile(invalid))
        self.assertIsNone(access.profile(dict(self.admin, exp=0)))
        self.assertIsNone(access.profile({}))

    def test_requests_grants_scopes_and_revocation(self):
        group, _, _ = self.fixture()
        unknown = claims("pessoa@example.com")
        self.assertIsNone(access.profile(unknown))
        self.assertEqual(len(db.query("SELECT * FROM access_requests")), 1)
        with self.assertRaises(PermissionError):
            access.grant(unknown, "pessoa@example.com", "Administrador")
        access.grant(self.admin, "pessoa@example.com", "Responsável de grupo", "CEv de teste A", group)
        responsible = access.profile(unknown)
        self.assertTrue(access.can_use_group(responsible, db.groups("CEv de teste A")[0]))
        self.assertFalse(access.can_manage_cev(responsible, "CEv de teste A"))
        self.assertEqual(len(db.query("SELECT * FROM access_requests")), 0)
        other = db.save_group("CEv de teste B", "Outro grupo", None, "", "", "Permanente")
        self.assertFalse(access.can_use_group(responsible, db.groups("CEv de teste B")[0]))
        with self.assertRaises(ValueError):
            access.grant(self.admin, "x@example.com", "Responsável de grupo", "CEv de teste A", other)
        access.grant(self.admin, "pessoa@example.com", "Gestor de CEv", "CEv de teste A")
        manager = access.profile(unknown)
        self.assertTrue(access.can_manage_cev(manager, "CEv de teste A"))
        self.assertFalse(access.can_manage_cev(manager, "CEv de teste B"))
        access.revoke(self.admin, "pessoa@example.com")
        self.assertIsNone(access.profile(unknown))
        with self.assertRaises(ValueError):
            access.revoke(self.admin, self.admin["email"])

    def test_attendance_and_followup_integrity(self):
        group, member, meeting = self.fixture()
        db.save_attendance(meeting, member, "Liberado")
        db.save_attendance(meeting, member, "Presente")
        rows = db.attendance(group)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["presenca"], "Presente")
        outsider = db.save_person("CEv de teste B", "Outra pessoa", "Membro", "", None)
        with self.assertRaises(ValueError):
            db.save_attendance(meeting, outsider, "Liberado")
        with self.assertRaises(ValueError):
            db.save_followup(group, member, "2026-10-02", "Pessoa A", "2026-10-01", "")
        db.save_followup(group, member, "2026-10-02", "Pessoa A", "2026-10-10", "Observação")
        self.assertEqual(db.followups(group)[0]["acompanhador"], "Pessoa A")

    def test_photos_and_publication_targets(self):
        group, _, _ = self.fixture()
        buffer = BytesIO()
        Image.new("RGB", (10, 10), "green").save(buffer, format="PNG")
        raw = buffer.getvalue()
        upload = SimpleNamespace(size=len(raw), getvalue=lambda: raw)
        photo, kind = portal.photo_data(upload)
        self.assertEqual(kind, "PNG")
        db.save_notice("Geral", "Texto público", "Geral", foto=photo, foto_tipo=kind)
        db.save_notice("Do CEv", "Texto CEv", "CEv/Irradiação", "CEv de teste A")
        db.save_notice("Do grupo", "Texto grupo", "Grupo", "CEv de teste A", group)
        self.assertEqual([n["titulo"] for n in db.publications("notices")], ["Geral"])
        self.assertEqual([n["titulo"] for n in db.publications("notices", "CEv de teste A")], ["Do CEv"])
        self.assertEqual([n["titulo"] for n in db.publications("notices", "CEv de teste A", group)], ["Do grupo"])
        with self.assertRaises(ValueError):
            portal.photo_data(SimpleNamespace(size=3, getvalue=lambda: b"abc"))

    def app(self, user):
        content = {"cevs": ["CEv de teste A", "CEv de teste B"], "avisos": [], "campos_pessoa_aprovados": True}
        self.user_patch = patch.object(portal.st, "user", SimpleNamespace(to_dict=lambda: user))
        self.content_patch = patch.object(portal, "load_content", lambda: content)
        self.user_patch.start()
        self.content_patch.start()
        self.addCleanup(self.user_patch.stop)
        self.addCleanup(self.content_patch.stop)
        return AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20).run()

    def page(self, app, key):
        # AppTest.switch_page atende a arquivos; estas páginas usam funções.
        app._page_hash = app.session_state["pages"][key]._script_hash
        app.run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        return app

    def test_public_cannot_see_private_group_records(self):
        group, _, _ = self.fixture()
        app = self.app({})
        app.selectbox(key="cev_sidebar").select("CEv de teste A").run()
        app.session_state["selected_group"] = group
        self.page(app, "group")
        self.assertEqual(len(app.tabs), 0)
        self.assertEqual(len(app.dataframe), 0)
        text = " ".join(x.value for x in app.markdown)
        self.assertNotIn("Contato privado", text)
        self.assertNotIn("Tema privado", text)
        self.assertNotIn("Observação privada", text)

    def test_admin_can_save_group_form_and_navigate(self):
        app = self.app(self.admin)
        app.selectbox(key="cev_sidebar").select("CEv de teste A").run()
        self.page(app, "group_create")
        app.text_input[0].set_value("Grupo criado pela interface")
        phase = next(x for x in app.selectbox if x.label == "Fase do grupo *")
        phase.select("Santidade")
        next(x for x in app.selectbox if x.label == "Dia do encontro").select("Sexta-feira")
        next(button for button in app.button if button.label == "Cadastrar grupo").click().run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        self.assertEqual(db.groups("CEv de teste A")[0]["fase"], "Santidade")
        self.assertEqual(db.groups("CEv de teste A")[0]["dia_encontro"], "Sexta-feira")
        self.fixture()
        for key in ("login", "cev", "group", "person_create", "meeting", "attendance", "followup", "access", "notice", "event_create", "event_manage"):
            self.page(app, key)

    def event_fixture(self, title="Evento de teste"):
        event_id = db.save_event(title, "Retiro", "2026-10-02", "2026-10-03", "Local de teste", "Descrição pública",
                                 "Geral", None, None, None, None, "#177d73", "", self.admin["email"])
        fields = [{"id": "apelido", "label": "Apelido definido pelo organizador", "type": "Texto", "required": True, "options": []}]
        db.execute("UPDATE events SET campos = ? WHERE id = ?", (json.dumps(fields), event_id))
        return event_id

    def test_registration_tokens_and_order_isolation(self):
        event_id = self.event_fixture()
        other_id = self.event_fixture("Outro evento")
        registration_id, token = db.register(event_id, {"apelido": {"campo": "Apelido", "valor": "Pessoa"}})
        self.assertIsNone(db.find_registration(other_id, token))
        self.assertNotEqual(db.find_registration(event_id, token)["token_hash"], token)
        request_id = db.execute("INSERT INTO event_requests (evento_id,titulo,opcoes) VALUES (?,?,?)",
                                (event_id, "Camisa de teste", json.dumps(["P", "M"])))
        db.save_order(event_id, token, request_id, "M", 2)
        db.save_order(event_id, token, request_id, "M", 3)
        self.assertEqual(db.order_rows(event_id, registration_id)[0]["quantidade"], 3)
        self.assertEqual(len(db.order_rows(event_id)), 1)
        with self.assertRaises(ValueError):
            db.save_order(other_id, token, request_id, "M", 1)
        with self.assertRaises(ValueError):
            db.save_order(event_id, token, request_id, "G", 1)
        with self.assertRaises(ValueError):
            db.save_order(event_id, token, request_id, "M", 0)

    def test_public_registration_uses_organizer_fields(self):
        event_id = self.event_fixture()
        app = self.app({})
        app.session_state["selected_event"] = event_id
        self.page(app, "event")
        field = next(w for w in app.text_input if w.label == "Apelido definido pelo organizador *")
        field.set_value("Inscrito de teste")
        next(b for b in app.button if b.label == "Confirmar inscrição").click().run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        rows = db.query("SELECT * FROM registrations WHERE evento_id = ?", (event_id,))
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0]["respostas"])["apelido"]["valor"], "Inscrito de teste")
        self.assertEqual(len(app.dataframe), 0)

    def test_testimonials_require_organizer_approval(self):
        event_id = self.event_fixture()
        db.save_testimonial(event_id, "Pessoa de teste", "Testemunho aguardando moderação")
        app = self.app({})
        app.session_state["selected_event"] = event_id
        self.page(app, "event")
        text = " ".join(x.value for x in app.markdown)
        self.assertNotIn("Testemunho aguardando moderação", text)
        db.execute("UPDATE testimonials SET status = 'Aprovado' WHERE evento_id = ?", (event_id,))
        app.run()
        text = " ".join(x.value for x in app.markdown)
        self.assertIn("Testemunho aguardando moderação", text)

    def test_event_management_tabs(self):
        event_id = self.event_fixture()
        app = self.app(self.admin)
        self.page(app, "event_manage")
        next(w for w in app.selectbox if w.label == "Evento/retiro").select(event_id).run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        self.assertEqual(len(app.tabs), 5)

    def test_person_form_saves_approved_fields(self):
        app = self.app(self.admin)
        app.selectbox(key="cev_sidebar").select("CEv de teste A").run()
        self.page(app, "person_create")
        values = {"Nome *": "Núcleo pela interface", "Contato": "Contato teste", "Instagram": "@teste",
                  "Acompanhador": "Acompanhador teste", "Ministério": "Ministério teste"}
        for widget in app.text_input:
            if widget.label in values:
                widget.set_value(values[widget.label])
        next(w for w in app.text_area if w.label == "Endereço").set_value("Endereço teste")
        next(w for w in app.selectbox if w.label == "Categoria *").select("Núcleo")
        next(b for b in app.button if b.label == "Cadastrar pessoa").click().run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        person = db.people("CEv de teste A")[0]
        self.assertEqual(person["categoria"], "Núcleo")
        self.assertEqual(person["ministerio"], "Ministério teste")
        self.assertEqual(person["acompanhador"], "Acompanhador teste")
        self.assertEqual(person["endereco"], "Endereço teste")

    def test_access_form_authorizes_and_revokes(self):
        app = self.app(self.admin)
        self.page(app, "access")
        next(w for w in app.selectbox if w.label == "Nível de acesso").select("Gestor de CEv").run()
        next(w for w in app.selectbox if w.label == "CEv/Irradiação autorizado").select("CEv de teste A").run()
        next(w for w in app.text_input if w.label == "E-mail da conta Google").set_value("novo@example.com")
        next(b for b in app.button if b.label == "Salvar autorização").click().run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        self.assertEqual(access.profile(claims("novo@example.com"))["nivel"], "Gestor de CEv")
        next(w for w in app.selectbox if w.label == "Conta para revogar acesso").select("novo@example.com")
        next(b for b in app.button if b.label == "Revogar acesso").click().run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        self.assertIsNone(access.profile(claims("novo@example.com")))

    def test_removing_registration_field_preserves_old_answers(self):
        access.set_deletion_permission(self.admin, self.admin["email"], True)
        event_id = self.event_fixture()
        db.register(event_id, {"apelido": {"campo": "Campo antigo", "valor": "Resposta preservada"}})
        app = self.app(self.admin)
        self.page(app, "event_manage")
        next(w for w in app.selectbox if w.label == "Evento/retiro").select(event_id).run()
        next(w for w in app.selectbox if w.label == "Campo para remover").select("apelido")
        next(w for w in app.checkbox if w.label == "Confirmo a remoção deste campo").check()
        next(b for b in app.button if b.label == "Remover campo").click().run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        self.assertEqual(json.loads(db.query("SELECT campos FROM events WHERE id = ?", (event_id,))[0]["campos"]), [])
        response = db.query("SELECT respostas FROM registrations WHERE evento_id = ?", (event_id,))[0]["respostas"]
        self.assertEqual(json.loads(response)["apelido"]["valor"], "Resposta preservada")

    def test_migration_preserves_people_and_group_links(self):
        with db.connection() as conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("ALTER TABLE groups DROP COLUMN dia_encontro")
            conn.executescript("""DROP TABLE people;
                CREATE TABLE people (id INTEGER PRIMARY KEY,cev TEXT NOT NULL,nome TEXT NOT NULL,
                    categoria TEXT CHECK(categoria IN ('Membro','Pastor')),contato TEXT,grupo_id INTEGER REFERENCES groups(id));
                INSERT INTO people VALUES (1,'CEv antigo','Pastor antigo','Pastor','contato',NULL);
                INSERT INTO groups (id,cev,nome,pastor_id,local,horario,fase) VALUES (1,'CEv antigo','Grupo antigo',1,'','','Kerigma');""")
        db.initialize()
        self.assertEqual(db.groups("CEv antigo")[0]["pastor"], "Pastor antigo")
        self.assertEqual(db.groups("CEv antigo")[0]["dia_encontro"], "")
        db.initialize()
        self.assertEqual(len(db.groups("CEv antigo")), 1)
        db.save_person("CEv antigo", "Novo núcleo", "Núcleo", "", 1)
        self.assertEqual(len(db.people("CEv antigo")), 2)
        with db.connection() as conn:
            self.assertFalse(conn.execute("PRAGMA foreign_key_check").fetchall())


if __name__ == "__main__":
    unittest.main()
