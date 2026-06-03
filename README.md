# Quotex Binary Signals

Aplicacion web profesional para monitorear velas reales de Quotex y generar pocas senales de alta calidad para expiraciones cortas.

## Fuente de datos

La aplicacion usa exclusivamente `pyquotex` de cleitonleonel mediante email y contrasena de Quotex. No usa TradingView, Twelve Data, Binance ni otra fuente externa.

## Version de Python

El proyecto fija Python `3.12.8` mediante `.python-version`. Esto evita que Render use Python `3.14.x`, donde `pydantic-core` puede intentar compilar Rust durante el build.

## Configuracion local

1. Crea `.env` a partir de `.env.example`.
2. Instala dependencias:

```bash
pip install -r requirements.txt
pip install git+https://github.com/cleitonleonel/pyquotex.git
```

3. Ejecuta:

```bash
uvicorn app.main:app --reload
```

4. Abre `http://127.0.0.1:8000`.

## Variables requeridas

```env
QUOTEX_EMAIL=tu_correo@gmail.com
QUOTEX_PASSWORD=tu_contrasena
TELEGRAM_BOT_TOKEN=tu_token_de_botfather
TELEGRAM_CHAT_ID=tu_chat_id
```

Telegram solo envia senales con puntuacion `>= 6`.
