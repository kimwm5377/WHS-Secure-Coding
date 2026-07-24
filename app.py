import sys

import getpass
import os
import re
import secrets
import sqlite3
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

import click
from flask import (
    Flask,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_socketio import SocketIO, send
from werkzeug.security import check_password_hash, generate_password_hash

DATABASE = "market.db"
SCHEMA_VERSION = "2"
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,20}$")
PASSWORD_MIN_LENGTH = 8
CSRF_SESSION_KEY = "_csrf_token"
LOGIN_FAILURE_WINDOW_SECONDS = 600
LOGIN_FAILURE_LIMIT = 5
DEFAULT_SESSION_LIFETIME = timedelta(minutes=30)
LOGIN_FAILURE_MESSAGE = "아이디 또는 비밀번호가 올바르지 않습니다."
LOGIN_RATE_LIMIT_MESSAGE = "로그인 시도가 너무 많습니다. 잠시 후 다시 시도해주세요."
MISSING_SECRET_KEY_MESSAGE = (
    "SECRET_KEY 환경변수가 필요합니다. 비밀값은 출력하지 않습니다. "
    "예시: export SECRET_KEY=... && export APP_ENV=development"
)
MISSING_DATABASE_MESSAGE = (
    "데이터베이스가 초기화되지 않았습니다. flask --app app init-db 명령으로 새 DB를 생성하세요."
)
UNSUPPORTED_SCHEMA_MESSAGE = (
    "지원하지 않는 데이터베이스 스키마입니다. 자동 파괴 변경은 수행하지 않습니다. "
    "flask --app app init-db 명령으로 새 DB를 명시적으로 초기화하세요."
)
EXPECTED_TABLE_COLUMNS = {
    "schema_meta": ["key", "value"],
    "user": [
        "id",
        "username",
        "password_hash",
        "bio",
        "role",
        "status",
        "balance",
        "created_at",
        "updated_at",
        "suspended_reason",
    ],
    "product": [
        "id",
        "title",
        "description",
        "price",
        "seller_id",
        "status",
        "created_at",
        "updated_at",
        "blocked_reason",
    ],
    "report": [
        "id",
        "reporter_id",
        "target_type",
        "target_id",
        "reason",
        "status",
        "admin_id",
        "action_type",
        "review_note",
        "created_at",
        "reviewed_at",
    ],
}



socketio = SocketIO()
_login_failures = {}


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_app_env(app):
    return str(app.config.get("APP_ENV", "development")).lower()


def is_production_env(app):
    return get_app_env(app) == "production"


def parse_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def resolve_secret_key(app):
    if app.config.get("TESTING") and app.config.get("SECRET_KEY"):
        return app.config["SECRET_KEY"]

    secret_key = os.getenv("SECRET_KEY")
    if not secret_key:
        raise RuntimeError(MISSING_SECRET_KEY_MESSAGE)
    return secret_key


def resolve_debug(app):
    if app.config.get("TESTING"):
        return bool(app.config.get("DEBUG", False))
    if is_production_env(app):
        return False
    return parse_bool(os.getenv("APP_DEBUG"), default=False)


def resolve_cookie_secure(app):
    if app.config.get("_HAS_SESSION_COOKIE_SECURE_OVERRIDE"):
        return bool(app.config["SESSION_COOKIE_SECURE"])
    return is_production_env(app)


def get_client_ip():
    return request.remote_addr or "unknown"


def cleanup_login_failures(now=None):
    now = now or time.time()
    expired_keys = []
    for key, attempts in _login_failures.items():
        kept = [attempt for attempt in attempts if now - attempt < LOGIN_FAILURE_WINDOW_SECONDS]
        if kept:
            _login_failures[key] = kept
        else:
            expired_keys.append(key)
    for key in expired_keys:
        _login_failures.pop(key, None)


def login_failure_key(ip_address, username):
    return f"{ip_address}:{username}"


def record_login_failure(ip_address, username, now=None):
    now = now or time.time()
    cleanup_login_failures(now)
    key = login_failure_key(ip_address, username)
    attempts = _login_failures.setdefault(key, [])
    attempts.append(now)


def clear_login_failures(ip_address, username):
    _login_failures.pop(login_failure_key(ip_address, username), None)


