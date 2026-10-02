"""Importa a missão numa cópia, aplica diferenças após revisão de integridade.

Não publica dados nem ativa permissões legadas. Sem --apply, só simula.
"""
import argparse
from collections import Counter, defaultdict
from datetime import date, datetime
import hashlib
import json
import re
from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ET
from zipfile import ZipFile

import database as db
from importar_planilha import read_workbook, excel_date, boolean, phone, NS
from ministerios import sync_member

EXCLUDED = {'Registro CB', 'FrequenciaCB'}


def snapshot(conn):
    data = {}
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        rows = [dict(r) for r in conn.execute(f'SELECT * FROM "{table}"')]
        data[table] = sorted(rows, key=lambda r:json.dumps(r,sort_keys=True,default=lambda b:hashlib.sha256(b).hexdigest()))
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=lambda b:hashlib.sha256(b).hexdigest()).encode()).hexdigest(), data


class Importer:
    def __init__(self, conn):
        self.conn = conn
        self.report = {'arquivos': [], 'inseridos': Counter(), 'pendencias': [], 'avisos': [], 'excluidos': sorted(EXCLUDED)}
        self.source = None
        self.digest = None
        self.maps = {}
    def archive(self, sheet, identity, row, status, reason=''):
        self.conn.execute('''INSERT INTO source_archive(source,sheet,source_id,sha256,original,status,reason)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(source,sheet,source_id) DO UPDATE SET
            sha256=excluded.sha256,original=excluded.original,status=excluded.status,reason=excluded.reason''',
            (self.source,sheet,identity,self.digest,json.dumps(row,ensure_ascii=False),status,reason))
        if status == 'Pendente':
            self.report['pendencias'].append({'origem':self.source,'aba':sheet,'linha':row['_row'],'motivo':reason})
    def linked(self, entity, identity):
        target = self.maps.get((entity, identity))
        if target is None:
            return None
        if not self.conn.execute(f'SELECT 1 FROM {entity} WHERE id=?', (target,)).fetchone():
            raise ValueError('Cadastro anteriormente importado foi excluído; não será recriado.')
        return target
    def resolve(self, entity, identity, required=True):
        identity = str(identity or '').strip()
        target = self.linked(entity,identity) if identity else None
        if target is None and required:
            raise ValueError(f'Referência ausente em {entity}.')
        return target
    def insert(self, entity, identity, data):
        found = self.linked(entity,identity)
        if found is not None:
            return found
        columns = list(data)
        target = self.conn.execute(f'INSERT INTO {entity} ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')',tuple(data.values())).lastrowid
        self.bind(entity,identity,target)
        self.report['inseridos'][entity] += 1
        return target
    def bind(self, entity, identity, target):
        self.conn.execute('INSERT INTO import_links(source,entity,source_id,target_id) VALUES(?,?,?,?) ON CONFLICT(source,entity,source_id) DO NOTHING',
                          (self.source,entity,identity,target))
        self.maps[(entity,identity)] = target
    def date(self, value, required=False):
        result = excel_date(value,self.epoch)
        if required and not result:
            raise ValueError('Data obrigatória ausente.')
        return result
    def process(self, sheet, key, callback):
        rows = self.sheets.get(sheet, [])
        counts = Counter(r.get(key,'') for r in rows if r.get(key)) if key else Counter()
        for row in rows:
            identity = str(row.get(key,'') or f'linha:{row["_row"]}')
            prior_maps = self.maps.copy()
            prior_counts = self.report['inseridos'].copy()
            prior_warnings = len(self.report['avisos'])
            self.conn.execute('SAVEPOINT import_row')
            try:
                if key and (not row.get(key) or counts[row[key]] > 1):
                    raise ValueError('ID ausente ou duplicado.')
                callback(identity,row)
            except (ValueError,sqlite3.IntegrityError) as exc:
                self.conn.execute('ROLLBACK TO SAVEPOINT import_row')
                self.maps = prior_maps
                self.report['inseridos'] = prior_counts
                del self.report['avisos'][prior_warnings:]
                self.archive(sheet,identity,row,'Pendente',str(exc))
            else:
                self.archive(sheet,identity,row,'Importado')
            finally:
                self.conn.execute('RELEASE SAVEPOINT import_row')
    def workbook(self, path, cev):
        with ZipFile(path) as z:
            names = [s.get('name') for s in ET.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet',NS)]
        self.digest,self.epoch,self.sheets = read_workbook(path,{name:set() for name in names if name not in EXCLUDED})
        self.source = f'CPaz:{cev}'
        existing_people_names = {r[0].strip().casefold() for r in self.conn.execute('SELECT nome FROM people WHERE cev=?',(cev,))}
        old_group_ids = {r[0] for r in self.conn.execute('SELECT id FROM groups WHERE cev=?',(cev,))}
        self.report['arquivos'].append({'arquivo':path.name,'cev':cev,'sha256':self.digest})
        self.maps = {(r['entity'],r['source_id']):r['target_id'] for r in self.conn.execute('SELECT * FROM import_links WHERE source=?',(self.source,))}
        if self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='import_records'").fetchone():
            self.maps.update({(r['entity'],r['source_id']):r['target_id'] for r in self.conn.execute('SELECT * FROM import_records WHERE source=?',(self.source,))})
        def group(identity,row):
            if self.linked('groups',identity) is not None:return
            name = row.get('Nome','').strip()
            if not name or row.get('Fase') not in db.PHASES:raise ValueError('Nome ou fase inválida.')
            if self.conn.execute('SELECT 1 FROM groups WHERE cev=? AND lower(nome)=lower(?)',(cev,name)).fetchone():raise ValueError('Grupo existente sem vínculo de origem; revisar correspondência.')
            neutral = name.casefold().replace(' ','').replace('.','') in ('cal','cvida')
            self.insert('groups',identity,{'cev':cev,'nome':name,'fase':row['Fase'],'ativo':boolean(row.get('Ativo')),
                'data_inicio':self.date(row.get('DataInicio')),'publico':row.get('Publico',''),'neutro':int(neutral)})
        self.process('Grupos','ID_Grupo',group)
        people_source = {r.get('ID_Pessoa'):r for r in self.sheets.get('Pessoas',[]) if r.get('ID_Pessoa')}
        def person(identity,row):
            if self.linked('people',identity) is not None:return
            name = row.get('Nome','').strip()
            if not name or row.get('Função') not in db.CATEGORIES:raise ValueError('Nome ou categoria ausente/inválida.')
            if name.casefold() in existing_people_names:raise ValueError('Pessoa existente sem vínculo de origem; revisar correspondência.')
            group_id = self.resolve('groups',row.get('Grupo'),False)
            if row.get('Grupo') and group_id is None:self.report['avisos'].append({'origem':self.source,'linha':row['_row'],'motivo':'Pessoa importada sem grupo: referência ausente.'})
            birth = self.date(row.get('DataNascimento'))
            if birth and birth > date.today().isoformat():
                birth = None
                self.report['avisos'].append({'origem':self.source,'linha':row['_row'],'motivo':'Nascimento futuro importado em branco.'})
            companion = people_source.get(row.get('Acompanhador'),{}).get('Nome','')
            self.insert('people',identity,{'cev':cev,'nome':name,'categoria':row['Função'],'contato':phone(row.get('Telefone')),
                'grupo_id':group_id,'instagram':row.get('Instagram',''),'email':row.get('Email',''),'nascimento':birth,
                'acompanhador':companion,'genero':row.get('Genero',''),'servico':row.get('Serviço',''),
                'funcao_servico':row.get('Funcao_Serv',row.get('Função do Serviço','')),
                'eh_comunidade':boolean(row.get('EhComunidade')),'ativo':boolean(row.get('Ativo'))})
        self.process('Pessoas','ID_Pessoa',person)
        # Só completar o pastor de grupos novos; grupos do portal conservam sua gestão.
        for identity,row in [(r.get('ID_Grupo'),r) for r in self.sheets.get('Grupos',[])]:
            target = self.maps.get(('groups',identity))
            if not target or target in old_group_ids:continue
            record = self.conn.execute('SELECT pastor_id FROM groups WHERE id=?',(target,)).fetchone()
            if not record or record['pastor_id'] is not None:continue
            candidates = [self.maps.get(('people',pid)) for pid,p in people_source.items() if p.get('Grupo')==identity and p.get('Função')=='Pastor']
            candidates = [p for p in candidates if p and self.conn.execute('SELECT 1 FROM people WHERE id=?',(p,)).fetchone()]
            if len(candidates)==1:self.conn.execute('UPDATE groups SET pastor_id=? WHERE id=?',(candidates[0],target))
        def ministry(identity,row):
            if self.linked('ministries',identity) is not None:return
            name = row.get('Nome','').strip()
            if not name:raise ValueError('Nome do ministério ausente.')
            root = self.conn.execute('SELECT id FROM ministries WHERE cev IS NULL AND nome=?',(name,)).fetchone()
            if not root:
                root_id = self.conn.execute('INSERT INTO ministries(nome,ativo,pastoreio) VALUES(?,?,?)',(name,boolean(row.get('Ativo')),int(name.casefold()=='pastoreio'))).lastrowid
                self.report['inseridos']['ministries_general']+=1
            else:root_id = root['id']
            local = self.conn.execute('SELECT id FROM ministries WHERE cev=? AND nome=?',(cev,name)).fetchone()
            if local:self.bind('ministries',identity,local['id'])
            else:self.insert('ministries',identity,{'nome':name,'cev':cev,'parent_id':root_id,'ativo':boolean(row.get('Ativo'))})
        self.process('Ministerios','ID_Ministerio',ministry)
        def member(identity,row):
            ministry_id = self.resolve('ministries',row.get('Ministerio'))
            person_id = self.resolve('people',row.get('Pessoa'))
            role = row.get('Função') or None
            if role not in (None,'Membro','Núcleo','Coordenador'):raise ValueError('Função do ministério não reconhecida.')
            existing = self.conn.execute('SELECT id FROM ministry_members WHERE ministerio_id=? AND pessoa_id=?',(ministry_id,person_id)).fetchone()
            if existing:self.bind('ministry_members',identity,existing['id'])
            else:self.insert('ministry_members',identity,{'ministerio_id':ministry_id,'pessoa_id':person_id,'funcao':role,'ativo':boolean(row.get('Ativo'))})
            sync_member(self.conn,person_id,preserve_current=True)
        self.process('MinisterioPessoa','ID_MinisterioPessoa',member)
        def month(identity,row):
            person = self.resolve('people',row.get('Pessoa'))
            group = self.resolve('groups',row.get('Grupo'))
            # A origem armazena o mês como data Excel; não inventar mês/ano.
            value = row.get('Mês','').strip()
            match = re.fullmatch(r'(0[1-9]|1[0-2])/(\d{4})',value)
            if match:
                value = f'{match[2]}-{match[1]}'
            else:
                value = self.date(value,True)[:7]
            self.insert('person_group_months',identity,{'pessoa_id':person,'grupo_id':group,'mes':value})
        self.process('Pessoa_Grupo_Mes',None,month)
        def meeting(identity,row):
            self.insert('meetings',identity,{'grupo_id':self.resolve('groups',row.get('Grupo')),
                'data':self.date(row.get('Data'),True),'tema':row.get('Tema',''),'observacoes':row.get('Detalhes','')})
        self.process('Encontros','ID_Encontro',meeting)
        conflicts = defaultdict(set)
        for r in self.sheets.get('Frequencia',[]):conflicts[(r.get('Encontro'),r.get('Pessoa'))].add(r.get('Status'))
        def attendance(identity,row):
            if len(conflicts[(row.get('Encontro'),row.get('Pessoa'))])>1:raise ValueError('Presenças conflitantes para a mesma pessoa/encontro.')
            meeting_id=self.resolve('meetings',row.get('Encontro'));person_id=self.resolve('people',row.get('Pessoa'))
            if row.get('Status') not in db.PRESENCES:raise ValueError('Presença inválida.')
            existing=self.conn.execute('SELECT id FROM attendance WHERE encontro_id=? AND membro_id=?',(meeting_id,person_id)).fetchone()
            if existing:self.bind('attendance',identity,existing['id'])
            else:self.insert('attendance',identity,{'encontro_id':meeting_id,'membro_id':person_id,'presenca':row['Status']})
        self.process('Frequencia','ID_Frequencia',attendance)
        def followup(identity,row):
            person_id=self.resolve('people',row.get('Pessoa')); when=self.date(row.get('Data'),True)
            historical = {r[0] for r in self.conn.execute('SELECT grupo_id FROM person_group_months WHERE pessoa_id=? AND mes=?',(person_id,when[:7]))}
            group_id = next(iter(historical)) if len(historical)==1 else None
            if not historical:
                group_id=self.resolve('groups',people_source.get(row.get('Pessoa'),{}).get('Grupo'),False)
            if not group_id:raise ValueError('Grupo do acompanhamento não determinado; revisar o vínculo histórico.')
            companion_id=self.resolve('people',row.get('Acompanhador'),False)
            companion=self.conn.execute('SELECT nome FROM people WHERE id=?',(companion_id,)).fetchone()[0] if companion_id else ''
            self.insert('followups',identity,{'grupo_id':group_id,'membro_id':person_id,'data':when,'acompanhador':companion})
        self.process('Acompanhamentos','ID_Acompanhamento',followup)
        def ministry_meeting(identity,row):
            self.insert('ministry_meetings',identity,{'ministerio_id':self.resolve('ministries',row.get('Ministerio')),
                'data':self.date(row.get('Data'),True),'detalhes':row.get('Detalhes',''),'situacao':row.get('Status') or None})
        self.process('MinisterioEncontros','ID_EncontroMinisterio',ministry_meeting)
        conflicts=defaultdict(set)
        for r in self.sheets.get('MinisterioFrequencia',[]):conflicts[(r.get('EncontroMinisterio'),r.get('Pessoa'))].add(r.get('Status'))
        def ministry_attendance(identity,row):
            if len(conflicts[(row.get('EncontroMinisterio'),row.get('Pessoa'))])>1:raise ValueError('Presenças de ministério conflitantes.')
            mid=self.resolve('ministry_meetings',row.get('EncontroMinisterio'));pid=self.resolve('people',row.get('Pessoa'))
            target=self.conn.execute('SELECT ministerio_id FROM ministry_meetings WHERE id=?',(mid,)).fetchone()[0]
            if row.get('Status') not in db.PRESENCES:raise ValueError('Presença inválida.')
            existing=self.conn.execute('SELECT id FROM ministry_attendance WHERE registro_id=? AND pessoa_id=?',(mid,pid)).fetchone()
            if existing:self.bind('ministry_attendance',identity,existing['id'])
            else:self.insert('ministry_attendance',identity,{'ministerio_id':target,'registro_id':mid,'pessoa_id':pid,'presenca':row['Status']})
        self.process('MinisterioFrequencia','ID_FreqMinisterio',ministry_attendance)
        def ministry_followup(identity,row):
            pid=self.resolve('people',row.get('Pessoa'));target=self.resolve('ministries',row.get('Ministerio'))
            aid=self.resolve('people',row.get('Acompanhador'),False)
            companion=self.conn.execute('SELECT nome FROM people WHERE id=?',(aid,)).fetchone()[0] if aid else ''
            self.insert('ministry_followups',identity,{'ministerio_id':target,'pessoa_id':pid,'acompanhador':companion,'data':self.date(row.get('Data'),True)})
        self.process('MinisterioAcompanhamento','ID_AcompMinisterio',ministry_followup)
        def notice(identity,row):
            name=row.get('Título','').strip()
            if not name:raise ValueError('Título ausente.')
            self.insert('notices',identity,{'titulo':name,'texto':row.get('Detalhes',''),'destino':'CEv/Irradiação','cev':cev})
        self.process('Avisos','ID_Avisos',notice)
        for sheet in ('UsuariosPermissoes','Config','Página5'):
            for row in self.sheets.get(sheet,[]):self.archive(sheet,str(row.get('ID_Usuario') or row.get('ID_CEV') or f'linha:{row["_row"]}'),row,'Preservado','Origem preservada; não concede acesso nem cria cadastros.')


def run(root, *, apply=False):
    if db.remote_settings():raise RuntimeError('Execute a importação local antes da migração para PostgreSQL.')
    root=Path(root); staging=root/'.qa/importacao-missao.sqlite3'; staging.parent.mkdir(exist_ok=True)
    original=db.DATABASE
    with sqlite3.connect(original) as live:
        live.row_factory=sqlite3.Row
        before,_=snapshot(live)
        with sqlite3.connect(staging) as stage:live.backup(stage)
    db.DATABASE=staging
    try:
        db.initialize()
        with db.connection() as conn:
            importer=Importer(conn)
            cevs=json.loads((root/'conteudo.json').read_text(encoding='utf-8'))['cevs']
            for cev in cevs:
                path=root/f'CPaz - {cev}.xlsx'
                if not path.is_file():raise ValueError(f'Arquivo ausente: {path.name}')
                importer.workbook(path,cev)
            if conn.execute('PRAGMA foreign_key_check').fetchall():raise RuntimeError('Falha de integridade na simulação.')
            if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('Banco de simulação inválido.')
            report=importer.report
        report_path=root/'dados/importacoes'/('missao-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
        report_path.parent.mkdir(parents=True,exist_ok=True)
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        if apply:
            with sqlite3.connect(original) as live:
                live.row_factory=sqlite3.Row
                live.execute('BEGIN IMMEDIATE')
                if snapshot(live)[0]!=before:raise RuntimeError('O banco foi alterado durante a simulação. Execute novamente.')
                live.rollback()
            # Backup adicional antes das mudanças reais.
            backup=root/'dados/backups'/('portal-antes-importacao-missao-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.sqlite3')
            with sqlite3.connect(original) as live,sqlite3.connect(backup) as dest:live.backup(dest)
            with sqlite3.connect(backup) as saved:
                saved.row_factory=sqlite3.Row
                if snapshot(saved)[0]!=before:
                    raise RuntimeError('Dados mudaram antes do backup; execute a importação novamente.')
            db.DATABASE=original
            db.initialize()
            with db.connection() as live:
                live.execute('BEGIN IMMEDIATE')
                with sqlite3.connect(staging) as stage:
                    stage.row_factory=sqlite3.Row
                    # Schema atualizado sem modificar os dados: comparar o conteúdo das tabelas antigas.
                    _,current=snapshot(live)
                    _,final=snapshot(stage)
                    with sqlite3.connect(backup) as old:
                        old.row_factory=sqlite3.Row
                        _,baseline=snapshot(old)
                    for table,rows in baseline.items():
                        old_cols=set(rows[0]) if rows else set()
                        trimmed=[{k:v for k,v in r.items() if k in old_cols} for r in current.get(table,[])]
                        if sorted(map(str,trimmed))!=sorted(map(str,rows)):
                            raise RuntimeError('Dados mudaram antes da aplicação; importação cancelada.')
                    live.execute('PRAGMA defer_foreign_keys=ON')
                    for table,rows in final.items():
                        info=list(live.execute(f'PRAGMA table_info({table})'))
                        pks=[r['name'] for r in sorted(info,key=lambda r:r['pk']) if r['pk']]
                        known={tuple(r[k] for k in pks):r for r in current.get(table,[])}
                        for row in rows:
                            identity=tuple(row[k] for k in pks)
                            keys=list(row)
                            if identity not in known:
                                live.execute(f'INSERT INTO {table} ('+','.join(keys)+') VALUES ('+','.join('?' for _ in keys)+')',tuple(row.values()))
                            elif row != known[identity]:
                                changes={k:v for k,v in row.items() if known[identity].get(k)!=v}
                                live.execute(f'UPDATE {table} SET '+','.join(k+'=?' for k in changes)+' WHERE '+' AND '.join(k+'=?' for k in pks),(*changes.values(),*identity))
                    if live.execute('PRAGMA foreign_key_check').fetchall():raise RuntimeError('Falha de integridade; aplicação revertida.')
        return {'aplicado':apply,'inseridos':dict(report['inseridos']),'pendencias':len(report['pendencias']),
                'avisos':len(report['avisos']),'relatorio':str(report_path)}
    finally:db.DATABASE=original


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    print(json.dumps(run(Path(__file__).parent,apply=args.apply),ensure_ascii=True))
