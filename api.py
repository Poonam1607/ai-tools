import importlib

from quart import Quart, g, request, jsonify, abort, Response
from markupsafe import escape
import json
import aiohttp
import os
from functools import wraps
from dotenv import load_dotenv
from quart_compress import Compress
import time
from watchgod import awatch 
import asyncio

load_dotenv()

app = Quart(__name__)
Compress(app)

src_dir = "src"
debug_mode = app.debug

# flag to avoid multiple watch_task running at the same time
watch_task_running = False


async def restart_quart():
    """
    Restart the Quart application.
    """
    print("Reloading Quart...")
    await app.shutdown()
    await app.startup()
    print("Quart reloaded.")


async def create_client_session():
    """
    Create an aiohttp ClientSession.
    """
    async with aiohttp.ClientSession() as session:
        return session


async def watch_src_folder():
    """
    Monitor changes in the 'src' folder and restart Quart on change.
    """
    global watch_task_running
    if watch_task_running:
        return
    watch_task_running = True
    async for changes in awatch(src_dir):
        await restart_quart()
    watch_task_running = False


extra_dirs = ['src']
extra_files = extra_dirs[:]
for extra_dir in extra_dirs:
    for dirname, dirs, files in os.walk(extra_dir):
        for filename in files:
            filename = os.path.join(dirname, filename)
            if os.path.isfile(filename):
                extra_files.append(filename)


app.config.update(extra_files=extra_files)

with open('repository_data.json') as f:
    repository_data = json.load(f)

AUTH_HEADER = os.getenv("AUTH_HEADER")
AUTH_HEADER_KEY = os.getenv("AUTH_HEADER_KEY")


def verify_auth_header(auth_header_key, expected_value):
    def decorator(f):
        @wraps(f)
        async def decorated_function(*args, **kwargs):
            auth_header = request.headers.get(auth_header_key)
            if not auth_header or auth_header != expected_value:
                print("Unauthorized access");
                abort(401)  # Unauthorized
            return await f(*args, **kwargs)

        return decorated_function

    return decorator


@app.route("/")
def welcome():
    return "<p>Welcome!</p>"


@app.route("/repository")
def repository():
    """ Returns the repository data, which contains the available models and their configurations"""
    return jsonify(repository_data)


def json_to_object(request_class, json_str):
    """Converts a JSON string to an object of the given class at level 1."""
    data = json.loads(json_str)
    return request_class(**data)


def get_model_config(use_case, provider, mode):
    """ Returns the model config for the given use case, provider and mode """
    use_case_data = repository_data.get('use_cases').get(use_case)
    if use_case_data is None:
        return f'{escape(use_case)} Use case is not available', 400

    provider_data = use_case_data.get(provider)
    if provider_data is None:
        return f'{escape(provider)} Provider is not available', 400

    mode = provider_data.get(mode)
    if mode is None:
        return f'{escape(mode)} Mode is not available', 400

    return mode, 200


@app.route("/<use_case>/<provider>/<mode>", methods=['POST'])
@verify_auth_header(AUTH_HEADER_KEY, AUTH_HEADER)
async def transformer(use_case, provider, mode):
    """ Returns the translation for the given tex; provider and mode are as mentioned in the repository"""
    start_time = time.time()

    model_config = get_model_config(use_case, provider, mode)
    if model_config[1] != 200:
        return model_config

    model_class_name = model_config[0].get('model_class')
    model_request_class_name = model_config[0].get('request_class')
    module = importlib.import_module("src" + "." + use_case + "." + provider + "." + mode)
    model = getattr(module, model_class_name)(app)
    model_request = getattr(module, model_request_class_name)
    request_class = json_to_object(model_request, json.dumps(await request.json))
    if model_config[0].get("__is_async"):
        response = await model.inference(request_class)
    else:
        response = model.inference(request_class)

    end_time = time.time()
    response_time_ms = int((end_time - start_time) * 1000)
    headers = {"ai-tools-response-time": str(response_time_ms)}
    return Response(response, headers=headers)


@app.before_serving
async def startup():
    """
    Startup function called before serving requests.
    """
    app.client = await create_client_session()
    # monitor src if in debug mode
    if debug_mode:
        asyncio.get_event_loop().create_task(watch_src_folder())
        
# quart --app api --debug run
# hypercorn api -b 0.0.0.0:8000