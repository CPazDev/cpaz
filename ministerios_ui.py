"""Gestão e consulta dos ministérios com escopo validado no servidor."""
from datetime import date
import sqlite3
import streamlit as st
import access
import avisos
import database as db
import ministerios as model
import servicos


def ui():
    import portal
    return portal


def options(actor):
    email = access.verified_email(st.user.to_dict())
    linked = {r['ministerio_id'] for r in db.query('''SELECT v.ministerio_id FROM ministry_members v
        JOIN account_profiles p ON p.membro_id=v.pessoa_id WHERE p.email=? AND (v.ativo IS NULL OR v.ativo=1)''', (email,))} if email else set()
    return [m for m in db.query('SELECT * FROM ministries ORDER BY nome,cev')
            if m['cev'] is None or m['id'] in linked or servicos.can_manage(actor, m)]


def choose(rows, label='Ministério', key='ministry_picker'):
    labels = {m['id']: m['nome']+' · '+(m['cev'] or 'Geral') for m in rows}
    selected = st.selectbox(label, list(labels), format_func=labels.get, index=None, key=key)
    return next((m for m in rows if m['id'] == selected), None)


def _status(label, value, key):
    return st.selectbox(label, [None,1,0], index=[None,1,0].index(value),
                        format_func=lambda v: 'Não informado' if v is None else 'Ativo' if v else 'Inativo', key=key)


def _record_form(table, target, rows):
    names = {'ministry_members':'Vínculos', 'ministry_meetings':'Registros',
             'ministry_attendance':'Frequência', 'ministry_followups':'Acompanhamentos'}
    st.subheader(names[table])
    labels={p['id']:p['nome'] for p in db.query('SELECT id,nome FROM people')}
    columns={'ministry_members':{'pessoa_id':'Pessoa','funcao':'Função','ativo':'Situação'},
             'ministry_meetings':{'data':'Data','detalhes':'Detalhes','situacao':'Situação'},
             'ministry_attendance':{'registro_id':'Encontro','pessoa_id':'Pessoa','presenca':'Presença'},
             'ministry_followups':{'pessoa_id':'Pessoa','data':'Data','acompanhador':'Acompanhador',
                                   'proximo_acompanhamento':'Próximo acompanhamento','observacoes':'Observações'}}
    display=[]
    meetings = {r['id']:ui().date_label(r['data'])+' · '+r['detalhes'][:60]
                for r in db.query('SELECT id,data,detalhes FROM ministry_meetings WHERE ministerio_id=?', (target['id'],))}
    for row in rows:
        display.append({label:labels.get(row[key],'Pessoa não localizada') if key=='pessoa_id' else meetings.get(row[key],'Encontro não localizado') if key=='registro_id' else ui().status_label(row[key]) if key=='ativo' else ui().date_label(row[key]) if key in ('data','proximo_acompanhamento') else row[key]
                        for key,label in columns[table].items()})
    st.dataframe(display, hide_index=True, width='stretch')
    identity = st.selectbox('Registro para editar', [None]+[r['id'] for r in rows],
                            format_func=lambda v: 'Novo registro' if v is None else f'#{v}', key=table+'_record')
    old = next((r for r in rows if r['id'] == identity), {})
    people = db.query('SELECT id,nome,cev FROM people ORDER BY nome')
    if target['cev']:
        people = [p for p in people if p['cev'] == target['cev']]
    if table in ('ministry_attendance','ministry_followups'):
        enrolled = {r['pessoa_id'] for r in db.query('SELECT pessoa_id FROM ministry_members WHERE ministerio_id=?', (target['id'],))}
        # Um vínculo histórico continua selecionável para editar seu registro.
        people = [p for p in people if p['id'] in enrolled or p['id'] == old.get('pessoa_id')]
    with st.form(table+'_form'):
        values = {}
        if table != 'ministry_meetings':
            labels = {p['id']:p['nome']+' · '+p['cev'] for p in people}
            ids = list(labels)
            values['pessoa_id'] = st.selectbox('Pessoa', ids, index=ids.index(old['pessoa_id']) if old.get('pessoa_id') in ids else None,
                                              format_func=labels.get)
        if table == 'ministry_members':
            roles = [None,'Membro','Núcleo','Coordenador']
            values['funcao'] = st.selectbox('Função', roles, index=roles.index(old.get('funcao')), format_func=lambda v:v or 'Não informado')
            values['ativo'] = _status('Situação do vínculo', old.get('ativo'), table+'_active')
        if table == 'ministry_attendance':
            meetings = {r['id']:r['data']+' · '+r['detalhes'][:60] for r in db.query('SELECT * FROM ministry_meetings WHERE ministerio_id=? ORDER BY data DESC', (target['id'],))}
            ids = list(meetings)
            values['registro_id'] = st.selectbox('Registro', ids, index=ids.index(old['registro_id']) if old.get('registro_id') in ids else None, format_func=meetings.get)
            values['presenca'] = st.selectbox('Presença', db.PRESENCES, index=db.PRESENCES.index(old.get('presenca')) if old.get('presenca') in db.PRESENCES else None)
        if table in ('ministry_meetings','ministry_followups'):
            selected_date = st.date_input('Data', value=date.fromisoformat(old['data']) if old.get('data') else ui().today(), format='DD/MM/YYYY')
            values['data'] = selected_date.isoformat()
        if table == 'ministry_meetings':
            values['detalhes'] = st.text_area('Detalhes', old.get('detalhes',''))
            values['situacao'] = st.text_input('Situação', old.get('situacao') or '') or None
        if table == 'ministry_followups':
            values['acompanhador'] = st.text_input('Acompanhador', old.get('acompanhador',''))
            upcoming = st.date_input('Próximo acompanhamento', value=date.fromisoformat(old['proximo_acompanhamento']) if old.get('proximo_acompanhamento') else None, format='DD/MM/YYYY')
            values['proximo_acompanhamento'] = upcoming.isoformat() if upcoming else None
            values['observacoes'] = st.text_area('Observações', old.get('observacoes',''))
        save = st.form_submit_button('Salvar', type='primary')
    if save:
        try:
            model.save(st.user.to_dict(), table, target['id'], values, identity)
        except (ValueError, PermissionError, sqlite3.IntegrityError) as exc:
            st.error(str(exc))
        else:
            ui().saved('Registro salvo.')
    if identity:
        with db.connection() as conn:
            allowed = servicos.can_delete(access._locked_actor(conn, st.user.to_dict()), target, conn)
        if allowed:
            confirmed = st.checkbox('Confirmo excluir este registro', key=table+'_confirm_delete')
            if st.button('Excluir registro', disabled=not confirmed, key=table+'_delete'):
                try:
                    model.delete(st.user.to_dict(), table, identity)
                except (ValueError,PermissionError) as exc:
                    st.error(str(exc))
                else:
                    ui().saved('Registro excluído.')


