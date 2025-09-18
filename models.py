# models.py
import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, DateTime, Date, func
)
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, index=True, nullable=False)
    username = Column(String, nullable=True)
    is_premium = Column(Boolean, default=False)
    premium_expires = Column(DateTime, nullable=True)
    requests_today = Column(Integer, default=0)
    last_request_date = Column(Date, nullable=True)
    created_at = Column(DateTime, default=func.now())

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, index=True)
    amount = Column(Integer)
    currency = Column(String)
    provider = Column(String)
    payload = Column(String)
    timestamp = Column(DateTime, default=func.now())

def get_sessionmaker(db_url):
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)
