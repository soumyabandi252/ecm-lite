import os, uuid
from datetime import datetime
from sqlalchemy import create_engine, Column, String, Integer, Boolean, DateTime, Text, JSON
from sqlalchemy.orm import sessionmaker, declarative_base

DB_URL = os.getenv("DATABASE_URL", "sqlite:///./ecm.db")
if DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql://", 1)
engine = create_engine(DB_URL, connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {})
Session = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()
uid = lambda: str(uuid.uuid4())
now = datetime.utcnow

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=uid)
    email = Column(String, unique=True, index=True)
    pw = Column(String)

class Node(Base):
    __tablename__ = "nodes"
    id = Column(String, primary_key=True, default=uid)
    parent_id = Column(String, nullable=True)
    name = Column(String)
    kind = Column(String)
    category = Column(String, nullable=True)
    attributes = Column(JSON, default=dict)
    owner_id = Column(String)
    created_at = Column(DateTime, default=now)
    deleted = Column(Boolean, default=False)
    legal_hold = Column(Boolean, default=False)
    retention_until = Column(DateTime, nullable=True)
    lifecycle = Column(String, default="draft")
    text = Column(Text, default="")

class Version(Base):
    __tablename__ = "versions"
    id = Column(String, primary_key=True, default=uid)
    node_id = Column(String, index=True)
    number = Column(Integer)
    path = Column(String)
    filename = Column(String)
    mime = Column(String)
    size = Column(Integer)
    created_at = Column(DateTime, default=now)

class Record(Base):
    # generic store: case, case_note, task, esign, quality_event, form_def, form_sub, retention_policy, comment, audit
    __tablename__ = "records"
    id = Column(String, primary_key=True, default=uid)
    type = Column(String, index=True)
    ref = Column(String, index=True, nullable=True)
    data = Column(JSON, default=dict)
    created_at = Column(DateTime, default=now)

Base.metadata.create_all(engine)
