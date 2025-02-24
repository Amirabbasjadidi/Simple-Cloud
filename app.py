from flask import Flask, render_template, request, jsonify, redirect, url_for, send_from_directory, session
import os
import sqlite3
from werkzeug.utils import secure_filename
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import requests
from werkzeug.security import generate_password_hash, check_password_hash
from urllib.parse import urlparse
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import random
import base64


app = Flask(__name__)

def load_secret_key():
    secret_file = "/etc/simplecloud.env"
    try:
        with open(secret_file) as f:
            for line in f:
                if line.startswith("SECRET_KEY="):
                    return line.strip().split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return "default_secret_if_missing"

SECRET_KEY = load_secret_key()

app.config['SECRET_KEY'] = SECRET_KEY

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

DATABASE = 'database.db'

login_manager = LoginManager(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, id, username, password):
        self.id = id
        self.username = username
        self.password = password

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    hashed_password = generate_password_hash('1234')

    with get_db_connection() as conn:
        conn.execute(''' 
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            )
        ''')
        conn.execute(''' 
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                uploaded_by TEXT NOT NULL
            )
        ''')
        conn.execute('INSERT OR IGNORE INTO users (username, password) VALUES (?, ?)',
                     ('root', hashed_password))
        conn.commit()

init_db()

@app.route('/')
@login_required
def index():
    return redirect(url_for('upload'))


@login_manager.user_loader
def load_user(user_id):
    with get_db_connection() as conn:
        user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        if user:
            return User(user['id'], user['username'], user['password'])
    return None


def generate_captcha():
    num1, num2 = random.randint(1, 9), random.randint(1, 9)
    captcha_text = f"{num1} + {num2}"
    session['captcha_result'] = num1 + num2

    font_path = os.path.join(app.root_path, 'static', 'Ubuntu.ttf')

    img = Image.new('RGB', (200, 100), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype(font_path, 50)
    except Exception as e:
        print(f"Error loading font: {e}")
        font = ImageFont.load_default()

    x, y = 50, 25
    angle = random.randint(-15, 15)

    text_img = Image.new('RGBA', (200, 100), (255, 255, 255, 0))
    text_draw = ImageDraw.Draw(text_img)
    text_draw.text((x, y), captcha_text, font=font, fill=(0, 0, 0, 255))
    text_img = text_img.rotate(angle, resample=Image.BICUBIC, center=(100, 50))

    img.paste(text_img, (0, 0), text_img)

    for _ in range(8):
        x1, y1, x2, y2 = [random.randint(0, 200) for _ in range(4)]
        draw.line((x1, y1, x2, y2), fill=(random.randint(100, 150), random.randint(100, 150), random.randint(100, 150)), width=2)

    for _ in range(200):
        x, y = random.randint(0, 200), random.randint(0, 100)
        draw.point((x, y), fill=(random.randint(150, 200), random.randint(150, 200), random.randint(150, 200)))

    pixels = img.load()
    for i in range(img.size[0]):
        for j in range(img.size[1]):
            if i + 2 < img.size[0] and j + 2 < img.size[1]:
                dx = random.randint(-1, 1)
                dy = random.randint(-1, 1)
                pixels[i, j] = pixels[i + dx, j + dy]

    buffer = BytesIO()
    img.save(buffer, format="PNG")
    img_data = base64.b64encode(buffer.getvalue()).decode('utf-8')
    return img_data

@app.route('/refresh_captcha')
def refresh_captcha():
    captcha = generate_captcha()
    return jsonify({'captcha_image': captcha})


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('upload'))

    if request.method == 'POST':
        data = request.json
        username = data.get('username')
        password = data.get('password')
        remember_me = data.get('remember_me', False)
        captcha_input = int(data.get('captcha'))

        if captcha_input != session.get('captcha_result'):
            return jsonify(success=False, error="Invalid Captcha.")

        with get_db_connection() as conn:
            user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()

            if user and check_password_hash(user['password'], password):
                user_obj = User(user['id'], user['username'], user['password'])
                login_user(user_obj, remember=remember_me)
                return jsonify(success=True, redirect_url=url_for('upload'))

            return jsonify(success=False, error="Invalid username or password.")

    captcha_image = generate_captcha()
    return render_template('login.html', captcha_image=captcha_image)


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

