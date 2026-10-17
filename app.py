from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import sqlite3
import sys
import time
import uuid
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from flask import (
    Flask,
    abort,
    current_app,
    g,
    jsonify,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask_wtf.csrf import CSRFError, CSRFProtect, generate_csrf
from werkzeug.security import check_password_hash, generate_password_hash


ROOT = Path(__file__).resolve().parent
STATIC_FILES = {
    "index.html",
    "login.html",
    "manage.html",
    "reader.html",
    "detail.html",
    "register.html",
    "api.js",
}
csrf = CSRFProtect()


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    session_secret = os.environ.get("SESSION_SECRET") or secrets.token_hex(32)
    if os.environ.get("APP_ENV") == "production" and len(session_secret) < 32:
        raise RuntimeError("Production requires SESSION_SECRET with at least 32 characters.")
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=session_secret,
        DATABASE_PATH=os.environ.get(
            "DATABASE_PATH", str(Path(app.instance_path) / "stories.sqlite3")
        ),
        UPLOAD_FOLDER=os.environ.get(
            "UPLOAD_FOLDER", str(Path(app.instance_path) / "uploads")
        ),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("APP_ENV") == "production",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
        MAX_CONTENT_LENGTH=6 * 1024 * 1024,
        MAX_IMAGE_BYTES=5 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    csrf.init_app(app)
    initialize_database(app)

    @app.teardown_appcontext
    def close_database(_error: BaseException | None = None) -> None:
        del _error
        connection = g.pop("database", None)
        if connection is not None:
            connection.close()

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error: CSRFError):
        del error
        if request.path.startswith("/api/"):
            return jsonify(error="Your session expired. Refresh the page and try again."), 400
        return "The request could not be verified. Refresh the page and try again.", 400

    @app.errorhandler(413)
    def handle_payload_too_large(error):
        del error
        if request.path.startswith("/api/"):
            return jsonify(error="The request is too large."), 413
        return "The request is too large.", 413

    @app.errorhandler(404)
    def handle_not_found(error):
        del error
        if request.path.startswith("/api/"):
            return jsonify(error="Not found."), 404
        return "Not found.", 404

    @app.get("/")
    def homepage():
        return send_from_directory(ROOT, "index.html")

    @app.get("/<path:filename>")
    def static_page(filename: str):
        if filename in STATIC_FILES:
            return send_from_directory(ROOT, filename)
        abort(404)

    @app.get("/api/public")
    def public_content():
        return jsonify(
            stories=list_stories(),
            bio=get_bio(),
        )

    @app.get("/uploads/<path:filename>")
    def uploaded_image(filename: str):
        return send_from_directory(current_app.config["UPLOAD_FOLDER"], filename)

    @app.get("/api/auth/session")
    def auth_session():
        user = get_current_user()
        return jsonify(
            authenticated=user is not None,
            email=user["email"] if user else None,
            displayName=user["display_name"] if user else None,
            role=user["role"] if user else None,
            csrfToken=generate_csrf(),
        )

    @app.post("/api/auth/register")
    def register_reader():
        payload = json_payload()
        email = valid_email(payload.get("email"))
        display_name = valid_text(payload, "displayName", 60)
        password = payload.get("password")
        if not email or not display_name:
            return jsonify(error="Enter a valid email address and display name."), 400
        if not isinstance(password, str) or len(password) < 12 or len(password) > 1024:
            return jsonify(error="Your password must be at least 12 characters long."), 400
        database = get_database()
        now = int(time.time())
        limited = database.execute(
            """SELECT COUNT(*) FROM login_attempts
               WHERE ip_address = ? AND attempted_at >= ?""",
            (request.remote_addr or "unknown", now - 15 * 60),
        ).fetchone()[0]
        if limited >= 10:
            return jsonify(error="Too many account attempts. Please try again in 15 minutes."), 429
        try:
            cursor = database.execute(
                """INSERT INTO users (email, password_hash, role, display_name)
                   VALUES (?, ?, 'reader', ?)""",
                (email, generate_password_hash(password, method="scrypt"), display_name),
            )
            database.commit()
        except sqlite3.IntegrityError:
            return jsonify(error="An account with this email already exists."), 409
        session.clear()
        session["user_id"] = cursor.lastrowid
        session.permanent = True
        return jsonify(
            authenticated=True,
            email=email,
            displayName=display_name,
            role="reader",
            csrfToken=generate_csrf(),
        ), 201

    @app.post("/api/auth/login")
    def login():
        payload = json_payload()
        email = valid_text(payload, "email", 254).lower()
        password = payload.get("password")
        if not email or not isinstance(password, str) or len(password) > 1024:
            return jsonify(error="Enter a valid email address and password."), 400

        database = get_database()
        now = int(time.time())
        window_start = now - 15 * 60
        failures = database.execute(
            """SELECT COUNT(*) AS count FROM login_attempts
               WHERE ip_address = ? AND succeeded = 0 AND attempted_at >= ?""",
            (request.remote_addr or "unknown", window_start),
        ).fetchone()["count"]
        database.execute(
            "DELETE FROM login_attempts WHERE attempted_at < ?", (now - 24 * 60 * 60,)
        )
        if failures >= 10:
            return jsonify(error="Too many sign-in attempts. Please try again in 15 minutes."), 429

        user = database.execute(
            "SELECT id, email, password_hash, role, display_name FROM users WHERE email = ?", (email,)
        ).fetchone()
        authenticated = user is not None and check_password_hash(
            user["password_hash"], password
        )
        database.execute(
            "INSERT INTO login_attempts (ip_address, attempted_at, succeeded) VALUES (?, ?, ?)",
            (request.remote_addr or "unknown", now, int(authenticated)),
        )
        database.commit()
        if not authenticated:
            return jsonify(error="Email or password is incorrect."), 401

        session.clear()
        session["user_id"] = user["id"]
        session.permanent = payload.get("remember") is True
        return jsonify(
            authenticated=True,
            email=user["email"],
            displayName=user["display_name"],
            role=user["role"],
            csrfToken=generate_csrf(),
        )

    @app.post("/api/auth/logout")
    @signed_in_required
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get("/api/stories")
    @author_required
    def author_stories():
        return jsonify(stories=list_stories())

    @app.post("/api/stories")
    @author_required
    def create_story():
        payload = json_payload()
        story, error = validate_story(payload)
        if error:
            return jsonify(error=error), 400
        story["id"] = str(uuid.uuid4())
        database = get_database()
        cover_index = database.execute("SELECT COUNT(*) FROM stories").fetchone()[0] % 3
        story["cover"] = f"cover-{('one', 'two', 'three')[cover_index]}"
        cover_image = payload.get("coverImage", "")
        if cover_image and not valid_cover_path(cover_image):
            return jsonify(error="Choose an uploaded cover image."), 400
        story["cover_image"] = cover_image
        story["sort_order"] = (
            database.execute("SELECT COALESCE(MIN(sort_order), 0) - 1 FROM stories")
            .fetchone()[0]
        )
        insert_story(database, story, story["sort_order"])
        database.commit()
        return jsonify(story=story), 201

    @app.put("/api/stories/<story_id>")
    @author_required
    def update_story(story_id: str):
        payload = json_payload()
        story, error = validate_story(payload)
        if error:
            return jsonify(error=error), 400
        database = get_database()
        existing = database.execute(
            "SELECT cover, cover_image FROM stories WHERE id = ?", (story_id,)
        ).fetchone()
        if existing is None:
            return jsonify(error="Story not found."), 404
        story["id"] = story_id
        story["cover"] = existing["cover"]
        uploaded_cover = payload.get("coverImage")
        if uploaded_cover:
            if not valid_cover_path(uploaded_cover):
                return jsonify(error="Choose an uploaded cover image."), 400
            story["cover_image"] = uploaded_cover
        else:
            story["cover_image"] = existing["cover_image"]
        database.execute("DELETE FROM chapters WHERE story_id = ?", (story_id,))
        database.execute(
            """UPDATE stories
               SET title = ?, description = ?, genre = ?, updated = ?, blurb = ?,
                   cover = ?, cover_image = ?
               WHERE id = ?""",
            (
                story["title"],
                story["description"],
                story["genre"],
                story["updated"],
                story["blurb"],
                story["cover"],
                story["cover_image"],
                story_id,
            ),
        )
        insert_chapters(database, story)
        database.commit()
        return jsonify(story=story)

    @app.delete("/api/stories/<story_id>")
    @author_required
    def delete_story(story_id: str):
        database = get_database()
        cursor = database.execute("DELETE FROM stories WHERE id = ?", (story_id,))
        if cursor.rowcount == 0:
            return jsonify(error="Story not found."), 404
        database.commit()
        return jsonify(ok=True)

    @app.put("/api/bio")
    @author_required
    def update_bio():
        payload = json_payload()
        bio = payload.get("bio")
        if not isinstance(bio, str) or not bio.strip() or len(bio) > 1200:
            return jsonify(error="Biography must be between 1 and 1,200 characters."), 400
        database = get_database()
        database.execute(
            "INSERT INTO settings (key, value) VALUES ('bio', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (bio.strip(),),
        )
        database.commit()
        return jsonify(bio=bio.strip())

    @app.post("/api/uploads/story-cover")
    @author_required
    def upload_story_cover():
        image = request.files.get("image")
        if image is None or not image.filename:
            return jsonify(error="Choose an image file to upload."), 400
        header = image.stream.read(16)
        image.stream.seek(0)
        image_type = detect_image_type(header)
        if image_type is None:
            return jsonify(error="Upload a valid JPEG, PNG, or WebP image."), 400
        image_bytes = image.stream.read(current_app.config["MAX_IMAGE_BYTES"] + 1)
        if len(image_bytes) > current_app.config["MAX_IMAGE_BYTES"]:
            return jsonify(error="Cover images must be 5 MB or smaller."), 413
        extension, mime_type = image_type
        filename = f"{uuid.uuid4().hex}{extension}"
        uploads = Path(current_app.config["UPLOAD_FOLDER"])
        uploads.mkdir(parents=True, exist_ok=True)
        (uploads / filename).write_bytes(image_bytes)
        return jsonify(url=url_for("uploaded_image", filename=filename), type=mime_type), 201

    @app.get("/api/stories/<story_id>/feedback")
    def story_feedback(story_id: str):
        story = get_story(story_id)
        if story is None:
            return jsonify(error="Story not found."), 404
        feedback = get_database().execute(
            """SELECT f.id, f.kind, f.chapter_position, f.rating, f.title, f.body,
                      f.created_at, u.display_name
               FROM feedback AS f JOIN users AS u ON u.id = f.user_id
               WHERE f.story_id = ? AND f.status = 'approved'
               ORDER BY f.created_at DESC""",
            (story_id,),
        ).fetchall()
        return jsonify(
            reviews=[serialize_feedback(item) for item in feedback if item["kind"] == "review"],
            comments=[serialize_feedback(item) for item in feedback if item["kind"] == "comment"],
        )

    @app.post("/api/stories/<story_id>/reviews")
    @reader_required
    def create_review(story_id: str):
        story = get_story(story_id)
        if story is None:
            return jsonify(error="Story not found."), 404
        payload = json_payload()
        title = valid_text(payload, "title", 100)
        body = valid_text(payload, "body", 2000)
        rating = payload.get("rating")
        if title is None or body is None:
            return jsonify(error="Review title and text are required (up to 2,000 characters)."), 400
        if not isinstance(rating, int) or isinstance(rating, bool) or not 1 <= rating <= 5:
            return jsonify(error="Choose a rating from 1 to 5 stars."), 400
        return save_feedback(story_id, "review", None, rating, title, body)

    @app.post("/api/stories/<story_id>/chapters/<int:position>/comments")
    @reader_required
    def create_comment(story_id: str, position: int):
        story = get_story(story_id)
        if story is None:
            return jsonify(error="Story not found."), 404
        if not 0 <= position < len(story["chapters"]):
            return jsonify(error="Chapter not found."), 404
        body = valid_text(json_payload(), "body", 2000)
        if body is None:
            return jsonify(error="Comment text is required (up to 2,000 characters)."), 400
        return save_feedback(story_id, "comment", position, None, None, body)

    @app.get("/api/author/feedback")
    @author_required
    def pending_feedback():
        rows = get_database().execute(
            """SELECT f.id, f.story_id, s.title AS story_title, f.kind,
                      f.chapter_position, c.title AS chapter_title, f.rating,
                      f.title, f.body, f.created_at, u.display_name, u.email
               FROM feedback AS f
               JOIN stories AS s ON s.id = f.story_id
               JOIN users AS u ON u.id = f.user_id
               LEFT JOIN chapters AS c
                 ON c.story_id = f.story_id AND c.position = f.chapter_position
               WHERE f.status = 'pending'
               ORDER BY f.created_at"""
        ).fetchall()
        return jsonify(feedback=[dict(row) for row in rows])

    @app.put("/api/author/feedback/<int:feedback_id>")
    @author_required
    def moderate_feedback(feedback_id: int):
        status = json_payload().get("status")
        if status not in {"approved", "rejected"}:
            return jsonify(error="Choose approved or rejected."), 400
        database = get_database()
        cursor = database.execute(
            "UPDATE feedback SET status = ? WHERE id = ? AND status = 'pending'",
            (status, feedback_id),
        )
        if cursor.rowcount == 0:
            return jsonify(error="Pending feedback not found."), 404
        database.commit()
        return jsonify(ok=True, status=status)

    return app


