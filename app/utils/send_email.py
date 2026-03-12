import asyncio
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig
from fastapi import HTTPException
from pydantic import EmailStr
from typing import Optional
from app.config import settings


conf = ConnectionConfig(
    MAIL_USERNAME=settings.EMAIL_HOST_USER,
    MAIL_PASSWORD=settings.EMAIL_HOST_PASSWORD,
    MAIL_FROM=settings.DEFAULT_FROM_EMAIL,
    MAIL_PORT=settings.EMAIL_PORT,
    MAIL_SERVER=settings.EMAIL_HOST,
    MAIL_STARTTLS=True,
    MAIL_SSL_TLS=False,
    USE_CREDENTIALS=True,
)


async def send_email(
    subject: str,
    message: str,
    to_email: EmailStr,
    html_message: Optional[str] = None,
    *,
    retries: int = 2,
    delay: int = 2
) -> bool:
    body = html_message if html_message else message
    subtype = "html" if html_message else "plain"

    msg = MessageSchema(
        subject=subject,
        recipients=[to_email],
        body=body,
        subtype=subtype,
    )

    fast_mail = FastMail(conf)

    for attempt in range(1, retries + 2):
        try:
            await fast_mail.send_message(msg)
            print(f"Email sent to {to_email}")
            return True

        except Exception as e:
            print(f"Email failed (attempt {attempt}/{retries+1}) → {e}")

            if attempt <= retries:
                print(f"⏳ Retrying in {delay} seconds...")
                await asyncio.sleep(delay)
            else:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to send email after retries"
                )
