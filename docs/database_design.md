# Database Design

## 1. 설계 원칙
- SQLite 유지
- `sqlite3` + 파라미터 바인딩 유지
- 기존 테이블은 호환 범위 내에서 확장
- 감사 가능성과 관리자 제재 흐름을 우선 반영

## 2. 현재 테이블
### `user`
| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | TEXT PK | 사용자 ID |
| username | TEXT UNIQUE NOT NULL | 사용자명 |
| password | TEXT NOT NULL | 현재는 평문, 이후 해시 저장 |
| bio | TEXT | 소개글 |

### `product`
| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | TEXT PK | 상품 ID |
| title | TEXT NOT NULL | 제목 |
| description | TEXT NOT NULL | 설명 |
| price | TEXT NOT NULL | 가격 |
| seller_id | TEXT NOT NULL | 판매자 ID |

### `report`
| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | TEXT PK | 신고 ID |
| reporter_id | TEXT NOT NULL | 신고자 ID |
| target_id | TEXT NOT NULL | 신고 대상 ID |
| reason | TEXT NOT NULL | 신고 사유 |

## 3. 목표 스키마
### 3.1 `user` (변경)
| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | TEXT PK | 사용자 ID |
| username | TEXT UNIQUE NOT NULL | 사용자명 |
| password_hash | TEXT NOT NULL | 비밀번호 해시 |
| bio | TEXT | 소개글 |
| role | TEXT NOT NULL DEFAULT 'user' | `user` / `admin` |
| status | TEXT NOT NULL DEFAULT 'active' | `active` / `suspended` |
| balance | INTEGER NOT NULL DEFAULT 100000 | 신규 가입자 기본 가상 잔액 |
| created_at | TEXT NOT NULL | 생성 시각 |
| updated_at | TEXT NOT NULL | 수정 시각 |
| suspended_reason | TEXT NULL | 정지 사유 |

### 3.2 `product` (변경)
| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | TEXT PK | 상품 ID |
| title | TEXT NOT NULL | 제목 |
| description | TEXT NOT NULL | 설명 |
| price | INTEGER NOT NULL | 정수 가격 |
| seller_id | TEXT NOT NULL | 판매자 ID |
| status | TEXT NOT NULL DEFAULT 'active' | `active` / `blocked` |
| created_at | TEXT NOT NULL | 생성 시각 |
| updated_at | TEXT NOT NULL | 수정 시각 |
| blocked_reason | TEXT NULL | 차단 사유 |

### 3.3 `report` (변경)
| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | TEXT PK | 신고 ID |
| reporter_id | TEXT NOT NULL | 신고자 |
| target_type | TEXT NOT NULL | `user` / `product` |
| target_id | TEXT NOT NULL | 대상 ID |
| reason | TEXT NOT NULL | 신고 사유 |
| status | TEXT NOT NULL DEFAULT 'pending' | `pending` / `dismissed` / `actioned` |
| admin_id | TEXT NULL | 처리 관리자 |
| action_type | TEXT NULL | `none` / `suspend_user` / `block_product` |
| review_note | TEXT NULL | 검토 메모 |
| created_at | TEXT NOT NULL | 생성 시각 |
| reviewed_at | TEXT NULL | 검토 시각 |

### 3.4 `product_inquiry` (선택 기능)
| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | TEXT PK | 문의 스레드 ID |
| product_id | TEXT NOT NULL | 상품 ID |
| buyer_id | TEXT NOT NULL | 문의 생성자 |
| seller_id | TEXT NOT NULL | 판매자 |
| status | TEXT NOT NULL DEFAULT 'open' | `open` / `closed` |
| created_at | TEXT NOT NULL | 생성 시각 |
| updated_at | TEXT NOT NULL | 수정 시각 |

### 3.5 `inquiry_message` (선택 기능)
| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | TEXT PK | 메시지 ID |
| inquiry_id | TEXT NOT NULL | 문의 스레드 ID |
| sender_id | TEXT NOT NULL | 발신자 |
| content | TEXT NOT NULL | 메시지 내용 |
| created_at | TEXT NOT NULL | 생성 시각 |

