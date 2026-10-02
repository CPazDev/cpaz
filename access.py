"""As duas contas administradoras foram determinadas pelo usuário."""

import time
import re
import database as db

ADMIN_EMAILS = frozenset(("contatomaicondouglass@gmail.com", "projetocpaz@gmail.com"))
LEVELS = ("Administrador", "Gestor de CEv", "Responsável de grupo")


def verified_email(claims):
    if not claims.get("is_logged_in") or claims.get("email_verified") is not True:
        return None
    expiration = claims.get("exp")
    if not isinstance(expiration, (int, float)) or expiration <= time.time():
        return None
    email = claims.get("email", "")
    return email.strip().lower() if isinstance(email, str) and email.strip() else None


def profile(claims):
    email = verified_email(claims)
    if not email:
        return None
    if email in ADMIN_EMAILS:
        return {"email": email, "nivel": "Administrador", "cev": None, "grupo_id": None}
    grants = db.query("SELECT * FROM access_grants WHERE email = ?", (email,))
    if grants and grants[0]["nivel"] in LEVELS:
        return grants[0]
    nome = claims.get("name", "")
    db.execute("""INSERT INTO access_requests (email,nome) VALUES (?,?)
        ON CONFLICT(email) DO UPDATE SET nome = excluded.nome""", (email, str(nome)))
    return None


def can_manage_cev(user, cev):
    return bool(user and (user["nivel"] == "Administrador"
                         or (user["nivel"] == "Gestor de CEv" and user["cev"] == cev)))


def can_use_group(user, group):
    return bool(can_manage_cev(user, group["cev"]) or (user and user["nivel"] == "Responsável de grupo"
                and user["cev"] == group["cev"] and user["grupo_id"] == group["id"]))


def can_view_group_people(user, group):
    if can_manage_cev(user, group["cev"]):
        return True
    if not can_use_group(user, group):
        return False
    return bool(db.query("""SELECT 1 FROM account_profiles a JOIN people p ON p.id=a.membro_id
        WHERE a.email=? AND p.cev=? AND p.categoria IN ('Pastor','Núcleo')
        AND (p.grupo_id=? OR p.id=?)""",
        (user["email"], group["cev"], group["id"], group.get("pastor_id"))))


def can_view_person(user, person):
    if can_manage_cev(user, person["cev"]):
        return True
    if not user or user["nivel"] != "Responsável de grupo" or person["cev"] != user.get("cev"):
        return False
    groups = db.query("SELECT * FROM groups WHERE id=? AND cev=?", (user["grupo_id"], person["cev"]))
    return bool(groups and (person.get("grupo_id") == groups[0]["id"] or person["id"] == groups[0]["pastor_id"])
                and can_view_group_people(user, groups[0]))


def can_manage_accounts(user):
    return bool(user and user["nivel"] in ("Administrador", "Gestor de CEv"))


def delegable_levels(user):
    return LEVELS if user and user["nivel"] == "Administrador" else LEVELS[1:] if can_manage_accounts(user) else ()


def _check_account_scope(actor, target):
    if actor["nivel"] != "Administrador" and target and (
            target["nivel"] not in LEVELS[1:] or target["cev"] != actor["cev"]):
        raise PermissionError("Esta conta possui acesso fora do seu CEv. Somente um administrador pode alterá-la.")


def _locked_actor(conn, claims):
    email = verified_email(claims)
    if email in ADMIN_EMAILS:
        return {"email": email, "nivel": "Administrador", "cev": None, "grupo_id": None}
    row = conn.execute("SELECT * FROM access_grants WHERE email=?", (email,)).fetchone() if email else None
    return dict(row) if row and row["nivel"] in LEVELS else None


def account_grants(claims):
    actor = profile(claims)
    if not can_manage_accounts(actor):
        return []
    rows = db.query("""SELECT a.*,g.nome AS grupo FROM access_grants a
                       LEFT JOIN groups g ON g.id=a.grupo_id ORDER BY a.email""")
    return rows if actor["nivel"] == "Administrador" else [r for r in rows
        if r["cev"] == actor["cev"] and r["nivel"] in LEVELS[1:]]


def can_manage_publication(user, destino, cev=None, grupo_id=None):
    if not user or destino not in ("Geral", "CEv/Irradiação", "Grupo"):
        return False
    if user["nivel"] == "Administrador":
        return True
    if destino == "Geral" or cev != user["cev"]:
        return False
    if destino == "Grupo":
        groups = db.query("SELECT * FROM groups WHERE id=? AND cev=?", (grupo_id, cev))
        return bool(groups and can_use_group(user, groups[0]))
    return can_manage_cev(user, cev)


def can_view_publication(user, event):
    # A consulta pública é livre; a navegação de um responsável fica no seu grupo.
    return bool(not user or user["nivel"] != "Responsável de grupo" or
                (event["destino"] == "Grupo" and event["cev"] == user["cev"]
                 and event["grupo_id"] == user["grupo_id"]))


