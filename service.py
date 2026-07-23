from flask import Flask, request, abort, jsonify, send_file
from flask_cors import CORS, cross_origin
from werkzeug.serving import WSGIRequestHandler

import certifi
import datetime
import glob
import hashlib
import hmac
import json
import logging
from logging import handlers
import os
import ssl
import struct
import sys
import time
import paho.mqtt.publish as publish
from paho import mqtt

from mqtt import AcsMqtt
from syncwatcher import SyncWatcher

# Contains log files written by acsmqttlogger
LOG_DIR='/opt/service/logs'
# Mounted at /srv/acsgw/firmware
FIRMWARE_DIR='/opt/service/persistent/firmware'
ACS_SYNC_STATUS_FILE="/opt/service/monitoring/acs-sync-status"

DEVICE_ACTIONS = ['lock', 'unlock', 'reboot', 'setdesc', 'setacstoken', 'dummy']
GLOBAL_ACTIONS = ['open', 'close', 'dummy']
CAMCTL_ACTIONS = ['on', 'off', 'reboot']

MQTT_KEY = bytes.fromhex(os.environ['MQTT_KEY'])
MQTT_USER = os.environ['MQTT_USER']
MQTT_PASSWORD = os.environ['MQTT_PASSWORD']

global_allow_open = None
global_camera_action = {}
global_camctl_action = {}
global_acs_camaction = None
global_camctl_status = None
global_last_cameras_on = None
global_space_open = False
global_space_open_lastchange = 0 # UNIX timestamp


app = Flask(__name__)
cors = CORS(app)
app.config['CORS_HEADERS'] = 'Content-Type'
app.status = {}

logger = logging.getLogger('werkzeug')
handler = logging.handlers.RotatingFileHandler('acsgw.log', maxBytes=500*1024*1024, backupCount=5)
formatter = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')
handler.setFormatter(formatter)
handler.setLevel(logging.INFO)
logger.setLevel(logging.INFO)
logger.addHandler(handler)
if os.environ.get('DEBUG', False):
    debug_handler = logging.StreamHandler(sys.stdout)
    debug_handler.setLevel(logging.DEBUG)
    logger.addHandler(debug_handler)
app.logger.addHandler(handler)

# Validate Slack request using signing secret
def is_slack_request_valid(request):
    try:
        slack_signing_secret = os.environ.get('SLACK_SIGNING_SECRET')
        if not slack_signing_secret:
            logger.error('SLACK_SIGNING_SECRET not configured')
            return False
        
        # Get timestamp and signature from request headers
        timestamp = request.headers.get('X-Slack-Request-Timestamp')
        signature = request.headers.get('X-Slack-Signature')
        
        if not timestamp or not signature:
            logger.info('Missing Slack timestamp or signature headers')
            return False
        
        # Verify timestamp is not too old (5 minutes)
        try:
            ts = int(timestamp)
            current_time = int(datetime.datetime.now().timestamp())
            if abs(current_time - ts) > 300:
                logger.info('Slack request timestamp too old: %d' % ts)
                return False
        except (ValueError, TypeError) as e:
            logger.info('Slack invalid timestamp: %s' % e)
            return False
        
        # Get raw request body
        request_body = request.get_data(as_text=True)
        
        # Construct base string
        base_string = f'v0:{timestamp}:{request_body}'
        
        # Compute HMAC-SHA256
        computed_signature = 'v0=' + hmac.new(
            slack_signing_secret.encode(),
            base_string.encode(),
            hashlib.sha256
        ).hexdigest()
        
        # Compare signatures securely
        if not hmac.compare_digest(signature, computed_signature):
            logger.info('Invalid Slack signature')
            return False
        
        return True
    except Exception as e:
        logger.info('Exception validating Slack request: %s' % e)
        return False    