def get_database() -> sqlite3.Connection:
    if "database" not in g:
        database_path = current_app.config["DATABASE_PATH"]
        if database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        g.database = connection
    return g.database


def initialize_database(app: Flask) -> None:
    database_path = app.config["DATABASE_PATH"]
    if database_path != ":memory:":
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'author',
            display_name TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS stories (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            genre TEXT NOT NULL,
            updated TEXT NOT NULL,
            blurb TEXT NOT NULL DEFAULT '',
            cover TEXT NOT NULL,
            cover_image TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS chapters (
            id INTEGER PRIMARY KEY,
            story_id TEXT NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            UNIQUE (story_id, position)
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER PRIMARY KEY,
            ip_address TEXT NOT NULL,
            attempted_at INTEGER NOT NULL,
            succeeded INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS login_attempts_recent
            ON login_attempts (ip_address, attempted_at, succeeded);
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY,
            story_id TEXT NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK (kind IN ('comment', 'review')),
            chapter_position INTEGER,
            rating INTEGER,
            title TEXT,
            body TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected')),
            created_at INTEGER NOT NULL,
            CHECK (
                (kind = 'comment' AND chapter_position IS NOT NULL AND rating IS NULL)
                OR (kind = 'review' AND chapter_position IS NULL AND rating BETWEEN 1 AND 5)
            )
        );
        CREATE INDEX IF NOT EXISTS feedback_public
            ON feedback (story_id, kind, status, chapter_position, created_at);
        CREATE UNIQUE INDEX IF NOT EXISTS one_review_per_reader
            ON feedback (story_id, user_id) WHERE kind = 'review';
        """
    )
    user_columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
    if "role" not in user_columns:
        connection.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'author'")
    if "display_name" not in user_columns:
        connection.execute("ALTER TABLE users ADD COLUMN display_name TEXT NOT NULL DEFAULT ''")
    story_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(stories)")
    }
    if "sort_order" not in story_columns:
        connection.execute(
            "ALTER TABLE stories ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0"
        )
        connection.execute("UPDATE stories SET sort_order = rowid")
    if "blurb" not in story_columns:
        connection.execute("ALTER TABLE stories ADD COLUMN blurb TEXT NOT NULL DEFAULT ''")
        connection.execute("UPDATE stories SET blurb = description")
    if "cover_image" not in story_columns:
        connection.execute("ALTER TABLE stories ADD COLUMN cover_image TEXT NOT NULL DEFAULT ''")
    seed = json.loads((ROOT / "seed_data.json").read_text(encoding="utf-8"))
    seed_initialized = connection.execute(
        "SELECT 1 FROM settings WHERE key = 'seed_initialized'"
    ).fetchone()
    if seed_initialized is None:
        existing_stories = connection.execute(
            "SELECT COUNT(*) FROM stories"
        ).fetchone()[0]
        if existing_stories == 0:
            for index, story in enumerate(seed["stories"]):
                insert_story(connection, story, sort_order=index)
        connection.execute(
            "INSERT INTO settings (key, value) VALUES ('seed_initialized', 'true')"
        )
    connection.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('bio', ?)",
        (seed["bio"],),
    )
    connection.commit()
    connection.close()


def insert_story(
    database: sqlite3.Connection, story: dict[str, Any], sort_order: int = 0
) -> None:
    database.execute(
        """INSERT INTO stories
           (id, title, description, genre, updated, blurb, cover, cover_image, sort_order)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            story["id"],
            story["title"],
            story["description"],
            story["genre"],
            story["updated"],
            story.get("blurb", story["description"]),
            story["cover"],
            story.get("cover_image", ""),
            sort_order,
        ),
    )
    insert_chapters(database, story)


