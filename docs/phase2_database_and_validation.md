# Phase 2 Database and Validation

## 1. 기존 DB 구조의 문제
Phase 1 이전/직후 DB는 다음 한계가 있었다.
- `user.password` 평문/임시 해시 호환 구조
- 역할, 상태, 잔액 컬럼 부재
- 상품 가격이 TEXT
- 신고 상태/대상 종류/검토 메타데이터 부재
- 스키마 버전 확인 수단 부재

## 2. 목표 DB 스키마
Phase 2 현재 스키마는 다음 테이블만 생성한다.
- `schema_meta`
- `user`
- `product`
- `report`

제외 테이블:
- `transfer`
- `admin_audit_log`
- `product_inquiry`
- `inquiry_message`

## 3. 백업 및 신규 DB 생성 방식
- `market.baseline.db`: 최초 취약 상태 DB 백업
- `market.phase1.db`: Phase 1 완료 시점 DB 백업
- `market.db`: 현재 실행 대상 DB

초기화 원칙:
1. 기존 DB를 직접 ALTER하며 누적 변경하지 않음
2. 새 목표 스키마 DB를 명시적으로 생성
3. 기존 `market.db` 교체 시 `market.phase1.db` 백업 존재 확인
4. `market.baseline.db`, `market.phase1.db`는 절대 삭제하지 않음
5. `init-db --replace`는 서버를 완전히 종료한 뒤에만 실행
6. 임시 DB에 스키마를 생성·검증한 뒤 `os.replace()`로 교체

## 4. 스키마 버전 확인 방식
- `schema_meta` 테이블의 `schema_version` 사용
- 현재 지원 버전: `2`
- `schema_meta`, `user`, `product`, `report` 필수 테이블과 필수 컬럼 목록까지 함께 검증
- `python app.py`, `flask --app app run`, `python -m flask --app app run`은 요청 처리 전에 검증 실패 시 서버를 중단하고 `init-db` 실행을 안내
- 구형 스키마 발견 시 자동 변경하지 않고 실행 중단 + `init-db` 안내
- `init-db`는 DB 생성 목적의 예외 경로이므로 미초기화/구형 DB 상태에서도 실행 가능


## 5. user 테이블 변경
- `password` 제거
- `password_hash` 사용
- `role`, `status`, `balance`, `created_at`, `updated_at`, `suspended_reason` 추가
- 일반 회원가입은 항상:
  - `role='user'`
  - `status='active'`
  - `balance=100000`

## 6. product 테이블 변경
- `price`를 INTEGER로 변경
- `status`, `created_at`, `updated_at`, `blocked_reason` 추가
- `seller_id`는 `user.id` 외래키 참조
- 서버 검증:
  - 제목 1~100자
  - 설명 최대 2000자
  - 가격은 0보다 큰 정수

## 7. report 테이블 변경
- `target_type`, `status`, `admin_id`, `action_type`, `review_note`, `created_at`, `reviewed_at` 추가
- 신고 생성 시 항상:
  - `status='pending'`
  - `admin_id=NULL`
  - `action_type=NULL`
  - `review_note=NULL`
  - `reviewed_at=NULL`
- `target_id`는 다형 대상이라 앱에서 존재 여부 검증

## 8. 입력값 검증 기준
### 회원가입
- 사용자명: 3~20자, 영문/숫자/밑줄
- 비밀번호: 최소 8자

### 상품
- 제목: 앞뒤 공백 제거, 1~100자, 공백만 입력 금지
- 설명: 앞뒤 공백 제거, 최대 2000자, 공백만 입력 금지
- 가격: 정수만 허용, 0보다 커야 함

### 신고
- `target_type`: `user` 또는 `product`
- `target_id`: 공백 금지, 실제 대상 존재 필요
- `reason`: 앞뒤 공백 제거, 1~500자, 공백만 입력 금지

## 9. 초기 관리자 생성 방식
명령:
```bash
python -m flask --app app create-admin
```

동작:
- `getpass` 기반 대화형 비밀번호 입력
- 사용자명 검증
- 비밀번호 최소 8자 검증
- 비밀번호 확인 일치 검증
- 중복 사용자명 거부
- `role='admin'`, `status='active'`, `balance=100000`
- 비밀번호는 해시로만 저장
- 유효한 `schema_version=2` DB가 없으면 생성 거부


## 10. 실행한 테스트
- Phase 1 전체 테스트 재실행
- Phase 2 필수 테스트: T-006, T-007, T-009, T-010, T-104, T-105, T-201, T-202
- 추가 테스트:
  - 일반 회원가입으로 admin 생성 불가
  - 신규 사용자 기본 상태값 검증
  - `create-admin` CLI 성공/중복 거부
  - `create-admin`의 미초기화/구형 DB 거부
  - `init-db`의 DB 없음 성공, 기본 비덮어쓰기, `--replace` 백업 요구 확인
  - CHECK 제약조건 위반 거부
  - FOREIGN KEY 위반 거부
  - `python app.py` 구형 스키마 조기 중단
  - `flask --app app run --no-reload` 구형 스키마 조기 중단


## 11. 테스트 결과
- 공식 명령 `conda run -n secure_coding python -m unittest discover -s tests -p 'test_phase*.py' -v` 통과
- 상품 가격 INTEGER 저장 확인
- 신고 상태 `pending` 저장 확인
- 신고 생성만으로 자동 제재 없음 확인
- 런타임 `PRAGMA foreign_keys = ON` 확인
- `schema_version = 2` 확인
- `python app.py`, `flask --app app run --no-reload` 모두 구형 스키마에서 포트를 열기 전에 안전 중단 확인
- `init-db --replace`는 서버 중지 상태에서만 수행해야 한다는 운영 경고를 README에 반영


## 12. 남아 있는 기능과 보안 문제
- 관리자 권한 가드 미구현
- 신고 승인/기각 미구현
- 중복 신고 제한 미구현
- 상품 검색/가격 필터 미구현
- 송금/감사 로그 미구현
- 채팅 rate limiting 미구현
- 문의 기능 미구현

## 13. 유지보수 시 주의사항
- 구형 DB를 앱 시작 시 자동으로 ALTER하지 않는다.
- 새 스키마가 필요하면 `init-db` 또는 별도 명시적 마이그레이션 절차를 사용한다.
- `market.baseline.db`, `market.phase1.db`는 덮어쓰지 않는다.
- Phase 1에서 사용하던 시작 시 자동 평문 비밀번호 마이그레이션은 제거되었고, 기존 상태는 `market.phase1.db`에 보존된다.
- 서버 실행 경로와 CLI 경로 구분은 현재 프로세스의 공식 실행 인자(`run`, `init-db`, `create-admin`)를 기준으로 판단한다. 비표준 실행 방식에서는 동일한 조기 검증이 보장되지 않을 수 있다.
