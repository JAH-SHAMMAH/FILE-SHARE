from typing import Optional, List
from sqlmodel import SQLModel, Field, Relationship
from datetime import datetime, date


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    email: str = Field(index=True, unique=True)
    hashed_password: str
    full_name: Optional[str] = None
    bio: Optional[str] = None
    avatar: Optional[str] = None
    date_of_birth: Optional[date] = None
    is_premium: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)

    presentations: List["Presentation"] = Relationship(back_populates="owner")


class Category(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)

    presentations: List["Presentation"] = Relationship(back_populates="category")


class PresentationTag(SQLModel, table=True):
    presentation_id: int = Field(foreign_key="presentation.id", primary_key=True)
    tag_id: int = Field(foreign_key="tag.id", primary_key=True)


class Tag(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)

    presentations: List["Presentation"] = Relationship(
        back_populates="tags", link_model=PresentationTag
    )


class Presentation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    description: Optional[str] = None
    filename: str
    mimetype: Optional[str] = None
    owner_id: Optional[int] = Field(default=None, foreign_key="user.id")
    category_id: Optional[int] = Field(default=None, foreign_key="category.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    views: int = 0

    owner: Optional[User] = Relationship(back_populates="presentations")
    category: Optional[Category] = Relationship(back_populates="presentations")
    tags: List[Tag] = Relationship(
        back_populates="presentations", link_model=PresentationTag
    )


class Follow(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    follower_id: int = Field(foreign_key="user.id")
    following_id: int = Field(foreign_key="user.id")


class Like(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    presentation_id: int = Field(foreign_key="presentation.id")


class Comment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    presentation_id: int = Field(foreign_key="presentation.id")
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Transaction(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    order_id: Optional[str] = None
    payer_id: Optional[str] = None
    user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    amount: Optional[str] = None
    currency: Optional[str] = None
    status: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WebhookEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    event_type: Optional[str] = None
    payload: Optional[str] = None
    verified: Optional[bool] = False
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ConversionJob(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    presentation_id: Optional[int] = Field(default=None, foreign_key="presentation.id")
    job_id: Optional[str] = None
    status: Optional[str] = None
    result: Optional[str] = None
    log: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Activity(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    verb: Optional[str] = None
    target_id: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    sender_id: int = Field(foreign_key="user.id")
    recipient_id: int = Field(foreign_key="user.id")
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    read: bool = False


class Bookmark(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    presentation_id: int = Field(foreign_key="presentation.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
