import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import SCHEMA_VERSION, create_app, get_db, socketio


class Phase2TestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "phase2_market.db")
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "phase2-test-secret",
                "DATABASE": self.db_path,
                "APP_ENV": "development",
                "SESSION_COOKIE_SECURE": False,
                "AUTO_INIT_DB": True,
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def extract_csrf_token(self, response_text):
        import re

        match = re.search(r'name="csrf_token" value="([^"]+)"', response_text)
        self.assertIsNotNone(match, "CSRF token not found in response")
        return match.group(1)

    def get_csrf(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return self.extract_csrf_token(response.get_data(as_text=True))

    def register_user(self, username="phase2_user", password="password123", **extra):
        data = {"csrf_token": self.get_csrf("/register"), "username": username, "password": password}
        data.update(extra)
        response = self.client.post("/register", data=data, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        return response

    def login_user(self, username="phase2_user", password="password123"):
        response = self.client.post(
            "/login",
            data={"csrf_token": self.get_csrf("/login"), "username": username, "password": password},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        return response

    def fetch_user(self, username):
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute("SELECT * FROM user WHERE username = ?", (username,)).fetchone()

    def fetch_product(self, title):
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute("SELECT * FROM product WHERE title = ?", (title,)).fetchone()

    def fetch_report(self, reason):
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute("SELECT * FROM report WHERE reason = ?", (reason,)).fetchone()

    def create_old_schema_db(self, path):
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE user (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, bio TEXT)"
            )
            connection.commit()

    def list_tables(self, path):
        with sqlite3.connect(path) as connection:
            return [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
            ]

    def run_app_subprocess(self, command, db_path):
        env = os.environ.copy()
        env["SECRET_KEY"] = "phase2-old-schema-secret"
        env["APP_ENV"] = "development"
        env["MARKET_DB_PATH"] = str(db_path)
        return subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )


    def test_T006_product_create_stores_integer_price(self):
        self.register_user(username="seller1")
        self.login_user(username="seller1")
        response = self.client.post(
            "/product/new",
            data={
                "csrf_token": self.get_csrf("/product/new"),
                "title": "정상 상품",
                "description": "정상 설명",
                "price": "12345",
                "seller_id": "forged-seller",
            },
            follow_redirects=True,
        )
        self.assertIn("상품이 등록되었습니다.", response.get_data(as_text=True))
        product = self.fetch_product("정상 상품")
        self.assertIsNotNone(product)
        self.assertEqual(product["price"], 12345)
        self.assertEqual(product["status"], "active")

    def test_T007_product_detail_shows_seller(self):
        self.register_user(username="seller2")
        self.login_user(username="seller2")
        self.client.post(
            "/product/new",
            data={
                "csrf_token": self.get_csrf("/product/new"),
                "title": "상세 상품",
                "description": "상세 설명",
                "price": "5000",
            },
            follow_redirects=True,
        )
        product = self.fetch_product("상세 상품")
        response = self.client.get(f"/product/{product['id']}")
        text = response.get_data(as_text=True)
        self.assertIn("상세 상품", text)
        self.assertIn("상세 설명", text)
        self.assertIn("seller2", text)

    def test_T009_report_is_saved_as_pending(self):
        self.register_user(username="reporter")
        self.register_user(username="target_user")
        self.login_user(username="reporter")
        target_user = self.fetch_user("target_user")
        response = self.client.post(
            "/report",
            data={
                "csrf_token": self.get_csrf("/report"),
                "target_type": "user",
                "target_id": target_user["id"],
                "reason": "정상 신고 사유",
            },
            follow_redirects=True,
        )
        self.assertIn("신고가 접수되었습니다.", response.get_data(as_text=True))
        report = self.fetch_report("정상 신고 사유")
        self.assertEqual(report["status"], "pending")
        self.assertEqual(report["target_type"], "user")
        self.assertIsNone(report["admin_id"])
        self.assertIsNone(report["action_type"])
        self.assertIsNone(report["review_note"])
        self.assertIsNone(report["reviewed_at"])

    def test_T010_report_does_not_trigger_automatic_action(self):
        self.register_user(username="seller3")
        self.login_user(username="seller3")
        self.client.post(
            "/product/new",
            data={
                "csrf_token": self.get_csrf("/product/new"),
                "title": "신고 대상 상품",
                "description": "설명",
                "price": "3000",
            },
            follow_redirects=True,
        )
        product = self.fetch_product("신고 대상 상품")
        seller = self.fetch_user("seller3")
        self.client.post(
            "/report",
            data={
                "csrf_token": self.get_csrf("/report"),
                "target_type": "product",
                "target_id": product["id"],
                "reason": "상품 신고",
            },
            follow_redirects=True,
        )
        product_after = self.fetch_product("신고 대상 상품")
        seller_after = self.fetch_user("seller3")
        self.assertEqual(product_after["status"], "active")
        self.assertIsNone(product_after["blocked_reason"])
        self.assertEqual(seller_after["status"], "active")
        self.assertIsNone(seller_after["suspended_reason"])

    def test_T104_product_validation(self):
        self.register_user(username="validator")
        self.login_user(username="validator")
        invalid_cases = [
            {"title": "   ", "description": "설명", "price": "1000", "message": "상품 제목을 입력해주세요."},
            {"title": "a" * 101, "description": "설명", "price": "1000", "message": "상품 제목은 100자 이하여야 합니다."},
            {"title": "제목", "description": "   ", "price": "1000", "message": "상품 설명을 입력해주세요."},
            {"title": "제목", "description": "a" * 2001, "price": "1000", "message": "상품 설명은 2000자 이하여야 합니다."},
            {"title": "제목", "description": "설명", "price": "abc", "message": "가격은 0보다 큰 정수만 입력할 수 있습니다."},
            {"title": "제목", "description": "설명", "price": "12.3", "message": "가격은 0보다 큰 정수만 입력할 수 있습니다."},
            {"title": "제목", "description": "설명", "price": "0", "message": "가격은 0보다 큰 정수만 입력할 수 있습니다."},
            {"title": "제목", "description": "설명", "price": "-1", "message": "가격은 0보다 큰 정수만 입력할 수 있습니다."},
        ]
        for case in invalid_cases:
            response = self.client.post(
                "/product/new",
                data={"csrf_token": self.get_csrf("/product/new"), **{k: v for k, v in case.items() if k != "message"}},
                follow_redirects=True,
            )
            self.assertIn(case["message"], response.get_data(as_text=True))

    def test_T104_report_validation(self):
        self.register_user(username="report_validator")
        self.register_user(username="report_target")
        self.login_user(username="report_validator")
        target_user = self.fetch_user("report_target")
        invalid_cases = [
            {"target_type": "invalid", "target_id": target_user["id"], "reason": "사유", "message": "신고 대상 종류를 올바르게 선택해주세요."},
            {"target_type": "user", "target_id": "missing-id", "reason": "사유", "message": "존재하지 않는 신고 대상입니다."},
            {"target_type": "user", "target_id": target_user["id"], "reason": "   ", "message": "신고 사유를 입력해주세요."},
            {"target_type": "user", "target_id": target_user["id"], "reason": "a" * 501, "message": "신고 사유는 500자 이하여야 합니다."},
        ]
        for case in invalid_cases:
            response = self.client.post(
                "/report",
                data={"csrf_token": self.get_csrf("/report"), **{k: v for k, v in case.items() if k != "message"}},
                follow_redirects=True,
            )
            self.assertIn(case["message"], response.get_data(as_text=True))

    def test_T105_boundary_validation(self):
        self.register_user(username="boundary_user")
        self.login_user(username="boundary_user")
        title = "t" * 100
        description = "d" * 2000
        reason = "r" * 500
        product_response = self.client.post(
            "/product/new",
            data={
                "csrf_token": self.get_csrf("/product/new"),
                "title": title,
                "description": description,
                "price": "1",
            },
            follow_redirects=True,
        )
        self.assertIn("상품이 등록되었습니다.", product_response.get_data(as_text=True))
        target_user = self.fetch_user("boundary_user")
        report_response = self.client.post(
            "/report",
            data={
                "csrf_token": self.get_csrf("/report"),
                "target_type": "user",
                "target_id": target_user["id"],
                "reason": reason,
            },
            follow_redirects=True,
        )
        self.assertIn("신고가 접수되었습니다.", report_response.get_data(as_text=True))

    def test_T201_sqlite_sqlite3_foreign_keys_and_schema_version(self):
        source = Path(REPO_ROOT / "app.py").read_text(encoding="utf-8").lower()
        self.assertIn("sqlite3", source)
        self.assertNotIn("sqlalchemy", source)

        with sqlite3.connect(self.db_path) as connection:
            fk_value = connection.execute("PRAGMA foreign_keys").fetchone()[0]
            schema_version = connection.execute(
                "SELECT value FROM schema_meta WHERE key = ?",
                ("schema_version",),
            ).fetchone()[0]
        self.assertEqual(fk_value, 0)
        self.assertEqual(schema_version, SCHEMA_VERSION)

        with self.app.app_context():
            runtime_connection = get_db()
            runtime_value = runtime_connection.execute("PRAGMA foreign_keys").fetchone()[0]
        self.assertEqual(runtime_value, 1)

    def test_T202_route_regression_and_chat(self):
        register_response = self.register_user(username="regression_user")
        self.assertIn("회원가입이 완료되었습니다. 로그인 해주세요.", register_response.get_data(as_text=True))

        login_response = self.login_user(username="regression_user")
        self.assertIn("로그인 성공!", login_response.get_data(as_text=True))

        profile_response = self.client.post(
            "/profile",
            data={"csrf_token": self.get_csrf("/profile"), "bio": "phase2 profile"},
            follow_redirects=True,
        )
        self.assertIn("프로필이 업데이트되었습니다.", profile_response.get_data(as_text=True))

        product_response = self.client.post(
            "/product/new",
            data={
                "csrf_token": self.get_csrf("/product/new"),
                "title": "Regression Product",
                "description": "Regression Description",
                "price": "7000",
            },
            follow_redirects=True,
        )
        self.assertIn("상품이 등록되었습니다.", product_response.get_data(as_text=True))
        product = self.fetch_product("Regression Product")
        detail_response = self.client.get(f"/product/{product['id']}")
        self.assertIn("Regression Product", detail_response.get_data(as_text=True))

        user = self.fetch_user("regression_user")
        report_response = self.client.post(
            "/report",
            data={
                "csrf_token": self.get_csrf("/report"),
                "target_type": "user",
                "target_id": user["id"],
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

        logout_response = self.client.post(
            "/logout",
            data={"csrf_token": self.extract_csrf_token(self.client.get("/dashboard").get_data(as_text=True))},
            follow_redirects=True,
        )
        self.assertIn("로그아웃되었습니다.", logout_response.get_data(as_text=True))

    def test_regular_register_cannot_create_admin_role(self):
        self.register_user(username="normal_user", role="admin", status="suspended", balance="999999")
        user = self.fetch_user("normal_user")
        self.assertEqual(user["role"], "user")
        self.assertEqual(user["status"], "active")
        self.assertEqual(user["balance"], 100000)

    def test_create_admin_cli_success_and_duplicate_rejected(self):
        cli_db = str(Path(self.temp_dir.name) / "cli_market.db")
        cli_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "cli-secret",
                "DATABASE": cli_db,
                "APP_ENV": "development",
                "AUTO_INIT_DB": False,
            }
        )
        runner = cli_app.test_cli_runner()
        init_result = runner.invoke(args=["init-db"])
        self.assertEqual(init_result.exit_code, 0)

        with mock.patch("app.getpass.getpass", side_effect=["password123", "password123"]):
            result = runner.invoke(args=["create-admin"], input="admin_user\n")
        self.assertEqual(result.exit_code, 0)
        with sqlite3.connect(cli_db) as connection:
            connection.row_factory = sqlite3.Row
            admin_row = connection.execute("SELECT * FROM user WHERE username = ?", ("admin_user",)).fetchone()
        self.assertEqual(admin_row["role"], "admin")
        self.assertEqual(admin_row["status"], "active")
        self.assertEqual(admin_row["balance"], 100000)
        self.assertTrue(admin_row["password_hash"].startswith(("scrypt:", "pbkdf2:")))

        with mock.patch("app.getpass.getpass", side_effect=["password123", "password123"]):
            duplicate_result = runner.invoke(args=["create-admin"], input="admin_user\n")
        self.assertNotEqual(duplicate_result.exit_code, 0)
        self.assertIn("이미 존재하는 사용자명입니다.", duplicate_result.output)

    def test_create_admin_requires_phase2_database(self):
        missing_db = str(Path(self.temp_dir.name) / "missing_admin.db")
        cli_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "cli-secret",
                "DATABASE": missing_db,
                "APP_ENV": "development",
                "AUTO_INIT_DB": False,
            }
        )
        runner = cli_app.test_cli_runner()
        missing_result = runner.invoke(args=["create-admin"])
        self.assertNotEqual(missing_result.exit_code, 0)
        self.assertIn("init-db", missing_result.output)

        old_db = Path(self.temp_dir.name) / "old_admin.db"
        self.create_old_schema_db(old_db)
        old_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "cli-secret",
                "DATABASE": str(old_db),
                "APP_ENV": "development",
                "AUTO_INIT_DB": False,
            }
        )
        old_runner = old_app.test_cli_runner()
        old_result = old_runner.invoke(args=["create-admin"])
        self.assertNotEqual(old_result.exit_code, 0)
        self.assertIn("지원하지 않는 데이터베이스 스키마입니다.", old_result.output)


    def test_init_db_does_not_overwrite_existing_db_by_default(self):
        cli_db = str(Path(self.temp_dir.name) / "existing_market.db")
        cli_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "cli-secret",
                "DATABASE": cli_db,
                "APP_ENV": "development",
                "AUTO_INIT_DB": False,
            }
        )
        runner = cli_app.test_cli_runner()
        first_result = runner.invoke(args=["init-db"])
        self.assertEqual(first_result.exit_code, 0)
        second_result = runner.invoke(args=["init-db"])
        self.assertNotEqual(second_result.exit_code, 0)
        self.assertIn("이미 존재합니다", second_result.output)

    def test_init_db_handles_missing_and_replace_paths_safely(self):
        fresh_db = str(Path(self.temp_dir.name) / "fresh_market.db")
        fresh_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "cli-secret",
                "DATABASE": fresh_db,
                "APP_ENV": "development",
                "AUTO_INIT_DB": False,
            }
        )
        fresh_runner = fresh_app.test_cli_runner()
        fresh_result = fresh_runner.invoke(args=["init-db"])
        self.assertEqual(fresh_result.exit_code, 0)
        self.assertTrue(Path(fresh_db).exists())

        replace_dir = Path(self.temp_dir.name) / "replace_case"
        replace_dir.mkdir()
        replace_db = replace_dir / "market.db"
        self.create_old_schema_db(replace_db)
        replace_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "cli-secret",
                "DATABASE": str(replace_db),
                "APP_ENV": "development",
                "AUTO_INIT_DB": False,
            }
        )
        replace_runner = replace_app.test_cli_runner()

        reject_result = replace_runner.invoke(args=["init-db"])
        self.assertNotEqual(reject_result.exit_code, 0)
        self.assertIn("이미 존재합니다", reject_result.output)

        no_backup_result = replace_runner.invoke(args=["init-db", "--replace"])
        self.assertNotEqual(no_backup_result.exit_code, 0)
        self.assertIn("market.phase1.db 백업 파일이 필요합니다.", no_backup_result.output)

        (replace_dir / "market.phase1.db").write_bytes(b"backup")
        replace_result = replace_runner.invoke(args=["init-db", "--replace"])
        self.assertEqual(replace_result.exit_code, 0)
        self.assertIn("schema_version=2", replace_result.output)
        with sqlite3.connect(replace_db) as connection:
            connection.row_factory = sqlite3.Row
            schema_version = connection.execute(
                "SELECT value FROM schema_meta WHERE key = ?",
                ("schema_version",),
            ).fetchone()[0]
        self.assertEqual(schema_version, SCHEMA_VERSION)


    def test_db_constraints_enforced(self):
        self.register_user(username="constraint_seller")
        seller = self.fetch_user("constraint_seller")
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO user (id, username, password_hash, bio, role, status, balance, created_at, updated_at, suspended_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("bad-user", "bad_user", "hash", None, "superadmin", "active", 100000, "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", None),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO product (id, title, description, price, seller_id, status, created_at, updated_at, blocked_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("bad-product", "bad", "bad", 0, seller["id"], "active", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", None),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO product (id, title, description, price, seller_id, status, created_at, updated_at, blocked_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("fk-product", "bad", "bad", 10, "missing-user", "active", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", None),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO report (id, reporter_id, target_type, target_id, reason, status, admin_id, action_type, review_note, created_at, reviewed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("bad-report", seller["id"], "user", seller["id"], "reason", "bad", None, None, None, "2026-01-01T00:00:00+00:00", None),
                )

    def test_old_schema_detection_does_not_auto_migrate(self):
        old_db_path = Path(self.temp_dir.name) / "old_market.db"
        self.create_old_schema_db(old_db_path)
        result = self.run_app_subprocess([sys.executable, "app.py"], old_db_path)
        combined_output = result.stderr + result.stdout
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("지원하지 않는 데이터베이스 스키마입니다.", combined_output)
        self.assertIn("init-db", combined_output)
        self.assertNotIn("Running on", combined_output)
        self.assertNotIn("wsgi starting up", combined_output)
        self.assertEqual(self.list_tables(old_db_path), ["user"])

    def test_old_schema_detection_blocks_flask_run_before_serving(self):
        old_db_path = Path(self.temp_dir.name) / "old_market_flask.db"
        self.create_old_schema_db(old_db_path)
        result = self.run_app_subprocess(
            [sys.executable, "-m", "flask", "--app", "app", "run", "--no-reload", "--port", "52345"],
            old_db_path,
        )
        combined_output = result.stderr + result.stdout
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("init-db", combined_output)
        self.assertNotIn("Running on", combined_output)
        self.assertNotIn("wsgi starting up", combined_output)
        self.assertEqual(self.list_tables(old_db_path), ["user"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