def grant(claims, email, nivel, cev=None, grupo_id=None):
    actor = profile(claims)
    if not can_manage_accounts(actor):
        raise PermissionError("Sua conta não pode autorizar usuários.")
    email = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("Informe um e-mail válido.")
    if email in ADMIN_EMAILS:
        raise ValueError("As contas administradoras iniciais já possuem acesso completo.")
    if nivel not in LEVELS:
        raise ValueError("Selecione um nível de acesso.")
    if actor["nivel"] != "Administrador" and (nivel not in LEVELS[1:] or cev != actor["cev"]):
        raise PermissionError("Você pode autorizar gestores e responsáveis de grupo somente no seu CEv.")
    if nivel == "Administrador":
        cev, grupo_id = None, None
    elif not cev:
        raise ValueError("Selecione o CEv/Irradiação autorizado.")
    elif nivel == "Gestor de CEv":
        grupo_id = None
    else:
        groups = db.query("SELECT id FROM groups WHERE id = ? AND cev = ?", (grupo_id, cev))
        if not groups:
            raise ValueError("Selecione um grupo do CEv/Irradiação autorizado.")
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        actor = _locked_actor(conn, claims)
        if not can_manage_accounts(actor) or (actor["nivel"] != "Administrador" and
                (nivel not in LEVELS[1:] or cev != actor["cev"])):
            raise PermissionError("Sua conta não pode conceder este acesso.")
        previous = conn.execute("SELECT nivel,cev,grupo_id FROM access_grants WHERE email=?", (email,)).fetchone()
        _check_account_scope(actor, previous)
        if previous and tuple(previous) != (nivel, cev, grupo_id):
            conn.execute("DELETE FROM deletion_permissions WHERE email=?", (email,))
        conn.execute("""INSERT INTO access_grants (email,nivel,cev,grupo_id) VALUES (?,?,?,?)
            ON CONFLICT(email) DO UPDATE SET nivel=excluded.nivel,cev=excluded.cev,grupo_id=excluded.grupo_id""",
            (email, nivel, cev, grupo_id))
        conn.execute("DELETE FROM access_requests WHERE email = ?", (email,))


def revoke(claims, email):
    actor = profile(claims)
    if not can_manage_accounts(actor):
        raise PermissionError("Sua conta não pode revogar acessos.")
    email = email.strip().lower()
    if email in ADMIN_EMAILS:
        raise ValueError("As contas administradoras iniciais devem manter acesso completo.")
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        actor = _locked_actor(conn, claims)
        if not can_manage_accounts(actor):
            raise PermissionError("Sua conta não pode revogar acessos.")
        target = conn.execute("SELECT * FROM access_grants WHERE email=?", (email,)).fetchone()
        _check_account_scope(actor, target)
        conn.execute("DELETE FROM access_grants WHERE email = ?", (email,))
        conn.execute("DELETE FROM deletion_permissions WHERE email = ?", (email,))


def can_delete(claims, cev=None, grupo_id=None):
    """Uma concessão explícita cobre apenas o destino autorizado da conta."""
    actor = profile(claims)
    if not actor:
        return False
    if cev is None:
        return actor["nivel"] == "Administrador" and bool(db.query(
            "SELECT 1 FROM deletion_permissions WHERE email=? AND escopo='Geral'",
            (actor["email"],)))
    if not can_manage_cev(actor, cev):
        group = db.query("SELECT * FROM groups WHERE id=? AND cev=?", (grupo_id, cev))
        if not group or not can_use_group(actor, group[0]):
            return False
    return bool(db.query("""SELECT 1 FROM deletion_permissions WHERE email=? AND cev=?
        AND (escopo=? OR (grupo_id=? AND escopo=?))""",
        (actor["email"], cev, "CEv:" + cev, grupo_id, f"Grupo:{grupo_id}")))


def set_deletion_permission(claims, email, allowed, cev=None, grupo_id=None):
    actor = profile(claims)
    if not actor or actor["email"] not in ADMIN_EMAILS:
        raise PermissionError("Somente os administradores iniciais podem conceder ou retirar a permissão de exclusão.")
    email = email.strip().lower()
    grants = db.query("SELECT * FROM access_grants WHERE email=?", (email,))
    target = {"nivel": "Administrador"} if email in ADMIN_EMAILS else grants[0] if grants else None
    if not target:
        raise ValueError("Autorize esta conta no portal antes de conceder permissão de exclusão.")
    if grupo_id is not None:
        group = db.query("SELECT * FROM groups WHERE id=? AND cev=?", (grupo_id, cev))
        if not group:
            raise ValueError("Selecione um grupo do CEv autorizado.")
    if target["nivel"] == "Gestor de CEv" and (not cev or target["cev"] != cev):
        raise ValueError("A permissão de exclusão deve ficar dentro do CEv desta conta.")
    if target["nivel"] == "Responsável de grupo" and (target["cev"] != cev or target["grupo_id"] != grupo_id):
        raise ValueError("A permissão de exclusão deve ficar dentro do grupo desta conta.")
    if cev is None and target["nivel"] != "Administrador":
        raise ValueError("Somente administradores podem receber permissão para publicações gerais.")
    scope = f"Grupo:{grupo_id}" if grupo_id is not None else "CEv:" + cev if cev else "Geral"
    if allowed:
        db.execute("""INSERT INTO deletion_permissions (email,escopo,cev,grupo_id,autorizado_por)
            VALUES (?,?,?,?,?) ON CONFLICT(email,escopo) DO UPDATE SET autorizado_por=excluded.autorizado_por""",
            (email, scope, cev, grupo_id, actor["email"]))
    else:
        db.execute("DELETE FROM deletion_permissions WHERE email=? AND escopo=?", (email, scope))
