"""Ministérios próprios da missão e dos CEvs, com vínculos e históricos."""
import re
import sqlite3
import database as db

TABLES = ('ministries', 'ministry_members', 'ministry_meetings', 'ministry_attendance',
          'ministry_followups', 'person_group_months')
FIELDS = {
    'ministries': {'nome', 'foto', 'foto_tipo', 'ativo'},
    'ministry_members': {'pessoa_id', 'funcao', 'ativo'},
    'ministry_meetings': {'data', 'detalhes', 'situacao'},
    'ministry_attendance': {'registro_id', 'pessoa_id', 'presenca'},
    'ministry_followups': {'pessoa_id', 'acompanhador', 'data', 'proximo_acompanhamento', 'observacoes'},
}


def initialize_schema():
    with db.connection() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS ministries (
            id INTEGER PRIMARY KEY, nome TEXT NOT NULL, cev TEXT,
            parent_id INTEGER REFERENCES ministries(id), foto BLOB, foto_tipo TEXT,
            ativo INTEGER CHECK(ativo IN (0,1)), pastoreio INTEGER NOT NULL DEFAULT 0 CHECK(pastoreio IN (0,1)),
            CHECK((cev IS NULL AND parent_id IS NULL) OR (cev IS NOT NULL AND parent_id IS NOT NULL))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ministry_general_name ON ministries(nome) WHERE cev IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS ministry_local_name ON ministries(cev,nome) WHERE cev IS NOT NULL;
        CREATE TABLE IF NOT EXISTS ministry_members (
            id INTEGER PRIMARY KEY, ministerio_id INTEGER NOT NULL REFERENCES ministries(id),
            pessoa_id INTEGER NOT NULL REFERENCES people(id),
            funcao TEXT CHECK(funcao IN ('Membro','Núcleo','Coordenador')),
            ativo INTEGER CHECK(ativo IN (0,1)), UNIQUE(ministerio_id,pessoa_id)
        );
        CREATE TABLE IF NOT EXISTS ministry_meetings (
            id INTEGER PRIMARY KEY, ministerio_id INTEGER NOT NULL REFERENCES ministries(id),
            data TEXT NOT NULL, detalhes TEXT NOT NULL DEFAULT '', situacao TEXT
        );
        CREATE TABLE IF NOT EXISTS ministry_attendance (
            id INTEGER PRIMARY KEY, ministerio_id INTEGER NOT NULL REFERENCES ministries(id),
            registro_id INTEGER NOT NULL REFERENCES ministry_meetings(id),
            pessoa_id INTEGER NOT NULL REFERENCES people(id), presenca TEXT NOT NULL
                CHECK(presenca IN ('Presente','Ausente','Liberado')),
            UNIQUE(registro_id,pessoa_id)
        );
        CREATE TABLE IF NOT EXISTS ministry_followups (
            id INTEGER PRIMARY KEY, ministerio_id INTEGER NOT NULL REFERENCES ministries(id),
            pessoa_id INTEGER NOT NULL REFERENCES people(id), acompanhador TEXT NOT NULL DEFAULT '',
            data TEXT NOT NULL, proximo_acompanhamento TEXT, observacoes TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS person_group_months (
            id INTEGER PRIMARY KEY, pessoa_id INTEGER NOT NULL REFERENCES people(id),
            grupo_id INTEGER NOT NULL REFERENCES groups(id), mes TEXT NOT NULL,
            UNIQUE(pessoa_id,grupo_id,mes)
        );
        CREATE TABLE IF NOT EXISTS service_grants (
            id INTEGER PRIMARY KEY, email TEXT NOT NULL,
            ministerio_id INTEGER NOT NULL REFERENCES ministries(id),
            papel TEXT NOT NULL CHECK(papel IN ('Coordenador','Núcleo')),
            UNIQUE(email,ministerio_id,papel)
        );
        CREATE TABLE IF NOT EXISTS service_delete_permissions (
            id INTEGER PRIMARY KEY, email TEXT NOT NULL,
            ministerio_id INTEGER NOT NULL REFERENCES ministries(id), autorizado_por TEXT NOT NULL,
            UNIQUE(email,ministerio_id)
        );
        CREATE TABLE IF NOT EXISTS source_archive (
            id INTEGER PRIMARY KEY, source TEXT NOT NULL, sheet TEXT NOT NULL,
            source_id TEXT NOT NULL, sha256 TEXT NOT NULL, original TEXT NOT NULL,
            status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
            UNIQUE(source,sheet,source_id)
        );
        CREATE TABLE IF NOT EXISTS import_links (
            source TEXT NOT NULL, entity TEXT NOT NULL, source_id TEXT NOT NULL,
            target_id INTEGER NOT NULL, PRIMARY KEY(source,entity,source_id)
        );
        CREATE TABLE IF NOT EXISTS schema_meta (id INTEGER PRIMARY KEY, versao INTEGER NOT NULL);
        INSERT INTO schema_meta(id,versao) VALUES(1,1) ON CONFLICT(id) DO UPDATE SET versao=excluded.versao;
        ''')
        existing = {r['name'] for r in conn.execute('PRAGMA table_info(ministries)')}
        if 'pastoreio' not in existing:
            conn.execute('ALTER TABLE ministries ADD COLUMN pastoreio INTEGER NOT NULL DEFAULT 0 CHECK(pastoreio IN (0,1))')
        # Destino de ministério usa uma referência real, não a escolha do navegador.
        columns = {r['name'] for r in conn.execute('PRAGMA table_info(notices)')}
        if 'ministerio_id' not in columns:
            conn.execute('ALTER TABLE notices ADD COLUMN ministerio_id INTEGER REFERENCES ministries(id)')
        columns = {r['name'] for r in conn.execute('PRAGMA table_info(events)')}
        if 'ministerio_id' not in columns:
            conn.execute('ALTER TABLE events ADD COLUMN ministerio_id INTEGER REFERENCES ministries(id)')
        definition = conn.execute("SELECT sql FROM sqlite_master WHERE name='notices'").fetchone()[0]
        if "'Ministério'" not in definition:
            conn.execute('''CREATE TABLE notices_new (
                id INTEGER PRIMARY KEY, titulo TEXT NOT NULL, texto TEXT NOT NULL DEFAULT '',
                destino TEXT NOT NULL CHECK(destino IN ('Geral','CEv/Irradiação','Grupo','Ministério')),
                cev TEXT, grupo_id INTEGER REFERENCES groups(id), foto BLOB, foto_tipo TEXT,
                publicado_em TEXT, evento_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
                ministerio_id INTEGER REFERENCES ministries(id))''')
            conn.execute('INSERT INTO notices_new SELECT * FROM notices')
            conn.execute('DROP TABLE notices')
            conn.execute('ALTER TABLE notices_new RENAME TO notices')


def ministry(identity, conn=None):
    rows = conn.execute('SELECT * FROM ministries WHERE id=?', (identity,)).fetchone() if conn else None
    if conn:
        return dict(rows) if rows else None
    rows = db.query('SELECT * FROM ministries WHERE id=?', (identity,))
    return rows[0] if rows else None


def descendants(identity):
    return [r['id'] for r in db.query('SELECT id FROM ministries WHERE id=? OR parent_id=?', (identity, identity))]


def create(claims, nome, cev=None, parent_id=None, ativo=None):
    import access, servicos, json
    from pathlib import Path
    if not nome.strip():
        raise ValueError('Informe o nome.')
    db.validate_details(ativo=ativo)
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        actor = access._locked_actor(conn, claims)
        if cev is None:
            if not actor or actor['nivel'] != 'Administrador' or parent_id is not None:
                raise PermissionError('Somente administradores criam ministérios gerais.')
        else:
            cevs = json.loads(Path(__file__).with_name('conteudo.json').read_text(encoding='utf-8'))['cevs']
            root = ministry(parent_id, conn)
            target = {'id': -1, 'parent_id': parent_id, 'cev': cev}
            if cev not in cevs or not root or root['cev'] is not None:
                raise ValueError('Selecione o CEv e o ministério Geral correspondente.')
            if not servicos.can_delegate(actor, target, 'Coordenador'):
                raise PermissionError('Sua conta não pode criar este ministério local.')
        return conn.execute('INSERT INTO ministries(nome,cev,parent_id,ativo,pastoreio) VALUES(?,?,?,?,?)',
            (nome.strip(), cev, parent_id, ativo, int(cev is None and nome.strip().casefold() == 'pastoreio'))).lastrowid


def sync_member(conn, person_id, preserve_current=False):
    labels = [r[0] for r in conn.execute('''SELECT DISTINCT m.nome FROM ministry_members v
        JOIN ministries m ON m.id=v.ministerio_id WHERE v.pessoa_id=? AND m.ativo=1
        AND (v.ativo IS NULL OR v.ativo=1) ORDER BY m.nome''', (person_id,))]
    current = conn.execute('SELECT ministerio FROM people WHERE id=?', (person_id,)).fetchone()[0]
    known = {r[0] for r in conn.execute('SELECT DISTINCT nome FROM ministries')}
    manual = [v.strip() for v in current.split(';') if v.strip() and (preserve_current or v.strip() not in known)]
    conn.execute('UPDATE people SET ministerio=? WHERE id=?', ('; '.join(sorted(set(labels + manual))), person_id))


def save(claims, table, ministry_id, data, record_id=None):
    import access, servicos
    if table not in FIELDS or not data or not set(data) <= FIELDS[table]:
        raise ValueError('Campos de ministério inválidos.')
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        actor = access._locked_actor(conn, claims)
        target = ministry(ministry_id, conn)
        if not target or not servicos.can_manage(actor, target):
            raise PermissionError('Este ministério está fora do seu acesso.')
        old = None
        if record_id is not None:
            raw = conn.execute(f'SELECT * FROM {table} WHERE id=?', (record_id,)).fetchone()
            old = dict(raw) if raw else None
            if not old or (old['id'] if table == 'ministries' else old['ministerio_id']) != ministry_id:
                raise ValueError('Registro não pertence ao ministério selecionado.')
        row = {**(old or {}), **data}
        for field in ('data', 'proximo_acompanhamento'):
            if row.get(field):
                from datetime import date
                date.fromisoformat(row[field])
        if row.get('proximo_acompanhamento') and row['proximo_acompanhamento'] < row['data']:
            raise ValueError('O próximo acompanhamento não pode ser anterior ao registro.')
        if 'ativo' in row and row['ativo'] not in (None, 0, 1):
            raise ValueError('Situação inválida.')
        if table == 'ministries' and not row.get('nome', '').strip():
            raise ValueError('Informe o nome do ministério.')
        if table == 'ministry_members' and row.get('funcao') not in (None, 'Membro', 'Núcleo', 'Coordenador'):
            raise ValueError('Função inválida.')
        if table in ('ministry_members', 'ministry_followups', 'ministry_attendance'):
            person = conn.execute('SELECT cev FROM people WHERE id=?', (row.get('pessoa_id'),)).fetchone()
            if not person or (target['cev'] and person['cev'] != target['cev']):
                raise ValueError('Selecione uma pessoa do alcance do ministério.')
        if table in ('ministry_followups', 'ministry_attendance') and not conn.execute(
                'SELECT 1 FROM ministry_members WHERE ministerio_id=? AND pessoa_id=?',
                (ministry_id, row['pessoa_id'])).fetchone():
            raise ValueError('A pessoa precisa estar vinculada ao ministério.')
        if table == 'ministry_attendance':
            if row.get('presenca') not in db.PRESENCES or not conn.execute(
                    'SELECT 1 FROM ministry_meetings WHERE id=? AND ministerio_id=?',
                    (row.get('registro_id'), ministry_id)).fetchone():
                raise ValueError('Registro ou presença inválida.')
        keys = list(data)
        if old:
            conn.execute(f'UPDATE {table} SET '+','.join(f'{k}=?' for k in keys)+' WHERE id=?',
                         (*[data[k] for k in keys], record_id))
        else:
            if table == 'ministries':
                raise ValueError('Crie o ministério através da gestão de catálogos.')
            keys = ['ministerio_id', *keys]
            record_id = conn.execute(f'INSERT INTO {table} ('+','.join(keys)+') VALUES ('+','.join('?' for _ in keys)+')',
                                     (ministry_id, *data.values())).lastrowid
        if table == 'ministry_members':
            sync_member(conn, row['pessoa_id'])
            if old and old['pessoa_id'] != row['pessoa_id']:
                sync_member(conn, old['pessoa_id'])
        return record_id


def delete(claims, table, record_id):
    import access, servicos
    if table not in FIELDS:
        raise ValueError('Registro inválido.')
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute(f'SELECT * FROM {table} WHERE id=?', (record_id,)).fetchone()
        if not row:
            raise ValueError('Registro não encontrado.')
        ministry_id = row['id'] if table == 'ministries' else row['ministerio_id']
        actor = access._locked_actor(conn, claims)
        target = ministry(ministry_id, conn)
        if not servicos.can_manage(actor, target) or not servicos.can_delete(actor, target, conn):
            raise PermissionError('Sua conta não possui exclusão autorizada neste ministério.')
        try:
            if table == 'ministries':
                conn.execute('DELETE FROM service_delete_permissions WHERE ministerio_id=?',(ministry_id,))
            conn.execute(f'DELETE FROM {table} WHERE id=?', (record_id,))
        except sqlite3.IntegrityError:
            raise ValueError('Revise os registros vinculados antes de excluir.') from None
        if table == 'ministry_members':
            sync_member(conn, row['pessoa_id'])