def is_login_limited(ip_address, username, now=None):
    now = now or time.time()
    cleanup_login_failures(now)
    attempts = _login_failures.get(login_failure_key(ip_address, username), [])
    return len(attempts) >= LOGIN_FAILURE_LIMIT


def generate_csrf_token():
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def validate_csrf_or_abort():
    submitted_token = request.form.get("csrf_token", "")
    session_token = session.get(CSRF_SESSION_KEY, "")
    if not submitted_token or not session_token:
        abort(400, description="CSRF token missing.")
    if not secrets.compare_digest(submitted_token, session_token):
        abort(400, description="CSRF token invalid.")


def validate_username(username):
    return bool(USERNAME_PATTERN.fullmatch(username or ""))


def validate_password(password):
    return isinstance(password, str) and len(password) >= PASSWORD_MIN_LENGTH


def normalize_text(value):
    return (value or "").strip()


def validate_product_form(title, description, price_text):
    normalized_title = normalize_text(title)
    normalized_description = normalize_text(description)
    normalized_price = normalize_text(price_text)

    if not normalized_title:
        return None, "상품 제목을 입력해주세요."
    if len(normalized_title) > 100:
        return None, "상품 제목은 100자 이하여야 합니다."
    if not normalized_description:
        return None, "상품 설명을 입력해주세요."
    if len(normalized_description) > 2000:
        return None, "상품 설명은 2000자 이하여야 합니다."
    if not re.fullmatch(r"-?\d+", normalized_price):
        return None, "가격은 0보다 큰 정수만 입력할 수 있습니다."

    price = int(normalized_price)
    if price <= 0:
        return None, "가격은 0보다 큰 정수만 입력할 수 있습니다."

    return {
        "title": normalized_title,
        "description": normalized_description,
        "price": price,
    }, None


def target_exists(target_type, target_id):
    cursor = get_db().cursor()
    if target_type == "user":
        cursor.execute("SELECT id FROM user WHERE id = ?", (target_id,))
    elif target_type == "product":
        cursor.execute("SELECT id FROM product WHERE id = ?", (target_id,))
    else:
        return False
    return cursor.fetchone() is not None


def validate_report_form(target_type, target_id, reason):
    normalized_type = normalize_text(target_type)
    normalized_target_id = normalize_text(target_id)
    normalized_reason = normalize_text(reason)

    if normalized_type not in {"user", "product"}:
        return None, "신고 대상 종류를 올바르게 선택해주세요."
    if not normalized_target_id:
        return None, "신고 대상 ID를 입력해주세요."
    if not normalized_reason:
        return None, "신고 사유를 입력해주세요."
    if len(normalized_reason) > 500:
        return None, "신고 사유는 500자 이하여야 합니다."
    if not target_exists(normalized_type, normalized_target_id):
        return None, "존재하지 않는 신고 대상입니다."

    return {
        "target_type": normalized_type,
        "target_id": normalized_target_id,
        "reason": normalized_reason,
    }, None


def configure_connection(db):
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def connect_database(path):
    return configure_connection(sqlite3.connect(path))


def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = connect_database(current_app.config["DATABASE"])
    return db


def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


def table_exists(connection, table_name):
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def schema_meta_exists(connection):
    return table_exists(connection, "schema_meta")


def get_schema_version(connection):
    if not schema_meta_exists(connection):
        return None
    row = connection.execute(
        "SELECT value FROM schema_meta WHERE key = ?",
        ("schema_version",),
    ).fetchone()
    if row is None:
        return None
    return row["value"]


def get_table_columns(connection, table_name):
    if not table_exists(connection, table_name):
        return None
    if table_name == "schema_meta":
        rows = connection.execute("PRAGMA table_info(schema_meta)").fetchall()
    elif table_name == "user":
        rows = connection.execute("PRAGMA table_info(user)").fetchall()
    elif table_name == "product":
        rows = connection.execute("PRAGMA table_info(product)").fetchall()
    elif table_name == "report":
        rows = connection.execute("PRAGMA table_info(report)").fetchall()
    else:
        return None
    return [row["name"] for row in rows]


def has_expected_phase2_schema(connection):
    for table_name, expected_columns in EXPECTED_TABLE_COLUMNS.items():
        actual_columns = get_table_columns(connection, table_name)
        if actual_columns != expected_columns:
            return False
    return True


def list_app_tables(connection):
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [row["name"] for row in rows]


