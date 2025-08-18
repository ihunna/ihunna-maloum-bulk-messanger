import os, json, sys,uuid,sqlite3,base64,io,ast,httpx,time,re,shutil,random,requests,socket,math
from urllib.parse import urlencode, urljoin
from os import listdir
from os.path import isfile
from datetime import datetime, timedelta, timezone

from threading import Thread,Event
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from functools import wraps

from flask import Flask, flash, redirect, render_template,send_file, abort, url_for,request,session,jsonify,g,send_from_directory,make_response
from werkzeug.security import check_password_hash, generate_password_hash
from flask_session import Session
from flask_socketio import SocketIO
from flask_cors import CORS
import webview, asyncio


# adding the folder to path
root = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(root, '..'))

# defining the neccessary folders
parent_folder = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(parent_folder, '..'))

app_folder = os.path.join(parent_folder,'bot')
env_path = os.path.join(parent_folder, '.env')

creators_file = os.path.join(app_folder,'creators.json')
configs_folder = os.path.join(app_folder,'settings')
logs_file = os.path.join(parent_folder,'app.log')

universal_folder = os.path.join(parent_folder,'universals')
universal_files = {
    'proxies':os.path.join(universal_folder,'proxies.txt'),
    'captions':os.path.join(universal_folder,'captions.txt')
}

# Ensure folders and files exist
os.makedirs(configs_folder, exist_ok=True)
os.makedirs(universal_folder, exist_ok=True)

for file_path in universal_files.values():
    if not os.path.exists(file_path):
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write('')

if not os.path.exists(logs_file):
    with open(logs_file, 'w', encoding='utf-8') as f:
        f.write('')


# loading environment variables
load_dotenv(env_path)
session_key = os.getenv('SECRET_KEY')
server_key = os.getenv('SERVER_KEY')
host = os.getenv('HOST')

# Configure application
app = Flask(__name__)
app.debug = True
CORS(app,origins=host)
socketio = SocketIO(app)

# Ensure templates are auto-reloaded
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config['SERVER_KEY'] = server_key
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024


app.config["SESSION_TYPE"] = "filesystem"
app.config["SECRET_KEY"] = session_key.encode()
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
Session(app)


@app.template_filter("datetime")
def format_datetime(value):
    if not value:return ""
    try:
        # Parse ISO 8601 with 'Z' UTC
        dt_utc = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        # Convert to local timezone
        dt_local = dt_utc.astimezone()
        # Fixed format
        return dt_local.strftime("%d %b %Y, %I:%M %p")
    except Exception:
        return value  # Return original if parsing fails

@app.template_filter('date')
def date_filter(value):
    return value.strftime('%Y-%m-%d')

@app.template_filter('date_time')
def date_time_filter(value):
    return value.strftime('%Y-%m-%d %H:%M:%S')


@app.template_filter('len_or_val')
def len_or_val(value):
    if isinstance(value,list):return len(value)
    else:return value

@app.template_filter('time_ago')
def last_seen(value):
    timestamp = value.strftime('%Y-%m-%d %H:%M:%S')
    last_seen_time = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
    current_time = datetime.now()
    
    time_difference = current_time - last_seen_time

    hours, remainder = divmod(time_difference.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    if time_difference.days > 0:
        return f"{time_difference.days} days ago"
    elif hours > 0:
        return f"{hours} hours ago"
    elif minutes > 0:
        return f"{minutes} minutes ago"
    else:
        return f"{seconds} seconds ago"
    

@app.template_filter('urlencode')
def urlencode_filter(value):
    if isinstance(value, str):
        return urlencode({'param': value}).split('=')[-1]  # URL encode and extract the value
    return value

def login_required(func):
    @wraps(func)
    def decorated_function(*args, **kwargs):
        user = session.get('USER')
        if 'USER' not in session or user['status'] == 'blocked':
            if request.method == 'GET':
                return redirect(url_for('login'))
            elif request.method in ['POST', 'DELETE', 'PUT', 'PATCH']:
                return jsonify({'msg': 'you have to be logged in and active'}), 403
        return func(*args, **kwargs)
    return decorated_function

def check_role(func):
    @wraps(func)
    def decorated_function(*args, **kwargs):
        user = session.get('USER')
        if user['role'] != 'super-admin':
            if request.method == 'GET':
                return redirect(url_for('admins'))
            elif request.method in ['POST', 'DELETE', 'PUT', 'PATCH']:
                return jsonify({'msg': 'You must be a super admin to update a user'}), 403
        return func(*args, **kwargs)
    return decorated_function
    

def validate_email(email):
    pattern = r'^[\w\.-]+@[\w\.-]+\.\w+$'
    return re.match(pattern, email)

def validate_password(password):
    pattern = r'^(?=.*\d)(?=.*[a-zA-Z]).+$'
    return re.match(pattern, password)

def logout():
    try:
        for key in list(session.keys()):
            session.pop(key, None)
        session.modified = True
        return session.modified,'Logout successful'
    except Exception as error:
        return False,error
    

    