def insert_chapters(database: sqlite3.Connection, story: dict[str, Any]) -> None:
    database.executemany(
        """INSERT INTO chapters (story_id, position, title, body)
           VALUES (?, ?, ?, ?)""",
        [
            (story["id"], index, chapter["title"], chapter["body"])
            for index, chapter in enumerate(story["chapters"])
        ],
    )


def list_stories() -> list[dict[str, Any]]:
    database = get_database()
    rows = database.execute(
        """SELECT s.id, s.title, s.description, s.genre, s.updated, s.blurb,
                  s.cover, s.cover_image,
                  c.position, c.title AS chapter_title, c.body AS chapter_body
           FROM stories AS s
           LEFT JOIN chapters AS c ON c.story_id = s.id
           ORDER BY s.sort_order, s.rowid, c.position"""
    ).fetchall()
    stories: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        story = by_id.get(row["id"])
        if story is None:
            story = {
                "id": row["id"],
                "title": row["title"],
                "description": row["description"],
                "genre": row["genre"],
                "updated": row["updated"],
                "blurb": row["blurb"],
                "cover": row["cover"],
                "coverImage": row["cover_image"],
                "chapters": [],
            }
            by_id[row["id"]] = story
            stories.append(story)
        if row["position"] is not None:
            story["chapters"].append(
                {"title": row["chapter_title"], "body": row["chapter_body"]}
            )
    return stories


