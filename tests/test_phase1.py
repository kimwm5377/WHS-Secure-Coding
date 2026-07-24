import ast
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from werkzeug.security import check_password_hash

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import (
    LOGIN_FAILURE_MESSAGE,
    create_app,
    socketio,
    _login_failures,
)


class Phase1TestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_market.db")
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-secret-key",
                "DATABASE": self.db_path,
                "APP_ENV": "development",
                "SESSION_COOKIE_SECURE": False,
                "AUTO_INIT_DB": True,
            }
        )
        self.client = self.app.test_client()
        _login_failures.clear()

    def tearDown(self):
        _login_failures.clear()
        self.temp_dir.cleanup()

    def extract_csrf_token(self, response_text):
        match = re.search(r'name="csrf_token" value="([^"]+)"', response_text)
        self.assertIsNotNone(match, "CSRF token not found in response")
        return match.group(1)

    def get_csrf(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return self.extract_csrf_token(response.get_data(as_text=True))

    def register_user(self, username="phase1_user", password="password123"):
        response = self.client.post(
            "/register",
            data={
                "csrf_token": self.get_csrf("/register"),
                "username": username,
                "password": password,
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        return response

    def login_user(self, username="phase1_user", password="password123", remote_addr="127.0.0.1"):
        response = self.client.post(
            "/login",
            data={
                "csrf_token": self.get_csrf("/login"),
                "username": username,
                "password": password,
            },
            environ_overrides={"REMOTE_ADDR": remote_addr},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        return response

    def test_T001_register_success(self):
        response = self.register_user()
        self.assertIn("회원가입이 완료되었습니다. 로그인 해주세요.", response.get_data(as_text=True))

    def test_T002_register_validation_and_duplicates(self):
        self.register_user(username="dup_user", password="password123")

        duplicate_response = self.client.post(
            "/register",
            data={
                "csrf_token": self.get_csrf("/register"),
                "username": "dup_user",
                "password": "password123",
            },
            follow_redirects=True,
        )
        self.assertIn("이미 존재하는 사용자명입니다.", duplicate_response.get_data(as_text=True))

        invalid_username_response = self.client.post(
            "/register",
            data={
                "csrf_token": self.get_csrf("/register"),
                "username": "한글",
                "password": "password123",
            },
            follow_redirects=True,
        )
        self.assertIn("사용자명은 3~20자의 영문, 숫자, 밑줄만 사용할 수 있습니다.", invalid_username_response.get_data(as_text=True))

        invalid_password_response = self.client.post(
            "/register",
            data={
                "csrf_token": self.get_csrf("/register"),
                "username": "valid_user",
                "password": "short",
            },
            follow_redirects=True,
        )
        self.assertIn("비밀번호는 최소 8자 이상이어야 합니다.", invalid_password_response.get_data(as_text=True))

    def test_T003_login_success_and_hash_verification(self):
        self.register_user(username="login_user", password="password123")
        response = self.login_user(username="login_user", password="password123")
        self.assertIn("로그인 성공!", response.get_data(as_text=True))
        self.assertIn("대시보드", response.get_data(as_text=True))

    def test_T004_post_logout_clears_session(self):
        self.register_user(username="logout_user", password="password123")
        self.login_user(username="logout_user", password="password123")

        dashboard = self.client.get("/dashboard")
        csrf_token = self.extract_csrf_token(dashboard.get_data(as_text=True))

        get_logout = self.client.get("/logout")
        self.assertEqual(get_logout.status_code, 405)

        response = self.client.post("/logout", data={"csrf_token": csrf_token}, follow_redirects=True)
        self.assertIn("로그아웃되었습니다.", response.get_data(as_text=True))
        with self.client.session_transaction() as session_data:
            self.assertNotIn("user_id", session_data)

        blocked_response = self.client.get("/dashboard", follow_redirects=True)
        self.assertIn("로그인", blocked_response.get_data(as_text=True))

    def test_T005_profile_update_success(self):
        self.register_user(username="profile_user", password="password123")
        self.login_user(username="profile_user", password="password123")
        response = self.client.post(
            "/profile",
            data={"csrf_token": self.get_csrf("/profile"), "bio": "updated bio"},
            follow_redirects=True,
        )
        self.assertIn("프로필이 업데이트되었습니다.", response.get_data(as_text=True))
        self.assertIn("updated bio", response.get_data(as_text=True))

    def test_T101_passwords_are_hashed(self):
        self.register_user(username="hashed_user", password="password123")
        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT password_hash, role, status, balance FROM user WHERE username = ?",
                ("hashed_user",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertNotEqual(row[0], "password123")
        self.assertTrue(row[0].startswith(("scrypt:", "pbkdf2:")))
        self.assertTrue(check_password_hash(row[0], "password123"))
        self.assertEqual(row[1], "user")
        self.assertEqual(row[2], "active")
        self.assertEqual(row[3], 100000)

    def test_T102_secret_key_required(self):
        env = os.environ.copy()
        env.pop("SECRET_KEY", None)
        env.pop("APP_DEBUG", None)
        env["APP_ENV"] = "development"
        env["MARKET_DB_PATH"] = str(Path(self.temp_dir.name) / "missing_secret.db")
        result = subprocess.run(
            [sys.executable, "-c", "from app import create_app; create_app({'TESTING': False, 'AUTO_INIT_DB': False})"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SECRET_KEY 환경변수가 필요합니다.", result.stderr or result.stdout)

        ok_env = os.environ.copy()
        ok_env["SECRET_KEY"] = "test-secret-from-env"
        ok_env["APP_ENV"] = "development"
        ok_db_path = str(Path(self.temp_dir.name) / "with_secret.db")
        ok_env["MARKET_DB_PATH"] = ok_db_path
        ok_result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from app import create_app; create_app({'TESTING': True, 'DATABASE': r'" + ok_db_path + "'}); print('initialized')",
            ],
            cwd=REPO_ROOT,
            env=ok_env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(ok_result.returncode, 0)
        self.assertIn("initialized", ok_result.stdout)

    def test_T103_csrf_protection_on_state_changes(self):
        self.assertEqual(
            self.client.post("/register", data={"username": "a_user", "password": "password123"}).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/register",
                data={
                    "csrf_token": self.get_csrf("/register") + "x",
                    "username": "a_user",
                    "password": "password123",
                },
            ).status_code,
            400,
        )

        self.register_user(username="csrf_user", password="password123")
        self.assertEqual(
            self.client.post("/login", data={"username": "csrf_user", "password": "password123"}).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/login",
                data={
                    "csrf_token": self.get_csrf("/login") + "x",
                    "username": "csrf_user",
                    "password": "password123",
                },
            ).status_code,
            400,
        )

        self.login_user(username="csrf_user", password="password123")
        for path, payload in [
            ("/profile", {"bio": "csrf"}),
            ("/product/new", {"title": "item", "description": "desc", "price": "1000"}),
            ("/report", {"target_type": "user", "target_id": "missing", "reason": "reason"}),
        ]:
            self.assertEqual(self.client.post(path, data=payload).status_code, 400)
            self.assertEqual(
                self.client.post(path, data={**payload, "csrf_token": self.get_csrf(path) + "x"}).status_code,
                400,
            )

        dashboard = self.client.get("/dashboard")
        logout_token = self.extract_csrf_token(dashboard.get_data(as_text=True))
        self.assertEqual(self.client.post("/logout", data={}).status_code, 400)
        self.assertEqual(self.client.post("/logout", data={"csrf_token": logout_token + "x"}).status_code, 400)
        valid_logout = self.client.post("/logout", data={"csrf_token": logout_token}, follow_redirects=True)
        self.assertEqual(valid_logout.status_code, 200)
        self.assertIn("로그아웃되었습니다.", valid_logout.get_data(as_text=True))

    def test_T106_login_required_routes_are_protected(self):
        for path in ["/dashboard", "/profile", "/product/new", "/report"]:
            response = self.client.get(path, follow_redirects=True)
            self.assertIn("로그인", response.get_data(as_text=True))

    def test_T109_session_cookie_attributes(self):
        self.register_user(username="cookie_user", password="password123")
        response = self.client.post(
            "/login",
            data={
                "csrf_token": self.get_csrf("/login"),
                "username": "cookie_user",
                "password": "password123",
            },
        )
        cookie_header = "\n".join(response.headers.getlist("Set-Cookie"))
        self.assertIn("HttpOnly", cookie_header)
        self.assertIn("SameSite=Lax", cookie_header)
        self.assertNotIn("Secure", cookie_header)

        production_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "production-secret",
                "DATABASE": str(Path(self.temp_dir.name) / "production.db"),
                "APP_ENV": "production",
                "AUTO_INIT_DB": True,
            }
        )
        production_client = production_app.test_client()
        production_client.post(
            "/register",
            data={
                "csrf_token": self.extract_csrf_token(production_client.get("/register").get_data(as_text=True)),
                "username": "prod_user",
                "password": "password123",
            },
        )
        production_response = production_client.post(
            "/login",
            data={
                "csrf_token": self.extract_csrf_token(production_client.get("/login").get_data(as_text=True)),
                "username": "prod_user",
                "password": "password123",
            },
        )
        production_cookie = "\n".join(production_response.headers.getlist("Set-Cookie"))
        self.assertIn("HttpOnly", production_cookie)
        self.assertIn("SameSite=Lax", production_cookie)
        self.assertIn("Secure", production_cookie)

    def test_T110_login_rate_limit_behavior(self):
        self.register_user(username="rate_user", password="password123")

        wrong_password_response = self.client.post(
            "/login",
            data={
                "csrf_token": self.get_csrf("/login"),
                "username": "rate_user",
                "password": "wrongpass",
            },
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            follow_redirects=True,
        )
        self.assertIn(LOGIN_FAILURE_MESSAGE, wrong_password_response.get_data(as_text=True))

        nonexistent_response = self.client.post(
            "/login",
            data={
                "csrf_token": self.get_csrf("/login"),
                "username": "missing_user",
                "password": "wrongpass",
            },
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            follow_redirects=True,
        )
        self.assertIn(LOGIN_FAILURE_MESSAGE, nonexistent_response.get_data(as_text=True))

        for _ in range(3):
            response = self.client.post(
                "/login",
                data={
                    "csrf_token": self.get_csrf("/login"),
                    "username": "rate_user",
                    "password": "wrongpass",
                },
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
                follow_redirects=True,
            )
            self.assertIn(LOGIN_FAILURE_MESSAGE, response.get_data(as_text=True))

        success_response = self.login_user(username="rate_user", password="password123", remote_addr="127.0.0.1")
        self.assertIn("로그인 성공!", success_response.get_data(as_text=True))
        self.client.post(
            "/logout",
            data={"csrf_token": self.extract_csrf_token(self.client.get("/dashboard").get_data(as_text=True))},
            follow_redirects=True,
        )

        for attempt in range(5):
            response = self.client.post(
                "/login",
                data={
                    "csrf_token": self.get_csrf("/login"),
                    "username": "rate_user",
                    "password": "wrongpass",
                },
                environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
                follow_redirects=True,
            )
            self.assertIn(LOGIN_FAILURE_MESSAGE, response.get_data(as_text=True), f"attempt {attempt + 1} should record a failure")

        sixth_response = self.client.post(
            "/login",
            data={
                "csrf_token": self.get_csrf("/login"),
                "username": "rate_user",
                "password": "wrongpass",
            },
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
            follow_redirects=True,
        )
        self.assertIn("로그인 시도가 너무 많습니다. 잠시 후 다시 시도해주세요.", sixth_response.get_data(as_text=True))

    def test_T114_debug_defaults_to_false(self):
        with mock.patch.dict(os.environ, {"SECRET_KEY": "debug-secret", "APP_ENV": "development"}, clear=False):
            development_app = create_app(
                {"TESTING": False, "DATABASE": str(Path(self.temp_dir.name) / "debug_dev.db"), "AUTO_INIT_DB": False}
            )
            self.assertFalse(development_app.config["DEBUG"])

        with mock.patch.dict(
            os.environ,
            {"SECRET_KEY": "debug-secret", "APP_ENV": "production", "APP_DEBUG": "true"},
            clear=False,
        ):
            production_app = create_app(
                {"TESTING": False, "DATABASE": str(Path(self.temp_dir.name) / "debug_prod.db"), "AUTO_INIT_DB": False}
            )
            self.assertFalse(production_app.config["DEBUG"])

        env = os.environ.copy()
        env["SECRET_KEY"] = "debug-secret"
        env["APP_ENV"] = "development"
        env.pop("APP_DEBUG", None)
        env["APP_PORT"] = "5123"
        env["MARKET_DB_PATH"] = str(Path(self.temp_dir.name) / "debug_run.db")
        runner_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "debug-secret",
                "DATABASE": env["MARKET_DB_PATH"],
                "APP_ENV": "development",
                "AUTO_INIT_DB": True,
            }
        )
        del runner_app
        process = subprocess.Popen(
            [sys.executable, "app.py"],
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        time.sleep(2)
        process.terminate()
        output, _ = process.communicate(timeout=10)
        self.assertNotIn("Debugger PIN", output)

    def test_T115_sql_uses_parameter_binding(self):
        source = Path(REPO_ROOT / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        class SqlCallVisitor(ast.NodeVisitor):
            def __init__(self):
                self.total_calls = 0
                self.forbidden_patterns = []

            def visit_Call(self, node):
                if isinstance(node.func, ast.Attribute) and node.func.attr in {"execute", "executemany", "executescript"}:
                    self.total_calls += 1
                    sql_arg = node.args[0] if node.args else None
                    if sql_arg is None:
                        self.forbidden_patterns.append((node.lineno, "missing-sql-argument"))
                    elif node.func.attr == "executescript":
                        if not self.is_string_literal(sql_arg):
                            self.forbidden_patterns.append((node.lineno, "dynamic-executescript"))
                    else:
                        if self.is_forbidden_sql_expression(sql_arg) or not self.is_string_literal(sql_arg):
                            self.forbidden_patterns.append((node.lineno, "dynamic-sql"))
                self.generic_visit(node)

            def is_string_literal(self, node):
                return isinstance(node, ast.Constant) and isinstance(node.value, str)

            def is_forbidden_sql_expression(self, node):
                if isinstance(node, ast.JoinedStr):
                    return True
                if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
                    return True
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Attribute) and node.func.attr == "format":
                        return True
                    return True
                return False

        visitor = SqlCallVisitor()
        visitor.visit(tree)
        print(f"T-115 inspected {visitor.total_calls} SQL calls; forbidden patterns found: {len(visitor.forbidden_patterns)}")
        self.assertGreater(visitor.total_calls, 0)
        self.assertEqual(len(visitor.forbidden_patterns), 0)

    def test_regression_product_create_detail_report_and_chat(self):
        self.register_user(username="regression_user", password="password123")
        self.login_user(username="regression_user", password="password123")

        product_response = self.client.post(
            "/product/new",
            data={
                "csrf_token": self.get_csrf("/product/new"),
                "title": "Regression Product",
                "description": "Regression Description",
                "price": "1000",
            },
            follow_redirects=True,
        )
        self.assertIn("상품이 등록되었습니다.", product_response.get_data(as_text=True))
        self.assertIn("Regression Product", product_response.get_data(as_text=True))

        import sqlite3

        with sqlite3.connect(self.db_path) as connection:
            product_row = connection.execute("SELECT id FROM product WHERE title = ?", ("Regression Product",)).fetchone()
        self.assertIsNotNone(product_row)

        detail_response = self.client.get(f"/product/{product_row[0]}")
        detail_text = detail_response.get_data(as_text=True)
        self.assertIn("Regression Product", detail_text)
        self.assertIn("Regression Description", detail_text)

        with sqlite3.connect(self.db_path) as connection:
            user_row = connection.execute("SELECT id FROM user WHERE username = ?", ("regression_user",)).fetchone()
        report_response = self.client.post(
            "/report",
            data={
                "csrf_token": self.get_csrf("/report"),
                "target_type": "user",
                "target_id": user_row[0],
                "reason": "Regression report",
            },
            follow_redirects=True,
        )
        self.assertIn("신고가 접수되었습니다.", report_response.get_data(as_text=True))

        chat_client_one = socketio.test_client(self.app, flask_test_client=self.client)
        chat_client_two = socketio.test_client(self.app, flask_test_client=self.client)
        try:
            chat_client_one.emit("send_message", {"username": "regression_user", "message": "hello chat"})
            received_one = chat_client_one.get_received()
            received_two = chat_client_two.get_received()
            self.assertTrue(any(event["name"] == "message" for event in received_one))
            self.assertTrue(any(event["name"] == "message" for event in received_two))
        finally:
            chat_client_one.disconnect()
            chat_client_two.disconnect()


if __name__ == "__main__":
    unittest.main(verbosity=2)
