"""Perfil da conta Google e consulta restrita ao membro vinculado."""

import re
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import access
import database as db


def _email(value):
    value = value.strip().lower() if isinstance(value, str) else ""
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        raise ValueError("Informe um e-mail válido da conta Google.")
    return value


def _name(value, label):
    if not isinstance(value, str) or len(value.strip()) > 120:
        raise ValueError(f"{label} deve ter no máximo 120 caracteres.")
    return value.strip()


def own(claims):
    email = access.verified_email(claims)
    if not email:
        return None
    rows = db.query("""SELECT a.*,p.nome AS membro_nome,p.cev AS membro_cev,p.grupo_id,
        g.nome AS grupo,g.neutro FROM account_profiles a LEFT JOIN people p ON p.id=a.membro_id
        LEFT JOIN groups g ON g.id=p.grupo_id WHERE a.email=?""", (email,))
    row = rows[0] if rows else {"email": email, "nome": "", "apelido": "", "membro_id": None,
                              "membro_nome": None, "membro_cev": None, "grupo_id": None, "grupo": None, "neutro": None}
    google_name = claims.get("name")
    row["nome_exibido"] = row["apelido"] or row["nome"] or (google_name if isinstance(google_name, str) and google_name.strip() else "Conta Google")
    row["nome_formulario"] = row["nome"] or (google_name if isinstance(google_name, str) else "")
    return row


def save_own(claims, nome, apelido):
    email = access.verified_email(claims)
    if not email:
        raise PermissionError("Entre com sua conta Google para editar seu perfil.")
    nome, apelido = _name(nome, "Nome"), _name(apelido, "Apelido")
    if not nome:
        raise ValueError("Informe seu nome.")
    db.execute("""INSERT INTO account_profiles(email,nome,apelido) VALUES(?,?,?)
        ON CONFLICT(email) DO UPDATE SET nome=excluded.nome,apelido=excluded.apelido""", (email, nome, apelido))


def candidates(claims):
    email = access.verified_email(claims)
    if not email:
        return []
    return db.query("""SELECT p.id,p.nome,p.cev,g.nome AS grupo FROM people p
        LEFT JOIN groups g ON g.id=p.grupo_id WHERE lower(trim(p.email))=? ORDER BY p.nome,p.id""", (email,))


def self_link(claims, membro_id):
    email = access.verified_email(claims)
    if not email:
        raise PermissionError("Entre com sua conta Google para vincular seu cadastro.")
    if membro_id is not None and (not isinstance(membro_id, int) or isinstance(membro_id, bool)):
        raise ValueError("Selecione seu cadastro de membro.")
    try:
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if membro_id is not None and not conn.execute(
                    "SELECT 1 FROM people WHERE id=? AND lower(trim(email))=?", (membro_id, email)).fetchone():
                raise PermissionError("O e-mail deste cadastro não corresponde à sua conta Google.")
            conn.execute("""INSERT INTO account_profiles(email,membro_id) VALUES(?,?)
                ON CONFLICT(email) DO UPDATE SET membro_id=excluded.membro_id""", (email, membro_id))
    except sqlite3.IntegrityError:
        raise ValueError("Seu cadastro já está vinculado a outra conta. Solicite a revisão à gestão.") from None


def _check_target(conn, actor, email):
    if not access.can_manage_accounts(actor):
        raise PermissionError("Somente administradores e gestores de CEv podem definir vínculos.")
    if actor["nivel"] == "Administrador":
        return
    if email in access.ADMIN_EMAILS:
        raise PermissionError("Somente administradores podem vincular as contas administradoras iniciais.")
    grant = conn.execute("SELECT * FROM access_grants WHERE email=?", (email,)).fetchone()
    access._check_account_scope(actor, grant)
    old = conn.execute("""SELECT p.cev FROM account_profiles a JOIN people p ON p.id=a.membro_id
                           WHERE a.email=?""", (email,)).fetchone()
    if old and old["cev"] != actor["cev"]:
        raise PermissionError("Esta conta está vinculada a um membro de outro CEv.")


def link(claims, email, membro_id):
    email = _email(email)
    if membro_id is not None and (not isinstance(membro_id, int) or isinstance(membro_id, bool)):
        raise ValueError("Selecione um membro válido.")
    try:
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            actor = access._locked_actor(conn, claims)
            _check_target(conn, actor, email)
            if membro_id is not None:
                member = conn.execute("SELECT * FROM people WHERE id=?", (membro_id,)).fetchone()
                if not member:
                    raise ValueError("O membro selecionado não está disponível.")
                if not access.can_manage_cev(actor, member["cev"]):
                    raise PermissionError("Selecione um membro do seu CEv.")
            conn.execute("""INSERT INTO account_profiles(email,membro_id) VALUES(?,?)
                ON CONFLICT(email) DO UPDATE SET membro_id=excluded.membro_id""", (email, membro_id))
    except sqlite3.IntegrityError:
        raise ValueError("Este membro já está vinculado a outra conta. Revise o vínculo existente.") from None


def links(claims):
    actor = access.profile(claims)
    if not access.can_manage_accounts(actor):
        return []
    rows = db.query("""SELECT a.email,a.nome,a.apelido,a.membro_id,p.nome AS membro,p.cev,g.nome AS grupo
        FROM account_profiles a JOIN people p ON p.id=a.membro_id LEFT JOIN groups g ON g.id=p.grupo_id
        ORDER BY p.nome,a.email""")
    if actor["nivel"] == "Administrador":
        return rows
    with db.connection() as conn:
        allowed = []
        for row in rows:
            try:
                _check_target(conn, actor, row["email"])
            except PermissionError:
                continue
            allowed.append(row)
        return allowed


def personal(claims, today=None):
    email = access.verified_email(claims)
    if not email:
        return None
    today = today or datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    # O ID não vem da URL nem do formulário: é resolvido pelo e-mail verificado.
    with db.connection() as conn:
        member = conn.execute("""SELECT p.*,g.nome AS grupo,g.neutro FROM account_profiles a
            JOIN people p ON p.id=a.membro_id LEFT JOIN groups g ON g.id=p.grupo_id WHERE a.email=?""", (email,)).fetchone()
        if not member:
            return None
        attendance = [dict(row) for row in conn.execute("""SELECT e.data,e.tema,g.nome AS grupo,a.presenca
            FROM attendance a JOIN meetings e ON e.id=a.encontro_id JOIN groups g ON g.id=e.grupo_id
            WHERE a.membro_id=? ORDER BY e.data DESC,a.id DESC""", (member["id"],))]
        followups = conn.execute("""SELECT count(CASE WHEN data>=? AND data<=? THEN 1 END) AS no_ano,
            max(CASE WHEN data<=? THEN data END) AS ultima_data FROM followups WHERE membro_id=?""",
            (f"{today.year}-01-01", today.isoformat(), today.isoformat(), member["id"])).fetchone()
        return {"membro": dict(member), "frequencia": attendance,
                "acompanhamentos": dict(followups), "ano": today.year}
