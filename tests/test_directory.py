"""Contagens exclusivas e navegação entre ativos, inativos e revisão."""

from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import database as db
import diretorio as directory
import portal


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="directory-tests-")
        self.addCleanup(self.temp.cleanup)
        db_patch = patch.object(db, "DATABASE", Path(self.temp.name) / "test.sqlite3")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        db.initialize()
        self.cev = "CEv de teste"
        self.admin = {"is_logged_in": True, "email_verified": True, "email": "contatomaicondouglass@gmail.com", "exp": time.time() + 3600}
        self.active_group = db.save_group(self.cev, "Grupo ativo", None, "", "", "Kerigma", ativo=1)
        self.inactive_group = db.save_group(self.cev, "Grupo inativo", None, "", "", "Kerigma", ativo=0)
        self.review_group = db.save_group(self.cev, "Grupo sem situação", None, "", "", "Kerigma")
        self.member = db.save_person(self.cev, "Membro da obra", "Membro", "", self.active_group, ativo=1, eh_comunidade=0)
        self.community = db.save_person(self.cev, "Pastor da comunidade", "Pastor", "", self.active_group, ativo=1, eh_comunidade=1)
        self.nucleus = db.save_person(self.cev, "Núcleo da obra", "Núcleo", "", self.active_group, ativo=1, eh_comunidade=0)
        self.inactive = db.save_person(self.cev, "Pessoa inativa", "Membro", "", self.active_group, ativo=0, eh_comunidade=1)
        self.no_status = db.save_person(self.cev, "Situação desconhecida", "Pastor", "", self.active_group, eh_comunidade=0)
        self.no_community = db.save_person(self.cev, "Comunidade desconhecida", "Membro", "", self.active_group, ativo=1)

    def app(self, claims=None):
        user_patch = patch.object(portal.st, "user", SimpleNamespace(to_dict=lambda: self.admin if claims is None else claims))
        user_patch.start()
        self.addCleanup(user_patch.stop)
        content_patch = patch.object(portal, "load_content", lambda: {"cevs": [self.cev], "avisos": [], "campos_pessoa_aprovados": True})
        content_patch.start()
        self.addCleanup(content_patch.stop)
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20).run()
        app.selectbox(key="cev_sidebar").select(self.cev).run()
        self.page(app, "cev")
        return app

    def page(self, app, key):
        app._page_hash = app.session_state["pages"][key]._script_hash
        app.run()
        self.assertFalse(app.exception, [e.message for e in app.exception])

    def click(self, app, key):
        app.button(key=key).click().run()
        self.assertFalse(app.exception, [e.message for e in app.exception])

    def test_counts_have_no_overlap_and_unknowns_are_reviewed(self):
        people, groups = directory.summary(db.people(self.cev), db.groups(self.cev))
        self.assertEqual([len(people[c]) for c in (directory.MEMBERS, directory.COMMUNITY, directory.LEADERS, "Inativos", "Revisão")], [1, 1, 1, 1, 2])
        ids = [p["id"] for rows in people.values() for p in rows]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), {p["id"] for p in db.people(self.cev)})
        self.assertEqual([len(groups[c]) for c in directory.TABS], [1, 1, 1, 0])
        self.assertNotIn(self.community, [p["id"] for p in people[directory.LEADERS]])

    def test_clicking_indicators_opens_lists_including_community_leaders(self):
        app = self.app()
        key = "directory_" + self.cev
        self.assertEqual([tab.label for tab in app.tabs], list(directory.TABS))
        initial_names = set(app.dataframe[0].value["Nome"])
        self.assertEqual(initial_names, {"Membro da obra", "Pastor da comunidade", "Núcleo da obra"})
        self.assertNotIn("Grupo inativo", [h.value for h in app.subheader])
        for label, expected in ((directory.MEMBERS, {"Membro da obra"}),
                                (directory.COMMUNITY, {"Pastor da comunidade"}),
                                (directory.LEADERS, {"Núcleo da obra", "Pastor da comunidade"})):
            self.click(app, key + "_indicator_" + label)
            self.assertEqual(app.session_state[key + "_tabs"], "Ativos")
            self.assertEqual(app.selectbox(key=key + "_filter").value, label)
            self.assertEqual(set(app.dataframe[0].value["Nome"]), expected)
            self.assertFalse(any(h.value == "Grupo ativo" for h in app.subheader))
        self.assertEqual(app.button(key=key + "_indicator_" + directory.LEADERS).label,
                         "**1** " + directory.LEADERS)
        self.assertIn(directory.LEADERS + " · 2", [h.value for h in app.subheader])
        self.click(app, key + "_indicator_" + directory.GROUPS)
        self.assertEqual(len(app.dataframe), 0)
        self.assertIn("Grupo ativo", [h.value for h in app.subheader])

    def test_leaders_list_includes_community_nucleus_and_excludes_inactive_and_review(self):
        community_nucleus = db.save_person(self.cev, "Núcleo da comunidade", "Núcleo", "",
                                           self.active_group, ativo=1, eh_comunidade=1)
        db.save_person(self.cev, "Pastor inativo", "Pastor", "", self.active_group,
                       ativo=0, eh_comunidade=1)
        db.save_person(self.cev, "Pastor sem pertencimento", "Pastor", "", self.active_group, ativo=1)
        db.save_person(self.cev, "Membro da comunidade", "Membro", "", self.active_group,
                       ativo=1, eh_comunidade=1)
        self.assertEqual({p["id"] for p in directory.leaders(db.people(self.cev))},
                         {self.community, self.nucleus, community_nucleus})
        app = self.app()
        key = "directory_" + self.cev
        app.selectbox(key=key + "_filter").select(directory.LEADERS).run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        self.assertEqual(set(app.dataframe[0].value["Nome"]),
                         {"Pastor da comunidade", "Núcleo da obra", "Núcleo da comunidade"})
        self.assertEqual(app.button(key=key + "_indicator_" + directory.COMMUNITY).label,
                         "**3** " + directory.COMMUNITY)
        self.assertEqual(app.button(key=key + "_indicator_" + directory.LEADERS).label,
                         "**1** " + directory.LEADERS)

    def test_inactive_and_review_navigation_then_return_to_active_indicator(self):
        app = self.app()
        key = "directory_" + self.cev
        self.click(app, key + "_shortcut_Inativos")
        self.assertEqual(app.session_state[key + "_tabs"], "Inativos")
        self.assertEqual(list(app.dataframe[0].value["Nome"]), ["Pessoa inativa"])
        self.assertIn("Grupo inativo", [h.value for h in app.subheader])
        app.selectbox(key=key + "_person").select(self.inactive).run()
        self.assertTrue(any(button.label == "Editar esta pessoa" for button in app.button))
        self.click(app, key + "_shortcut_Revisão")
        self.assertEqual(set(app.dataframe[0].value["Nome"]), {"Comunidade desconhecida", "Situação desconhecida"})
        self.assertIn("Grupo sem situação", [h.value for h in app.subheader])
        self.click(app, key + "_indicator_" + directory.COMMUNITY)
        self.assertEqual(app.session_state[key + "_tabs"], "Ativos")
        self.assertEqual(list(app.dataframe[0].value["Nome"]), ["Pastor da comunidade"])

    def test_archived_group_can_be_opened_by_management_and_is_hidden_publicly(self):
        app = self.app()
        key = "directory_" + self.cev
        self.click(app, key + "_shortcut_Inativos")
        self.click(app, f'{key}_Inativos_open_group_{self.inactive_group}')
        self.assertEqual(app.session_state["selected_group"], self.inactive_group)
        self.assertEqual(app.selectbox(key="group_page_picker").value, self.inactive_group)
        # O seletor não inclui outros grupos com situação desconhecida.
        self.assertNotIn(self.review_group, app.selectbox(key="group_page_picker").options)

    def test_public_does_not_render_private_counts_or_archived_groups(self):
        app = self.app({})
        groups = next(w for w in app.selectbox if w.label == "Grupo para consultar")
        self.assertIn("Grupo ativo", groups.options)
        self.assertNotIn("Grupo inativo", groups.options)
        self.assertNotIn("Grupo sem situação", groups.options)
        self.assertEqual(len(app.tabs), 0)
        self.assertEqual(len(app.dataframe), 0)
        app.session_state["selected_group"] = self.inactive_group
        self.page(app, "group")
        self.assertEqual(len(app.tabs), 0)
        self.assertEqual(len(app.dataframe), 0)
        self.assertNotIn("Grupo inativo", app.selectbox(key="group_page_picker").options)

    def test_group_people_list_separates_inactives(self):
        app = self.app()
        app.session_state["selected_group"] = self.active_group
        self.page(app, "group")
        self.assertEqual(set(app.dataframe[0].value["Nome"]), {"Membro da obra", "Pastor da comunidade", "Núcleo da obra"})
        app.session_state[f'group_people_{self.active_group}_tabs'] = "Inativos"
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(list(app.dataframe[0].value["Nome"]), ["Pessoa inativa"])

    def test_neutral_links_are_not_groups_and_survive_rename(self):
        import crud
        neutral = db.save_group(self.cev, "CAL", None, "", "", "Permanente", ativo=1, neutro=True)
        db.save_group(self.cev, "C. Vida", None, "", "", "Permanente", ativo=0, neutro=True)
        db.save_person(self.cev, "Pessoa sem grupo", "Pastor", "", neutral, ativo=1, eh_comunidade=1)
        people, groups = directory.summary(db.people(self.cev), db.groups(self.cev))
        self.assertEqual(len(groups["Ativos"]), 1)
        self.assertEqual([g["id"] for g in groups[directory.NEUTRAL]], [neutral])
        self.assertEqual(len(groups["Inativos"]), 2)
        self.assertEqual(len(people[directory.COMMUNITY]), 2)
        crud.update(self.admin, "groups", neutral, {"nome": "Vínculo renomeado"})
        _, groups = directory.summary(db.people(self.cev), db.groups(self.cev))
        self.assertEqual(len(groups["Ativos"]), 1)
        self.assertEqual(groups[directory.NEUTRAL][0]["nome"], "Vínculo renomeado")
        crud.update(self.admin, "groups", neutral, {"neutro": False})
        _, groups = directory.summary(db.people(self.cev), db.groups(self.cev))
        self.assertEqual(len(groups["Ativos"]), 2)
        with self.assertRaises(ValueError):
            crud.update(self.admin, "groups", neutral, {"neutro": "1"})

    def test_neutral_tab_and_member_link_remain_accessible(self):
        neutral = db.save_group(self.cev, "CAL", None, "", "", "Permanente", ativo=1, neutro=True)
        app = self.app()
        key = "directory_" + self.cev
        self.assertNotIn("CAL", [h.value for h in app.subheader])
        self.click(app, key + "_shortcut_" + directory.NEUTRAL)
        self.assertIn("CAL", [h.value for h in app.subheader])
        self.click(app, f'{key}_{directory.NEUTRAL}_open_group_{neutral}')
        self.assertEqual(app.selectbox(key="group_page_picker").value, neutral)
        self.page(app, "person_create")
        group_picker = next(w for w in app.selectbox if w.label == "Grupo")
        self.assertTrue(any("CAL" in option and "Vínculo neutro" in option for option in group_picker.options))
        group_picker.select(neutral)
        next(w for w in app.text_input if w.label == "Nome *").set_value("Pessoa vinculada a CAL")
        next(w for w in app.selectbox if w.label == "Categoria *").select("Membro")
        next(b for b in app.button if b.label == "Cadastrar pessoa").click().run()
        self.assertFalse(app.exception)
        linked = next(p for p in db.people(self.cev) if p["nome"] == "Pessoa vinculada a CAL")
        self.assertEqual(linked["grupo_id"], neutral)

    def test_public_does_not_offer_neutral_links(self):
        neutral = db.save_group(self.cev, "CAL", None, "", "", "Permanente", ativo=1, neutro=True)
        app = self.app({})
        self.assertNotIn("CAL", [b.label for b in app.button])
        app.session_state["selected_group"] = neutral
        self.page(app, "group")
        self.assertFalse(any("CAL" in option for option in app.selectbox(key="group_page_picker").options))


if __name__ == "__main__":
    unittest.main()