def get_bio() -> str:
    row = get_database().execute(
        "SELECT value FROM settings WHERE key = 'bio'"
    ).fetchone()
    return row["value"] if row else ""


def get_story(story_id: str) -> dict[str, Any] | None:
    return next((story for story in list_stories() if story["id"] == story_id), None)


def serialize_feedback(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "chapterPosition": row["chapter_position"],
        "rating": row["rating"],
        "title": row["title"],
        "body": row["body"],
        "createdAt": row["created_at"],
        "displayName": row["display_name"],
    }


def save_feedback(
    story_id: str,
    kind: str,
    chapter_position: int | None,
    rating: int | None,
    title: str | None,
    body: str,
):
    database = get_database()
    user_id = session["user_id"]
    latest = database.execute(
        """SELECT created_at FROM feedback
           WHERE user_id = ? AND kind = ? ORDER BY created_at DESC LIMIT 1""",
        (user_id, kind),
    ).fetchone()
    now = int(time.time())
    if latest is not None and now - latest["created_at"] < 30:
        return jsonify(error="Please wait a moment before posting again."), 429
    try:
        cursor = database.execute(
            """INSERT INTO feedback
               (story_id, user_id, kind, chapter_position, rating, title, body, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (story_id, user_id, kind, chapter_position, rating, title, body, now),
        )
        database.commit()
    except sqlite3.IntegrityError:
        return jsonify(error="You have already reviewed this story."), 409
    return jsonify(
        ok=True,
        id=cursor.lastrowid,
        status="pending",
        message="Thanks! Your submission is waiting for the author’s approval.",
    ), 201


def get_current_user() -> sqlite3.Row | None:
    user_id = session.get("user_id")
    if not isinstance(user_id, int):
        return None
    return get_database().execute(
        "SELECT id, email, role, display_name FROM users WHERE id = ?", (user_id,)
    ).fetchone()


def author_required(function: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any):
        user = get_current_user()
        if user is None or user["role"] != "author":
            return jsonify(error="Sign in to manage author content."), 401
        return function(*args, **kwargs)

    return wrapped


def signed_in_required(function: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any):
        if get_current_user() is None:
            return jsonify(error="Sign in to continue."), 401
        return function(*args, **kwargs)

    return wrapped


def reader_required(function: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any):
        user = get_current_user()
        if user is None or user["role"] != "reader":
            return jsonify(error="Sign in with a reader account to post feedback."), 401
        return function(*args, **kwargs)

    return wrapped


def valid_email(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    email = value.strip().lower()
    if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return None
    return email


def valid_cover_path(value: Any) -> bool:
    return isinstance(value, str) and bool(
        re.fullmatch(r"/uploads/[a-f0-9]{32}\.(?:jpg|png|webp)", value)
    )


def detect_image_type(header: bytes) -> tuple[str, str] | None:
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return None


def json_payload() -> dict[str, Any]:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def valid_text(payload: dict[str, Any], field: str, maximum: int) -> str | None:
    value = payload.get(field)
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > maximum:
        return None
    return value


def validate_story(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    title = valid_text(payload, "title", 90)
    description = valid_text(payload, "description", 220)
    blurb = payload.get("blurb", description)
    genre = payload.get("genre", "")
    updated = payload.get("updated", "Recently updated")
    chapters = payload.get("chapters")
    if title is None:
        return None, "Story title must be between 1 and 90 characters."
    if description is None:
        return None, "Description must be between 1 and 220 characters."
    if not isinstance(blurb, str) or not blurb.strip() or len(blurb) > 5000:
        return None, "Story blurb must be between 1 and 5,000 characters."
    if not isinstance(genre, str) or len(genre.strip()) > 40:
        return None, "Genre must be 40 characters or fewer."
    if not isinstance(updated, str) or len(updated.strip()) > 60:
        return None, "Collection note must be 60 characters or fewer."
    if not isinstance(chapters, list) or not 1 <= len(chapters) <= 100:
        return None, "A story must have between 1 and 100 chapters."

    validated_chapters = []
    for index, chapter in enumerate(chapters, start=1):
        if not isinstance(chapter, dict):
            return None, f"Chapter {index} is invalid."
        chapter_title = valid_text(chapter, "title", 90)
        chapter_body = valid_text(chapter, "body", 12000)
        if chapter_title is None or chapter_body is None:
            return None, f"Chapter {index} needs a title and text (up to 12,000 characters)."
        validated_chapters.append({"title": chapter_title, "body": chapter_body})

    return {
        "title": title,
        "description": description,
        "blurb": blurb.strip(),
        "genre": genre.strip() or "A story",
        "updated": updated.strip() or "Recently updated",
        "chapters": validated_chapters,
    }, None


def create_admin(email: str, password: str) -> None:
    normalized_email = email.strip().lower()
    if "@" not in normalized_email or len(normalized_email) > 254:
        raise ValueError("Enter a valid email address.")
    if len(password) < 12:
        raise ValueError("The password must be at least 12 characters long.")
    database_path = os.environ.get(
        "DATABASE_PATH", str(ROOT / "instance" / "stories.sqlite3")
    )
    if database_path != ":memory:":
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    try:
        connection = sqlite3.connect(database_path)
        connection.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            (normalized_email, generate_password_hash(password, method="scrypt")),
        )
        connection.commit()
    except sqlite3.IntegrityError as error:
        raise ValueError("An author account with that email already exists.") from error
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Between the Lines website backend")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="Run the website backend")
    author_command = commands.add_parser(
        "create-author", help="Create an author login account"
    )
    author_command.add_argument("email")
    args = parser.parse_args()
    if args.command == "create-author":
        password = getpass.getpass("Password (12+ characters): ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            parser.error("Passwords do not match.")
        try:
            create_admin(args.email, password)
        except ValueError as error:
            parser.error(str(error))
        print(f"Author account created for {args.email.strip().lower()}.")
        return 0

    if os.environ.get("APP_ENV") == "production":
        secret = os.environ.get("SESSION_SECRET", "")
        if len(secret) < 32:
            parser.error("Set SESSION_SECRET to a random value of at least 32 characters.")
    elif not os.environ.get("SESSION_SECRET"):
        print(
            "Warning: using a temporary session key. Set SESSION_SECRET to keep "
            "sessions across restarts.",
            file=sys.stderr,
        )
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8765")),
        debug=False,
    )
    return 0


app = create_app()


if __name__ == "__main__":
    raise SystemExit(main())
