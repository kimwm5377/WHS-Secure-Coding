# Phase 1 Security Changes

## 1. 평문 비밀번호 저장
1. 보안 약점
   - 신규 회원 비밀번호와 기존 사용자 비밀번호가 평문으로 저장됐다.
2. 기존 코드 위치
   - baseline `app.py:68-85`, `app.py:89-104`
3. 발생 가능한 영향
   - DB 유출 시 계정 비밀번호가 즉시 노출된다.
4. 수정한 파일
   - `app.py`
   - `tests/test_phase1.py`
5. 수정 방법
   - `generate_password_hash`, `check_password_hash`를 적용했다.
   - 기존 `market.db`의 평문 비밀번호는 `market.baseline.db` 백업이 있을 때만 애플리케이션 초기화 시 해시로 전환되게 했다.
   - 이미 해시된 값은 다시 해시하지 않게 했다.
   - 평문 비밀번호가 하나라도 있으면 전체 변경을 하나의 명시적 트랜잭션으로 처리하고, 실패 시 rollback 하도록 했다.
6. 대응 요구사항 ID
   - `SEC-001`
7. 테스트 ID
   - `T-003`, `T-101`
8. 실제 테스트 결과
   - 통과. 신규 가입 비밀번호는 해시로 저장되고 로그인 검증이 정상 동작했다.
   - 통과. 기존 평문 비밀번호 샘플 DB도 해시로 전환됨을 검증했다.
   - 통과. 기본 `market.db` 이름의 평문 비밀번호 DB는 `market.baseline.db` 백업이 없으면 마이그레이션을 거부함을 검증했다.
   - 통과. 변환 중 예외 발생 시 rollback 되어 일부 사용자만 변환된 상태로 남지 않음을 검증했다.
9. 남은 한계
   - 컬럼명은 여전히 `user.password`다. 컬럼명 정리는 후속 Phase 범위다.

## 2. 하드코딩된 SECRET_KEY
1. 보안 약점
   - `SECRET_KEY`가 코드에 하드코딩되어 있었다.
2. 기존 코드 위치
   - baseline `app.py:6-9`
3. 발생 가능한 영향
   - 세션 위조 및 예측 가능성 증가
4. 수정한 파일
   - `app.py`
   - `readme.md`
   - `.env.example`
   - `tests/test_phase1.py`
5. 수정 방법
   - `SECRET_KEY`를 환경변수에서만 읽도록 변경했다.
   - 값이 없으면 앱 실행을 즉시 중단하고 설정 방법만 안내한다.
   - 임시 키나 기본 키를 사용하지 않는다.
6. 대응 요구사항 ID
   - `SEC-002`
7. 테스트 ID
   - `T-102`
8. 실제 테스트 결과
   - 통과. `SECRET_KEY` 미설정 시 초기화 실패, 설정 시 정상 초기화 확인.
9. 남은 한계
   - 비밀값 배포 및 운영 회전 정책은 아직 문서 수준만 반영됐다.

## 3. CSRF 보호 부재
1. 보안 약점
   - 상태 변경 POST 요청에 CSRF 보호가 없었다.
2. 기존 코드 위치
   - baseline `templates/login.html`, `templates/register.html`, `templates/profile.html`, `templates/new_product.html`, `templates/report.html`
3. 발생 가능한 영향
   - 사용자의 의도 없는 요청 위조 가능
4. 수정한 파일
   - `app.py`
   - `templates/base.html`
   - `templates/login.html`
   - `templates/register.html`
   - `templates/profile.html`
   - `templates/new_product.html`
   - `templates/report.html`
   - `tests/test_phase1.py`
5. 수정 방법
   - 세션 기반 CSRF 토큰을 추가했다.
   - 회원가입, 로그인, 로그아웃, 프로필 수정, 상품 등록, 신고 제출에 토큰을 적용했다.
6. 대응 요구사항 ID
   - `SEC-003`
7. 테스트 ID
   - `T-103`
8. 실제 테스트 결과
   - 통과. 토큰 누락/변조 요청이 차단되고 정상 토큰 요청은 성공했다.
9. 남은 한계
   - Socket.IO 채팅에는 Phase 1에서 CSRF를 적용하지 않았다.

## 4. GET 로그아웃
1. 보안 약점
   - 로그아웃이 GET 요청으로 수행됐다.
2. 기존 코드 위치
   - baseline `app.py:107-112`, `templates/base.html:89-99`
3. 발생 가능한 영향
   - 의도하지 않은 로그아웃 유도 및 CSRF 대응 부재
4. 수정한 파일
   - `app.py`
   - `templates/base.html`
   - `tests/test_phase1.py`
5. 수정 방법
   - `GET /logout`을 제거하고 `POST /logout`만 허용했다.
   - 로그아웃 링크를 CSRF 토큰이 포함된 POST form 버튼으로 변경했다.
6. 대응 요구사항 ID
   - `SEC-003`, `SEC-005`
7. 테스트 ID
   - `T-004`, `T-103`
