# Baseline Analysis

## 1. 목적
이 문서는 보안 개선 및 기능 확장 전에 현재 저장소의 기준 상태를 정리한다. 본 문서의 기준은 `baseline-before-secure-coding` Git 태그와 실행 검증 결과다.

## 2. 기준 상태 요약
- 실행 환경: `secure_coding` Conda 환경
- 실행 주소: `http://127.0.0.1:5000`
- 실행 조건: `eventlet` 설치 후 `python app.py` 정상 실행
- 데이터베이스: SQLite `market.db`
- DB 접근 방식: Python `sqlite3` 직접 사용, SQLAlchemy 미사용
- 검증 완료 기능:
  - 회원가입
  - 로그인 / 로그아웃
  - 프로필 수정
  - 상품 등록 및 조회
  - 신고 접수
  - 전체 공개 채팅
- 미구현 필수 예정 기능:
  - 검색 및 가격 필터
  - 가상 잔액 기반 송금
  - 관리자 관리 기능
  - 실제 사용자 정지 / 상품 차단
- 미구현 선택 기능:
  - 상품별 1:1 문의

## 3. 현재 구조
- `app.py`: 라우트, DB 초기화, 인증, 상품, 신고, 채팅 로직 집중
- `templates/`: 화면 템플릿
- `enviroments.yaml`: Python 3.9, Flask 계열 의존성
- `secure_coding_checklist.csv`: 보안 점검 항목 초안

## 4. 현재 애플리케이션 구조
### 4.1 라우트 및 기능
| 경로/이벤트 | 현재 상태 | 근거 |
|---|---|---|
| `/` | 인덱스, 로그인 상태면 대시보드 리다이렉트 | `app.py:61-65` |
| `/register` | 회원가입 | `app.py:68-86` |
| `/login` | 로그인 | `app.py:89-105` |
| `/logout` | 로그아웃 | `app.py:108-112` |
| `/dashboard` | 대시보드 + 전체 상품 목록 + 공개 채팅 UI | `app.py:115-127`, `templates/dashboard.html:1-44` |
| `/profile` | 프로필 조회/수정 | `app.py:130-144` |
| `/product/new` | 상품 등록 | `app.py:147-165` |
| `/product/<product_id>` | 상품 상세 조회 | `app.py:168-180` |
| `/report` | 신고 접수 | `app.py:183-200` |
| `send_message` | 공개 채팅 브로드캐스트 | `app.py:203-206` |

### 4.2 현재 템플릿
- 공통 레이아웃: `templates/base.html`
- 메인 화면: `templates/index.html`
- 인증: `templates/login.html`, `templates/register.html`
- 사용자: `templates/profile.html`
- 상품: `templates/dashboard.html`, `templates/new_product.html`, `templates/view_product.html`
- 신고: `templates/report.html`

## 5. 현재 데이터베이스 구조
### 5.1 `user`
- `id TEXT PRIMARY KEY`
- `username TEXT UNIQUE NOT NULL`
- `password TEXT NOT NULL`
- `bio TEXT`

### 5.2 `product`
- `id TEXT PRIMARY KEY`
- `title TEXT NOT NULL`
- `description TEXT NOT NULL`
- `price TEXT NOT NULL`
- `seller_id TEXT NOT NULL`

### 5.3 `report`
- `id TEXT PRIMARY KEY`
- `reporter_id TEXT NOT NULL`
- `target_id TEXT NOT NULL`
- `reason TEXT NOT NULL`

## 6. 기준 상태 실행 검증 결과
| 항목 | 결과 | 비고 |
|---|---|---|
| 홈 화면 접속 | 성공 | HTTP 200 |
| 회원가입 | 성공 | 사용자 생성 확인 |
| 로그인 | 성공 | 세션 생성 확인 |
| 프로필 수정 | 성공 | `bio` 반영 확인 |
| 상품 등록 | 성공 | 상품 row 생성 확인 |
| 상품 상세 조회 | 성공 | 판매자 표시 확인 |
| 신고 접수 | 성공 | 신고 row 생성 확인 |
| 전체 채팅 | 성공 | Socket.IO polling 기반 수신 확인 |

## 7. 현재 기준 상태의 한계
### 7.1 기능 한계
- 관리자 역할이 없다.
- 신고 검토 프로세스가 없다.
- 사용자 정지 및 상품 차단 기능이 없다.
- 상품 검색과 가격 필터가 없다.
- 가상 잔액 및 송금 기능이 없다.
- 선택 기능인 상품별 1:1 문의 및 메시지 저장 기능이 없다.

### 7.2 구조 한계
- `app.py` 단일 파일 집중 구조라 기능 확장 시 복잡도 증가 가능성이 높다.
- 테이블 간 외래키/상태 관리 컬럼이 부족하다.
- 공개 채팅은 저장되지 않으며 감사 추적이 약하다.

### 7.3 보안 한계
- `SECRET_KEY` 하드코딩 (`app.py:7`)
- 비밀번호 평문 저장 및 평문 비교 (`app.py:32-37`, `app.py:96-97`)
- `debug=True` 실행 (`app.py:210`)
- CSRF 보호 없음 (현재 템플릿 전반)
- 서버 측 입력 검증 미흡 (`app.py:70-82`, `app.py:136-139`, `app.py:151-162`, `app.py:187-197`)
- 관리자 권한 검증 로직 부재
- 객체 소유자 검증 확장 여지 부족
- 채팅 요청 제한 없음

## 8. 이번 설계의 제약 조건
- 현재 시점에서는 `app.py`, `templates`, DB 스키마를 수정하지 않는다.
- 최종 구현에서도 SQLite + `sqlite3` 방식을 유지한다.
- 기존 전체 공개 채팅 기능은 유지한다.

## 9. 설계 기준 결론
현재 애플리케이션은 과제의 최소 골격은 일부 갖추고 있으나, 관리자 기능·검색·송금·차단·보안 통제가 빠져 있다. 다음 단계의 문서와 구현은 기존 기능을 보존하면서 안전한 인증/권한 구조, 관리자 검토 흐름, 가상 잔액 송금, 검색/필터, 감사 로그를 우선 추가하고, 시간이 허용되면 선택 기능인 상품별 문의 저장을 추가하는 방향으로 진행한다.