def get_unique_filename(directory, filename):
    base, ext = os.path.splitext(filename)
    counter = 1
    new_filename = filename
    while os.path.exists(os.path.join(directory, new_filename)):
        new_filename = f"{base} ({counter}){ext}"
        counter += 1
    return new_filename


@app.route('/upload', methods=['POST'])
@login_required
def upload():
    if request.method == 'POST':
        if 'file' not in request.files:
            return jsonify(success=False, error="No file provided")
        file = request.files['file']
        if file.filename == '':
            return jsonify(success=False, error="No file selected")

        filename = secure_filename(file.filename)
        filename = get_unique_filename(app.config['UPLOAD_FOLDER'], filename)

        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)

        with get_db_connection() as conn:
            conn.execute('INSERT INTO files (name, path, uploaded_by) VALUES (?, ?, ?)',
                         (filename, file_path, current_user.username))
            conn.commit()

        return jsonify(success=True, filename=filename)

@app.route('/download', methods=['POST'])
@login_required
def download_file_from_url():
    data = request.get_json()
    url = data.get('url')

    if not url:
        return jsonify(success=False, error="No URL provided"), 400

    try:
        response = requests.get(url, stream=True)
        if response.status_code == 200:
            filename = secure_filename(urlparse(url).path.split("/")[-1])
            filename = get_unique_filename(app.config['UPLOAD_FOLDER'], filename)

            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)

            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            with get_db_connection() as conn:
                conn.execute('INSERT INTO files (name, path, uploaded_by) VALUES (?, ?, ?)',
                             (filename, file_path, current_user.username))
                conn.commit()

            return jsonify(success=True, filename=filename)
        else:
            return jsonify(success=False, error=f"Failed to download file: HTTP {response.status_code}"), 500
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500


@app.route('/api/files', methods=['GET'])
@login_required
def api_files():
    with get_db_connection() as conn:
        files = conn.execute(
            'SELECT * FROM files WHERE uploaded_by = ?',
            (current_user.username,)
        ).fetchall()

    files_data = [{'id': file['id'], 'name': file['name'], 'path': f'/uploads/{file["name"]}'} for file in files]

    return jsonify(files=files_data)

@app.route('/api/files/<int:file_id>', methods=['DELETE'])
@login_required
def delete_file(file_id):
    with get_db_connection() as conn:
        file = conn.execute(
            'SELECT * FROM files WHERE id = ? AND uploaded_by = ?',
            (file_id, current_user.username)
        ).fetchone()

        if not file:
            return jsonify(success=False, error="File not found or unauthorized."), 403

        file_path = file['path']

        try:
            if os.path.exists(file_path):
                os.remove(file_path)

            conn.execute('DELETE FROM files WHERE id = ?', (file_id,))
            conn.commit()

            return jsonify(success=True)
        except Exception as e:
            return jsonify(success=False, error=str(e)), 500

@app.route('/files', methods=['GET'])
@login_required
def files_page():
    return render_template('filemanager.html')

@app.route('/download', methods=['GET'])
@login_required
def download_page():
    return render_template('Download.html')

@app.route('/upload', methods=['GET'])
@login_required
def upload_page():
    return render_template('Upload.html')

@app.route('/uploads/<filename>')
@login_required
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/media/<filename>')
@login_required
def media(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

def add_user_helper(username, password):
    hashed_password = generate_password_hash(password)
    with get_db_connection() as conn:
        try:
            conn.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, hashed_password))
            conn.commit()
            print(f"User {username} added successfully.")
        except sqlite3.IntegrityError:
            print(f"User {username} already exists.")


@app.cli.command('adduser')
def add_user():
    username = input("Enter username: ")
    password = input("Enter password: ")
    add_user_helper(username, password)

@app.cli.command('changepassword')
def change_password():
    username = input("Enter username: ")
    new_password = input("Enter new password: ")
    hashed_password = generate_password_hash(new_password)
    with get_db_connection() as conn:
        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        if user:
            conn.execute('UPDATE users SET password = ? WHERE username = ?', (hashed_password, username))
            conn.commit()
            print(f"Password for user {username} updated successfully.")
        else:
            print(f"User {username} not found.")