def page():
    ui().header('Ministérios', 'Serviços da missão e dos CEvs.')
    actor = access.profile(st.user.to_dict())
    if actor and (actor['nivel'] in ('Administrador','Gestor de CEv') or any(v['papel']=='Coordenador' for v in actor.get('servicos',()))):
        with st.expander('Cadastrar ministério'):
            roots=[m for m in db.query('SELECT * FROM ministries WHERE cev IS NULL ORDER BY nome')
                   if actor['nivel'] in ('Administrador','Gestor de CEv') or any(v['ministerio_id']==m['id'] and v['papel']=='Coordenador' for v in actor.get('servicos',()))]
            general=st.checkbox('Ministério Geral',disabled=actor['nivel']!='Administrador')
            cevs=ui().load_content()['cevs'] if actor['nivel']=='Administrador' or actor['nivel']=='Serviço' else [actor['cev']]
            cev=st.selectbox('CEv do ministério',cevs,index=None,disabled=general)
            root=choose(roots,'Ministério Geral ao qual está ligado','ministry_create_parent') if not general else None
            with st.form('ministry_create'):
                name=st.text_input('Nome do ministério')
                active=_status('Situação inicial',None,'ministry_create_active')
                create=st.form_submit_button('Cadastrar ministério')
            if create:
                try:
                    model.create(st.user.to_dict(),name,None if general else cev,None if general else root['id'] if root else None,active)
                except (ValueError,PermissionError,sqlite3.IntegrityError) as exc:
                    st.error(str(exc))
                else:
                    ui().saved('Ministério cadastrado.')
    target = choose(options(actor))
    if not target:
        return
    if target['foto']:
        st.image(target['foto'], width=240)
    st.caption(target['cev'] or 'Ministério Geral da missão')
    if target['parent_id']:
        st.caption('Ligado a '+model.ministry(target['parent_id'])['nome']+' Geral')
    can_manage = servicos.can_manage(actor, target)
    st.subheader('Avisos')
    avisos.render(st.user.to_dict(), target['cev'], ministry_id=target['id'])
    st.subheader('Eventos e retiros')
    root = target['parent_id'] or target['id']
    for event in db.query("SELECT * FROM events WHERE destino='Ministério' AND (ministerio_id=? OR ministerio_id=?) ORDER BY id DESC", (target['id'], root)):
        st.write(event['titulo'])
        if st.button('Ver evento/retiro', key='ministry_event_'+str(event['id'])):
            st.session_state['selected_event'] = event['id']
            st.switch_page(st.session_state['pages']['event'])
    if not can_manage:
        st.caption('Os registros internos são restritos à gestão autorizada.')
        return
    with st.expander('Informações do ministério'):
        with st.form('ministry_details'):
            name = st.text_input('Nome', target['nome'])
            active = _status('Situação', target['ativo'], 'ministry_active')
            photo = st.file_uploader('Foto', type=['png','jpg','jpeg','webp'])
            remove_photo = st.checkbox('Remover foto atual') if target['foto'] else False
            save = st.form_submit_button('Salvar informações')
        if save:
            try:
                data = {'nome': name, 'ativo': active}
                if photo:
                    data['foto'], data['foto_tipo'] = ui().photo_data(photo)
                elif remove_photo:
                    data['foto'],data['foto_tipo']=None,None
                model.save(st.user.to_dict(), 'ministries', target['id'], data, target['id'])
            except (ValueError,PermissionError,sqlite3.IntegrityError) as exc:
                st.error(str(exc))
            else:
                ui().saved('Ministério atualizado.')
        with db.connection() as conn:
            deletable=servicos.can_delete(access._locked_actor(conn,st.user.to_dict()),target,conn)
        if deletable:
            confirmed=st.checkbox('Confirmo excluir este ministério e já revisei seus vínculos',key='ministry_delete_confirm')
            if st.button('Excluir ministério',disabled=not confirmed):
                try:
                    model.delete(st.user.to_dict(),'ministries',target['id'])
                except (PermissionError,ValueError) as exc:
                    st.error(str(exc))
                else:
                    ui().saved('Ministério excluído.')
    section = st.selectbox('Registros do ministério', ['Membros','Encontros','Frequência','Acompanhamentos'])
    table = {'Membros':'ministry_members','Encontros':'ministry_meetings','Frequência':'ministry_attendance','Acompanhamentos':'ministry_followups'}[section]
    rows = db.query(f'SELECT * FROM {table} WHERE ministerio_id=? ORDER BY id DESC', (target['id'],))
    _record_form(table, target, rows)
    if section == 'Membros':
        import diretorio
        people = db.query('SELECT p.*,g.nome AS grupo FROM people p LEFT JOIN groups g ON g.id=p.grupo_id WHERE p.id IN (SELECT pessoa_id FROM ministry_members WHERE ministerio_id=?)', (target['id'],))
        diretorio.person_list(people, 'ministry_people')


