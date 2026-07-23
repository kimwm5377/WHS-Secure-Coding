import os
import re
import secrets
import sqlite3
import time
import uuid
from datetime import timedelta
from functools import wraps

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
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,20}$")
PASSWORD_MIN_LENGTH = 8
CSRF_SESSION_KEY = "_csrf_token"
LOGIN_FAILURE_WINDOW_SECONDS = 600
LOGIN_FAILURE_LIMIT = 5
DEFAULT_SESSION_LIFETIME = timedelta(minutes=30)
PASSWORD_HASH_PREFIXES = ("scrypt:", "pbkdf2:")
LOGIN_FAILURE_MESSAGE = "아이디 또는 비밀번호가 올바르지 않습니다."
LOGIN_RATE_LIMIT_MESSAGE = "로그인 시도가 너무 많습니다. 잠시 후 다시 시도해주세요."
MISSING_SECRET_KEY_MESSAGE = (
    "SECRET_KEY 환경변수가 필요합니다. 비밀값은 출력하지 않습니다. "
    "예시: export SECRET_KEY=... && export APP_ENV=development"
)

socketio = SocketIO()
_login_failures = {}


def is_password_hash(value):
    return isinstance(value, str) and value.startswith(PASSWORD_HASH_PREFIXES)


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


def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(current_app.config["DATABASE"])
        db.row_factory = sqlite3.Row
    return db


def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            bio TEXT
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS product (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            price TEXT NOT NULL,
            seller_id TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS report (
            id TEXT PRIMARY KEY,
            reporter_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            reason TEXT NOT NULL
        )
        """
    )
    db.commit()


def migrate_plaintext_passwords():
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id, password FROM user")
    users = cursor.fetchall()

    plaintext_users = []
    for user in users:
        password_value = user["password"]
        if not password_value or is_password_hash(password_value):
            continue
        plaintext_users.append((user["id"], password_value))

    if not plaintext_users:
        return

    db_path = os.path.abspath(current_app.config["DATABASE"])
    if not current_app.config.get("TESTING") and os.path.basename(db_path) == DATABASE:
        backup_path = os.path.join(os.path.dirname(db_path), "market.baseline.db")
        if not os.path.exists(backup_path):
            raise RuntimeError(
                "market.baseline.db 백업 파일이 필요합니다. 먼저 market.db를 market.baseline.db로 백업하세요."
            )

    try:
        cursor.execute("BEGIN")
        for user_id, password_value in plaintext_users:
            cursor.execute(
                "UPDATE user SET password = ? WHERE id = ?",
                (generate_password_hash(password_value), user_id),
            )
        db.commit()
    except Exception as exc:
        db.rollback()
        raise RuntimeError("비밀번호 마이그레이션에 실패했습니다.") from exc


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
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")

            if not validate_username(username):
                flash("사용자명은 3~20자의 영문, 숫자, 밑줄만 사용할 수 있습니다.")
                return redirect(url_for("register"))
            if not validate_password(password):
                flash("비밀번호는 최소 8자 이상이어야 합니다.")
                return redirect(url_for("register"))

            db = get_db()
            cursor = db.cursor()
            cursor.execute("SELECT * FROM user WHERE username = ?", (username,))
            if cursor.fetchone() is not None:
                flash("이미 존재하는 사용자명입니다.")
                return redirect(url_for("register"))

            user_id = str(uuid.uuid4())
            cursor.execute(
                "INSERT INTO user (id, username, password) VALUES (?, ?, ?)",
                (user_id, username, generate_password_hash(password)),
            )
            db.commit()
            flash("회원가입이 완료되었습니다. 로그인 해주세요.")
            return redirect(url_for("login"))
        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            validate_csrf_or_abort()
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            ip_address = get_client_ip()

            if is_login_limited(ip_address, username):
                flash(LOGIN_RATE_LIMIT_MESSAGE)
                return redirect(url_for("login"))

            cursor = get_db().cursor()
            cursor.execute("SELECT * FROM user WHERE username = ?", (username,))
            user = cursor.fetchone()

            if user and check_password_hash(user["password"], password):
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
        cursor.execute("SELECT * FROM product")
        all_products = cursor.fetchall()
        return render_template("dashboard.html", products=all_products, user=g.current_user)

    @app.route("/profile", methods=["GET", "POST"])
    @login_required
    def profile():
        cursor = get_db().cursor()
        if request.method == "POST":
            validate_csrf_or_abort()
            bio = request.form.get("bio", "")
            cursor.execute("UPDATE user SET bio = ? WHERE id = ?", (bio, g.current_user["id"]))
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
            title = request.form.get("title", "")
            description = request.form.get("description", "")
            price = request.form.get("price", "")
            product_id = str(uuid.uuid4())
            cursor = get_db().cursor()
            cursor.execute(
                "INSERT INTO product (id, title, description, price, seller_id) VALUES (?, ?, ?, ?, ?)",
                (product_id, title, description, price, g.current_user["id"]),
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
            target_id = request.form.get("target_id", "")
            reason = request.form.get("reason", "")
            report_id = str(uuid.uuid4())
            cursor = get_db().cursor()
            cursor.execute(
                "INSERT INTO report (id, reporter_id, target_id, reason) VALUES (?, ?, ?, ?)",
                (report_id, g.current_user["id"], target_id, reason),
            )
            get_db().commit()
            flash("신고가 접수되었습니다.")
            return redirect(url_for("dashboard"))
        return render_template("report.html")


@socketio.on("send_message")
def handle_send_message_event(data):
    send(data, broadcast=True)


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
    )
    if test_config:
        app.config.update(test_config)
        app.config["_HAS_SESSION_COOKIE_SECURE_OVERRIDE"] = "SESSION_COOKIE_SECURE" in test_config
    else:
        app.config["_HAS_SESSION_COOKIE_SECURE_OVERRIDE"] = False

    configure_app(app)
    app.teardown_appcontext(close_connection)
    app.jinja_env.globals["csrf_token"] = generate_csrf_token
    register_routes(app)
    socketio.init_app(app)

    with app.app_context():
        init_db()
        migrate_plaintext_passwords()

    return app


if __name__ == "__main__":
    app = create_app()
    socketio.run(
        app,
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "5000")),
        debug=app.config["DEBUG"],
    )
