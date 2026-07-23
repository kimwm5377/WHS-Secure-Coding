# Secure Coding

## Tiny Secondhand Shopping Platform

You should add some functions and complete the security requirements.

## requirements

If you don't have Miniconda (or Anaconda), install it first:
- https://docs.anaconda.com/free/miniconda/index.html

```bash
git clone https://github.com/ugonfor/secure-coding
cd secure-coding
conda env create -f enviroments.yaml
conda activate secure_coding
```

## environment setup

Create environment variables before running the server.

```bash
cp .env.example .env.example.local-note
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export APP_ENV=development
# optional for local debugging only
export APP_DEBUG=false
```

`SECRET_KEY` is required. The application stops immediately if it is missing.
It does not generate a temporary key and it does not use a hardcoded fallback key.

## secure secret generation

```bash
python -c 'import secrets; print(secrets.token_hex(32))'
```

## usage

Run the server process in the `secure_coding` Conda environment.

```bash
conda activate secure_coding
python app.py
```

## tests

Use the reproducible test command below first:

```bash
conda run -n secure_coding python -m unittest discover -s tests -p 'test_phase1.py' -v
```

If the `secure_coding` environment is already activated, you can also run:

```bash
python -m unittest discover -s tests -p 'test_phase1.py' -v
```

Default behavior:
- `APP_ENV=development` uses HTTP-friendly cookies (`SESSION_COOKIE_SECURE=False`)
- debug is disabled by default
- `APP_DEBUG=true` only enables debug in development mode
- production mode keeps debug disabled

## development vs production cookie behavior

Development HTTP (`APP_ENV=development`):
- `SESSION_COOKIE_HTTPONLY=True`
- `SESSION_COOKIE_SAMESITE=Lax`
- `SESSION_COOKIE_SECURE=False`

Production HTTPS (`APP_ENV=production`):
- `SESSION_COOKIE_HTTPONLY=True`
- `SESSION_COOKIE_SAMESITE=Lax`
- `SESSION_COOKIE_SECURE=True`

## login rate limiting

The login protection uses an in-memory limiter keyed by `request.remote_addr + username`.
Rules:
- up to 5 failed logins are recorded during 10 minutes
- the 6th attempt is blocked
- a successful login clears the failure record for that key
- the same failure message is used whether the username exists or not

Limitations of the in-memory approach:
- records reset when the process restarts
- records are not shared across multiple processes or servers

## operational notes

- Set `APP_ENV=production` only behind HTTPS.
- Keep `SECRET_KEY` out of Git and shell history when possible.
- `eventlet` is included because it is required for the current Flask-SocketIO runtime.

## external access (optional)

If you want to test on an external machine, you can use ngrok to forward the URL.

```bash
sudo snap install ngrok
ngrok http 5000
```