8. 실제 테스트 결과
   - 통과. POST 로그아웃 성공 후 세션 제거 확인, 토큰 없는 로그아웃은 차단 확인.
9. 남은 한계
   - 별도 로그아웃 확인 화면은 없다. 기존 화면 흐름을 유지했다.

## 5. 반복된 인증 검사
1. 보안 약점
   - 보호 라우트마다 로그인 확인 로직이 반복됐다.
2. 기존 코드 위치
   - baseline `app.py:115-150`, `app.py:183-200`
3. 발생 가능한 영향
   - 누락 가능성 증가, 보호 정책 일관성 저하
4. 수정한 파일
   - `app.py`
   - `tests/test_phase1.py`
5. 수정 방법
   - 공통 `login_required` 가드를 추가해 보호 라우트에 적용했다.
6. 대응 요구사항 ID
   - `SEC-005`
7. 테스트 ID
   - `T-106`
8. 실제 테스트 결과
   - 통과. 비로그인 사용자는 보호 라우트 접근이 차단됐다.
9. 남은 한계
   - 관리자 권한 가드와 객체 소유자 가드는 Phase 1 범위가 아니다.

## 6. 로그인 무차별 대입 제한 부재
1. 보안 약점
   - 동일 IP와 사용자명에 대한 반복 로그인 실패 제한이 없었다.
2. 기존 코드 위치
   - baseline `app.py:89-104`
3. 발생 가능한 영향
   - 무차별 대입 시도에 취약
4. 수정한 파일
   - `app.py`
   - `readme.md`
   - `tests/test_phase1.py`
5. 수정 방법
   - 메모리 기반 로그인 실패 기록을 추가했다.
   - 동일 IP + 사용자명 기준 10분 동안 5회 실패를 기록하고 6번째 시도부터 제한한다.
   - 성공 시 실패 기록을 초기화한다.
   - 사용자 존재 여부와 관계없이 동일한 실패 메시지를 유지한다.
6. 대응 요구사항 ID
   - `SEC-009`
7. 테스트 ID
   - `T-110`
8. 실제 테스트 결과
   - 통과. 실패 5회 기록, 6번째 제한, 성공 후 초기화, 동일 실패 메시지 확인.
9. 남은 한계
   - 메모리 기반이라 프로세스 재시작 시 기록이 초기화되고 다중 프로세스/다중 서버에 공유되지 않는다.

## 7. 안전하지 않은 세션 쿠키 기본값
1. 보안 약점
   - 세션 쿠키 보안 속성이 명시되지 않았다.
2. 기존 코드 위치
   - baseline `app.py:6-9`
3. 발생 가능한 영향
   - XSS/CSRF/전송 구간 설정 누락에 따른 위험 증가
4. 수정한 파일
   - `app.py`
   - `readme.md`
   - `tests/test_phase1.py`
5. 수정 방법
   - `SESSION_COOKIE_HTTPONLY=True`
   - `SESSION_COOKIE_SAMESITE='Lax'`
   - `PERMANENT_SESSION_LIFETIME` 설정
   - 개발 HTTP 환경에서는 `SESSION_COOKIE_SECURE=False`
   - 운영 HTTPS 환경에서는 `SESSION_COOKIE_SECURE=True`
6. 대응 요구사항 ID
   - `SEC-008`
7. 테스트 ID
   - `T-109`
8. 실제 테스트 결과
   - 통과. 개발/운영 환경별 쿠키 속성 차이를 검증했다.
9. 남은 한계
   - 실제 운영 HTTPS 배포 구성은 별도 인프라 설정이 필요하다.

## 8. debug=True 하드코딩
1. 보안 약점
   - 기본 실행이 `debug=True`로 고정되어 있었다.
2. 기존 코드 위치
   - baseline `app.py:208-210`
3. 발생 가능한 영향
   - 디버거 노출, 내부 정보 노출, 운영 보안 저하
4. 수정한 파일
   - `app.py`
   - `readme.md`
   - `tests/test_phase1.py`
5. 수정 방법
   - 기본 실행은 `debug=False`로 변경했다.
   - `APP_DEBUG=true`를 명시한 개발 환경에서만 debug 허용, 운영 환경에서는 항상 비활성화한다.
6. 대응 요구사항 ID
   - `SEC-013`
7. 테스트 ID
   - `T-114`
8. 실제 테스트 결과
   - 통과. 기본/운영 환경에서 debug 비활성화 확인, 기본 실행 로그에서 Debugger PIN 미출력 확인.
9. 남은 한계
   - 상세 사용자 친화 오류 페이지는 Phase 6 이후 범위다.

## 9. app factory 적용 이유
1. 이유
   - 환경별 보안 설정(`SECRET_KEY`, debug, Secure 쿠키)을 초기화 시점에 일관되게 적용하기 위해 유지했다.
   - 테스트에서 임시 SQLite DB를 안전하게 분리하기 위해 유지했다.
2. 남은 한계
   - 이번 Phase에서는 구조를 유지하되 추가 리팩터링은 하지 않았다.