def create_schema(connection):
    connection.executescript(
        """
CREATE TABLE schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE user (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    bio TEXT,
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    balance INTEGER NOT NULL DEFAULT 100000 CHECK (balance >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    suspended_reason TEXT
);

CREATE TABLE product (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    price INTEGER NOT NULL CHECK (typeof(price) = 'integer' AND price > 0),
    seller_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'blocked')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    blocked_reason TEXT,
    FOREIGN KEY (seller_id) REFERENCES user(id)
);

CREATE TABLE report (
    id TEXT PRIMARY KEY,
    reporter_id TEXT NOT NULL,
    target_type TEXT NOT NULL CHECK (target_type IN ('user', 'product')),
    target_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'dismissed', 'actioned')),
    admin_id TEXT,
    action_type TEXT CHECK (action_type IS NULL OR action_type IN ('none', 'suspend_user', 'block_product')),
    review_note TEXT,
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    FOREIGN KEY (reporter_id) REFERENCES user(id),
    FOREIGN KEY (admin_id) REFERENCES user(id)
);

INSERT INTO schema_meta (key, value) VALUES ('schema_version', '2');
"""
    )
    connection.commit()


def initialize_database_file(db_path, replace=False):
    target_path = os.path.abspath(db_path)
    target_dir = os.path.dirname(target_path) or "."
    os.makedirs(target_dir, exist_ok=True)

    if os.path.exists(target_path) and not replace:
        raise RuntimeError("데이터베이스 파일이 이미 존재합니다. 덮어쓰려면 명시적 옵션을 사용하세요.")

    if replace and os.path.exists(target_path) and os.path.basename(target_path) == DATABASE:
        phase1_backup = os.path.join(target_dir, "market.phase1.db")
        if not os.path.exists(phase1_backup):
            raise RuntimeError("market.phase1.db 백업 파일이 필요합니다. 먼저 현재 market.db를 백업하세요.")

    fd, temp_path = tempfile.mkstemp(prefix=".init-db-", suffix=".sqlite3", dir=target_dir)
    os.close(fd)
    try:
        with connect_database(temp_path) as connection:
            create_schema(connection)
            if get_schema_version(connection) != SCHEMA_VERSION:
                raise RuntimeError("스키마 버전 검증에 실패했습니다.")
        os.replace(temp_path, target_path)
    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


def ensure_database_usable(app):
    db_path = app.config["DATABASE"]
    if not os.path.exists(db_path):
        raise RuntimeError(MISSING_DATABASE_MESSAGE)

    try:
        with connect_database(db_path) as connection:
            if get_schema_version(connection) != SCHEMA_VERSION:
                raise RuntimeError(UNSUPPORTED_SCHEMA_MESSAGE)
            if not has_expected_phase2_schema(connection):
                raise RuntimeError(UNSUPPORTED_SCHEMA_MESSAGE)
    except sqlite3.Error as exc:
        raise RuntimeError(UNSUPPORTED_SCHEMA_MESSAGE) from exc



def should_validate_database_on_startup(app):
    if app.config.get("TESTING"):
        return False
    args = sys.argv[1:]
    if "init-db" in args:
        return False
    if any(arg == "run" for arg in args):
        return True
    return False



def maybe_prepare_testing_database(app):
    if not app.config.get("TESTING"):
        return
    if not app.config.get("AUTO_INIT_DB", True):
        return
    db_path = app.config["DATABASE"]
    if os.path.exists(db_path):
        return
    initialize_database_file(db_path, replace=False)


def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("login"))

        cursor = get_db().cursor()
        cursor.execute("SELECT * FROM user WHERE id = ?", (user_id,))
        current_user = cursor.fetchone()
        if current_user is None:
            session.clear()
            flash("로그인이 필요합니다.")
            return redirect(url_for("login"))

        g.current_user = current_user
        return view_func(*args, **kwargs)

    return wrapped_view