- `product_inquiry`, `inquiry_message`는 Phase 7 선택 기능을 구현할 때만 생성한다.
- Phase 7을 구현하지 않으면 두 테이블은 생성하지 않는다.
### 3.6 `transfer` (신규)
| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | TEXT PK | 송금 ID |
| sender_id | TEXT NOT NULL | 송신자 |
| receiver_id | TEXT NOT NULL | 수신자 |
| amount | INTEGER NOT NULL | 송금 금액 |
| note | TEXT NULL | 메모 |
| created_at | TEXT NOT NULL | 송금 시각 |

### 3.7 `admin_audit_log` (신규)
| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | TEXT PK | 감사 로그 ID |
| admin_id | TEXT NOT NULL | 관리자 |
| action | TEXT NOT NULL | 수행 작업 |
| target_type | TEXT NOT NULL | 대상 종류 |
| target_id | TEXT NOT NULL | 대상 ID |
| detail | TEXT NOT NULL | 작업 세부 내용 |
| created_at | TEXT NOT NULL | 수행 시각 |

## 4. 인덱스 설계
| 대상 | 인덱스 | 목적 |
|---|---|---|
| `user.username` | UNIQUE | 로그인/중복 방지 |
| `product.title` | INDEX | 상품 검색 보조 |
| `product.price` | INDEX | 가격 필터 |
| `report(reporter_id, target_type, target_id, status)` | INDEX | 중복 신고 제한 |
| `transfer(sender_id, created_at)` | INDEX | 사용자 거래 조회 |
| `admin_audit_log(admin_id, created_at)` | INDEX | 감사 조회 |
| `product_inquiry(product_id, buyer_id, seller_id)` | INDEX | 선택 기능 구현 시 문의 스레드 조회 |
| `inquiry_message(inquiry_id, created_at)` | INDEX | 선택 기능 구현 시 메시지 시간순 조회 |

## 5. 데이터 무결성 규칙
- `user.role`은 `user`, `admin`만 허용
- `user.status`는 `active`, `suspended`만 허용
- 신규 가입자 `user.balance` 기본값은 `100000`
- 정지 사용자는 신규 로그인을 할 수 없고, 이미 로그인된 경우 다음 보호 요청에서 세션을 종료한다.
- `product.status`는 `active`, `blocked`만 허용
- 차단 상품은 일반 사용자 목록과 상세 조회에서 숨긴다.
- `report.status`는 `pending`, `dismissed`, `actioned`만 허용
- `transfer.amount > 0`
- 송금 시 `sender_id != receiver_id`
- 문의 메시지 작성자는 Phase 7 선택 기능 구현 시 스레드 참여자여야 함
- 신고 처리 시 관리자 ID와 검토 시각을 함께 저장

## 6. 마이그레이션 방향
1. 구현 단계 시작 전에 현재 `market.db`를 로컬 백업 파일 `market.baseline.db`로 복사한다.
2. `market.db`와 `market.baseline.db`는 모두 Git에 포함하지 않는다.
3. 기존 검증용 DB를 직접 ALTER하는 대신 목표 스키마 기준으로 새 `market.db`를 생성한다.
4. 기존 `user.password`를 `password_hash`로 전환하고, 사용자명은 3~20자 영문/숫자/밑줄만 허용한다.
5. 기존 `product.price TEXT`를 정수 가격으로 정규화하고, 상품 제목은 1~100자, 설명은 최대 2000자로 검증한다.
6. 기존 `report`에 상태/대상 유형/검토 메타데이터를 추가하고, 신고 사유는 1~500자로 제한한다.
7. 신규 테이블 생성: `transfer`, `admin_audit_log`
8. 선택 기능 Phase 7을 구현할 경우에만 `product_inquiry`, `inquiry_message`를 생성한다.
9. 초기 관리자 계정은 코드나 DB에 하드코딩하지 않고 `flask --app app create-admin` CLI 명령으로 생성한다.
10. 관리자 비밀번호는 `getpass` 기반 대화형 입력으로 받고, 평문으로 출력하거나 저장하지 않고 해시로만 저장한다.

## 7. 주의사항
- SQLite는 ALTER 제약이 있으므로 일부 변경은 임시 테이블 복사 전략보다 새 DB 생성 전략이 더 단순하고 안전할 수 있다.
- 구현 단계 전까지는 실제 DB 백업, 초기화, 스키마 변경을 수행하지 않는다.
