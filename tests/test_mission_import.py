from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import database as db
from importar_missao import Importer
from test_import import fixture


class MissionImportTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        p=patch.object(db,'DATABASE',self.root/'db.sqlite3');p.start();self.addCleanup(p.stop);db.initialize()
        self.book=self.root/'test.xlsx';fixture(self.book,[],[])
        self.sheets={
            'Grupos':[{'_row':2,'ID_Grupo':'g1','Nome':'Grupo','Fase':'Kerigma','Ativo':'1'}],
            'Pessoas':[{'_row':2,'ID_Pessoa':'p1','Nome':'Mesmo nome','Função':'Membro','Grupo':'g1','Ativo':'1','EhComunidade':'0'},
                       {'_row':3,'ID_Pessoa':'p2','Nome':'Mesmo nome','Função':'Pastor','Grupo':'missing','DataNascimento':'2099-01-01'}],
            'Ministerios':[{'_row':2,'ID_Ministerio':'m1','Nome':'PJJ','Ativo':'1'}],
            'MinisterioPessoa':[{'_row':2,'ID_MinisterioPessoa':'v1','Ministerio':'m1','Pessoa':'p1'}],
            'Encontros':[{'_row':2,'ID_Encontro':'e1','Grupo':'g1','Data':'2026-09-01','Detalhes':'Origem'}],
            'Frequencia':[{'_row':2,'ID_Frequencia':'f1','Encontro':'e1','Pessoa':'p1','Status':'Presente'}],
            'Pessoa_Grupo_Mes':[{'_row':2,'Pessoa':'p1','Grupo':'g1','Mês':'09/2026'}],
            'UsuariosPermissoes':[{'_row':2,'ID_Usuario':'u1','Email':'legacy@example.com','Papel':'CAP','Ativo':'1'}],
        }
    def run_import(self):
        with patch('importar_missao.read_workbook',return_value=('reviewed-sha',False,self.sheets)),db.connection() as conn:
            importer=Importer(conn);importer.workbook(self.book,'Itarema');return importer.report
    def test_distinct_ids_same_names_future_birth_and_missing_group(self):
        report=self.run_import();people=db.people('Itarema')
        self.assertEqual(len(people),2);self.assertFalse(report['pendencias'])
        pastor=next(p for p in people if p['categoria']=='Pastor')
        self.assertIsNone(pastor['nascimento']);self.assertIsNone(pastor['grupo_id'])
        self.assertFalse(db.query('SELECT * FROM access_grants'))
        self.assertEqual(db.query('SELECT mes FROM person_group_months')[0]['mes'],'2026-09')
        self.assertIsNone(db.query('SELECT funcao,ativo FROM ministry_members')[0]['funcao'])
    def test_repeat_preserves_manual_changes_and_no_duplicates(self):
        self.run_import();person=db.people('Itarema')[0]
        db.execute('UPDATE people SET nome=?,ministerio=? WHERE id=?',('Nome corrigido','PJJ; Manual',person['id']))
        report=self.run_import()
        self.assertFalse(report['inseridos']);self.assertEqual(len(db.people('Itarema')),2)
        self.assertEqual(db.query('SELECT nome,ministerio FROM people WHERE id=?',(person['id'],))[0],{'nome':'Nome corrigido','ministerio':'Manual; PJJ'})
        self.assertEqual(len(db.query('SELECT * FROM attendance')),1)
    def test_deleted_person_is_not_recreated(self):
        self.sheets['MinisterioPessoa']=[];self.sheets['Frequencia']=[];self.sheets['Pessoa_Grupo_Mes']=[]
        self.run_import();person=db.people('Itarema')[0]
        db.execute('DELETE FROM people WHERE id=?',(person['id'],))
        report=self.run_import()
        self.assertEqual(len(db.people('Itarema')),1)
        self.assertTrue(any('excluído' in p['motivo'] for p in report['pendencias']))
    def test_conflicting_attendance_is_quarantined(self):
        self.sheets['Frequencia'].append({'_row':3,'ID_Frequencia':'f2','Encontro':'e1','Pessoa':'p1','Status':'Ausente'})
        report=self.run_import()
        self.assertFalse(db.query('SELECT * FROM attendance'))
        self.assertEqual(sum(p['aba']=='Frequencia' for p in report['pendencias']),2)