def make_signed_payload(message):
    hasher = hashlib.sha256()
    hasher.update(MQTT_KEY)
    now = int(time.time())
    hasher.update(struct.pack('>Q', now))
    hasher.update(message.encode('utf-8'))
    data = {
        "text": message,
        "stamp": now,
        "hash": hasher.hexdigest(),
    }
    logger.info(f"Signed payload: {data}")
    return json.dumps(data)

def mqtt_publish(device, payload):
    topic = "hal9k/acs/action"
    if device is not None:
        topic += f"/{device}"
    publish.single(topic,
                   make_signed_payload(payload),
                   hostname="mqtt.hal9k.dk",
                   port=8883,
                   auth={'username': MQTT_USER, 'password': MQTT_PASSWORD},
                   tls={'tls_version': ssl.PROTOCOL_TLSv1_2, 'ca_certs': certifi.where()})

# Validate user in /acsaction
def is_acs_action_allowed(request):
    try:
        userid = request.form['user_id']
        logger.info('ACS action user ID: %s' % userid)
        return userid in os.environ['ACS_ACTION_USERS'].split(',')
    except Exception as e:
        logger.info('Exception: %s' % e)
        return False

# Validate user in /camaction
def is_cam_action_allowed(request):
    try:
        userid = request.form['user_id']
        logger.info('Camera action user ID: %s' % userid)
        return userid in os.environ['CAM_ACTION_USERS'].split(',')
    except Exception as e:
        logger.info('Exception: %s' % e)
        return False

# Validate token in /acsquery
def is_acs_request_valid(request):
    #logger.info('is_acs_request_valid')
    if not request.is_json:
        logger.info('is_acs_request_valid: No JSON')
        return False
    if not 'token' in request.json:
        logger.info('is_acs_request_valid: No token')
        return False
    try:
        token = request.json['token']
        if token == os.environ['ACS_VERIFICATION_TOKEN']:
            return True
        logger.info('is_acs_request_valid: Bad token %s' % token)
    except Exception as e:
        logger.info('Exception: %s' % e)
    logger.info('is_acs_request_valid: No?')
    return False

# Validate token in /camctl
def is_camctl_request_valid(request):
    try:
        auth = request.headers.get('Authentication')
        cam_auth = 'Bearer %s' % os.environ['CAMCTL_VERIFICATION_TOKEN']
        acs_auth = 'Bearer %s' % os.environ['ACS_VERIFICATION_TOKEN']
        is_token_valid = (auth == cam_auth) or (auth == acs_auth)
        if not is_token_valid:
            logger.info('Bad camctl token: %s' % str(auth))
    except Exception as e:
        logger.info('Exception: %s' % e)
        return False
    logger.info(f"is_camctl_request_valid: {is_token_valid}")
    return is_token_valid

# Return ACS status set via MQTT
def get_acs_status():
    status = ""
    for device in app.status:
        dev_status = app.status[device]
        if not "data" in dev_status:
            # Not ACS frontend
            continue
        status += f"*{device.capitalize()}*:\n"
        ts = dev_status["timestamp"]
        status += f"    Last update: _{ts}_\n"
        data = dev_status["data"]
        for key in data:
            pretty_key = key.replace('_', ' ').capitalize()
            pretty_data = str(data[key]).replace('_', ' ')
            if not pretty_data[0].isdigit():
                pretty_data = pretty_data.capitalize()
            status += f"    {pretty_key}: _{pretty_data}_\n"
    return { 'type': 'section', 'text': { 'text': status, 'type': 'mrkdwn' } }

def format_lines(device, lines):
    blocks = {
        'type': 'section',
        'text': {
            'text': f'*Log for {device}*\n' + ''.join(lines),
            'type': 'mrkdwn'
        }
    }
    json = jsonify(
        response_type='in_channel',
        blocks=[ blocks ],
    )
    logger.info(f'Slack logs: {json}')
    return json