def register_routes(app):
    @app.route("/")
    def index():
        if session.get("user_id"):
            return redirect(url_for("dashboard"))
        return render_template("index.html")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            validate_csrf_or_abort()
            username = normalize_text(request.form.get("username", ""))
            password = request.form.get("password", "")

            if not validate_username(username):
                flash("사용자명은 3~20자의 영문, 숫자, 밑줄만 사용할 수 있습니다.")
                return redirect(url_for("register"))
            if not validate_password(password):
                flash("비밀번호는 최소 8자 이상이어야 합니다.")
                return redirect(url_for("register"))

            db = get_db()
            cursor = db.cursor()
            cursor.execute("SELECT id FROM user WHERE username = ?", (username,))
            if cursor.fetchone() is not None:
                flash("이미 존재하는 사용자명입니다.")
                return redirect(url_for("register"))

            now = utc_now_iso()
            cursor.execute(
                """
                INSERT INTO user (
                    id, username, password_hash, bio, role, status, balance, created_at, updated_at, suspended_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    username,
                    generate_password_hash(password),
                    None,
                    "user",
                    "active",
                    100000,
                    now,
                    now,
                    None,
                ),
            )
            db.commit()
            flash("회원가입이 완료되었습니다. 로그인 해주세요.")
            return redirect(url_for("login"))
        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            validate_csrf_or_abort()
            username = normalize_text(request.form.get("username", ""))
            password = request.form.get("password", "")
            ip_address = get_client_ip()

            if is_login_limited(ip_address, username):
                flash(LOGIN_RATE_LIMIT_MESSAGE)
                return redirect(url_for("login"))

            cursor = get_db().cursor()
            cursor.execute("SELECT * FROM user WHERE username = ?", (username,))
            user = cursor.fetchone()

            if user and check_password_hash(user["password_hash"], password):
                clear_login_failures(ip_address, username)
                session.clear()
                session.permanent = True
                session["user_id"] = user["id"]
                generate_csrf_token()
                flash("로그인 성공!")
                return redirect(url_for("dashboard"))

            record_login_failure(ip_address, username)
            flash(LOGIN_FAILURE_MESSAGE)
            return redirect(url_for("login"))
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    @login_required
    def logout():
        validate_csrf_or_abort()
        session.clear()
        flash("로그아웃되었습니다.")
        return redirect(url_for("index"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        cursor = get_db().cursor()
        cursor.execute("SELECT * FROM product ORDER BY created_at DESC")
        all_products = cursor.fetchall()
        return render_template("dashboard.html", products=all_products, user=g.current_user)

    @app.route("/profile", methods=["GET", "POST"])
    @login_required
    def profile():
        cursor = get_db().cursor()
        if request.method == "POST":
            validate_csrf_or_abort()
            bio = request.form.get("bio", "")
            cursor.execute(
                "UPDATE user SET bio = ?, updated_at = ? WHERE id = ?",
                (bio, utc_now_iso(), g.current_user["id"]),
            )
            get_db().commit()
            flash("프로필이 업데이트되었습니다.")
            cursor.execute("SELECT * FROM user WHERE id = ?", (g.current_user["id"],))
            g.current_user = cursor.fetchone()
            return redirect(url_for("profile"))
        return render_template("profile.html", user=g.current_user)

    @app.route("/product/new", methods=["GET", "POST"])
    @login_required
    def new_product():
        if request.method == "POST":
            validate_csrf_or_abort()
            cleaned, error_message = validate_product_form(
                request.form.get("title", ""),
                request.form.get("description", ""),
                request.form.get("price", ""),
            )
            if error_message:
                flash(error_message)
                return redirect(url_for("new_product"))

            now = utc_now_iso()
            cursor = get_db().cursor()
            cursor.execute(
                """
                INSERT INTO product (
                    id, title, description, price, seller_id, status, created_at, updated_at, blocked_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    cleaned["title"],
                    cleaned["description"],
                    cleaned["price"],
                    g.current_user["id"],
                    "active",
                    now,
                    now,
                    None,
                ),
            )
            get_db().commit()
            flash("상품이 등록되었습니다.")
            return redirect(url_for("dashboard"))
        return render_template("new_product.html")

    @app.route("/product/<product_id>")
    def view_product(product_id):
        cursor = get_db().cursor()
        cursor.execute("SELECT * FROM product WHERE id = ?", (product_id,))
        product = cursor.fetchone()
        if not product:
            flash("상품을 찾을 수 없습니다.")
            return redirect(url_for("dashboard"))
        cursor.execute("SELECT * FROM user WHERE id = ?", (product["seller_id"],))
        seller = cursor.fetchone()
        return render_template("view_product.html", product=product, seller=seller)

    @app.route("/report", methods=["GET", "POST"])
    @login_required
    def report():
        if request.method == "POST":
            validate_csrf_or_abort()
            cleaned, error_message = validate_report_form(
                request.form.get("target_type", ""),
                request.form.get("target_id", ""),
                request.form.get("reason", ""),
            )
            if error_message:
                flash(error_message)
                return redirect(url_for("report"))

            cursor = get_db().cursor()
            cursor.execute(
                """
                INSERT INTO report (
                    id, reporter_id, target_type, target_id, reason, status,
                    admin_id, action_type, review_note, created_at, reviewed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    g.current_user["id"],
                    cleaned["target_type"],
                    cleaned["target_id"],
                    cleaned["reason"],
                    "pending",
                    None,
                    None,
                    None,
                    utc_now_iso(),
                    None,
                ),
            )
            get_db().commit()
            flash("신고가 접수되었습니다.")
            return redirect(url_for("dashboard"))
        return render_template("report.html")


@socketio.on("send_message")
def handle_send_message_event(data):
    send(data, broadcast=True)


def register_cli_commands(app):
    @app.cli.command("init-db")
    @click.option("--replace", is_flag=True, help="기존 DB 파일을 새 스키마 DB로 교체합니다.")
    def init_db_command(replace):
        db_path = current_app.config["DATABASE"]
        try:
            initialize_database_file(db_path, replace=replace)
            with connect_database(db_path) as connection:
                schema_version = get_schema_version(connection)
                table_names = list_app_tables(connection)
            click.echo(f"schema_version={schema_version}")
            click.echo("tables=" + ", ".join(table_names))
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc

    @app.cli.command("create-admin")
    def create_admin_command():
        db_path = current_app.config["DATABASE"]
        try:
            ensure_database_usable(current_app)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc

        username = click.prompt("관리자 사용자명", type=str).strip()
        if not validate_username(username):
            raise click.ClickException("사용자명은 3~20자의 영문, 숫자, 밑줄만 사용할 수 있습니다.")

        password = getpass.getpass("관리자 비밀번호: ")
        password_confirm = getpass.getpass("관리자 비밀번호 확인: ")
        if not validate_password(password):
            raise click.ClickException("비밀번호는 최소 8자 이상이어야 합니다.")
        if password != password_confirm:
            raise click.ClickException("비밀번호와 확인값이 일치하지 않습니다.")

        with connect_database(db_path) as connection:
            existing = connection.execute("SELECT id FROM user WHERE username = ?", (username,)).fetchone()
            if existing is not None:
                raise click.ClickException("이미 존재하는 사용자명입니다.")
            now = utc_now_iso()
            connection.execute(
                """
                INSERT INTO user (
                    id, username, password_hash, bio, role, status, balance, created_at, updated_at, suspended_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    username,
                    generate_password_hash(password),
                    None,
                    "admin",
                    "active",
                    100000,
                    now,
                    now,
                    None,
                ),
            )
            connection.commit()
        click.echo("관리자 계정이 생성되었습니다.")


