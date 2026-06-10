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
MARKETS=EURUSD-OTC,GBPUSD-OTC,USDJPY-OTC,BTCUSD-OTC,ETHUSD-OTC,NVDA/AMD-OTC,SOLUSD-OTC
DEFAULT_TIMEFRAME=60
POLL_INTERVAL_SECONDS=0.75
CANDLE_COUNT=80
SIGNAL_COOLDOWN_SECONDS=45
DATA_DIR=data
SIGNAL_HISTORY_LIMIT=500
API_SIGNAL_LIMIT=500
LEARNING_ENABLED=true
LEARNING_MIN_HISTORY=30
LEARNING_MIN_WIN_RATE=58
LEARNING_MIN_RULE_SAMPLES=5
LEARNING_MIN_SIMILARITY_SAMPLES=4
LEARNING_EXPLORATION_INTERVAL=20
ADVANTAGE_FILTER_ENABLED=true
ADVANTAGE_FILTER_MIN_WIN_RATE=60
ADVANTAGE_FILTER_MIN_SAMPLES=30
ADVANTAGE_FILTER_MIN_FACTOR_SCORE=4
BROKER_TRADING_ENABLED=false
BROKER_TRADE_ENTRY_WINDOW_SECONDS=3
SUPABASE_URL=https://kwbqjullmtrankjpmwfs.supabase.co
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_SERVICE_KEY=
SUPABASE_KEY=
SUPABASE_STATE_ENABLED=true
SUPABASE_STATE_TABLE=bot_state_files
SUPABASE_VERSIONS_TABLE=bot_state_file_versions
SUPABASE_BOOTSTRAP_LOCAL=false
SUPABASE_REMOTE_SAVE_INTERVAL_SECONDS=60
SUPABASE_VERSIONING_ENABLED=false
SUPABASE_VERSION_INTERVAL_SECONDS=3600
```

Telegram solo envia senales con puntuacion `>= 7`.

El monitor usa stream de velas en tiempo real cuando IQ Option lo permite, y la logica CCI puede alertar sobre la vela en formacion cuando ya hay rechazo/cansancio suficiente.

El historial visual de alertas se guarda en `DATA_DIR/signals.json`. Por defecto `DATA_DIR=data`.
`SIGNAL_HISTORY_LIMIT` controla cuantas senales se conservan en ese archivo y queda limitado a 500
para evitar que el panel se llene; `API_SIGNAL_LIMIT` controla cuantas se mandan al dashboard en cada
actualizacion. Este historial no es la memoria principal de aprendizaje: el aprendizaje se reconstruye
desde `performance.json`.

El dashboard de rendimiento guarda las senales emitidas en `DATA_DIR/performance.json` y las evalua
despues de la expiracion sugerida para medir ganadas, perdidas, empates, pendientes y acierto por mercado.

El filtro de aprendizaje usa todo `DATA_DIR/performance.json`, guarda su memoria en `DATA_DIR/learning.json`
y bloquea senales cuando casos historicos parecidos no superan el acierto minimo configurado. Para evitar
que el aprendizaje se quede sin muestras nuevas, `LEARNING_EXPLORATION_INTERVAL` permite una senal fuerte
de exploracion cada N bloqueos; usa `0` para desactivarlo.

El filtro de ventaja no cambia como aprende el sistema ni la tecnica de entrada: solo deja operar senales
que el aprendizaje ya permitio y que ademas superan `ADVANTAGE_FILTER_MIN_WIN_RATE`, tienen al menos
`ADVANTAGE_FILTER_MIN_SAMPLES` muestras parecidas y alcanzan `ADVANTAGE_FILTER_MIN_FACTOR_SCORE`.
Para volver al comportamiento mas libre, usa `ADVANTAGE_FILTER_ENABLED=false`.

## Trading automatico en IQ Option

Por seguridad, la ejecucion de operaciones en el broker esta apagada por defecto. Para activarla:

```env
BROKER_TRADING_ENABLED=true
IQ_OPTION_BALANCE_MODE=PRACTICE
```

Usa primero `PRACTICE` para validar que las entradas coinciden con el saldo virtual. Solo cambia
`IQ_OPTION_BALANCE_MODE=REAL` cuando quieras operar con dinero real.

El bot no compra al enviar la alerta de Telegram. Compra cuando el historial de rendimiento confirma
que la senal paso el chequeo de aborto y entro en estado `pending`, que es el mismo flujo usado por
el saldo virtual. Las operaciones abortadas no se envian al broker.

`BROKER_TRADE_ENTRY_WINDOW_SECONDS` controla cuantos segundos despues de la apertura esperara el bot
para enviar la orden; por defecto son `3`. Los intentos reales se guardan en
`DATA_DIR/broker_trades.json` y se pueden ver en el dashboard, en la seccion **Broker en vivo**, o en
`/api/broker/trades`.

El dashboard permite conectar/desconectar el envio al broker sin redeploy. El boton solo cambia la
duplicacion real de las operaciones aprobadas por el saldo virtual; no cambia la forma en que se
generan, bloquean o aprenden las senales.

## Supabase

Con Supabase configurado, la aplicacion carga primero el estado remoto de `performance.json`,
`learning.json`, `signals.json`, `telegram_notifications.json` y `broker_trades.json`. Cada guardado
se conserva como espejo local en `DATA_DIR`; Supabase recibe solo cambios reales y como maximo una
escritura remota por archivo cada `SUPABASE_REMOTE_SAVE_INTERVAL_SECONDS`. Esto reduce Disk IO sin
cambiar la logica de aprendizaje ni el formato de los JSON existentes.

Variables necesarias en Render:

```env
SUPABASE_URL=https://kwbqjullmtrankjpmwfs.supabase.co
SUPABASE_SERVICE_ROLE_KEY=tu_service_role_key
SUPABASE_STATE_ENABLED=true
```

Tambien se aceptan `SUPABASE_SERVICE_KEY` o `SUPABASE_KEY` si ya las tienes creadas con ese nombre.

`SUPABASE_BOOTSTRAP_LOCAL=false` evita subir datos precargados del repo cuando falta una fila remota.
Para migrar un JSON local existente hacia Supabase, cambialo temporalmente a `true`.

`SUPABASE_VERSIONING_ENABLED=false` evita llenar `bot_state_file_versions` con copias completas de los
JSON en cada guardado. Si necesitas auditoria historica, cambialo a `true`; en ese caso
`SUPABASE_VERSION_INTERVAL_SECONDS` controla cada cuanto se guarda una version por archivo.

## CONFIGURACION_MANUAL_REQUERIDA

- `IQ_OPTION_EMAIL`: correo de inicio de sesion de IQ Option.
- `IQ_OPTION_PASSWORD`: contrasena de IQ Option.
- `IQ_OPTION_2FA_CODE`: solo si IQ Option exige codigo 2FA/SMS. Es temporal y debe actualizarse manualmente cuando expire.
