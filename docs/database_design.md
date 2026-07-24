# Database Design

## 1. 설계 원칙
- SQLite 유지
- `sqlite3` + 파라미터 바인딩 유지
- 앱 시작 시 기존 DB를 자동 파괴 변경하지 않음
- 새 목표 스키마는 명시적 초기화 명령으로만 생성
- 선택 기능인 문의 테이블은 생성하지 않음

## 2. 백업 파일 역할
- `market.baseline.db`
  - 최초 취약 상태 DB 백업
- `market.phase1.db`
  - Phase 1 완료 시점 DB 백업
- `market.db`
  - 현재 실행 대상 DB

두 백업 파일은 모두 로컬 보존용이며 Git에 포함하지 않는다.

## 3. 현재 목표 스키마
### 3.1 `schema_meta`
| 컬럼 | 타입 | 설명 |
|---|---|---|
| key | TEXT PK | 메타 키 |
| value | TEXT NOT NULL | 메타 값 |

저장 값:
- `schema_version = 2`

### 3.2 `user`
| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | TEXT PK | 사용자 ID |
| username | TEXT UNIQUE NOT NULL | 사용자명 |
| password_hash | TEXT NOT NULL | 비밀번호 해시 |
| bio | TEXT | 소개글 |
| role | TEXT NOT NULL DEFAULT 'user' | `user` / `admin` |
| status | TEXT NOT NULL DEFAULT 'active' | `active` / `suspended` |
| balance | INTEGER NOT NULL DEFAULT 100000 | 가상 잔액 |
| created_at | TEXT NOT NULL | 생성 시각(UTC ISO 8601) |
| updated_at | TEXT NOT NULL | 수정 시각(UTC ISO 8601) |
| suspended_reason | TEXT | 정지 사유 |

제약조건:
- `role IN ('user', 'admin')`
- `status IN ('active', 'suspended')`
- `balance >= 0`
- `username` UNIQUE

### 3.3 `product`
| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | TEXT PK | 상품 ID |
| title | TEXT NOT NULL | 제목 |
| description | TEXT NOT NULL | 설명 |
| price | INTEGER NOT NULL | 정수 가격 |
| seller_id | TEXT NOT NULL | 판매자 ID |
| status | TEXT NOT NULL DEFAULT 'active' | `active` / `blocked` |
| created_at | TEXT NOT NULL | 생성 시각(UTC ISO 8601) |
| updated_at | TEXT NOT NULL | 수정 시각(UTC ISO 8601) |
| blocked_reason | TEXT | 차단 사유 |

제약조건:
- `typeof(price) = 'integer' AND price > 0`
- `status IN ('active', 'blocked')`
- `FOREIGN KEY (seller_id) REFERENCES user(id)`

### 3.4 `report`
| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | TEXT PK | 신고 ID |
| reporter_id | TEXT NOT NULL | 신고자 |
| target_type | TEXT NOT NULL | `user` / `product` |
| target_id | TEXT NOT NULL | 대상 ID |
| reason | TEXT NOT NULL | 신고 사유 |
| status | TEXT NOT NULL DEFAULT 'pending' | `pending` / `dismissed` / `actioned` |
| admin_id | TEXT | 처리 관리자 |
| action_type | TEXT | `NULL`, `none`, `suspend_user`, `block_product` |
| review_note | TEXT | 검토 메모 |
| created_at | TEXT NOT NULL | 생성 시각(UTC ISO 8601) |
| reviewed_at | TEXT | 검토 시각 |

제약조건:
- `target_type IN ('user', 'product')`
- `status IN ('pending', 'dismissed', 'actioned')`
- `action_type IS NULL OR action_type IN ('none', 'suspend_user', 'block_product')`
- `FOREIGN KEY (reporter_id) REFERENCES user(id)`
- `FOREIGN KEY (admin_id) REFERENCES user(id)`

주의사항:
- `target_id`는 다형 대상이라 일반 외래키로 강제하지 않는다.
- 대신 애플리케이션 레벨에서 실제 대상 존재 여부를 검증한다.

