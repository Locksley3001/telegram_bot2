# Quotex Binary Signals

Aplicacion web profesional para monitorear velas reales de Quotex y generar pocas senales de alta calidad para expiraciones cortas.

## Fuente de datos

La aplicacion usa exclusivamente `pyquotex` de cleitonleonel mediante email y contraseña de Quotex. No usa TradingView, Twelve Data, Binance ni otra fuente externa.

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
QUOTEX_PASSWORD=tu_contraseña
TELEGRAM_BOT_TOKEN=tu_token_de_botfather
TELEGRAM_CHAT_ID=tu_chat_id 
hola
```

Telegram solo envia senales con puntuacion `>= 6`.
