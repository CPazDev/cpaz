"""Persistência local dos campos aprovados pelo usuário."""

from contextlib import contextmanager
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import secrets
from zoneinfo import ZoneInfo

PHASES = ("Kerigma", "Filoteia", "Metanoia", "Martiria", "Santidade", "Permanente")
WEEKDAYS = ("Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo")
PRESENCES = ("Presente", "Ausente", "Liberado")
CATEGORIES = ("Membro", "Pastor", "Núcleo")
DATABASE = Path(os.environ.get("CEV_DATABASE", str(Path(__file__).parent / "dados" / "portal.sqlite3")))


def remote_settings():
    url = os.environ.get('CPAZ_DATABASE_URL', '')
    required = os.environ.get('CPAZ_REQUIRE_REMOTE', '').lower() in ('1', 'true')
    try:
        import streamlit as st
        config = st.secrets.get('database', {})
        url = url or config.get('url', '')
        required = required or bool(config.get('require_remote', False))
    except (FileNotFoundError, AttributeError):
        pass
    if required and not url:
        raise RuntimeError('O banco remoto não foi configurado. Preencha database.url em Secrets.')
    return url


@contextmanager
def connection():
    url = remote_settings()
    if url:
        from persistencia import connection as remote_connection
        with remote_connection(url) as conn:
            yield conn
        return
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def initialize():
    if remote_settings():
        with connection() as conn:
            version = conn.execute('SELECT versao FROM schema_meta WHERE id=1').fetchone()
            if not version or version[0] != 1:
                raise RuntimeError('O banco precisa ser migrado antes de iniciar o portal.')
        return
    with connection() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS people (
            id INTEGER PRIMARY KEY, cev TEXT NOT NULL, nome TEXT NOT NULL,
            categoria TEXT NOT NULL CHECK(categoria IN ('Membro', 'Pastor', 'Núcleo')),
            contato TEXT NOT NULL DEFAULT '', grupo_id INTEGER REFERENCES groups(id),
            instagram TEXT NOT NULL DEFAULT '', endereco TEXT NOT NULL DEFAULT '',
            nascimento TEXT, foto BLOB, foto_tipo TEXT, acompanhador TEXT NOT NULL DEFAULT '',
            ministerio TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY, cev TEXT NOT NULL, nome TEXT NOT NULL,
            pastor_id INTEGER REFERENCES people(id), local TEXT NOT NULL DEFAULT '',
            horario TEXT NOT NULL DEFAULT '', fase TEXT NOT NULL
                CHECK(fase IN ('Kerigma','Filoteia','Metanoia','Martiria','Santidade','Permanente')),
            foto BLOB, foto_tipo TEXT, dia_encontro TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS meetings (
            id INTEGER PRIMARY KEY, grupo_id INTEGER NOT NULL REFERENCES groups(id),
            data TEXT NOT NULL, tema TEXT NOT NULL DEFAULT '', observacoes TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY, encontro_id INTEGER NOT NULL REFERENCES meetings(id),
            membro_id INTEGER NOT NULL REFERENCES people(id), presenca TEXT NOT NULL
                CHECK(presenca IN ('Presente','Ausente','Liberado')),
            UNIQUE(encontro_id, membro_id)
        );
        CREATE TABLE IF NOT EXISTS followups (
            id INTEGER PRIMARY KEY, grupo_id INTEGER NOT NULL REFERENCES groups(id),
            membro_id INTEGER NOT NULL REFERENCES people(id), data TEXT NOT NULL,
            acompanhador TEXT NOT NULL, proximo_acompanhamento TEXT,
            observacoes TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS access_requests (
            email TEXT PRIMARY KEY, nome TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS account_profiles (
            email TEXT PRIMARY KEY, nome TEXT NOT NULL DEFAULT '', apelido TEXT NOT NULL DEFAULT '',
            membro_id INTEGER UNIQUE REFERENCES people(id)
        );
        CREATE TABLE IF NOT EXISTS access_grants (
            email TEXT PRIMARY KEY, nivel TEXT NOT NULL, cev TEXT,
            grupo_id INTEGER REFERENCES groups(id)
        );
        CREATE TABLE IF NOT EXISTS deletion_permissions (
            id INTEGER PRIMARY KEY, email TEXT NOT NULL, escopo TEXT NOT NULL,
            cev TEXT, grupo_id INTEGER REFERENCES groups(id), autorizado_por TEXT NOT NULL,
            UNIQUE(email,escopo),
            CHECK ((escopo = 'Geral' AND cev IS NULL AND grupo_id IS NULL)
                OR (escopo != 'Geral' AND cev IS NOT NULL))
        );
        CREATE TABLE IF NOT EXISTS notices (
            id INTEGER PRIMARY KEY, titulo TEXT NOT NULL, texto TEXT NOT NULL DEFAULT '',
            destino TEXT NOT NULL CHECK(destino IN ('Geral','CEv/Irradiação','Grupo')),
            cev TEXT, grupo_id INTEGER REFERENCES groups(id), foto BLOB, foto_tipo TEXT,
            publicado_em TEXT, evento_id INTEGER REFERENCES events(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY, titulo TEXT NOT NULL, tipo TEXT NOT NULL CHECK(tipo IN ('Evento','Retiro')),
            inicio TEXT NOT NULL, termino TEXT, local TEXT NOT NULL DEFAULT '', descricao TEXT NOT NULL DEFAULT '',
            destino TEXT NOT NULL, cev TEXT, grupo_id INTEGER REFERENCES groups(id),
            capa BLOB, capa_tipo TEXT, cor TEXT NOT NULL, whatsapp TEXT NOT NULL DEFAULT '', organizador TEXT NOT NULL,
            campos TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS registrations (
            id INTEGER PRIMARY KEY, evento_id INTEGER NOT NULL REFERENCES events(id),
            respostas TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS event_requests (
            id INTEGER PRIMARY KEY, evento_id INTEGER NOT NULL REFERENCES events(id),
            titulo TEXT NOT NULL, descricao TEXT NOT NULL DEFAULT '', opcoes TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS request_orders (
            id INTEGER PRIMARY KEY, pedido_id INTEGER NOT NULL REFERENCES event_requests(id),
            inscricao_id INTEGER NOT NULL REFERENCES registrations(id), opcao TEXT NOT NULL DEFAULT '',
            quantidade INTEGER NOT NULL CHECK(quantidade > 0), UNIQUE(pedido_id,inscricao_id,opcao)
        );
        CREATE TABLE IF NOT EXISTS testimonials (
            id INTEGER PRIMARY KEY, evento_id INTEGER NOT NULL REFERENCES events(id),
            nome TEXT NOT NULL, texto TEXT NOT NULL, foto BLOB, foto_tipo TEXT,
            status TEXT NOT NULL DEFAULT 'Pendente' CHECK(status IN ('Pendente','Aprovado','Rejeitado'))
        );
        """)
        group_columns = {row["name"] for row in conn.execute("PRAGMA table_info(groups)")}
        if "dia_encontro" not in group_columns:
            conn.execute("ALTER TABLE groups ADD COLUMN dia_encontro TEXT NOT NULL DEFAULT ''")
        schema = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'people'").fetchone()[0]
        if "'Núcleo'" not in schema:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("""CREATE TABLE people_new (
                    id INTEGER PRIMARY KEY, cev TEXT NOT NULL, nome TEXT NOT NULL,
                    categoria TEXT NOT NULL CHECK(categoria IN ('Membro','Pastor','Núcleo')),
                    contato TEXT NOT NULL DEFAULT '', grupo_id INTEGER REFERENCES groups(id),
                    instagram TEXT NOT NULL DEFAULT '', endereco TEXT NOT NULL DEFAULT '',
                    nascimento TEXT, foto BLOB, foto_tipo TEXT,
                    acompanhador TEXT NOT NULL DEFAULT '', ministerio TEXT NOT NULL DEFAULT '')""")
                conn.execute("""INSERT INTO people_new (id,cev,nome,categoria,contato,grupo_id)
                    SELECT id,cev,nome,categoria,contato,grupo_id FROM people""")
                conn.execute("DROP TABLE people")
                conn.execute("ALTER TABLE people_new RENAME TO people")
                if conn.execute("PRAGMA foreign_key_check").fetchall():
                    raise RuntimeError("Falha na integridade dos vínculos do cadastro.")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.execute("PRAGMA foreign_keys = ON")
        # Campos aprovados para a importação. NULL conserva indicação ausente.
        additions = {
            "notices": {"publicado_em": "TEXT", "evento_id": "INTEGER REFERENCES events(id) ON DELETE SET NULL"},
            "groups": {"data_inicio": "TEXT", "publico": "TEXT NOT NULL DEFAULT ''",
                       "ativo": "INTEGER CHECK(ativo IN (0,1))",
                       "neutro": "INTEGER NOT NULL DEFAULT 0 CHECK(neutro IN (0,1))"},
            "people": {"email": "TEXT NOT NULL DEFAULT ''", "genero": "TEXT NOT NULL DEFAULT ''",
                       "servico": "TEXT NOT NULL DEFAULT ''", "funcao_servico": "TEXT NOT NULL DEFAULT ''",
                       "eh_comunidade": "INTEGER CHECK(eh_comunidade IN (0,1))",
                       "ativo": "INTEGER CHECK(ativo IN (0,1))"},
        }
        for table, columns in additions.items():
            existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for column, definition in columns.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    from ministerios import initialize_schema
    initialize_schema()


def validate_details(data_inicio=None, nascimento=None, ativo=None, eh_comunidade=None):
    for value, label in ((data_inicio, "Data de início"), (nascimento, "Data de nascimento")):
        if value is not None:
            try:
                date.fromisoformat(value)
            except (ValueError, TypeError):
                raise ValueError(f"Informe uma data válida em {label}.") from None
    if nascimento and nascimento > date.today().isoformat():
        raise ValueError("A data de nascimento não pode estar no futuro.")
    for value in (ativo, eh_comunidade):
        if value is not None and (not isinstance(value, (int, bool)) or value not in (0, 1)):
            raise ValueError("Selecione Sim, Não ou Não informado.")


def query(sql, params=()):
    with connection() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def execute(sql, params=()):
    with connection() as conn:
        cursor = conn.execute(sql, params)
        return cursor.lastrowid


def groups(cev):
    return query("""SELECT g.*, p.nome AS pastor FROM groups g
                 LEFT JOIN people p ON p.id = g.pastor_id
                 WHERE g.cev = ? ORDER BY g.nome, g.id""", (cev,))


def people(cev):
    return query("""SELECT p.*, g.nome AS grupo FROM people p
                 LEFT JOIN groups g ON g.id = p.grupo_id
                 WHERE p.cev = ? ORDER BY p.nome, p.id""", (cev,))


def group_people(group_id):
    return query("""SELECT DISTINCT p.* FROM people p
                 JOIN groups g ON g.id = ?
                 WHERE p.cev = g.cev AND (p.grupo_id = g.id OR p.id = g.pastor_id)
                 ORDER BY p.nome, p.id""", (group_id,))


def meetings(group_id):
    return query("SELECT * FROM meetings WHERE grupo_id = ? ORDER BY data DESC, id DESC",
                 (group_id,))


def attendance(group_id):
    return query("""SELECT a.id, e.data, e.tema, p.nome AS membro, a.presenca
                 FROM attendance a JOIN meetings e ON e.id = a.encontro_id
                 JOIN people p ON p.id = a.membro_id WHERE e.grupo_id = ?
                 ORDER BY e.data DESC, p.nome""", (group_id,))


def followups(group_id):
    return query("""SELECT f.*, p.nome AS membro FROM followups f
                 JOIN people p ON p.id = f.membro_id WHERE f.grupo_id = ?
                 ORDER BY f.data DESC, f.id DESC""", (group_id,))


def save_group(cev, nome, pastor_id, local, horario, fase, foto=None, foto_tipo=None, dia_encontro=None,
               data_inicio=None, publico="", ativo=None, neutro=False):
    if not nome.strip() or fase not in PHASES:
        raise ValueError("Informe o nome e a fase do grupo.")
    if dia_encontro not in (None, "") and dia_encontro not in WEEKDAYS:
        raise ValueError("Selecione um dia da semana para o encontro.")
    validate_details(data_inicio=data_inicio, ativo=ativo)
    validate_neutral(neutro)
    with connection() as conn:
        if pastor_id is not None and not conn.execute(
            "SELECT 1 FROM people WHERE id = ? AND cev = ? AND categoria = 'Pastor'",
            (pastor_id, cev),
        ).fetchone():
            raise ValueError("Selecione um pastor deste CEv/Irradiação.")
        return conn.execute("""INSERT INTO groups
            (cev,nome,pastor_id,local,horario,fase,foto,foto_tipo,dia_encontro,data_inicio,publico,ativo,neutro)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cev, nome.strip(), pastor_id, local.strip(), horario, fase, foto, foto_tipo, dia_encontro or "",
             data_inicio, publico.strip(), ativo, int(neutro))).lastrowid


def validate_neutral(value):
    if not isinstance(value, (bool, int)) or value not in (0, 1):
        raise ValueError("Informe se o vínculo é neutro.")


def save_person(cev, nome, categoria, contato, grupo_id, instagram="", endereco="", nascimento=None,
                foto=None, foto_tipo=None, acompanhador="", ministerio="", email="", genero="",
                servico="", funcao_servico="", eh_comunidade=None, ativo=None):
    if not nome.strip() or categoria not in CATEGORIES:
        raise ValueError("Informe o nome e a categoria.")
    validate_details(nascimento=nascimento, ativo=ativo, eh_comunidade=eh_comunidade)
    with connection() as conn:
        if grupo_id is not None and not conn.execute(
            "SELECT 1 FROM groups WHERE id = ? AND cev = ?", (grupo_id, cev)
        ).fetchone():
            raise ValueError("Selecione um grupo deste CEv/Irradiação.")
        return conn.execute("""INSERT INTO people
            (cev,nome,categoria,contato,grupo_id,instagram,endereco,nascimento,foto,foto_tipo,acompanhador,ministerio,
             email,genero,servico,funcao_servico,eh_comunidade,ativo)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (cev, nome.strip(), categoria, contato.strip(), grupo_id,
            instagram.strip(), endereco.strip(), nascimento, foto, foto_tipo,
            acompanhador.strip(), ministerio.strip(), email.strip(), genero.strip(), servico.strip(),
            funcao_servico.strip(), eh_comunidade, ativo)).lastrowid


def save_attendance(encontro_id, membro_id, presenca):
    if presenca not in PRESENCES:
        raise ValueError("Selecione presente, ausente ou liberado.")
    with connection() as conn:
        allowed = conn.execute("""SELECT 1 FROM meetings e JOIN groups g ON g.id = e.grupo_id
            JOIN people p ON p.cev = g.cev AND (p.grupo_id = g.id OR p.id = g.pastor_id)
            WHERE e.id = ? AND p.id = ?""", (encontro_id, membro_id)).fetchone()
        if not allowed:
            raise ValueError("O membro precisa pertencer ao grupo do encontro.")
        conn.execute("""INSERT INTO attendance (encontro_id,membro_id,presenca) VALUES (?,?,?)
            ON CONFLICT(encontro_id,membro_id) DO UPDATE SET presenca = excluded.presenca""",
            (encontro_id, membro_id, presenca))


def save_followup(grupo_id, membro_id, data, acompanhador, proximo, observacoes):
    if not acompanhador.strip():
        raise ValueError("Informe o acompanhador.")
    if proximo and proximo < data:
        raise ValueError("O próximo acompanhamento não pode ser anterior à data registrada.")
    with connection() as conn:
        if not conn.execute("""SELECT 1 FROM groups g JOIN people p ON p.cev = g.cev
            AND (p.grupo_id = g.id OR p.id = g.pastor_id) WHERE g.id = ? AND p.id = ?""",
            (grupo_id, membro_id)).fetchone():
            raise ValueError("Selecione um membro deste grupo.")
        return conn.execute("""INSERT INTO followups
            (grupo_id,membro_id,data,acompanhador,proximo_acompanhamento,observacoes)
            VALUES (?,?,?,?,?,?)""",
            (grupo_id, membro_id, data, acompanhador.strip(), proximo, observacoes.strip())).lastrowid


def publications(table, cev=None, group_id=None):
    if table not in ("notices", "events"):
        raise ValueError("Tipo de publicação inválido.")
    if group_id is not None:
        return query(f"SELECT * FROM {table} WHERE destino = 'Grupo' AND cev = ? AND grupo_id = ? ORDER BY id DESC",
                     (cev, group_id))
    if cev is not None:
        return query(f"""SELECT * FROM {table} WHERE (destino = 'CEv/Irradiação' AND cev = ?)
            OR (destino='Ministério' AND ministerio_id IN (SELECT id FROM ministries WHERE cev IS NULL)) ORDER BY id DESC""", (cev,))
    return query(f"""SELECT * FROM {table} WHERE destino = 'Geral'
        OR (destino='Ministério' AND ministerio_id IN (SELECT id FROM ministries WHERE cev IS NULL)) ORDER BY id DESC""")


def validate_target(destino, cev, grupo_id, ministerio_id=None):
    if destino == 'Ministério':
        import ministerios
        target = ministerios.ministry(ministerio_id)
        if not target or target['cev'] != cev or grupo_id is not None:
            raise ValueError('Selecione o ministério de destino.')
        return cev, None
    if destino == "Geral":
        return None, None
    if destino not in ("CEv/Irradiação", "Grupo") or not cev:
        raise ValueError("Selecione o destino da publicação.")
    if destino == "Grupo" and not query("SELECT 1 FROM groups WHERE id = ? AND cev = ?", (grupo_id, cev)):
        raise ValueError("Selecione o grupo de destino.")
    return cev, grupo_id if destino == "Grupo" else None


def save_notice(titulo, texto, destino, cev=None, grupo_id=None, foto=None, foto_tipo=None, ministerio_id=None, *, conn=None):
    if not titulo.strip():
        raise ValueError("Informe o título do aviso.")
    cev, grupo_id = validate_target(destino, cev, grupo_id, ministerio_id)
    sql = """INSERT INTO notices (titulo,texto,destino,cev,grupo_id,foto,foto_tipo,publicado_em,ministerio_id)
        VALUES (?,?,?,?,?,?,?,?,?)"""
    params = (titulo.strip(), texto.strip(), destino, cev, grupo_id, foto, foto_tipo, publication_time(),
              ministerio_id if destino == 'Ministério' else None)
    return conn.execute(sql, params).lastrowid if conn is not None else execute(sql, params)


def publication_time():
    return datetime.now(ZoneInfo("America/Sao_Paulo")).isoformat(timespec="seconds")


def save_event(titulo, tipo, inicio, termino, local, descricao, destino, cev, grupo_id,
               capa, capa_tipo, cor, whatsapp, organizador, ministerio_id=None, *, conn=None):
    if not titulo.strip() or tipo not in ("Evento", "Retiro"):
        raise ValueError("Informe o título e o tipo do evento/retiro.")
    if termino and termino < inicio:
        raise ValueError("A data de término não pode ser anterior à data de início.")
    cev, grupo_id = validate_target(destino, cev, grupo_id, ministerio_id)
    sql = """INSERT INTO events
        (titulo,tipo,inicio,termino,local,descricao,destino,cev,grupo_id,capa,capa_tipo,cor,whatsapp,organizador,ministerio_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    params = (titulo.strip(), tipo, inicio, termino, local.strip(), descricao.strip(),
              destino, cev, grupo_id, capa, capa_tipo, cor, whatsapp, organizador, ministerio_id if destino == 'Ministério' else None)
    return conn.execute(sql, params).lastrowid if conn is not None else execute(sql, params)


def register(event_id, responses):
    if not query("SELECT 1 FROM events WHERE id = ?", (event_id,)):
        raise ValueError("Evento não encontrado.")
    token = secrets.token_urlsafe(24)
    registration_id = execute("INSERT INTO registrations (evento_id,respostas,token_hash) VALUES (?,?,?)",
                              (event_id, json.dumps(responses, ensure_ascii=False), hashlib.sha256(token.encode()).hexdigest()))
    return registration_id, token


def find_registration(event_id, token):
    if not isinstance(token, str) or not token.strip():
        return None
    rows = query("SELECT * FROM registrations WHERE evento_id = ? AND token_hash = ?",
                 (event_id, hashlib.sha256(token.strip().encode()).hexdigest()))
    return rows[0] if rows else None


def save_order(event_id, token, pedido_id, opcao, quantidade):
    with connection() as conn:
        registration = conn.execute("SELECT id FROM registrations WHERE evento_id = ? AND token_hash = ?",
            (event_id, hashlib.sha256(token.strip().encode()).hexdigest())).fetchone()
        request = conn.execute("SELECT * FROM event_requests WHERE id = ? AND evento_id = ?", (pedido_id, event_id)).fetchone()
        if not registration or not request:
            raise ValueError("Código de inscrição ou pedido inválido para este evento.")
        options = json.loads(request["opcoes"])
        if options and opcao not in options or not options and opcao != "":
            raise ValueError("Selecione uma opção válida.")
        if isinstance(quantidade, bool) or not isinstance(quantidade, int) or quantidade <= 0:
            raise ValueError("Informe uma quantidade maior que zero.")
        conn.execute("""INSERT INTO request_orders (pedido_id,inscricao_id,opcao,quantidade) VALUES (?,?,?,?)
            ON CONFLICT(pedido_id,inscricao_id,opcao) DO UPDATE SET quantidade = excluded.quantidade""",
            (pedido_id, registration["id"], opcao, quantidade))


def order_rows(event_id, registration_id=None):
    sql = """SELECT o.id, o.inscricao_id, p.titulo AS pedido, o.opcao, o.quantidade
        FROM request_orders o JOIN event_requests p ON p.id = o.pedido_id
        WHERE p.evento_id = ?"""
    params = [event_id]
    if registration_id is not None:
        sql += " AND o.inscricao_id = ?"
        params.append(registration_id)
    return query(sql + " ORDER BY p.titulo, o.inscricao_id, o.opcao", params)


def save_testimonial(event_id, nome, texto, foto=None, foto_tipo=None):
    if not nome.strip() or not texto.strip():
        raise ValueError("Informe seu nome e o testemunho.")
    return execute("INSERT INTO testimonials (evento_id,nome,texto,foto,foto_tipo) VALUES (?,?,?,?,?)",
                   (event_id, nome.strip(), texto.strip(), foto, foto_tipo))
