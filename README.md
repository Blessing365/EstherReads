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

The session secret must remain the same across server restarts to keep sessions valid. A random secret is fine for local development. For a public deployment, set a stable secret of at least 32 characters, set `APP_ENV=production` so session cookies are secure-only, and serve the site over HTTPS. Do not use Flask's development server for a public deployment.

GitHub Pages only hosts static files and cannot run this site's Flask API. The sign-in, registration, story data, and author tools require the Flask application to be deployed on a Python-capable host; publish and serve the site through that application or configure a separately hosted backend.

## Deploy the site and backend on PythonAnywhere

PythonAnywhere can host the Flask app, website pages, and API together. The app's SQLite database and uploaded cover images are stored in your account's home directory.

1. In PythonAnywhere, open **Web** and add a new **Flask** web app. Choose a Python version supported by the account.
2. Open a **Bash** console and put a clone of this GitHub repository in your home directory, for example `git clone https://github.com/Blessing365/EstherReads.git`. If GitHub access is unavailable from the free account, upload the project files instead.
3. Create and activate a virtual environment using the Python version selected for the web app, then install dependencies with `pip install -r ~/EstherReads/requirements.txt`.
4. Open the WSGI configuration file linked from the **Web** tab. Replace its contents with the following, changing `YOUR_USERNAME` and `PASTE_A_RANDOM_SECRET_HERE`:

   ```python
   import os
   import sys

   project_home = "/home/YOUR_USERNAME/EstherReads"
   if project_home not in sys.path:
       sys.path.insert(0, project_home)

   os.environ["APP_ENV"] = "production"
   os.environ["SESSION_SECRET"] = "PASTE_A_RANDOM_SECRET_HERE"
   os.environ["DATABASE_PATH"] = os.path.join(project_home, "instance", "stories.sqlite3")
   os.environ["UPLOAD_FOLDER"] = os.path.join(project_home, "instance", "uploads")

   from app import app as application
   ```

   Generate the secret in the Bash console with `python -c "import secrets; print(secrets.token_hex(32))"`. Paste its output into the WSGI file only; **never commit the secret to GitHub**.
5. In the **Web** tab, set the virtualenv path, then click **Reload**. Visit the PythonAnywhere web app URL to check the site. Use this URL, not GitHub Pages, for sign-in.
6. In a Bash console, activate the same virtual environment and create the author login: `cd ~/EstherReads && python app.py create-author your-email@example.com`. Reader accounts can register from the sign-in page.

Create additional author accounts with `python app.py create-author another@example.com`. Reader accounts can be created from the **Create a reader account** link on the sign-in page. Reader sign-up never creates author access. Password hashes are stored in SQLite, login attempts are rate-limited, and state-changing API requests use CSRF protection.

Each story has its own page with its cover image, blurb, and chapter list. Signed-in readers can leave one review per story and comments on chapters. Feedback is private until an author approves it from **Manage stories**; approved reviews then appear on the story page and comments at the end of their chapter. Authors can edit a story's blurb and upload a JPEG, PNG, or WebP cover image (up to 5 MB). Images are stored in `instance/uploads`; set `UPLOAD_FOLDER` to use another directory.

Authors can also publish, edit, and delete standalone poems from the homepage. Visitors can sign in from the homepage; both reader and author accounts use `login.html`.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
