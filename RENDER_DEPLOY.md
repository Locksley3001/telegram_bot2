# Despliegue en Render

## 1. Build Command exacto

Pega este comando en **Build Command**:

```bash
pip install -r requirements.txt && pip install git+https://github.com/cleitonleonel/pyquotex.git
```

## 2. Start Command exacto

Pega este comando en **Start Command**:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

El servidor usa FastAPI con Uvicorn y responde en el puerto asignado por Render mediante la variable `PORT`.

## 3. Variables de entorno requeridas

Configura estas variables en **Environment > Environment Variables**:

```text
QUOTEX_EMAIL -> Correo de inicio de sesion de la cuenta Quotex.
QUOTEX_PASSWORD -> Contraseña de la cuenta Quotex.
TELEGRAM_BOT_TOKEN -> Token del bot creado en @BotFather.
TELEGRAM_CHAT_ID -> ID del chat, usuario, grupo o canal donde recibiras las senales.
```

Variables opcionales:

```text
MARKETS -> Lista separada por comas. Ejemplo: EURUSD_otc,GBPUSD_otc,USDJPY_otc.
DEFAULT_TIMEFRAME -> 30, 45, 60, 120, 180 o 300. Por defecto: 60.
POLL_INTERVAL_SECONDS -> Frecuencia de consulta al broker. Por defecto: 2.0.
CANDLE_COUNT -> Cantidad de velas analizadas por activo. Por defecto: 80.
SIGNAL_COOLDOWN_SECONDS -> Enfriamiento por activo/direccion. Por defecto: 45.
```

## 4. Tipo de servicio en Render

Crea un **Web Service**.

La aplicacion necesita servir el panel web, exponer el WebSocket `/ws`, responder `/health` y ejecutar el motor de analisis en segundo plano dentro del mismo proceso. Un Background Worker no expone el panel ni el puerto HTTP que Render necesita para enrutar trafico web.

## 5. Consideraciones para que no colapse en produccion

- El servidor web arranca aunque `QUOTEX_EMAIL` o `QUOTEX_PASSWORD` no esten configurados; mostrara el estado en pantalla y seguira respondiendo `/health`.
- El motor de mercado corre como tarea asincrona separada del servidor FastAPI.
- Si falla la conexion con Quotex, el motor cambia a estado de reconexion y vuelve a intentar sin tumbar el proceso.
- Cada activo se analiza con manejo de errores propio; una vela defectuosa o un activo fallido no detiene el resto.
- Telegram se ejecuta solo para senales con puntuacion `>= 6` y no debe bloquear la respuesta HTTP del servidor.
- Render debe usar siempre `PORT`; el comando de inicio ya incluye `--port $PORT`.
- Para reducir carga, usa pocos mercados al comienzo y sube gradualmente. El sistema permite agregar mercados sin limite funcional, pero el broker y el plan de Render pueden imponer limites reales de conexion, latencia o CPU.
- Mantener `POLL_INTERVAL_SECONDS` en `2.0` o superior evita consultas excesivas al broker.

## Nota sobre la libreria Quotex

`pyquotex` es una libreria no oficial. Si la libreria falla, queda aislada en `app/quotex_client.py`, que es el unico punto donde debe implementarse una captura alternativa desde WebSocket interno, Playwright, Selenium o intercepcion de trafico del broker. No sustituyas esa capa por TradingView, Twelve Data, Binance ni feeds externos.
