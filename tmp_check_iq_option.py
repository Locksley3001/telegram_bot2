import asyncio
import os

from dotenv import load_dotenv

from app.iq_option_broker import IQOptionBroker


async def main() -> None:
    load_dotenv(override=True)
    print("EMAIL_LEN", len(os.getenv("IQ_OPTION_EMAIL", "")))
    print("PASSWORD_LEN", len(os.getenv("IQ_OPTION_PASSWORD", "")))
    print("2FA_CODE_PRESENT", bool(os.getenv("IQ_OPTION_2FA_CODE", "")))
    broker = IQOptionBroker(
        os.getenv("IQ_OPTION_EMAIL", ""),
        os.getenv("IQ_OPTION_PASSWORD", ""),
        two_factor_code=os.getenv("IQ_OPTION_2FA_CODE", ""),
        balance_mode=os.getenv("IQ_OPTION_BALANCE_MODE", "PRACTICE"),
    )
    try:
        await broker.connect()
        print("CONNECTED", broker.connected)
        print("CURRENT_PRICE", await broker.get_current_price("EURUSD-OTC"))
        await broker.disconnect()
    except Exception as exc:
        print("ERROR", type(exc).__name__, str(exc)[:500])


asyncio.run(main())