def configure_app(app):
    app.config["SECRET_KEY"] = resolve_secret_key(app)
    app.config["DEBUG"] = resolve_debug(app)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = resolve_cookie_secure(app)
    app.config["PERMANENT_SESSION_LIFETIME"] = DEFAULT_SESSION_LIFETIME
    app.config["DATABASE"] = app.config.get("DATABASE") or os.getenv("MARKET_DB_PATH", DATABASE)


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        APP_ENV=os.getenv("APP_ENV", "development"),
        DATABASE=os.getenv("MARKET_DB_PATH", DATABASE),
        TESTING=False,
        AUTO_INIT_DB=True,
    )
    if test_config:
        app.config.update(test_config)
        app.config["_HAS_SESSION_COOKIE_SECURE_OVERRIDE"] = "SESSION_COOKIE_SECURE" in test_config
    else:
        app.config["_HAS_SESSION_COOKIE_SECURE_OVERRIDE"] = False

    configure_app(app)
    maybe_prepare_testing_database(app)
    if should_validate_database_on_startup(app):
        try:
            ensure_database_usable(app)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
    app.teardown_appcontext(close_connection)
    app.jinja_env.globals["csrf_token"] = generate_csrf_token
    register_routes(app)
    register_cli_commands(app)

    socketio.init_app(app)
    return app


if __name__ == "__main__":
    try:
        app = create_app()
        ensure_database_usable(app)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc

    socketio.run(
        app,
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "5000")),
        debug=app.config["DEBUG"],
    )
