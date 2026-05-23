from passlib.context import CryptContext
from collections import defaultdict
import time

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

rate_limit_store = defaultdict(list)

def hash_password(password):
    return pwd_context.hash(password)

def verify_password(password, hashed):
    return pwd_context.verify(password, hashed)

def check_rate_limit(identifier, limit=5, window=60):

    now = time.time()

    rate_limit_store[identifier] = [
        t for t in rate_limit_store[identifier]
        if now - t < window
    ]

    if len(rate_limit_store[identifier]) >= limit:
        return False

    rate_limit_store[identifier].append(now)
    return True