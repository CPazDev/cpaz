from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from streamlit.testing.v1 import AppTest
import access
import avisos
import crud
import database as db
import ministerios as model
import portal
import servicos


def claims(email):
    return {'is_logged_in':True,'email_verified':True,'email':email,'exp':time.time()+3600}


class ServicesTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        p=patch.object(db,'DATABASE',Path(temp.name)/'db.sqlite3');p.start();self.addCleanup(p.stop)
        db.initialize()
        self.admin=claims('contatomaicondouglass@gmail.com')
        self.root=model.create(self.admin,'PJJ',ativo=1)
        self.local=model.create(self.admin,'PJJ','Itarema',self.root,1)
        self.other=model.create(self.admin,'PJJ','Paraipaba',self.root,1)
        self.pastoral=model.create(self.admin,'Pastoreio',ativo=1)
        self.pastoral_local=model.create(self.admin,'Pastoreio','Itarema',self.pastoral,1)
        self.group=db.save_group('Itarema','Grupo',None,'','','Kerigma',ativo=1)
        self.foreign=db.save_group('Paraipaba','Outro',None,'','','Kerigma',ativo=1)
        self.person=db.save_person('Itarema','Pessoa','Membro','',self.group,ativo=1,eh_comunidade=0)
        self.coordinator=claims('coord@example.com');self.nucleus=claims('nucleo@example.com')
        servicos.grant(self.admin,self.coordinator['email'],self.local,'Coordenador')
        servicos.grant(self.admin,self.nucleus['email'],self.local,'Núcleo')
    def test_scope_multiple_bindings_and_revocation(self):
        actor=access.profile(self.coordinator)
        self.assertTrue(servicos.can_manage(actor,model.ministry(self.local)))
        self.assertFalse(servicos.can_manage(actor,model.ministry(self.other)))
        servicos.grant(self.admin,self.coordinator['email'],self.other,'Núcleo')
        self.assertEqual(len(access.profile(self.coordinator)['servicos']),2)
        servicos.revoke(self.admin,servicos.grants(self.coordinator['email'])[0]['id'])
        self.assertTrue(servicos.can_manage(access.profile(self.coordinator),model.ministry(self.other)))
        self.assertFalse(servicos.can_manage(access.profile(self.coordinator),model.ministry(self.local)))
    def test_delegation_has_no_escalation(self):
        servicos.grant(self.coordinator,'new@example.com',self.local,'Núcleo')
        for target,role in ((self.other,'Núcleo'),(self.local,'Coordenador'),(self.root,'Núcleo')):
            with self.assertRaises(PermissionError):servicos.grant(self.coordinator,'new@example.com',target,role)
        with self.assertRaises(PermissionError):servicos.grant(self.nucleus,'new@example.com',self.local,'Núcleo')
        general=claims('geral@example.com');servicos.grant(self.admin,general['email'],self.root,'Coordenador')
        servicos.grant(general,'local@example.com',self.other,'Coordenador')
        with self.assertRaises(PermissionError):servicos.grant(general,'new@example.com',self.pastoral,'Coordenador')
    def test_pastoral_scope_and_name_cannot_escalate(self):
        local=claims('pastoreio@example.com');general=claims('pastoreiogeral@example.com')
        servicos.grant(self.admin,local['email'],self.pastoral_local,'Coordenador')
        servicos.grant(self.admin,general['email'],self.pastoral,'Coordenador')
        self.assertTrue(access.can_use_group(access.profile(local),db.query('SELECT * FROM groups WHERE id=?',(self.group,))[0]))
        self.assertFalse(access.can_manage_cev(access.profile(local),'Paraipaba'))
        self.assertTrue(access.can_manage_cev(access.profile(general),'Paraipaba'))
        model.save(self.coordinator,'ministries',self.local,{'nome':'pastoreio'},self.local)
        self.assertFalse(access.can_manage_cev(access.profile(self.coordinator),'Itarema'))
        self.assertFalse(access.can_manage_accounts(access.profile(general)))
    def test_profiles_and_server_record_checks(self):
        self.assertFalse(access.can_view_person(access.profile(self.coordinator),db.people('Itarema')[0]))
        model.save(self.coordinator,'ministry_members',self.local,{'pessoa_id':self.person,'funcao':'Membro','ativo':1})
        self.assertTrue(access.can_view_person(access.profile(self.coordinator),db.people('Itarema')[0]))
        with self.assertRaises(PermissionError):model.save(self.coordinator,'ministry_meetings',self.other,{'data':'2026-09-01','detalhes':'','situacao':None})
        with self.assertRaises(PermissionError):crud.update(self.coordinator,'people',self.person,{'nome':'Mudado'})
    def test_publication_creation_rechecks_permission_under_lock(self):
        with patch.object(access,'_locked_actor',return_value=None) as checked:
            with self.assertRaises(PermissionError):
                crud.create_notice(self.coordinator,'Aviso','','Ministério','Itarema',ministerio_id=self.local)
            with self.assertRaises(PermissionError):
                crud.create_event(self.coordinator,'Evento','Evento','2026-11-01',None,'','','Ministério','Itarema',None,None,None,'#177d73','',ministerio_id=self.local)
            self.assertEqual(checked.call_count,2)
        self.assertFalse(db.query('SELECT * FROM notices'));self.assertFalse(db.query('SELECT * FROM events'))
    def test_public_general_and_private_local_notices(self):
        general=claims('geral@example.com');servicos.grant(self.admin,general['email'],self.root,'Coordenador')
        broad=crud.create_notice(general,'Geral do PJJ','','Ministério',ministerio_id=self.root)
        private=crud.create_notice(self.coordinator,'PJJ local','','Ministério','Itarema',ministerio_id=self.local)
        self.assertIn(broad,[n['id'] for n in avisos.visible({},'Paraipaba')])
        self.assertNotIn(private,[n['id'] for n in avisos.visible({},'Itarema')])
        linked=claims('pessoa@example.com')
        db.execute('INSERT INTO account_profiles(email,membro_id) VALUES(?,?)',(linked['email'],self.person))
        model.save(self.coordinator,'ministry_members',self.local,{'pessoa_id':self.person,'funcao':None,'ativo':None})
        self.assertIn(private,[n['id'] for n in avisos.visible(linked,'Itarema')])
        self.assertNotIn(private,[n['id'] for n in avisos.visible(claims('unknown@example.com'),'Itarema')])
    def test_explicit_deletion_for_records_and_events(self):
        record=model.save(self.coordinator,'ministry_meetings',self.local,{'data':'2026-09-01','detalhes':'','situacao':None})
        with self.assertRaises(PermissionError):model.delete(self.coordinator,'ministry_meetings',record)
        servicos.set_delete(self.admin,self.coordinator['email'],self.local,True)
        model.delete(self.coordinator,'ministry_meetings',record)
        event=crud.create_event(self.coordinator,'Evento','Evento','2026-11-01',None,'','','Ministério','Itarema',None,None,None,'#177d73','',ministerio_id=self.local)
        notice=crud.share_event(self.coordinator,event,'Ministério','Itarema',ministerio_id=self.local)
        self.assertTrue(crud.deletion_info(self.coordinator,'events',event)[0])
        crud.delete(self.coordinator,'events',event)
        self.assertIsNone(db.query('SELECT evento_id FROM notices WHERE id=?',(notice,))[0]['evento_id'])
        with self.assertRaises(PermissionError):servicos.set_delete(self.coordinator,self.nucleus['email'],self.local,True)
    def test_revocation_does_not_remove_other_scopes(self):
        servicos.grant(self.admin,self.coordinator['email'],self.other,'Coordenador')
        servicos.set_delete(self.admin,self.coordinator['email'],self.local,True)
        servicos.set_delete(self.admin,self.coordinator['email'],self.other,True)
        grant=next(r for r in servicos.grants(self.coordinator['email']) if r['ministerio_id']==self.local)
        servicos.revoke(self.admin,grant['id'])
        self.assertEqual([r['ministerio_id'] for r in db.query('SELECT * FROM service_delete_permissions')],[self.other])
    def test_ministry_pages_for_coordinator_and_member(self):
        with patch.object(portal.st,'user',SimpleNamespace(to_dict=lambda:self.coordinator)):
            app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=20).run()
            app._page_hash=app.session_state['pages']['ministries']._script_hash;app.run()
            self.assertFalse(app.exception,[e.message for e in app.exception])
            app.selectbox(key='ministry_picker').select(self.local).run()
            self.assertFalse(app.exception,[e.message for e in app.exception])
            app._page_hash=app.session_state['pages']['service_access']._script_hash;app.run()
            app.selectbox(key='service_access_ministry').select(self.local).run()
            self.assertFalse(app.exception,[e.message for e in app.exception])


if __name__=='__main__':unittest.main()
