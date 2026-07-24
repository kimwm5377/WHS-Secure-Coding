import sqlite3
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from werkzeug.security import generate_password_hash

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import create_app, socketio, utc_now_iso  # noqa: E402


class FinalFeatureTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "final_market.db")
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "final-test-secret",
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

    def get_csrf(self, client, path):
        response = client.get(path)
        self.assertEqual(response.status_code, 200)
        return self.extract_csrf_token(response.get_data(as_text=True))

    def register_user(self, client=None, username="user1", password="password123", **extra):
        client = client or self.client
        data = {
            "csrf_token": self.get_csrf(client, "/register"),
            "username": username,
            "password": password,
        }
        data.update(extra)
        response = client.post("/register", data=data, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        return response

    def login_user(self, client=None, username="user1", password="password123"):
        client = client or self.client
        response = client.post(
            "/login",
            data={
                "csrf_token": self.get_csrf(client, "/login"),
                "username": username,
                "password": password,
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        return response

    def fetch_one(self, query, params=()):
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute(query, params).fetchone()

    def fetch_all(self, query, params=()):
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute(query, params).fetchall()

    def create_admin_user(self, username="admin_user", password="password123"):
        now = utc_now_iso()
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
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

    def create_logged_in_admin(self, username="admin_user", password="password123"):
        self.create_admin_user(username=username, password=password)
        admin_client = self.app.test_client()
        login_response = self.login_user(client=admin_client, username=username, password=password)
        self.assertIn("로그인 성공!", login_response.get_data(as_text=True))
        return admin_client

    def create_product(self, client=None, title="상품", description="설명", price="1000"):
        client = client or self.client
        response = client.post(
            "/product/new",
            data={
                "csrf_token": self.get_csrf(client, "/product/new"),
                "title": title,
                "description": description,
                "price": price,
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        return response

    def test_T014_search_by_title_and_description(self):
        self.register_user(username="searcher")
        self.login_user(username="searcher")
        self.create_product(title="중고 노트북", description="빠른 배송")
        self.create_product(title="스피커", description="회의용 노트북 받침대 포함")
        self.create_product(title="차단 예정 상품", description="숨김 테스트", price="3000")

        admin_client = self.create_logged_in_admin()
        blocked_product = self.fetch_one("SELECT * FROM product WHERE title = ?", ("차단 예정 상품",))
        block_response = admin_client.post(
            f"/admin/products/{blocked_product['id']}/block",
            data={
                "csrf_token": self.get_csrf(admin_client, "/admin/products"),
                "reason": "검색 제외 테스트",
            },
            follow_redirects=True,
        )
        self.assertIn("상품을 차단했습니다.", block_response.get_data(as_text=True))

        title_response = self.client.get("/dashboard?q=노트북")
        title_text = title_response.get_data(as_text=True)
        self.assertIn("중고 노트북", title_text)
        self.assertIn("스피커", title_text)
        self.assertNotIn("차단 예정 상품", title_text)

        description_response = self.client.get("/dashboard?q=빠른")
        description_text = description_response.get_data(as_text=True)
        self.assertIn("중고 노트북", description_text)
        self.assertNotIn("차단 예정 상품", description_text)

    def test_T015_price_filter_and_invalid_range(self):
        self.register_user(username="filter_user")
        self.login_user(username="filter_user")
        self.create_product(title="저가 상품", description="cheap", price="1000")
        self.create_product(title="중가 상품", description="mid", price="5000")
        self.create_product(title="고가 상품", description="high", price="10000")

        min_response = self.client.get("/dashboard?min_price=5000")
        min_text = min_response.get_data(as_text=True)
        self.assertNotIn("저가 상품", min_text)
        self.assertIn("중가 상품", min_text)
        self.assertIn("고가 상품", min_text)

        max_response = self.client.get("/dashboard?max_price=5000")
        max_text = max_response.get_data(as_text=True)
        self.assertIn("저가 상품", max_text)
        self.assertIn("중가 상품", max_text)
        self.assertNotIn("고가 상품", max_text)

        combo_response = self.client.get("/dashboard?q=상품&min_price=2000&max_price=8000")
        combo_text = combo_response.get_data(as_text=True)
        self.assertNotIn("저가 상품", combo_text)
        self.assertIn("중가 상품", combo_text)
        self.assertNotIn("고가 상품", combo_text)

        invalid_response = self.client.get("/dashboard?min_price=9000&max_price=1000")
        invalid_text = invalid_response.get_data(as_text=True)
        self.assertIn("최소 가격은 최대 가격보다 클 수 없습니다.", invalid_text)

    def test_T011_duplicate_pending_report_is_blocked(self):
        self.register_user(username="reporter")
        self.register_user(username="target_user")
        self.login_user(username="reporter")
        target_user = self.fetch_one("SELECT * FROM user WHERE username = ?", ("target_user",))

        first_response = self.client.post(
            "/report",
            data={
                "csrf_token": self.get_csrf(self.client, "/report"),
                "target_type": "user",
                "target_id": target_user["id"],
                "reason": "첫 번째 신고",
            },
            follow_redirects=True,
        )
        self.assertIn("신고가 접수되었습니다.", first_response.get_data(as_text=True))

        second_response = self.client.post(
            "/report",
            data={
                "csrf_token": self.get_csrf(self.client, "/report"),
                "target_type": "user",
                "target_id": target_user["id"],
                "reason": "중복 신고",
            },
            follow_redirects=True,
        )
        self.assertIn("동일한 대상에 대한 검토 중 신고가 이미 존재합니다.", second_response.get_data(as_text=True))
        rows = self.fetch_all(
            "SELECT * FROM report WHERE reporter_id = ? AND target_type = ? AND target_id = ?",
            (self.fetch_one("SELECT * FROM user WHERE username = ?", ("reporter",))["id"], "user", target_user["id"]),
        )
        self.assertEqual(len(rows), 1)

    def test_T012_T013_T108_admin_access_control(self):
        self.register_user(username="normal_user")
        self.login_user(username="normal_user")
        forbidden_page = self.client.get("/admin")
        self.assertEqual(forbidden_page.status_code, 403)

        target = self.fetch_one("SELECT * FROM user WHERE username = ?", ("normal_user",))
        forbidden_action = self.client.post(
            f"/admin/users/{target['id']}/suspend",
            data={"csrf_token": self.get_csrf(self.client, "/dashboard"), "reason": "forbidden"},
        )
        self.assertEqual(forbidden_action.status_code, 403)

        admin_client = self.create_logged_in_admin()
        allowed_response = admin_client.get("/admin")
        self.assertEqual(allowed_response.status_code, 200)
        self.assertIn("관리자 대시보드", allowed_response.get_data(as_text=True))

    def test_T023_suspend_and_unsuspend_user(self):
        self.register_user(username="member_user")
        member_client = self.app.test_client()
        self.login_user(client=member_client, username="member_user")
        admin_client = self.create_logged_in_admin()
        target = self.fetch_one("SELECT * FROM user WHERE username = ?", ("member_user",))

        suspend_response = admin_client.post(
            f"/admin/users/{target['id']}/suspend",
            data={"csrf_token": self.get_csrf(admin_client, "/admin/users"), "reason": "정지 테스트"},
            follow_redirects=True,
        )
        self.assertIn("사용자를 정지했습니다.", suspend_response.get_data(as_text=True))
        suspended = self.fetch_one("SELECT * FROM user WHERE id = ?", (target["id"],))
        self.assertEqual(suspended["status"], "suspended")

        login_again = self.client.post(
            "/login",
            data={
                "csrf_token": self.get_csrf(self.client, "/login"),
                "username": "member_user",
                "password": "password123",
            },
            follow_redirects=True,
        )
        self.assertIn("정지된 계정은 로그인할 수 없습니다.", login_again.get_data(as_text=True))

        protected_response = member_client.get("/profile", follow_redirects=True)
        self.assertIn("정지된 계정은 사용할 수 없습니다.", protected_response.get_data(as_text=True))

        self_response = admin_client.post(
            f"/admin/users/{self.fetch_one('SELECT * FROM user WHERE username = ?', ('admin_user',))['id']}/suspend",
            data={"csrf_token": self.get_csrf(admin_client, "/admin/users"), "reason": "self"},
            follow_redirects=True,
        )
        self.assertIn("관리자는 자기 자신을 정지할 수 없습니다.", self_response.get_data(as_text=True))

        unsuspend_response = admin_client.post(
            f"/admin/users/{target['id']}/unsuspend",
            data={"csrf_token": self.get_csrf(admin_client, "/admin/users")},
            follow_redirects=True,
        )
        self.assertIn("사용자 정지를 해제했습니다.", unsuspend_response.get_data(as_text=True))
        active = self.fetch_one("SELECT * FROM user WHERE id = ?", (target["id"],))
        self.assertEqual(active["status"], "active")

    def test_T024_block_and_unblock_product(self):
        seller_client = self.app.test_client()
        self.register_user(client=seller_client, username="seller")
        self.login_user(client=seller_client, username="seller")
        self.create_product(client=seller_client, title="차단 대상 상품", description="secret", price="7000")
        product = self.fetch_one("SELECT * FROM product WHERE title = ?", ("차단 대상 상품",))

        admin_client = self.create_logged_in_admin()
        block_response = admin_client.post(
            f"/admin/products/{product['id']}/block",
            data={"csrf_token": self.get_csrf(admin_client, "/admin/products"), "reason": "차단 테스트"},
            follow_redirects=True,
        )
        self.assertIn("상품을 차단했습니다.", block_response.get_data(as_text=True))
        blocked = self.fetch_one("SELECT * FROM product WHERE id = ?", (product["id"],))
        self.assertEqual(blocked["status"], "blocked")

        buyer_client = self.app.test_client()
        self.register_user(client=buyer_client, username="buyer")
        self.login_user(client=buyer_client, username="buyer")
        dashboard_text = buyer_client.get("/dashboard").get_data(as_text=True)
        self.assertNotIn("차단 대상 상품", dashboard_text)
        detail_text = buyer_client.get(f"/product/{product['id']}", follow_redirects=True).get_data(as_text=True)
        self.assertIn("상품을 찾을 수 없습니다.", detail_text)

        unblock_response = admin_client.post(
            f"/admin/products/{product['id']}/unblock",
            data={"csrf_token": self.get_csrf(admin_client, "/admin/products")},
            follow_redirects=True,
        )
        self.assertIn("상품 차단을 해제했습니다.", unblock_response.get_data(as_text=True))
        unblocked = self.fetch_one("SELECT * FROM product WHERE id = ?", (product["id"],))
        self.assertEqual(unblocked["status"], "active")

    def test_T025_T113_T203_report_review_and_audit_log(self):
        self.register_user(username="reporter")
        self.register_user(username="target_user")
        seller_client = self.app.test_client()
        self.register_user(client=seller_client, username="seller")
        self.login_user(client=seller_client, username="seller")
        self.create_product(client=seller_client, title="신고 상품", description="desc", price="9000")
        product = self.fetch_one("SELECT * FROM product WHERE title = ?", ("신고 상품",))

        reporter_client = self.app.test_client()
        self.login_user(client=reporter_client, username="reporter")
        target_user = self.fetch_one("SELECT * FROM user WHERE username = ?", ("target_user",))
        reporter_client.post(
            "/report",
            data={
                "csrf_token": self.get_csrf(reporter_client, "/report"),
                "target_type": "user",
                "target_id": target_user["id"],
                "reason": "사용자 신고",
            },
            follow_redirects=True,
        )
        reporter_client.post(
            "/report",
            data={
                "csrf_token": self.get_csrf(reporter_client, "/report"),
                "target_type": "product",
                "target_id": product["id"],
                "reason": "상품 신고",
            },
            follow_redirects=True,
        )

        user_report = self.fetch_one("SELECT * FROM report WHERE reason = ?", ("사용자 신고",))
        product_report = self.fetch_one("SELECT * FROM report WHERE reason = ?", ("상품 신고",))
        admin_client = self.create_logged_in_admin()

        action_response = admin_client.post(
            f"/admin/reports/{user_report['id']}/action",
            data={
                "csrf_token": self.get_csrf(admin_client, "/admin/reports"),
                "action_type": "suspend_user",
                "review_note": "반복 위반",
            },
            follow_redirects=True,
        )
        self.assertIn("신고를 처리했습니다.", action_response.get_data(as_text=True))

        dismiss_response = admin_client.post(
            f"/admin/reports/{product_report['id']}/dismiss",
            data={
                "csrf_token": self.get_csrf(admin_client, "/admin/reports"),
                "review_note": "증거 부족",
            },
            follow_redirects=True,
        )
        self.assertIn("신고를 기각했습니다.", dismiss_response.get_data(as_text=True))

        actioned_report = self.fetch_one("SELECT * FROM report WHERE id = ?", (user_report["id"],))
        dismissed_report = self.fetch_one("SELECT * FROM report WHERE id = ?", (product_report["id"],))
        target_after = self.fetch_one("SELECT * FROM user WHERE id = ?", (target_user["id"],))
        self.assertEqual(actioned_report["status"], "actioned")
        self.assertEqual(actioned_report["action_type"], "suspend_user")
        self.assertIsNotNone(actioned_report["admin_id"])
        self.assertIsNotNone(actioned_report["reviewed_at"])
        self.assertEqual(dismissed_report["status"], "dismissed")
        self.assertEqual(dismissed_report["action_type"], "none")
        self.assertEqual(target_after["status"], "suspended")

        audit_rows = self.fetch_all("SELECT * FROM admin_audit_log ORDER BY created_at ASC")
        self.assertGreaterEqual(len(audit_rows), 3)
        actions = [row["action"] for row in audit_rows]
        self.assertIn("suspend_user", actions)
        self.assertIn("action_report", actions)
        self.assertIn("dismiss_report", actions)

        audit_page = admin_client.get("/admin/audit-logs")
        self.assertEqual(audit_page.status_code, 200)
        audit_text = audit_page.get_data(as_text=True)
        self.assertIn("감사 로그", audit_text)
        self.assertIn("suspend_user", audit_text)

    def test_T019_T020_T021_T022_T026_transfer_and_admin_view(self):
        self.register_user(username="sender")
        self.register_user(username="receiver")
        self.login_user(username="sender")

        success_response = self.client.post(
            "/transfer",
            data={
                "csrf_token": self.get_csrf(self.client, "/wallet"),
                "receiver_username": "receiver",
                "amount": "5000",
                "note": "테스트 송금",
            },
            follow_redirects=True,
        )
        self.assertIn("송금이 완료되었습니다.", success_response.get_data(as_text=True))
        sender = self.fetch_one("SELECT * FROM user WHERE username = ?", ("sender",))
        receiver = self.fetch_one("SELECT * FROM user WHERE username = ?", ("receiver",))
        transfer = self.fetch_one("SELECT * FROM transfer WHERE note = ?", ("테스트 송금",))
        self.assertEqual(sender["balance"], 95000)
        self.assertEqual(receiver["balance"], 105000)
        self.assertEqual(transfer["amount"], 5000)

        self_response = self.client.post(
            "/transfer",
            data={
                "csrf_token": self.get_csrf(self.client, "/wallet"),
                "receiver_username": "sender",
                "amount": "100",
                "note": "self",
            },
            follow_redirects=True,
        )
        self.assertIn("자기 자신에게 송금할 수 없습니다.", self_response.get_data(as_text=True))

        zero_response = self.client.post(
            "/transfer",
            data={
                "csrf_token": self.get_csrf(self.client, "/wallet"),
                "receiver_username": "receiver",
                "amount": "0",
                "note": "zero",
            },
            follow_redirects=True,
        )
        self.assertIn("송금 금액은 0보다 큰 정수만 입력할 수 있습니다.", zero_response.get_data(as_text=True))

        over_response = self.client.post(
            "/transfer",
            data={
                "csrf_token": self.get_csrf(self.client, "/wallet"),
                "receiver_username": "receiver",
                "amount": "999999",
                "note": "over",
            },
            follow_redirects=True,
        )
        self.assertIn("잔액이 부족합니다.", over_response.get_data(as_text=True))

        admin_client = self.create_logged_in_admin()
        transfer_page = admin_client.get("/admin/transfers")
        self.assertEqual(transfer_page.status_code, 200)
        transfer_text = transfer_page.get_data(as_text=True)
        self.assertIn("sender", transfer_text)
        self.assertIn("receiver", transfer_text)
        self.assertIn("5000", transfer_text)

    def test_T008_T111_chat_auth_and_rate_limit(self):
        sender_client = self.app.test_client()
        self.register_user(client=sender_client, username="chat_user")
        self.login_user(client=sender_client, username="chat_user")
        receiver_client = self.app.test_client()
        self.register_user(client=receiver_client, username="chat_receiver")
        self.login_user(client=receiver_client, username="chat_receiver")

        sender_socket = socketio.test_client(self.app, flask_test_client=sender_client)
        receiver_socket = socketio.test_client(self.app, flask_test_client=receiver_client)
        anonymous_socket = socketio.test_client(self.app, flask_test_client=self.app.test_client())
        try:
            sender_socket.emit("send_message", {"username": "fake", "message": "hello world"})
            receiver_events = receiver_socket.get_received()
            self.assertTrue(any(event["name"] == "message" for event in receiver_events))
            payloads = [event["args"] for event in receiver_events if event["name"] == "message"]
            flat_payloads = []
            for payload in payloads:
                if isinstance(payload, list):
                    flat_payloads.extend(payload)
                else:
                    flat_payloads.append(payload)
            self.assertTrue(any(item["username"] == "chat_user" and item["message"] == "hello world" for item in flat_payloads))

            anonymous_socket.emit("send_message", {"username": "anon", "message": "blocked"})
            self.assertEqual(receiver_socket.get_received(), [])
        finally:
            sender_socket.disconnect()
            receiver_socket.disconnect()
            anonymous_socket.disconnect()

        limited_client = self.app.test_client()
        self.register_user(client=limited_client, username="limited_user")
        self.login_user(client=limited_client, username="limited_user")
        limited_sender_socket = socketio.test_client(self.app, flask_test_client=limited_client)
        limited_receiver_socket = socketio.test_client(self.app, flask_test_client=receiver_client)
        try:
            for index in range(6):
                limited_sender_socket.emit("send_message", {"username": "fake", "message": f"msg {index}"})
            rate_limited_events = [event for event in limited_receiver_socket.get_received() if event["name"] == "message"]
            self.assertEqual(len(rate_limited_events), 5)
        finally:
            limited_sender_socket.disconnect()
            limited_receiver_socket.disconnect()


    def test_T112_internal_error_details_are_hidden(self):
        error_db_path = str(Path(self.temp_dir.name) / "error_market.db")
        from unittest import mock

        with mock.patch.dict("os.environ", {"SECRET_KEY": "error-secret", "APP_ENV": "development"}, clear=False):
            error_app = create_app(
                {
                    "TESTING": False,
                    "PROPAGATE_EXCEPTIONS": False,
                    "DATABASE": error_db_path,
                    "APP_ENV": "development",
                    "SESSION_COOKIE_SECURE": False,
                    "AUTO_INIT_DB": True,
                }
            )

        def boom():
            raise RuntimeError("sqlite syntax error secret detail")

        error_app.add_url_rule("/boom", "boom", boom)
        error_client = error_app.test_client()
        response = error_client.get("/boom")
        text = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 500)
        self.assertIn("요청을 처리하는 중 오류가 발생했습니다.", text)
        self.assertNotIn("sqlite syntax error secret detail", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
