# IQ Option Binary Signals

Aplicacion web profesional para monitorear velas reales de IQ Option y generar pocas senales de alta calidad para expiraciones cortas.

## Fuente de datos

La aplicacion usa `iqoptionapi`, una libreria no oficial mantenida por la comunidad, mediante email y contrasena de IQ Option. No usa TradingView, Twelve Data, Binance ni otra fuente externa.

La integracion del broker esta aislada en:

- `app/broker_interface.py`: contrato comun del broker.
- `app/iq_option_broker.py`: adaptador de IQ Option que normaliza velas al modelo interno `Candle`.

## Version de Python

El proyecto fija Python `3.12.8` mediante `.python-version`. Esto evita que Render use Python `3.14.x`, donde algunas dependencias pueden requerir compilacion adicional durante el build.

## Configuracion local

1. Crea `.env` a partir de `.env.example`.
2. Instala dependencias:

```bash
pip install -r requirements.txt
```

3. Ejecuta:

```bash
uvicorn app.main:app --reload
```

4. Abre `http://127.0.0.1:8000`.

## Variables requeridas

```env
IQ_OPTION_EMAIL=tu_correo@gmail.com
IQ_OPTION_PASSWORD=tu_contrasena
TELEGRAM_BOT_TOKEN=tu_token_de_botfather
TELEGRAM_CHAT_ID=tu_chat_id
```

Variables opcionales:

```env
IQ_OPTION_BALANCE_MODE=PRACTICE
IQ_OPTION_2FA_CODE=
MARKETS=EURUSD-OTC,GBPUSD-OTC,USDJPY-OTC
DEFAULT_TIMEFRAME=60
POLL_INTERVAL_SECONDS=2.0
CANDLE_COUNT=80
SIGNAL_COOLDOWN_SECONDS=45
```

Telegram solo envia senales con puntuacion `>= 6`.

## CONFIGURACION_MANUAL_REQUERIDA

- `IQ_OPTION_EMAIL`: correo de inicio de sesion de IQ Option.
- `IQ_OPTION_PASSWORD`: contrasena de IQ Option.
- `IQ_OPTION_2FA_CODE`: solo si IQ Option exige codigo 2FA/SMS. Es temporal y debe actualizarse manualmente cuando expire.