# Return camera status set via MQTT
def get_camera_status_dict():
    cam_status = {}
    cutoff_time = datetime.datetime.now() - datetime.timedelta(days=3)
    for device in app.status:
        if not device.startswith("cam"):
            continue
        dev_status = app.status[device]
        ts = dev_status["timestamp"]
        # Parse ISO8601 timestamp and skip if older than 3 days
        try:
            ts_datetime = datetime.datetime.fromisoformat(ts)
            if ts_datetime < cutoff_time:
                continue
        except (ValueError, TypeError) as e:
            # Skip entries with invalid timestamps
            logger.warning(f"Invalid timestamp for {device}: {ts} - {e}")
            continue
        lp = dev_status["last_picture"]
        ver = dev_status["version"]
        status = { "V": ver, "H": ts, "LP": lp }
        cam_status[int(device[3:])] = status
    return cam_status

def get_camera_status():
    cam_status = get_camera_status_dict()
    if not cam_status:
        return 'No status'
    logger.info(f"cam_status {cam_status}")
    status = ''
    for key, value in sorted(cam_status.items()):
        if len(status) > 0:
            status = status + '\n'
        status = status + '*%02d:* ' % key
        substatus = ''
        istatus = value
        for subkey in istatus:
            if len(substatus) > 0:
                substatus = substatus + ', '
            subvalue = istatus[subkey]
            if subkey.lower() == 'active':
                substatus += 'Active' if subvalue == '1' else 'Inactive'
            else:
                substatus += '%s: %s' % (subkey, subvalue)
        status = status + substatus
    global global_camctl_status
    status += f"\n*Power*: {global_camctl_status}"
    return { 'type': 'section', 'text': { 'text': status, 'type': 'mrkdwn' } }

def handle_acsstatus():
    try:
        status = get_acs_status()
    except:
        status = "Internal error"
    json = jsonify(
        response_type='in_channel',
        blocks=[ status ],
    )
    logger.info(f'Slack ACS status: {json}')
    return json

def handle_camstatus():
    status = get_camera_status()
    return jsonify(
        response_type='in_channel',
        blocks=[ status ],
    )

def handle_acsaction(request):
    if not is_acs_action_allowed(request):
        return jsonify(
            response_type='in_channel',
            text='You are not allowed to perform ACS actions'
        )
    text = request.form['text']
    logger.info('ACS action: %s' % text)
    tokens = text.split(' ')
    if len(tokens) < 1:
        return jsonify(
            response_type='in_channel',
            text='Missing action')
    if len(tokens) < 2:
        action = tokens[0]
        if action == 'help':
            return jsonify(
                response_type='in_channel',
                text=('This command controls the ACS. See also /camctl. Available actions:\n' +
                      ', '.join(DEVICE_ACTIONS) + ' <device>\n' +
                      ', '.join(GLOBAL_ACTIONS)))
        if action in DEVICE_ACTIONS:
            return jsonify(
                response_type='in_channel',
                text='Missing device')
        elif action in GLOBAL_ACTIONS:
            global global_allow_open
            global_allow_open = action == 'open'
            mqtt_publish(None, action)
            return jsonify(
                response_type='in_channel',
                text=f'ACS open {"is" if global_allow_open else "not"} allowed')
        return jsonify(
            response_type='in_channel',
            text=f"ACS action '{action}' not supported")
    device = tokens[0]
    action = tokens[1]
    if action in DEVICE_ACTIONS:
        action_arg = None
        if len(tokens) > 2:
            action_arg = ' '.join(tokens[2:])
        # MQTT
        if action_arg is not None:
            payload += f" {action_arg}"
        mqtt_publish(device, action)
        return jsonify(
            response_type='in_channel',
            text=f"ACS action '{action}' queued for '{device}'")
    return jsonify(
        response_type='in_channel',
        text="ACS action '%s' not supported" % action
    )