@app.cli.command('changeusername')
def change_username():
    old_username = input("Enter current username: ")
    new_username = input("Enter new username: ")

    with get_db_connection() as conn:
        user = conn.execute('SELECT * FROM users WHERE username = ?', (old_username,)).fetchone()
        if not user:
            print(f"User {old_username} not found.")
            return

        existing_user = conn.execute('SELECT * FROM users WHERE username = ?', (new_username,)).fetchone()
        if existing_user:
            print(f"Error: The username {new_username} already exists. Please choose another one.")
            return

        try:
            conn.execute('UPDATE users SET username = ? WHERE username = ?', (new_username, old_username))
            conn.commit()

            if conn.total_changes > 0:
                print(f"Username changed from {old_username} to {new_username} successfully.")
                print("Updating uploaded files...")

                conn.execute('UPDATE files SET uploaded_by = ? WHERE uploaded_by = ?', (new_username, old_username))
                conn.commit()

                if conn.total_changes > 0:
                    print("Uploaded files updated successfully.")
                else:
                    print("No uploaded files found for this user.")

            else:
                print("Failed to change username. No changes detected.")

        except sqlite3.Error as e:
            print(f"Database error: {e}")


@app.cli.command('deleteuser')
def delete_user():
    username = input("Enter username to delete: ")
    with get_db_connection() as conn:
        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        if not user:
            print(f"User {username} not found.")
            return

        conn.execute('DELETE FROM users WHERE username = ?', (username,))
        conn.commit()
        print(f"User {username} deleted successfully.")

        while True:
            new_owner = input(
                "Enter an existing username to transfer files to, or type 'DELETE_FILES' to remove all files uploaded by this user: ")

            if new_owner == "DELETE_FILES":
                files = conn.execute('SELECT path FROM files WHERE uploaded_by = ?', (username,)).fetchall()

                for file in files:
                    file_path = file[0]
                    if os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                        except Exception as e:
                            print(f"Error deleting file {file_path}: {e}")

                conn.execute('DELETE FROM files WHERE uploaded_by = ?', (username,))
                conn.commit()
                print(f"All files uploaded by {username} have been deleted from the server and database.")
                return

            existing_user = conn.execute('SELECT * FROM users WHERE username = ?', (new_owner,)).fetchone()
            if existing_user:
                conn.execute('UPDATE files SET uploaded_by = ? WHERE uploaded_by = ?', (new_owner, username))
                conn.commit()
                print(f"All files uploaded by {username} have been transferred to {new_owner}.")
                return
            else:
                create_new = input(
                    f"User {new_owner} does not exist. Do you want to create it? (y/n): ").strip().lower()
                if create_new == 'y':
                    new_password = input(f"Enter password for {new_owner}: ")
                    add_user_helper(new_owner, new_password)
                    conn.execute('UPDATE files SET uploaded_by = ? WHERE uploaded_by = ?', (new_owner, username))
                    conn.commit()
                    print(f"User {new_owner} created and all files from {username} transferred.")
                    return
                elif create_new == 'n':
                    print("Please enter a valid username to transfer files to.")
                else:
                    print("Invalid input. Please enter 'y' or 'n'.")

@app.cli.command('listusers')
def list_users():
    with get_db_connection() as conn:
        users = conn.execute('SELECT id, username FROM users').fetchall()
        conn.commit()

        if users:
            print("Registered users:")
            for user in users:
                print(f"ID: {user['id']}, Username: {user['username']}")
        else:
            print("No users found.")

    with get_db_connection() as conn:
        conn.execute('VACUUM;')


@app.cli.command('help')
def helpme():
    commands = {
        "adduser": "Adds a new user to the database.",
        "changepassword": "Changes the password for an existing user.",
        "changeusername": "Updates the username of an existing user and transfers associated files.",
        "deleteuser": "Deletes a user and allows file transfer or removal.",
        "listusers": "Lists all registered users.",
    }

    print("Available CLI commands:")
    for cmd, desc in commands.items():
        print(f"  flask {cmd} - {desc}")


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=80, debug=False)