def access_page():
    ui().header('Acessos de serviços', 'Uma conta pode ter mais de um vínculo de coordenação ou núcleo.')
    actor = access.profile(st.user.to_dict())
    if not actor:
        st.info('Entre com uma conta autorizada.')
        return
    targets = [m for m in db.query('SELECT * FROM ministries ORDER BY nome,cev')
               if any(servicos.can_delegate(actor,m,r) for r in ('Coordenador','Núcleo'))]
    target = choose(targets, key='service_access_ministry')
    if not target:
        st.info('Selecione um ministério dentro do seu alcance de delegação.')
        return
    with st.form('service_access_form'):
        email = st.text_input('E-mail da conta Google')
        role = st.selectbox('Papel', [r for r in ('Coordenador','Núcleo') if servicos.can_delegate(actor,target,r)])
        submit = st.form_submit_button('Adicionar vínculo')
    if submit:
        try:
            servicos.grant(st.user.to_dict(),email,target['id'],role)
        except (ValueError,PermissionError) as exc:
            st.error(str(exc))
        else:
            ui().saved('Vínculo concedido.')
    rows = db.query('SELECT * FROM service_grants WHERE ministerio_id=? ORDER BY email,papel', (target['id'],))
    ui().records(rows, {'email':'Conta','papel':'Papel'}, 'Nenhum vínculo de gestão.')
    revocable = {r['id']:r['email']+' · '+r['papel'] for r in rows if servicos.can_delegate(actor,target,r['papel'])}
    selected = st.selectbox('Vínculo para revogar', list(revocable), index=None, format_func=revocable.get)
    if st.button('Revogar vínculo', disabled=selected is None):
        try:
            servicos.revoke(st.user.to_dict(),selected)
        except PermissionError as exc:
            st.error(str(exc))
        else:
            ui().saved('Vínculo revogado.')
    if actor['email'] in access.ADMIN_EMAILS:
        st.subheader('Permissão explícita de exclusão')
        with st.form('service_delete_form'):
            email = st.text_input('Conta autorizada para exclusão')
            allow = st.checkbox('Permitir exclusão neste ministério')
            save = st.form_submit_button('Salvar permissão de exclusão')
        if save:
            try:
                servicos.set_delete(st.user.to_dict(),email.strip().lower(),target['id'],allow)
            except (PermissionError,ValueError) as exc:
                st.error(str(exc))
            else:
                ui().saved('Permissão de exclusão atualizada.')
