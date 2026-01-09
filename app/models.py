from typing import Optional, List
from sqlmodel import SQLModel, Field, Relationship
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import UniqueConstraint, Column, Numeric


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
    spotify_refresh_token: Optional[str] = None
    # persisted site-wide role (passerby|student|teacher|individual)
    site_role: Optional[str] = None

    presentations: List["Presentation"] = Relationship(
        back_populates="owner",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


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
    filename: Optional[str] = None
    music_url: Optional[str] = None
    file_size: Optional[int] = None
    language: Optional[str] = None
    mimetype: Optional[str] = None
    owner_id: Optional[int] = Field(default=None, foreign_key="user.id")
    category_id: Optional[int] = Field(default=None, foreign_key="category.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    views: int = 0
    # simple visibility + download controls
    privacy: str = Field(default="public")  # public|private
    allow_download: bool = Field(default=True)

    owner: Optional[User] = Relationship(back_populates="presentations")
    category: Optional[Category] = Relationship(back_populates="presentations")
    tags: List[Tag] = Relationship(
        back_populates="presentations", link_model=PresentationTag
    )


class Follow(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("follower_id", "following_id", name="uq_follow"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    follower_id: int = Field(foreign_key="user.id")
    following_id: int = Field(foreign_key="user.id")


class Like(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("user_id", "presentation_id", name="uq_like"),)
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
    # Use Decimal in Python with a SQL Numeric column (precision 12, scale 2)
    amount: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(12, 2)))
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


class Notification(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    recipient_id: int = Field(foreign_key="user.id", index=True)
    actor_id: Optional[int] = Field(default=None, foreign_key="user.id")
    verb: str
    target_type: Optional[str] = None
    target_id: Optional[int] = None
    read: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    sender_id: int = Field(foreign_key="user.id")
    recipient_id: int = Field(foreign_key="user.id")
    content: str
    file_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    read: bool = False


class ClassroomMessage(SQLModel, table=True):
    """Group chat message scoped to a classroom.

    Each message belongs to a classroom and has a single sender. All
    classroom members can view the history when they open the classroom
    space.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    classroom_id: int = Field(foreign_key="classroom.id")
    sender_id: int = Field(foreign_key="user.id")
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    __table_args__ = {"extend_existing": True}


class Bookmark(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("user_id", "presentation_id", name="uq_bookmark"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    presentation_id: int = Field(foreign_key="presentation.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class School(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    slug: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Classroom(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    school_id: Optional[int] = Field(default=None, foreign_key="school.id")
    name: str
    code: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Membership(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    classroom_id: int = Field(foreign_key="classroom.id")
    role: str = Field(default="student")  # student|teacher|admin
    created_at: datetime = Field(default_factory=datetime.utcnow)


class LibraryItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    classroom_id: int = Field(foreign_key="classroom.id")
    presentation_id: Optional[int] = Field(default=None, foreign_key="presentation.id")
    title: Optional[str] = None
    filename: Optional[str] = None
    mimetype: Optional[str] = None
    uploaded_by: Optional[int] = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Assignment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    classroom_id: int = Field(foreign_key="classroom.id")
    title: str
    description: Optional[str] = None
    due_date: Optional[datetime] = None
    created_by: Optional[int] = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Submission(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    assignment_id: int = Field(foreign_key="assignment.id")
    student_id: int = Field(foreign_key="user.id")
    filename: Optional[str] = None
    mimetype: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    grade: Optional[float] = None
    feedback: Optional[str] = None


class AssignmentStatus(SQLModel, table=True):
        """Per-student status for an assignment.

        status values:
            - done        -> counted as turned in
            - almost      -> pending / almost done
            - rebel       -> not going to do it
        """

        __table_args__ = (
                UniqueConstraint("assignment_id", "student_id", name="uq_assignment_status"),
        )

        id: Optional[int] = Field(default=None, primary_key=True)
        assignment_id: int = Field(foreign_key="assignment.id")
        student_id: int = Field(foreign_key="user.id")
        status: str = Field(default="almost")
        created_at: datetime = Field(default_factory=datetime.utcnow)


class Attendance(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    classroom_id: int = Field(foreign_key="classroom.id")
    user_id: int = Field(foreign_key="user.id")
    date: datetime = Field(default_factory=datetime.utcnow)
    status: str = Field(default="present")  # present|absent|late


class AIResult(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    presentation_id: Optional[int] = Field(default=None, foreign_key="presentation.id")
    task_type: Optional[str] = None
    result: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class StudentAnalytics(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    classroom_id: Optional[int] = Field(default=None, foreign_key="classroom.id")
    event_type: str = Field(default="event")  # upload|submission|view|grade|attendance|quiz_result
    details: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)




class ConsentLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    consent: Optional[str] = None
    ip: Optional[str] = None
    ua: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
