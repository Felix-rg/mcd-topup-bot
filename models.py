from pydantic import BaseModel

class TopUpRequest(BaseModel):
    phone: str
    provider: str
    nominal: str
    method: str

class TopUpResponse(BaseModel):
    id: str
    status: str
    message: str
    invoice_url: str

class AdminLogin(BaseModel):
    username: str
    password: str