import base64
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import create_app


class WebsiteBackendTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temporary_directory.name) / "test.sqlite3"
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-session-secret-that-is-at-least-32-characters",
                "DATABASE_PATH": str(database_path),
                "UPLOAD_FOLDER": str(Path(self.temporary_directory.name) / "uploads"),
                "WTF_CSRF_ENABLED": True,
                "SESSION_COOKIE_SECURE": False,
            }
        )
        self.client = self.app.test_client()
        with self.app.app_context():
            from app import get_database

            get_database().execute(
                "INSERT INTO users (email, password_hash) VALUES (?, ?)",
                ("author@example.com", generate_password_hash("correct horse battery staple")),
            )
            get_database().commit()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def csrf_token(self):
        response = self.client.get("/api/auth/session")
        self.assertEqual(response.status_code, 200)
        return response.get_json()["csrfToken"]

    def json_request(self, method, path, data=None, csrf_token=None):
        headers = {"Content-Type": "application/json"}
        if csrf_token is not None:
            headers["X-CSRFToken"] = csrf_token
        return self.client.open(
            path,
            method=method,
            data=json.dumps(data) if data is not None else None,
            headers=headers,
        )

    def sign_in(self):
        token = self.csrf_token()
        response = self.json_request(
            "POST",
            "/api/auth/login",
            {
                "email": "author@example.com",
                "password": "correct horse battery staple",
                "remember": False,
            },
            token,
        )
        self.assertEqual(response.status_code, 200)
        cookie = self.client.get_cookie("session")
        self.assertIsNotNone(cookie)
        self.assertTrue(cookie.http_only)
        self.assertEqual(cookie.same_site, "Lax")
        return response.get_json()["csrfToken"]

    def test_public_content_is_persisted_in_backend(self):
        response = self.client.get("/api/public")
        self.assertEqual(response.status_code, 200)
        content = response.get_json()
        self.assertEqual(len(content["stories"]), 3)
        self.assertEqual(content["poems"], [])
        self.assertEqual(content["stories"][0]["chapters"][0]["title"], "The road home")
        self.assertIn("I'm Clara", content["bio"])

    def test_author_can_create_edit_and_delete_poems(self):
        token = self.csrf_token()
        unauthenticated = self.json_request(
            "POST",
            "/api/poems",
            {"title": "A small poem", "body": "A line"},
            token,
        )
        self.assertEqual(unauthenticated.status_code, 401)

        token = self.sign_in()
        created = self.json_request(
            "POST",
            "/api/poems",
            {"title": "  A small poem  ", "body": " First line\nSecond line "},
            token,
        )
        self.assertEqual(created.status_code, 201)
        poem = created.get_json()["poem"]
        self.assertEqual(poem["title"], "A small poem")
        self.assertEqual(poem["body"], "First line\nSecond line")
        self.assertEqual(self.client.get("/api/public").get_json()["poems"], [poem])

        updated = self.json_request(
            "PUT",
            f"/api/poems/{poem['id']}",
            {"title": "Revised poem", "body": "Revised lines"},
            token,
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["poem"]["title"], "Revised poem")

        invalid = self.json_request(
            "PUT",
            f"/api/poems/{poem['id']}",
            {"title": "", "body": "Text"},
            token,
        )
        self.assertEqual(invalid.status_code, 400)

        deleted = self.json_request(
            "DELETE", f"/api/poems/{poem['id']}", csrf_token=token
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get("/api/public").get_json()["poems"], [])
        missing = self.json_request(
            "DELETE", f"/api/poems/{poem['id']}", csrf_token=token
        )
        self.assertEqual(missing.status_code, 404)

    def test_author_routes_require_login_and_csrf(self):
        self.assertEqual(self.client.get("/api/stories").status_code, 401)
        token = self.csrf_token()
        missing_csrf = self.json_request(
            "POST",
            "/api/auth/login",
            {"email": "author@example.com", "password": "wrong password"},
        )
        self.assertEqual(missing_csrf.status_code, 400)

        valid_csrf_invalid_password = self.json_request(
            "POST",
            "/api/auth/login",
            {"email": "author@example.com", "password": "wrong password"},
            token,
        )
        self.assertEqual(valid_csrf_invalid_password.status_code, 401)

    def test_author_can_create_edit_delete_and_sign_out(self):
        token = self.sign_in()
        created = self.json_request(
            "POST",
            "/api/stories",
            {
                "title": "A new story",
                "description": "A test story description.",
                "blurb": "A longer introduction for the story page.",
                "genre": "Mystery",
                "updated": "Just written",
                "chapters": [{"title": "The clue", "body": "The story begins."}],
            },
            token,
        )
        self.assertEqual(created.status_code, 201)
        story = created.get_json()["story"]
        story_id = story["id"]
        self.assertEqual(
            self.client.get("/api/stories").get_json()["stories"][0]["id"], story_id
        )
        updated = self.json_request(
            "PUT",
            f"/api/stories/{story_id}",
            {
                "title": "An edited story",
                "description": "An updated description.",
                "blurb": "The updated story-page introduction.",
                "genre": "Drama",
                "updated": "Revised",
                "coverImage": "",
                "chapters": [
                    {"title": "A new first chapter", "body": "Rewritten."},
                    {"title": "A second chapter", "body": "Continued."},
                ],
            },
            token,
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["story"]["coverImage"], "")
        public_story = next(
            item
            for item in self.client.get("/api/public").get_json()["stories"]
            if item["id"] == story_id
        )
        self.assertEqual(public_story["title"], "An edited story")
        self.assertEqual(public_story["blurb"], "The updated story-page introduction.")
        self.assertEqual(len(public_story["chapters"]), 2)

        bio = self.json_request("PUT", "/api/bio", {"bio": "A new biography."}, token)
        self.assertEqual(bio.status_code, 200)
        self.assertEqual(self.client.get("/api/public").get_json()["bio"], "A new biography.")

        deleted = self.json_request(
            "DELETE", f"/api/stories/{story_id}", csrf_token=token
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertNotIn(
            story_id,
            [item["id"] for item in self.client.get("/api/public").get_json()["stories"]],
        )

        logout = self.json_request("POST", "/api/auth/logout", {}, token)
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(self.client.get("/api/stories").status_code, 401)

    def test_reader_registration_and_role_aware_session(self):
        token = self.csrf_token()
        response = self.json_request(
            "POST",
            "/api/auth/register",
            {
                "displayName": "Story Reader",
                "email": "reader@example.com",
                "password": "a sufficiently long password",
            },
            token,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["role"], "reader")
        self.assertEqual(response.get_json()["displayName"], "Story Reader")
        self.assertEqual(self.client.get("/api/auth/session").get_json()["role"], "reader")
        self.assertEqual(self.client.get("/api/stories").status_code, 401)

        duplicate = self.json_request(
            "POST",
            "/api/auth/register",
            {
                "displayName": "Another Name",
                "email": "reader@example.com",
                "password": "a sufficiently long password",
            },
            response.get_json()["csrfToken"],
        )
        self.assertEqual(duplicate.status_code, 409)

    def test_reader_feedback_requires_account_and_author_approval(self):
        author_token = self.sign_in()
        story_response = self.json_request(
            "POST",
            "/api/stories",
            {
                "title": "Feedback story",
                "description": "A story for testing reader feedback.",
                "blurb": "A longer story introduction.",
                "chapters": [{"title": "First chapter", "body": "A beginning."}],
            },
            author_token,
        )
        self.assertEqual(story_response.status_code, 201)
        story_id = story_response.get_json()["story"]["id"]

        reader = self.app.test_client()
        reader_token = reader.get("/api/auth/session").get_json()["csrfToken"]
        unauthorized = reader.post(
            f"/api/stories/{story_id}/reviews",
            data=json.dumps({"rating": 5, "title": "Lovely", "body": "I enjoyed this."}),
            content_type="application/json",
            headers={"X-CSRFToken": reader_token},
        )
        self.assertEqual(unauthorized.status_code, 401)

        registered = reader.post(
            "/api/auth/register",
            data=json.dumps({
                "displayName": "Reader One",
                "email": "feedback@example.com",
                "password": "a sufficiently long password",
            }),
            content_type="application/json",
            headers={"X-CSRFToken": reader_token},
        )
        self.assertEqual(registered.status_code, 201)
        reader_token = registered.get_json()["csrfToken"]
        review = reader.post(
            f"/api/stories/{story_id}/reviews",
            data=json.dumps({"rating": 5, "title": "Lovely", "body": "I enjoyed this."}),
            content_type="application/json",
            headers={"X-CSRFToken": reader_token},
        )
        comment = reader.post(
            f"/api/stories/{story_id}/chapters/0/comments",
            data=json.dumps({"body": "I liked the opening scene."}),
            content_type="application/json",
            headers={"X-CSRFToken": reader_token},
        )
        repeated_comment = reader.post(
            f"/api/stories/{story_id}/chapters/0/comments",
            data=json.dumps({"body": "One more immediate comment."}),
            content_type="application/json",
            headers={"X-CSRFToken": reader_token},
        )
        self.assertEqual(review.status_code, 201, review.get_data(as_text=True))
        self.assertEqual(comment.status_code, 201)
        self.assertEqual(repeated_comment.status_code, 429)
        self.assertEqual(
            reader.get(f"/api/stories/{story_id}/feedback").get_json(),
            {"reviews": [], "comments": []},
        )

        pending = self.client.get("/api/author/feedback").get_json()["feedback"]
        self.assertEqual(len(pending), 2)
        self.assertEqual({item["display_name"] for item in pending}, {"Reader One"})
        for item in pending:
            moderated = self.json_request(
                "PUT",
                f"/api/author/feedback/{item['id']}",
                {"status": "approved"},
                author_token,
            )
            self.assertEqual(moderated.status_code, 200)

        published = reader.get(f"/api/stories/{story_id}/feedback").get_json()
        self.assertEqual(len(published["reviews"]), 1)
        self.assertEqual(published["reviews"][0]["displayName"], "Reader One")
        self.assertEqual(published["reviews"][0]["rating"], 5)
        self.assertEqual(len(published["comments"]), 1)
        self.assertEqual(published["comments"][0]["chapterPosition"], 0)
        self.assertEqual(self.client.get("/api/author/feedback").get_json()["feedback"], [])

    def test_author_can_upload_and_retrieve_valid_cover_image(self):
        token = self.sign_in()
        png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/"
            "lWQAAAAASUVORK5CYII="
        )
        response = self.client.post(
            "/api/uploads/story-cover",
            data={"image": (io.BytesIO(png_bytes), "cover.png")},
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(response.status_code, 201)
        image_url = response.get_json()["url"]
        fetched = self.client.get(image_url)
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.data, png_bytes)
        fetched.close()

        invalid = self.client.post(
            "/api/uploads/story-cover",
            data={"image": (io.BytesIO(b"not an image"), "cover.png")},
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(invalid.status_code, 400)

    def test_story_validation_rejects_missing_chapters(self):
        token = self.sign_in()
        response = self.json_request(
            "POST",
            "/api/stories",
            {
                "title": "Invalid story",
                "description": "Missing chapters.",
                "chapters": [],
            },
            token,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("between 1 and 100", response.get_json()["error"])

    def test_deleted_stories_are_not_reseeded_after_restart(self):
        token = self.sign_in()
        story_ids = [
            story["id"] for story in self.client.get("/api/public").get_json()["stories"]
        ]
        for story_id in story_ids:
            response = self.json_request(
                "DELETE", f"/api/stories/{story_id}", csrf_token=token
            )
            self.assertEqual(response.status_code, 200)

        create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-session-secret-that-is-at-least-32-characters",
                "DATABASE_PATH": self.app.config["DATABASE_PATH"],
            }
        )
        response = self.client.get("/api/public")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["stories"], [])

    def test_login_attempts_are_rate_limited(self):
        token = self.csrf_token()
        for _ in range(10):
            response = self.json_request(
                "POST",
                "/api/auth/login",
                {"email": "author@example.com", "password": "wrong password"},
                token,
            )
            self.assertEqual(response.status_code, 401)
        limited = self.json_request(
            "POST",
            "/api/auth/login",
            {"email": "author@example.com", "password": "wrong password"},
            token,
        )
        self.assertEqual(limited.status_code, 429)

    def test_production_requires_a_persistent_session_secret(self):
        with patch.dict(
            os.environ,
            {"APP_ENV": "production", "SESSION_SECRET": "too-short"},
        ):
            with self.assertRaisesRegex(RuntimeError, "SESSION_SECRET"):
                create_app({"DATABASE_PATH": self.app.config["DATABASE_PATH"]})

    def test_unprotected_author_pages_redirect_only_via_backend_api(self):
        page_response = self.client.get("/manage.html")
        self.assertEqual(page_response.status_code, 200)
        page_response.close()
        self.assertEqual(self.client.get("/api/stories").status_code, 401)
        self.assertEqual(self.client.get("/api/public").status_code, 200)


if __name__ == "__main__":
    unittest.main()
