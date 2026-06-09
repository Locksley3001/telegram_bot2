# Despliegue en Render

## 0. Version de Python obligatoria

Este repo incluye un archivo `.python-version` con:

```text
3.12.8
```

Si Render no lo detecta automaticamente, agrega esta variable en **Environment > Environment Variables**:

```text
PYTHON_VERSION -> 3.12.8
```

## 1. Build Command exacto

Pega este comando en **Build Command**:

```bash
pip install -r requirements.txt
```

`requirements.txt` instala `iqoptionapi` desde GitHub y fija `websocket-client==0.56.0`, que es la version recomendada por el proyecto comunitario.

## 2. Start Command exacto

Pega este comando en **Start Command**:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

El servidor usa FastAPI con Uvicorn y responde en el puerto asignado por Render mediante la variable `PORT`.

## 3. Variables de entorno requeridas

Configura estas variables en **Environment > Environment Variables**:

```text
IQ_OPTION_EMAIL -> Correo de inicio de sesion de la cuenta IQ Option.
IQ_OPTION_PASSWORD -> Contrasena de la cuenta IQ Option.
TELEGRAM_BOT_TOKEN -> Token del bot creado en @BotFather.
TELEGRAM_CHAT_ID -> ID del chat, usuario, grupo o canal donde recibiras las senales.
DATA_DIR -> Ruta donde se guardan historial, aprendizaje y estado de Telegram. En Render gratis dejala sin configurar o usa data. Con Persistent Disk usa /var/data.
PYTHON_VERSION -> 3.12.8, solo si Render no detecta el archivo .python-version.
```

Variables opcionales:

```text
MARKETS -> Lista separada por comas. Ejemplo: EURUSD-OTC,GBPUSD-OTC,USDJPY-OTC.
DEFAULT_TIMEFRAME -> 30, 45, 60, 120, 180 o 300. Por defecto: 60.
POLL_INTERVAL_SECONDS -> Frecuencia de consulta al broker. Por defecto: 2.0.
CANDLE_COUNT -> Cantidad de velas analizadas por activo. Por defecto: 80.
SIGNAL_COOLDOWN_SECONDS -> Enfriamiento por activo/direccion. Por defecto: 45.
SIGNAL_HISTORY_LIMIT -> Senales conservadas en signals.json. Por defecto: 500; el codigo lo limita a 500 aunque Render tenga un valor mayor.
API_SIGNAL_LIMIT -> Senales recientes enviadas al dashboard. Por defecto: 500.
IQ_OPTION_BALANCE_MODE -> PRACTICE o REAL. Por defecto: PRACTICE.
IQ_OPTION_2FA_CODE -> Codigo temporal si IQ Option solicita 2FA/SMS.
LEARNING_ENABLED -> Activa o pausa el filtro de aprendizaje. Por defecto: true.
LEARNING_MIN_HISTORY -> Casos resueltos antes de bloquear por aprendizaje. Por defecto: 30.
LEARNING_MIN_WIN_RATE -> Porcentaje minimo esperado para permitir una senal. Por defecto: 58.
LEARNING_EXPLORATION_INTERVAL -> Permite una senal fuerte cada N bloqueos para seguir aprendiendo. Por defecto: 20. Usa 0 para desactivarlo.
BROKER_TRADING_ENABLED -> Envia operaciones al broker cuando una senal pasa a pending. Por defecto: false.
BROKER_TRADE_ENTRY_WINDOW_SECONDS -> Ventana maxima para entrar despues de abrir la vela. Por defecto: 3.
SUPABASE_URL -> URL del proyecto Supabase. Ejemplo: https://kwbqjullmtrankjpmwfs.supabase.co
SUPABASE_SERVICE_ROLE_KEY -> Clave service_role del proyecto Supabase para leer/escribir el estado.
SUPABASE_SERVICE_KEY / SUPABASE_KEY -> Alternativas aceptadas si ya tienes la clave con otro nombre.
SUPABASE_STATE_ENABLED -> Activa sincronizacion de estado con Supabase. Por defecto: true si hay URL y key.
SUPABASE_STATE_TABLE -> Tabla de estado actual. Por defecto: bot_state_files.
SUPABASE_VERSIONS_TABLE -> Tabla de versiones. Por defecto: bot_state_file_versions.
SUPABASE_BOOTSTRAP_LOCAL -> Sube JSON local si no existe fila remota. Por defecto: false.
SUPABASE_REMOTE_SAVE_INTERVAL_SECONDS -> Intervalo minimo entre escrituras remotas por archivo. Por defecto: 60.
SUPABASE_VERSIONING_ENABLED -> Guarda copias historicas en bot_state_file_versions. Por defecto: false para reducir Disk IO.
SUPABASE_VERSION_INTERVAL_SECONDS -> Intervalo minimo entre versiones historicas por archivo cuando el versionado esta activo. Por defecto: 3600.
```

