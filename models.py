from sqlalchemy import Column, Integer, String, Boolean, Date, DateTime, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
import datetime

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(Integer, unique=True, index=True)
    username = Column(String, nullable=True)
    is_premium = Column(Boolean, default=False)
    premium_expires = Column(DateTime, nullable=True)
    last_request_date = Column(Date, default=datetime.date.today)
    requests_today = Column(Integer, default=0)

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(Integer, index=True)
    amount = Column(Integer)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

def get_sessionmaker(db_url: str):
    if db_url.startswith("sqlite"):
        engine = create_engine(db_url, connect_args={"check_same_thread": False})
    else:
        engine = create_engine(db_url)  # ✅ Postgres, MySQL, etc.
    return sessionmaker(autocommit=False, autoflush=False, bind=engine), engine
