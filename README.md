# Between the Lines

A small author website with a Flask backend, SQLite story storage, and password-protected author tools.

## Run locally on Windows

In PowerShell, from the project folder:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:SESSION_SECRET = (& .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))")
.\.venv\Scripts\python.exe app.py create-author author@example.com
```

Enter and confirm a password of at least 12 characters when prompted. Start the website:

```powershell
.\.venv\Scripts\waitress-serve.exe --host=127.0.0.1 --port=8765 app:app
```

Open `http://127.0.0.1:8765`. The SQLite database is created at `instance/stories.sqlite3`; set `DATABASE_PATH` to store it elsewhere. The sample stories and biography are inserted when the database is first created. Existing browser-only edits are not imported automatically.

The session secret must remain the same across server restarts to keep sessions valid. A random secret is fine for local development. For a deployment, set a stable secret of at least 32 characters, set `APP_ENV=production` so session cookies are secure-only, serve the site over HTTPS, and run Waitress behind an HTTPS-capable reverse proxy. Do not use Flask's development server for a public deployment.

GitHub Pages only hosts static files and cannot run this site's Flask API. The sign-in, registration, story data, and author tools require the Flask application to be deployed on a Python-capable host; publish and serve the site through that application or configure a separately hosted backend.

## Deploy the site and backend on Render

The included `render.yaml` deploys the Flask app, website pages, and API together. It configures HTTPS production cookies, a generated persistent session secret, and a disk for the SQLite database and uploaded covers. The persistent disk requires a paid Render web service.

1. Push the repository to GitHub.
2. In Render, create a **Blueprint** and connect this repository. Review the service and disk before applying it.
3. Open the deployed Render URL. Use that URL for the working site; the GitHub Pages copy remains static and cannot sign users in.
4. Create the author login from the service Shell with `python app.py create-author author@example.com`, replacing the email with yours. Reader accounts can register from the sign-in page.

The database and uploaded covers are stored on the Render disk so they survive redeployments. Keep the disk attached to the service.

Create additional author accounts with `python app.py create-author another@example.com`. Reader accounts can be created from the **Create a reader account** link on the sign-in page. Reader sign-up never creates author access. Password hashes are stored in SQLite, login attempts are rate-limited, and state-changing API requests use CSRF protection.

Each story has its own page with its cover image, blurb, and chapter list. Signed-in readers can leave one review per story and comments on chapters. Feedback is private until an author approves it from **Manage stories**; approved reviews then appear on the story page and comments at the end of their chapter. Authors can edit a story's blurb and upload a JPEG, PNG, or WebP cover image (up to 5 MB). Images are stored in `instance/uploads`; set `UPLOAD_FOLDER` to use another directory.

Authors can also publish, edit, and delete standalone poems from the homepage. Visitors can sign in from the homepage; both reader and author accounts use `login.html`.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