def handle_camaction(request, command):
    logger.info('Camera action: %s' % command)
    if not is_cam_action_allowed(request):
        return jsonify(
            response_type='in_channel',
            text='You are not allowed to perform camera actions'
        )
    params = request.form['text'].split(' ')
    if len(params) != 2:
        return jsonify(
            response_type='in_channel',
            text='Invalid parameters for camera action'
        )
    instance = int(params[0])
    action = params[1]
    if action in ['on', 'off', 'continuous', 'motion']:
        global global_camera_action
        global_camera_action[instance] = action
        return jsonify(
            response_type='in_channel',
            text="Camera action '%s' queued for instance %d" % (action, instance))
    return jsonify(
        response_type='in_channel',
        text="Camera action '%s' not supported" % action
    )

def handle_camctl(request, command):
    logger.info('Camctl: %s' % command)
    if not is_cam_action_allowed(request):
        return jsonify(
            response_type='in_channel',
            text='You are not allowed to perform camera actions'
        )
    params = request.form['text'].split(' ')
    if len(params) != 1:
        return jsonify(
            response_type='in_channel',
            text='Invalid parameters for camctl'
        )
    action = params[0]
    if action == 'help':
        return jsonify(
            response_type='in_channel',
            text=('This command controls camera power. See also /acsaction. Available actions:\n' +
                  ', '.join(CAMCTL_ACTIONS)))
    if action in CAMCTL_ACTIONS:
        global global_camctl_action
        global_camctl_action = action
        return jsonify(
            response_type='in_channel',
            text="Camctl action '%s' queued" % action)
    return jsonify(
        response_type='in_channel',
        text="Camctl action '%s' not supported" % action)

def handle_lastlog(request):
    text = request.form['text']
    logger.info('lastlog: %s' % text)
    tokens = text.strip().split(' ')
    if len(tokens) < 1:
        return jsonify(
            response_type='in_channel',
            text='Missing device')
    device = tokens[0].strip()
    if len(device) < 1:
        return jsonify(
            response_type='in_channel',
            text='Missing device')
    lines = 5
    if len(tokens) > 1:
        try:
            lines = int(tokens[1])
        except ValueError:
            return jsonify(
                response_type='in_channel',
                text='Invalid number of lines')
    # Find the newest versioned file "acs.yyyy-mm-dd_HH"
    pattern = '%s/acs.*' % LOG_DIR
    files = list(filter(os.path.isfile, glob.glob(pattern)))
    if len(files) == 0:
        return jsonify(
            response_type='in_channel',
            text=f"No ACS logs!")
    files.sort(key=lambda x: os.path.getmtime(x))
    lastfile = files[-1]
    logger.info('lastfile: %s' % lastfile)
    file = open(lastfile, "r")
    all_lines = list(file.readlines())
    # Now add the current file "acs"
    file = open('%s/acs' % LOG_DIR, "r")
    all_lines += list(file.readlines())
    lst = []
    for line in all_lines:
        parts = line.split("|")
        if parts[1].lower() == device.lower():
            lst.append(f"{parts[0]} {parts[2]}")
    file.close()
    lastlines = lst[-lines:]
    return format_lines(device, lastlines)

# Handle Slack slash command.
# /acsaction will call /slash/action, etc.
@app.route('/slash/<command>', methods=['POST'])
def command(command):
    if not is_slack_request_valid(request):
        logger.info('Invalid Slack request. Aborting')
        return abort(403)
    logger.info('Slack command received: %s' % command)
    if command == 'status' or command == 'acsstatus':
        return handle_acsstatus()
    if command == 'camstatus':
        return handle_camstatus()
    if command == 'action' or command == 'acsaction':
        return handle_acsaction(request)
    if command == 'camaction':
        return handle_camaction(request, command)
    if command == 'camctl':
        return handle_camctl(request, command)
    if command == 'lastlog' or command == 'acslastlog':
        return handle_lastlog(request)
    return 'Unknown command', 200

