"""Autorização acumulável por ministério, CEv e missão."""
import re
import database as db


def grants(email, conn=None):
    sql = '''SELECT s.*,m.nome,m.cev,m.parent_id,COALESCE(root.pastoreio,m.pastoreio) AS pastoreio FROM service_grants s
        JOIN ministries m ON m.id=s.ministerio_id LEFT JOIN ministries root ON root.id=m.parent_id WHERE s.email=? ORDER BY s.id'''
    return [dict(r) for r in conn.execute(sql, (email,))] if conn else db.query(sql, (email,))


def attach(actor, email, conn=None):
    bindings = grants(email, conn)
    if not actor and bindings:
        actor = {'email': email, 'nivel': 'Serviço', 'cev': bindings[0]['cev'], 'grupo_id': None}
    if actor:
        actor = {**actor, 'servicos': bindings}
    return actor


def can_manage(actor, target):
    if not actor or not target:
        return False
    if actor['nivel'] == 'Administrador' or (actor['nivel'] == 'Gestor de CEv' and actor['cev'] == target['cev']):
        return True
    return any(v['ministerio_id'] in (target['id'], target['parent_id']) for v in actor.get('servicos', ()))


def pastoral(actor, cev):
    return bool(actor and any(v['papel'] == 'Coordenador' and v.get('pastoreio') == 1
                and (v['cev'] is None or v['cev'] == cev) for v in actor.get('servicos', ())))


def can_view_person(actor, person):
    if not actor:
        return False
    return any(can_manage(actor, r) for r in db.query('''SELECT m.* FROM ministries m
        JOIN ministry_members v ON v.ministerio_id=m.id WHERE v.pessoa_id=?
        AND (v.ativo IS NULL OR v.ativo=1)''', (person['id'],)))


def can_delegate(actor, target, role):
    if not actor or role not in ('Coordenador', 'Núcleo'):
        return False
    if actor['nivel'] == 'Administrador':
        return True
    if target['cev'] is not None and actor['nivel'] == 'Gestor de CEv' and actor['cev'] == target['cev']:
        return True
    for grant in actor.get('servicos', ()):
        if grant['papel'] != 'Coordenador':
            continue
        if grant['cev'] is None and target['parent_id'] == grant['ministerio_id']:
            return True
        if target['id'] == grant['ministerio_id'] and role == 'Núcleo':
            return True
    return False


def grant(claims, email, ministry_id, role):
    import access, ministerios
    email = email.strip().lower()
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        raise ValueError('Informe um e-mail válido.')
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        actor = access._locked_actor(conn, claims)
        target = ministerios.ministry(ministry_id, conn)
        if not target or not can_delegate(actor, target, role):
            raise PermissionError('Sua conta não pode conceder este vínculo.')
        conn.execute('''INSERT INTO service_grants(email,ministerio_id,papel) VALUES(?,?,?)
            ON CONFLICT(email,ministerio_id,papel) DO NOTHING''', (email, ministry_id, role))
        conn.execute('DELETE FROM access_requests WHERE email=?', (email,))


def revoke(claims, grant_id):
    import access, ministerios
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        actor = access._locked_actor(conn, claims)
        row = conn.execute('SELECT * FROM service_grants WHERE id=?', (grant_id,)).fetchone()
        if not row or not can_delegate(actor, ministerios.ministry(row['ministerio_id'], conn), row['papel']):
            raise PermissionError('Sua conta não pode revogar este vínculo.')
        conn.execute('DELETE FROM service_grants WHERE id=?', (grant_id,))
        # Uma autorização de exclusão não sobrevive à retirada do vínculo.
        conn.execute('DELETE FROM service_delete_permissions WHERE email=? AND ministerio_id=?',
                     (row['email'], row['ministerio_id']))


def can_delete(actor, target, conn):
    if not can_manage(actor, target):
        return False
    return bool(conn.execute('''SELECT 1 FROM service_delete_permissions WHERE email=?
        AND (ministerio_id=? OR ministerio_id=?)''',
        (actor['email'], target['id'], target['parent_id'] or target['id'])).fetchone())


def set_delete(claims, email, ministry_id, allowed):
    import access, ministerios
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        actor = access._locked_actor(conn, claims)
        if not actor or actor['email'] not in access.ADMIN_EMAILS:
            raise PermissionError('Somente os administradores iniciais concedem exclusão.')
        target = ministerios.ministry(ministry_id, conn)
        base = conn.execute('SELECT * FROM access_grants WHERE email=?', (email,)).fetchone()
        account = {'email': email, 'nivel': 'Administrador', 'cev': None, 'grupo_id': None} if email in access.ADMIN_EMAILS else dict(base) if base else None
        account = attach(account, email, conn)
        if not target or not can_manage(account, target):
            raise ValueError('A conta precisa ter acesso a este ministério.')
        if allowed:
            conn.execute('''INSERT INTO service_delete_permissions(email,ministerio_id,autorizado_por)
                VALUES(?,?,?) ON CONFLICT(email,ministerio_id) DO NOTHING''', (email, ministry_id, actor['email']))
        else:
            conn.execute('DELETE FROM service_delete_permissions WHERE email=? AND ministerio_id=?', (email, ministry_id))