### 3.5 `transfer`
| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | TEXT PK | 송금 ID |
| sender_id | TEXT NOT NULL | 송신자 |
| receiver_id | TEXT NOT NULL | 수신자 |
| amount | INTEGER NOT NULL | 송금 금액 |
| note | TEXT | 메모 |
| created_at | TEXT NOT NULL | 생성 시각(UTC ISO 8601) |

제약조건:
- `typeof(amount) = 'integer' AND amount > 0`
- `FOREIGN KEY (sender_id) REFERENCES user(id)`
- `FOREIGN KEY (receiver_id) REFERENCES user(id)`

### 3.6 `admin_audit_log`
| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | TEXT PK | 감사 로그 ID |
| admin_id | TEXT NOT NULL | 작업 관리자 |
| action | TEXT NOT NULL | 작업 종류 |
| target_type | TEXT NOT NULL | 대상 종류 |
| target_id | TEXT NOT NULL | 대상 ID |
| detail | TEXT NOT NULL | 상세 메모 |
| created_at | TEXT NOT NULL | 생성 시각(UTC ISO 8601) |

제약조건:
- `FOREIGN KEY (admin_id) REFERENCES user(id)`

## 4. 생성하지 않는 테이블
다음 테이블은 현재도 생성하지 않는다.
- `product_inquiry`
- `inquiry_message`

생성 시점:
- `product_inquiry`, `inquiry_message`: Phase 7 선택 기능 구현 시

## 5. SQLite 안전 설정
모든 런타임 DB 연결에서 적용:
- `PRAGMA foreign_keys = ON`
- `row_factory = sqlite3.Row`
- SQL 파라미터 바인딩 사용
- 사용자 입력 SQL 문자열 직접 결합 금지

## 6. DB 초기화 방식
### 기본 원칙
- 기존 `market.db`를 직접 ALTER하며 누적 변경하지 않는다.
- 새 목표 스키마 DB를 명시적으로 생성한다.
- 앱 시작 시 자동 삭제/자동 재생성하지 않는다.

### 명령
```bash
python -m flask --app app init-db
```

### 동작
- DB 파일이 없으면 새 스키마 생성
- DB 파일이 이미 있으면 기본적으로 중단
- 명시적 교체 옵션이 있을 때만 교체 허용
- 기본 `market.db` 교체 시 `market.phase1.db` 백업 존재 확인
- `market.baseline.db`, `market.phase1.db`는 절대 삭제하지 않음
- `init-db`는 DB 생성 목적의 예외 경로이므로 미초기화/구형 DB 상태에서도 실행 가능
- `init-db --replace`는 서버를 완전히 종료한 뒤에만 실행
- 임시 DB에 스키마를 생성·검증한 뒤 `os.replace()`로 교체

## 7. 스키마 버전 확인 방식
- `schema_meta` 테이블의 `schema_version` 값을 사용한다.
- 현재 지원 버전은 `2`다.
- `schema_meta`, `user`, `product`, `report`, `transfer`, `admin_audit_log` 필수 테이블과 필수 컬럼 목록까지 함께 검증한다.
- `python app.py`, `flask --app app run`, `python -m flask --app app run`은 요청 처리 전에 검증 실패 시 서버를 중단하고 `init-db` 실행을 안내한다.
- 지원하지 않는 스키마를 발견하면 자동 변경하지 않고 안내 후 실행을 중단한다.

## 8. 유지보수 시 주의사항
- 새 스키마 변경은 다시 명시적 초기화/마이그레이션 전략으로 다뤄야 한다.
- `market.baseline.db`, `market.phase1.db`는 실수로 덮어쓰지 않는다.
- 운영 코드에서 `schema_version` 불일치 시 자동 ALTER를 넣지 않는다.
- 서버 실행 경로와 CLI 경로 구분은 현재 프로세스의 공식 실행 인자(`run`, `init-db`, `create-admin`)를 기준으로 판단한다. 비표준 실행 방식에서는 동일한 조기 검증이 보장되지 않을 수 있다.
