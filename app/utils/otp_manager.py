from typing import Any, Coroutine

from fastapi import HTTPException, BackgroundTasks
import  httpx
import random
from app.config import settings
from app.redis import get_redis


TWOFACTOR_BASE_URL = "https://2factor.in/API/V1"
OTP_EXPIRY_SECONDS = 120
MAX_ATTEMPTS_PER_HOUR = 30
TWOFACTOR_API_KEY = settings.TWOFACTOR_API_KEY

def _otp_key(phone: str, purpose: str):
    return f"{purpose}otp:{phone}"

def _otp_attempts_key(phone: str, purpose: str):
    return f"{purpose}otp_attempts:{phone}"

async def generate_otp(phone: str, purpose: str) -> None:
    redis = get_redis()
    otp_key = _otp_key(phone, purpose)
    attempts_key = _otp_attempts_key(phone, purpose)

    recent_count = await redis.get(attempts_key)
    recent_count = int(recent_count) if recent_count else 0
    if recent_count >= MAX_ATTEMPTS_PER_HOUR:
        raise HTTPException(status_code=429, detail="Too many OTP requests.")

    url = f"{TWOFACTOR_BASE_URL}/{TWOFACTOR_API_KEY}/SMS/{phone}/AUTOGEN/{purpose}"

    # generate & send OTP
    if settings.DEBUG:
        print('>>>>>>>>>>>>>>>>>>>>   Using Radis')
        otp = str(random.randint(100000, 999999))
        await redis.set(otp_key, otp, ex=OTP_EXPIRY_SECONDS)

        attempts_count = await redis.incr(attempts_key)
        if attempts_count == 1:
            await redis.expire(attempts_key, 3600)

        print(f"📨 OTP for {phone}: {otp}")
        return otp

    try:
        print('>>>>>>>>>>>>>>>>>>>>   Using 2Factor.in')
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()

            if data.get("Status") != "Success":
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to send OTP: {data.get('Details', 'Unknown error')}"
                )

            session_id = data.get("Details")
            await redis.set(otp_key, session_id, ex=OTP_EXPIRY_SECONDS)

            attempts_count = await redis.incr(attempts_key)
            if attempts_count == 1:
                await redis.expire(attempts_key, 3600)

            print(f"📨 OTP sent to {phone} for {purpose}")
            print(f"🔑 Session ID: {session_id}")

    except httpx.HTTPError as e:
        print(f"❌ HTTP Error sending OTP: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to send OTP. Please try again later."
        )
    except Exception as e:
        print(f"❌ Error sending OTP: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to send OTP. Please try again later."
        )


async def verify_otp(phone: str, otp_value: str, purpose: str) -> bool:
    redis = get_redis()
    otp_key = _otp_key(phone, purpose)
    attempts_key = _otp_attempts_key(phone, purpose)

    if settings.DEBUG:
        print('>>>>>>>>>>>>>>>>>>>>   Using Radis for Verify')
        stored_otp = await redis.get(otp_key)
        if not stored_otp:
            raise HTTPException(status_code=400, detail="OTP expired or not found.")
        if stored_otp != otp_value:
            raise HTTPException(status_code=400, detail="Invalid OTP.")

        await redis.delete(otp_key)
        return True
    try:
        print('>>>>>>>>>>>>>>>>>>>>   Using 2Factor.in for Verify')
        stored_value = await redis.get(otp_key)
        stored_session_id = await redis.get(otp_key)

        if not stored_session_id:
            raise HTTPException(status_code=400, detail="OTP expired or not found.")
        
        url = f"{TWOFACTOR_BASE_URL}/{TWOFACTOR_API_KEY}/SMS/VERIFY/{stored_session_id}/{otp_value}"
        # print(f"🔍 Verifying OTP with URL: {url}")
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()

            if data.get("Status") == "Success":
                await redis.delete(otp_key)
                await redis.delete(attempts_key)
                return True
            else:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to verify OTP. Please try again."
                )

    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=500,
            detail="Failed to verify OTP. Please try again."
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Failed to verify OTP. Please try again."
        )