# /acscamctl: Called by ACS to control camera power
@app.route('/acscamctl', methods=['POST'])
def acscamctl():
    if not is_acs_request_valid(request):
        logger.info('Invalid request. Aborting')
        return abort(403)
    global global_acs_camaction
    global_acs_camaction = request.json['action']
    logger.info('acscamctl: action %s' % global_acs_camaction)
    return '', 200

# /firmware: Called by ACS to fetch firmware image
@app.route('/firmware/<image>', methods=['GET'])
def firmware(image):
    return send_file(f'{FIRMWARE_DIR}/{image}.bin')

# Get camctl parameters, store status
@app.route('/camctl', methods=['GET'])
def get_camctl():
    if not is_camctl_request_valid(request):
        logger.info('Invalid camctl request. Aborting')
        return abort(403)
    #logger.info('Camctl args %s' % request.args)
    status = []
    cameras_on = False
    if request.args.get('cameras'):
        cameras_on = request.args.get('cameras')
        status.append(f"Cameras on: {cameras_on}")
    if request.args.get('estop'):
        estop_on = request.args.get('estop')
        status.append(f"E-stop on: {estop_on}")
    if request.args.get('version'):
        status.append(f"V: {request.args.get('version')}")
    # global global_last_cameras_on
    # if cameras_on != global_last_cameras_on:
    #     slack_write(':camera: Cameras are %s' % ('on' if cameras_on == '1' else 'off'))
    #     global_last_cameras_on = cameras_on
    global global_camctl_action
    action = global_camctl_action
    global_camctl_action = None
    global global_acs_camaction
    if not action:
        if global_acs_camaction:
            action = global_acs_camaction
            global_acs_camaction = None
    status.append(f" H: {datetime.datetime.now().replace(microsecond=0).strftime('%Y-%m-%d %H:%M:%S')}")
    global global_camctl_status
    global_camctl_status = ", ".join(status)
    return jsonify(action=action)

# /spaceapi: SpaceAPI
@app.route('/spaceapi', methods=['GET'])
@cross_origin()
def spaceapi():
    info = {
        "api_compatibility": ["14"],
        "space": "Hal9k",
        "logo": "https://hal9k.dk/wp-content/uploads/2012/10/hal9k_log-sky2.png",
        "url": "https://hal9k.dk",
        "location": {
            "address": "Sofiendalsvej 80, 9000 Aalborg, Denmark",
            "lon": 9.882,
            "lat": 57.0187,
            "timezone": "Europe/Copenhagen"
        },
        "contact": {
            "email": "bestyrelse@hal9k.dk"
        },
        "state": {
            "open": global_space_open,
            "lastchange": global_space_open_lastchange
        },
        "projects": [
            "https://wiki.hal9k.dk"
        ],
        "membership_plans": [
            {
                "name": "Normal membership",
                "value": 450,
                "currency": "DKK",
                "billing_interval": "other",
                "description": "Billing is once per quarter"
            },
            {
                "name": "Student membership",
                "value": 225,
                "currency": "DKK",
                "billing_interval": "other",
                "description": "Billing is once per quarter"
            }
        ]
    }
    return jsonify(info)


# Start the server on port 5000
if __name__ == '__main__':
    WSGIRequestHandler.protocol_version = "HTTP/1.1"
    # Create MQTT client
    mqtt_client = AcsMqtt(logger, userdata=app)
    ctx = ssl.create_default_context(cafile=certifi.where())
    mqtt_client.tls_set_context(ctx)
    mqtt_client.connect("mqtt.hal9k.dk", 8883)
    mqtt_client.loop_start()
    # Check ACS_SYNC_STATUS_FILE every 60 seconds
    watcher = SyncWatcher(ACS_SYNC_STATUS_FILE, MQTT_USER, MQTT_PASSWORD, 60, logger)
    watcher.start()
    # Start HTTP server
    app.run(host='0.0.0.0', port=5000)