## 4. Persistencia en Render

Para que el aprendizaje continue despues de redeploys/restarts, agrega un **Persistent Disk** al Web Service.
Usa por ejemplo:

```text
Mount Path -> /var/data
DATA_DIR -> /var/data
```

Con esto se conservan:

- `/var/data/performance.json`: operaciones emitidas y resultados.
- `/var/data/learning.json`: memoria del filtro de aprendizaje.
- `/var/data/signals.json`: historial de senales.
- `/var/data/telegram_notifications.json`: senales/resultados/resumenes ya notificados.
- `/var/data/broker_trades.json`: intentos de operaciones enviados a IQ Option.

Si usas Supabase, el estado remoto tiene prioridad sobre esos archivos locales. El disco persistente
sigue siendo util como espejo y fallback, pero el aprendizaje arranca desde las filas remotas de
`bot_state_files` cuando Supabase responde.

Para evitar alertas de Disk IO en Supabase, deja `SUPABASE_VERSIONING_ENABLED=false` y usa
`SUPABASE_REMOTE_SAVE_INTERVAL_SECONDS=60` o mas. El bot seguira leyendo los JSON remotos anteriores,
pero no reescribira el mismo contenido ni creara una fila historica por cada decision del aprendizaje.

Si no montas disco persistente, Render puede perder esos JSON al redeplegar y el aprendizaje puede reiniciar.
En el plan gratis no configures `DATA_DIR=/var/data`, porque esa ruta no sera escribible sin disco. Usa `DATA_DIR=data` o elimina la variable.

## 5. Tipo de servicio en Render

Crea un **Web Service**.

La aplicacion necesita servir el panel web, exponer el WebSocket `/ws`, responder `/health` y ejecutar el motor de analisis en segundo plano dentro del mismo proceso. Un Background Worker no expone el panel ni el puerto HTTP que Render necesita para enrutar trafico web.

## 6. Consideraciones para produccion

- Despues de configurar variables y redeplegar, abre `/health`. Debe mostrar `iq_option_configured: true` y `telegram_configured: true`.
- `/health` tambien muestra `data_dir`, `signal_history_limit`, `api_signal_limit` y `telegram_last_error` sin revelar credenciales.
- El servidor web arranca aunque `IQ_OPTION_EMAIL` o `IQ_OPTION_PASSWORD` no esten configurados; mostrara el estado en pantalla y seguira respondiendo `/health`.
- Si IQ Option exige 2FA, el estado mostrara un error con `CONFIGURACION_MANUAL_REQUERIDA`; configura `IQ_OPTION_2FA_CODE` con el codigo vigente y redepliega.
- El motor de mercado corre como tarea asincrona separada del servidor FastAPI.
- Si falla la conexion con IQ Option, el motor cambia a estado de reconexion y vuelve a intentar sin tumbar el proceso.
- Cada activo se analiza con manejo de errores propio; una vela defectuosa o un activo fallido no detiene el resto.
- Telegram se ejecuta solo para senales con puntuacion `>= 7` y no debe bloquear la respuesta HTTP del servidor.
- Render debe usar siempre `PORT`; el comando de inicio ya incluye `--port $PORT`.
- Para reducir carga, usa pocos mercados al comienzo y sube gradualmente. El sistema permite agregar mercados sin limite funcional, pero el broker y el plan de Render pueden imponer limites reales de conexion, latencia o CPU.
- Mantener `POLL_INTERVAL_SECONDS` en `2.0` o superior evita consultas excesivas al broker.

## Nota sobre la libreria IQ Option

`iqoptionapi` es una libreria no oficial. Fue seleccionada porque expone `stable_api`, login, velas historicas, activos abiertos, streams de velas, precio via ultima vela y payout digital cuando el broker lo devuelve. Si la libreria cambia o IQ Option bloquea la sesion, el unico punto que deberia ajustarse es `app/iq_option_broker.py`.

## CONFIGURACION_MANUAL_REQUERIDA

- `IQ_OPTION_EMAIL`: correo real de IQ Option.
- `IQ_OPTION_PASSWORD`: contrasena real de IQ Option.
- `IQ_OPTION_2FA_CODE`: codigo temporal de 2FA/SMS si IQ Option lo solicita. No se puede inventar ni automatizar de forma segura porque expira y depende de la cuenta.
