"""Sign-in and roles for the StockFlow app.

Users come from Streamlit secrets (.streamlit/secrets.toml) or the STOCKFLOW_USERS environment variable (JSON):

    [users.priya]
    password = "pbkdf2$200000$<salt>$<hash>"     # make one with:  python auth.py
    role = "approver"                            # viewer | approver | admin
    stores = ""                                  # optional: comma-separated stores this user may see ("" = all)

Roles (see PERMS)
  viewer    sees the plan, problems and stores
  store     + marks dispatch and receipt for its own stores (set "stores")
  approver  + approves lines and trips, releases transfer orders, tracks all stores, reads the audit trail
  admin     + changes settings, manages users

Users can also be created in the app (Admin tab); they are stored in the database with PBKDF2 hashes.
Sign-in is mandatory: with no user anywhere, the app asks for the first admin account before anything else.
Five wrong passwords lock a database user for 15 minutes; a session ends after 30 minutes without activity.

"""
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets as pysecrets

ROLES = {"viewer": 0, "store": 0, "approver": 1, "admin": 2}
# what each role may do (the older can(role, level) check is kept and agrees with this table)
PERMS = {
    "viewer":   {"view"},
    "store":    {"view", "track_own"},                       # mark dispatch / receipt for its own stores
    "approver": {"view", "track_own", "track_all", "approve", "release", "audit"},
    "admin":    {"view", "track_own", "track_all", "approve", "release", "audit", "settings", "users"},
}
LOCK_AFTER, LOCK_MINUTES, IDLE_MINUTES = 5, 15, 30


def hash_password(pw, iterations=200_000):
    salt = pysecrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), iterations).hex()
    return f"pbkdf2${iterations}${salt}${h}"


def check_password(pw, stored):
    try:
        _, it, salt, h = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it)).hex()
        return hmac.compare_digest(got, h)
    except (ValueError, AttributeError):
        return False


def users(st_secrets=None):
    u = {}
    try:
        if st_secrets is not None and "users" in st_secrets:
            u = {k: dict(v) for k, v in st_secrets["users"].items()}
    except Exception:  # noqa: BLE001  (no secrets file)
        u = {}
    if not u and os.environ.get("STOCKFLOW_USERS"):
        u = json.loads(os.environ["STOCKFLOW_USERS"])
    return u


def can(role, need):
    return ROLES.get(role, -1) >= ROLES[need]



def allowed(role, perm):
    return perm in PERMS.get(role, set())


def password_problems(pw, username=""):
    """Password policy: 10+ characters, letters and digits, not the user name."""
    p = []
    if len(pw or "") < 10:
        p.append("at least 10 characters")
    if not re.search(r"[A-Za-z]", pw or "") or not re.search(r"\d", pw or ""):
        p.append("letters and numbers")
    if username and username.lower() in (pw or "").lower():
        p.append("not containing the user name")
    return p


# ---------------------------------------------------------------- users kept in the database (in addition to secrets)
def db_users():
    import db
    d = db.read("users")
    return {r.username: dict(password=r.password, role=r.role, stores=r.stores or "", active=bool(r.active), failed=int(r.failed or 0),
                             locked_until=r.locked_until, must_change=bool(r.must_change)) for r in d.itertuples()}


def all_users(st_secrets=None):
    """Secrets users win over database users with the same name (they are the break-glass accounts)."""
    u = {k: dict(v, active=True, source="secrets") for k, v in users(st_secrets).items()}
    try:
        for k, v in db_users().items():
            u.setdefault(k, dict(v, source="database"))
    except Exception:  # noqa: BLE001  (database not reachable: secrets users still work)
        pass
    return u


def create_user(username, pw, role, stores="", by="", must_change=True):
    import db
    from datetime import datetime
    username = username.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{3,60}", username or ""):
        raise ValueError("User name: 3-60 letters, numbers or . _ @ -")
    if role not in PERMS:
        raise ValueError("Unknown role")
    probs = password_problems(pw, username)
    if probs:
        raise ValueError("Password needs " + ", ".join(probs))
    if username in db_users():
        raise ValueError("That user name already exists")
    db.insert_rows("users", [dict(username=username, password=hash_password(pw), role=role, stores=stores, active=True, failed=0,
                                  created_at=datetime.now(), created_by=by, must_change=must_change)])


def update_user(username, **values):
    import db
    if "password" in values:
        values["password"] = hash_password(values["password"])
    db.update_rows("users", {"username": username}, values)


def attempt(username, pw, st_secrets=None):
    """Check a sign-in. Returns (ok, record_or_None, message). Locks a database user after repeated failures."""
    import db
    from datetime import datetime, timedelta
    u = all_users(st_secrets).get((username or "").strip())
    if not u or not u.get("active", True):
        return False, None, "User name or password not recognised."
    lu = u.get("locked_until")
    if u.get("source") == "database" and lu is not None and lu == lu and lu > datetime.now():
        return False, None, f"Too many attempts. Try again after {lu:%H:%M}."
    if check_password(pw, u.get("password", "")):
        if u.get("source") == "database":
            db.update_rows("users", {"username": username.strip()}, dict(failed=0, locked_until=None, last_login=datetime.now()))
        return True, u, ""
    if u.get("source") == "database":
        f = int(u.get("failed", 0)) + 1
        vals = dict(failed=f)
        if f >= LOCK_AFTER:
            vals.update(failed=0, locked_until=datetime.now() + timedelta(minutes=LOCK_MINUTES))
        db.update_rows("users", {"username": username.strip()}, vals)
        if f >= LOCK_AFTER:
            return False, None, f"Too many attempts. The account is locked for {LOCK_MINUTES} minutes."
    return False, None, "User name or password not recognised."


if __name__ == "__main__":
    print(hash_password(getpass.getpass("New password: ")))
