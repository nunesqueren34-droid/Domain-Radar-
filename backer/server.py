from fastapi import FastAPI, APIRouter, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid
from datetime import datetime
import dns.resolver
import asyncio

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

app = FastAPI()
api_router = APIRouter(prefix="/api")

class StatusCheck(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class StatusCheckCreate(BaseModel):
    client_name: str

class DomainCheck(BaseModel):
    domain: str
    available: bool
    checked_at: datetime = Field(default_factory=datetime.utcnow)

class DomainCheckRequest(BaseModel):
    domain: str

class Platform(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    url: str
    logo_url: Optional[str] = None
    is_default: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)

class PlatformCreate(BaseModel):
    name: str
    url: str
    logo_url: Optional[str] = None

class PlatformUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    logo_url: Optional[str] = None

async def check_domain_availability(domain: str) -> bool:
    try:
        domain = domain.lower().strip()
        if not domain.endswith(('.com','.org','.net','.br')):
            domain += '.com'
        resolver = dns.resolver.Resolver()
        resolver.timeout = 3
        resolver.lifetime = 3
        try:
            answers = resolver.resolve(domain, 'A')
            return False
        except dns.resolver.NXDOMAIN:
            return True
        except (dns.resolver.NoAnswer, dns.resolver.Timeout):
            try:
                answers = resolver.resolve(domain, 'ANY')
                return False
            except:
                return True
        except Exception:
            return True
    except Exception as e:
        print(f"Error checking domain {domain}: {e}")
        return True

async def init_default_platforms():
    default_platforms = [
        {"name": "GoDaddy", "url": "https://godaddy.com/domainsearch/find?checkAvail=1&domainToCheck={}", "logo_url": "https://img.godaddy.com/assets/brand/gd-logo.svg", "is_default": True},
        {"name": "Namecheap", "url": "https://www.namecheap.com/domains/registration/results/?domain={}", "logo_url": "https://www.namecheap.com/assets/img/nc-icon.svg", "is_default": True},
        {"name": "Google Domains", "url": "https://domains.google.com/registrar/search?searchTerm={}", "logo_url": "https://www.google.com/images/branding/googleg/1x/googleg_standard_color_128dp.png", "is_default": True},
        {"name": "Registro.br", "url": "https://registro.br/", "logo_url": "https://registro.br/images/logo-registro-br.png", "is_default": True}
    ]
    for platform_data in default_platforms:
        existing = await db.platforms.find_one({"name": platform_data["name"]})
        if not existing:
            platform = Platform(**platform_data)
            await db.platforms.insert_one(platform.dict())

@api_router.get("/")
async def root():
    return {"message": "Domain Radar API"}

@api_router.post("/check-domain", response_model=DomainCheck)
async def check_domain(request: DomainCheckRequest):
    is_available = await check_domain_availability(request.domain)
    domain_check = DomainCheck(domain=request.domain, available=is_available)
    await db.domain_checks.insert_one(domain_check.dict())
    return domain_check

@api_router.get("/platforms", response_model=List[Platform])
async def get_platforms():
    platforms = await db.platforms.find().to_list(1000)
    return [Platform(**platform) for platform in platforms]

@api_router.post("/platforms", response_model=Platform)
async def create_platform(platform_data: PlatformCreate):
    platform = Platform(**platform_data.dict())
    await db.platforms.insert_one(platform.dict())
    return platform

@api_router.put("/platforms/{platform_id}", response_model=Platform)
async def update_platform(platform_id: str, platform_data: PlatformUpdate):
    update_data = {k: v for k, v in platform_data.dict().items() if v is not None}
    result = await db.platforms.update_one({"id": platform_id}, {"$set": update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Platform not found")
    updated_platform = await db.platforms.find_one({"id": platform_id})
    return Platform(**updated_platform)

@api_router.delete("/platforms/{platform_id}")
async def delete_platform(platform_id: str):
    result = await db.platforms.delete_one({"id": platform_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Platform not found")
    return {"message": "Platform deleted successfully"}

@api_router.get("/domain-history", response_model=List[DomainCheck])
async def get_domain_history():
    checks = await db.domain_checks.find().sort("checked_at", -1).limit(50).to_list(50)
    return [DomainCheck(**check) for check in checks]

@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    status_obj = StatusCheck(**input.dict())
    await db.status_checks.insert_one(status_obj.dict())
    return status_obj

@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    status_checks = await db.status_checks.find().to_list(1000)
    return [StatusCheck(**status_check) for status_check in status_checks]

app.include_router(api_router)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@app.on_event("startup")
async def startup_event():
    await init_default_platforms()

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
