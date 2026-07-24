# Secure Coding

## Tiny Secondhand Shopping Platform

Flask + SQLite 기반 과제용 중고거래 플랫폼이다.

## 환경 준비
Miniconda(또는 Anaconda)가 없다면 먼저 설치한다.
- https://docs.anaconda.com/free/miniconda/index.html

```bash
git clone https://github.com/ugonfor/secure-coding
cd secure-coding
conda env create -f enviroments.yaml
conda activate secure_coding
```

## 환경변수 설정
`.env` 파일은 만들지 않고 셸 환경변수로 설정한다.

```bash
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export APP_ENV=development
export APP_DEBUG=false
```

- `SECRET_KEY`는 필수다.
- 값이 없으면 애플리케이션은 즉시 종료된다.
- 임시 키나 하드코딩 기본 키는 사용하지 않는다.

비밀값 생성 예시:
```bash
python -c 'import secrets; print(secrets.token_hex(32))'
```

## DB 백업 파일
- `market.baseline.db`: 최초 취약 상태 DB 백업
- `market.phase1.db`: Phase 1 완료 시점 DB 백업
- `market.db`: 현재 실행 대상 DB

백업 DB는 Git에 추가하지 않는다.

## Phase 2 DB 초기화
앱은 구형 스키마를 자동 변경하지 않는다.
새 DB는 명시적으로 초기화해야 한다.

```bash
conda run -n secure_coding python -m flask --app app init-db
```

기존 `market.db`를 교체하려면 명시적 옵션이 필요하다.

```bash
conda run -n secure_coding python -m flask --app app init-db --replace
```

기본 `market.db`를 교체할 때는 `market.phase1.db` 백업이 먼저 존재해야 한다.

### 운영 경고
- `init-db --replace` 실행 전에 애플리케이션 서버를 종료해야 한다.
- 실행 중인 서버가 기존 SQLite DB 연결을 유지한 상태에서 DB 파일을 교체하면 기존 프로세스와 새 프로세스가 서로 다른 DB 파일을 참조할 수 있다.
- 서버 실행 중에는 `market.db`를 교체하지 않는다.
- 서버를 종료한 뒤 백업 파일 존재를 확인하고 `init-db --replace`를 실행한다.
- 교체 후 서버를 다시 시작한다.

권장 실행 순서:
1. 서버 종료
2. `market.phase1.db` 존재 확인
3. `init-db --replace` 실행
4. `schema_version=2` 확인
5. 서버 재시작

백업 확인 예시:
```bash
test -f market.phase1.db && echo "phase1 backup exists"
```


## 초기 관리자 생성
```bash
conda run -n secure_coding python -m flask --app app create-admin
```

- `getpass` 기반 대화형 입력 사용
- 사용자명 3~20자, 영문/숫자/밑줄만 허용
- 비밀번호 최소 8자
- 평문 비밀번호는 출력하거나 저장하지 않음

## 실행
`market.db`가 Phase 2 스키마로 초기화된 뒤 실행한다.

```bash
conda activate secure_coding
python app.py
```

기본 동작:
- `APP_ENV=development`에서는 `SESSION_COOKIE_SECURE=False`
- 기본 `debug=False`
- `APP_DEBUG=true`는 개발 환경에서만 허용
- 운영 환경(`APP_ENV=production`)에서는 항상 `debug=False`
- `python app.py`, `flask --app app run`, `python -m flask --app app run`은 요청 처리 전에 Phase 2 스키마를 검증한다.
- `init-db`는 DB 생성 목적의 예외 경로로 동작하므로 미초기화/구형 DB 상태에서도 실행할 수 있다.
- `create-admin`은 유효한 `schema_version=2` DB가 있을 때만 실행할 수 있다.


## 개발/운영 쿠키 설정
개발 HTTP 환경 (`APP_ENV=development`):
- `SESSION_COOKIE_HTTPONLY=True`
- `SESSION_COOKIE_SAMESITE=Lax`
- `SESSION_COOKIE_SECURE=False`

운영 HTTPS 환경 (`APP_ENV=production`):
- `SESSION_COOKIE_HTTPONLY=True`
- `SESSION_COOKIE_SAMESITE=Lax`
- `SESSION_COOKIE_SECURE=True`

## 로그인 제한
로그인 보호는 메모리 기반 limiter를 사용한다.
기준 키는 `request.remote_addr + username`이다.

규칙:
- 10분 동안 실패 5회까지 기록
- 6번째 시도부터 차단
- 로그인 성공 시 실패 기록 초기화
- 사용자 존재 여부와 관계없이 동일한 실패 메시지 사용

한계:
- 프로세스 재시작 시 기록 초기화
- 다중 프로세스/다중 서버 간 공유되지 않음

## 테스트
공식 테스트 명령:

```bash
conda run -n secure_coding python -m unittest discover -s tests -p 'test_phase*.py' -v
```

이미 `secure_coding` 환경이 활성화되어 있다면 다음도 가능하다.

```bash
python -m unittest discover -s tests -p 'test_phase*.py' -v
```

## 운영 주의사항
- `APP_ENV=production`은 HTTPS 뒤에서만 사용한다.
- 구형 스키마 DB를 자동 변경하지 않는다.
- `init-db --replace`는 서버를 완전히 종료한 상태에서만 실행한다.
- 서버 실행 경로와 CLI 경로 구분은 현재 프로세스의 공식 실행 인자(`run`, `init-db`, `create-admin`)를 기준으로 판단한다. 비표준 실행 방식에서는 동일한 조기 검증이 보장되지 않을 수 있다.
- `eventlet`은 현재 Flask-SocketIO 실행을 위해 포함되어 있다.

## 수동 확인 예시
```bash
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export APP_ENV=development
conda run -n secure_coding python -m flask --app app init-db --replace
conda run -n secure_coding python app.py
```

브라우저에서 확인할 항목:
- 회원가입
- 로그인
- 프로필 수정
- 상품 등록
- 잘못된 가격 상품 등록 거부
- 사용자 신고 / 상품 신고
- 존재하지 않는 대상 신고 거부
- POST 로그아웃

### DB 교체 주의사항

`init-db --replace`는 애플리케이션 서버를 종료한 상태에서만 실행해야 한다.
서버가 기존 SQLite 연결을 유지한 상태에서 DB 파일을 교체하면 서로 다른 DB 파일을 참조할 수 있다.

권장 순서:

1. 서버 종료
2. `market.phase1.db` 백업 확인
3. `init-db --replace` 실행
4. `schema_version=2` 확인
5. 서버 재시작
