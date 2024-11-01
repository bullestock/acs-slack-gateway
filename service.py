from flask import Flask, request, abort, jsonify, send_file
from werkzeug.serving import WSGIRequestHandler

import datetime
import json
import logging
import os
import requests
import uuid

SLAGIOS_CAM_HEARTBEAT_FILE='/opt/service/monitoring/cam-heartbeat'
SLAGIOS_CAMCTL_HEARTBEAT_FILE='/opt/service/monitoring/camctl-heartbeat'
STATUS_DIR='/opt/service/persistent'
ACS_STATUS_DIR=STATUS_DIR + '/acs'
CAM_STATUS_DIR=STATUS_DIR + '/cams'
CAMCTL_STATUS_FILE=STATUS_DIR + '/camctl.json'
ACS_STATUS_FILE_TEMPLATE=STATUS_DIR + '/acs-%s'
ACS_CRASH_DUMP_FILE='/opt/service/monitoring/acs-crashdump'
LOG_DIR='/opt/service/persistent/logs'
FIRMWARE_DIR='/opt/service/persistent/firmware'

DEVICE_ACTIONS = ['lock', 'unlock', 'reboot', 'setdesc']
GLOBAL_ACTIONS = ['open', 'close']

for dir in [ ACS_STATUS_DIR, CAM_STATUS_DIR, LOG_DIR ]:
    if not os.path.isdir(dir):
        os.mkdir(dir)

if not os.path.isfile(SLAGIOS_CAM_HEARTBEAT_FILE):
    with open(SLAGIOS_CAM_HEARTBEAT_FILE, 'w', encoding = 'utf-8') as f:
        f.write("OK\nStarting|a=0")

if not os.path.isfile(SLAGIOS_CAMCTL_HEARTBEAT_FILE):
    with open(SLAGIOS_CAMCTL_HEARTBEAT_FILE, 'w', encoding = 'utf-8') as f:
        f.write("OK\nStarting|a=0")

global_acs_device = None
global_acs_action = None
global_acs_action_arg = None
global_allow_open = None
global_camera_action = {}
global_camctl_action = {}
global_acs_camaction = None
global_last_cameras_on = None
global_space_open = "closed"
global_space_open_lastchange = 0 # UNIX timestamp

def slack_write(msg):
    try:
        body = { 'channel': 'private-monitoring', 'icon_emoji': ':panopticon:', 'parse': 'full', 'text': msg }
        headers = {
                'content_type': 'application/json',
                'Authorization': 'Bearer %s' % os.environ['SLACK_WRITE_TOKEN']
            }
        r = requests.post(url = 'https://slack.com/api/chat.postMessage', data = body, headers = headers)
    except Exception as e:
        print('%s Slack exception: %s' % (datetime.now, e))


app = Flask(__name__)

logger = logging.getLogger('werkzeug')
handler = logging.FileHandler('acsgw.log')
logger.addHandler(handler)
app.logger.addHandler(handler)

# Validate token/team from Slack slash command
def is_slack_request_valid(request):
    try:
        is_token_valid = request.form['token'] == os.environ['SLACK_VERIFICATION_TOKEN']
        is_team_id_valid = request.form['team_id'] == os.environ['SLACK_TEAM_ID']
    except Exception as e:
        logger.info('Exception: %s' % e)
        return False
    return is_token_valid and is_team_id_valid    

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
    logger.info('is_acs_request_valid')
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

# Validate token in /camera
def is_camera_request_valid(request):
    try:
        is_token_valid = request.headers.get('Authentication') == ('Bearer %s' % os.environ['CAMERA_VERIFICATION_TOKEN'])
    except Exception as e:
        logger.info('Exception: %s' % e)
        return False
    return is_token_valid

# Validate token in /camctl
def is_camctl_request_valid(request):
    try:
        is_token_valid = request.headers.get('Authentication') == ('Bearer %s' % os.environ['CAMCTL_VERIFICATION_TOKEN'])
    except Exception as e:
        logger.info('Exception: %s' % e)
        return False
    return is_token_valid

def get_immediate_subdirectories(a_dir):
    return [name for name in os.listdir(a_dir)
            if os.path.isdir(os.path.join(a_dir, name))]

# Return ACS status set by most recent call to /acsstatus
def get_acs_status():
    dirs = get_immediate_subdirectories(ACS_STATUS_DIR)
    status = ''
    for dir in dirs:
        dir_path = os.path.join(ACS_STATUS_DIR, dir)
        with open(os.path.join(dir_path, 'status'), 'r', encoding = 'utf-8') as f:
            j = json.loads(f.read())
            logger.info(f'Stored {dir} status: {j}')
            status += f'*{dir.capitalize()}*:\n'
            for key in j:
                status += '    %s: _%s_\n' % (key.replace('_', ' ').capitalize(),
                                              str(j[key]).replace('_', ' ').capitalize())
    return { 'type': 'section', 'text': { 'text': status, 'type': 'mrkdwn' } }

# Return ACS door status
def get_acs_door_status():
    dirs = get_immediate_subdirectories(ACS_STATUS_DIR)
    doors = {}
    for dir in dirs:
        dir_path = os.path.join(ACS_STATUS_DIR, dir)
        with open(os.path.join(dir_path, 'status'), 'r', encoding = 'utf-8') as f:
            j = json.loads(f.read())
            if 'door' in j:
                doors[dir] = j['door']
            if 'space' in j:
                space_status = j['space']
                if space_status == 'open':
                    doors[dir] = 'unlocked'
                if dir == 'main':
                    global global_space_open
                    global global_space_open_lastchange
                    old_open = global_space_open
                    global_space_open = space_status == 'open'
                    if global_space_open != old_open:
                        global_space_open_lastchange = (datetime.utcnow() -
                                                        datetime.datetime(1970, 1, 1)).total_seconds()
    return doors

# Return camera status set by most recent call to /camstatus
def get_camera_status_dict():
    cam_status = {}
    dir = os.fsencode(CAM_STATUS_DIR)
    for file in os.listdir(dir):
        filename = os.fsdecode(file)
        path = '%s/%s' % (CAM_STATUS_DIR, filename)
        if filename.isdigit():
            with open(path, 'r', encoding = 'utf-8') as f:
                try:
                    j = json.loads(f.read())
                    cam_status[filename] = j
                except Exception as e:
                    logger.info('Exception reading %s: %s' % (path, e))
    with open(CAMCTL_STATUS_FILE, 'r', encoding = 'utf-8') as f:
        try:
            j = json.loads(f.read())
            cam_status['Power'] = j
        except Exception as e:
            logger.info('Exception reading %s: %s' % (path, e))
    return cam_status

def get_camera_status():
    cam_status = get_camera_status_dict()
    if not cam_status:
        return 'No status'
    status = ''
    for key, value in sorted(cam_status.items()):
        if len(status) > 0:
            status = status + '\n'
        status = status + '*%s:* ' % key
        substatus = ''
        istatus = value
        for subkey in istatus:
            if len(substatus) > 0:
                substatus = substatus + ', '
            subvalue = istatus[subkey]
            if subkey.lower() == 'active':
                substatus += 'Active' if subvalue == '1' else 'Inactive'
            else:
                if subkey.lower() == 'continuous mode':
                    subkey = 'CM'
                elif subkey.lower() == 'last picture':
                    subkey = 'LP'
                elif subkey.lower() == 'version':
                    subkey = 'V'
                elif subkey.lower() == 'heartbeat':
                    subkey = 'H'
                substatus += '%s: %s' % (subkey, subvalue)
        status = status + substatus
    return { 'type': 'section', 'text': { 'text': status, 'type': 'mrkdwn' } }

# Handle Slack slash command.
# /acsaction will call /slash/action, etc.
@app.route('/slash/<command>', methods=['POST'])
def command(command):
    if not is_slack_request_valid(request):
        logger.info('Invalid Slack request. Aborting')
        return abort(403)
    logger.info('Slack command received: %s' % command)
    if command == 'status' or command == 'acsstatus':
        status = get_acs_status()
        json = jsonify(
            response_type='in_channel',
            blocks=[ status ],
        )
        logger.info(f'Slack ACS status: {json}')
        return json
    elif command == 'camstatus':
        status = get_camera_status()
        return jsonify(
            response_type='in_channel',
            blocks=[ status ],
        )
    elif command == 'action' or command == 'acsaction':
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
            if action in DEVICE_ACTIONS:
                return jsonify(
                    response_type='in_channel',
                    text='Missing device')
            elif action in GLOBAL_ACTIONS:
                global global_allow_open
                global_allow_open = action == 'open'
                return jsonify(
                    response_type='in_channel',
                    text=f'ACS open {"is" if global_allow_open else "not"} allowed')
            else:
                return jsonify(
                    response_type='in_channel',
                    text=f"ACS action '{action}' not supported")
        device = tokens[0]
        action = tokens[1]
        if action in DEVICE_ACTIONS:
            global global_acs_device
            global_acs_device = device
            global global_acs_action
            global_acs_action = action
            global global_acs_action_arg
            global_acs_action_arg = None
            if len(tokens) > 2:
                global_acs_action_arg = ' '.join(tokens[2:])
            return jsonify(
                response_type='in_channel',
                text=f"ACS action '{action}' queued for '{device}'")
        else:
            return jsonify(
                response_type='in_channel',
                text="ACS action '%s' not supported" % action
        )
    elif command == 'camaction':
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
        else:
            return jsonify(
                response_type='in_channel',
                text="Camera action '%s' not supported" % action
        )
    elif command == 'camctl':
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
        if action in ['on', 'off', 'reboot']:
            global global_camctl_action
            global_camctl_action = action
            return jsonify(
                response_type='in_channel',
                text="Camctl action '%s' queued" % action)
        else:
            return jsonify(
                response_type='in_channel',
                text="Camctl action '%s' not supported" % action
        )
    else:
        return 'Unknown command', 200

# /acsquery: Called by ACS to see if an action is pending
@app.route('/acsquery', methods=['POST'])
def query():
    if not is_acs_request_valid(request):
        logger.info('Invalid request. Aborting')
        return abort(403)
    if not 'device' in request.json:
        logger.info('Ignoring /acsquery with no device')
        return abort(403)
    global global_acs_action
    global global_acs_action_arg
    global global_acs_device
    global global_allow_open
    if request.json['device'] == global_acs_device:
        device = global_acs_device
        action = global_acs_action
        arg = global_acs_action_arg
        logger.info(f'acsquery: device {device} action {action}')
        global_acs_action = None
        global_acs_device = None
        global_acs_action_arg = None
        return jsonify(action=action, arg=arg)
    logger.info('Ignoring /acsquery from other device')
    return jsonify(action=None, allow_open=global_allow_open)

# /acsstatus: Called by ACS to set status
@app.route('/acsstatus', methods=['POST'])
def status():
    #logger.info('acsstatus: %s' % request.json)
    if not is_acs_request_valid(request):
        logger.info('Invalid request. Aborting')
        return abort(403)
    status = request.json['status']
    status['last update'] = datetime.datetime.now().replace(microsecond=0).strftime('%Y-%m-%d %H:%M:%S')
    logger.info('Storing status: %s' % status)
    device = None
    if not 'device' in request.json:
        logger.info('Missing device in /acsstatus')
        return '', 403
    device = request.json['device']
    device_dir = os.path.join(ACS_STATUS_DIR, device)
    if not os.path.isdir(device_dir):
        os.mkdir(device_dir)
    statusfilename = os.path.join(device_dir, 'status')
    heartbeatfilename = os.path.join(device_dir, 'heartbeat')
    with open(statusfilename, 'w', encoding = 'utf-8') as f:
        f.write(json.dumps(status))
    with open(heartbeatfilename, 'w', encoding = 'utf-8') as f:
        f.write('OK\nUpdated|a=0')
    return '', 200

# /acslog: Called by ACS to store a log entry
@app.route('/acslog', methods=['POST'])
def acslog():
    #logger.info('acslog')
    logger.info('acslog: %s' % request.json)
    if not is_acs_request_valid(request):
        logger.info('Invalid request. Aborting')
        logger.info('acslog: request %s' % request.json)
        return abort(403)
    logger.info('acslog: request %s' % request.json)
    stamp = request.json['timestamp']
    text = request.json['text']
    day = datetime.datetime.now().strftime('%Y-%m-%d-%H')
    if 'device' in request.json:
        # Device-specific logging
        logfilename = '%s/acs-%s-%s.log' % (LOG_DIR, request.json['device'].lower(), day)
    else:
        # Legacy
        logfilename = '%s/acs-%s.log' % (LOG_DIR, day)
    with open(logfilename, 'a+', encoding = 'utf-8') as f:
        f.write('%s %s\n' % (stamp, text))
    # Check for crash dump
    if 'CORE DUMP START' in text:
        with open(ACS_CRASH_DUMP_FILE, 'a+', encoding = 'utf-8') as f:
            f.write('%s\n' % stamp)
    return '', 200

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

# Get camera parameters
@app.route('/camera/<instance>', methods=['GET'])
def get_camera(instance):
    if not is_camera_request_valid(request):
        logger.info('Invalid camera request. Aborting')
        return abort(403)
    if not instance.isdigit():
        logger.info('Invalid camera instance. Aborting')
        return abort(400)
    instance = int(instance)
    logger.info('Camera %d parameter query, args %s' % (instance, request.args))
    status = {}
    cam_status = get_camera_status_dict()
    if instance in cam_status:
        status = cam_status[instance]
    if request.args.get('active'):
        status['Active'] = request.args.get('active')
    if request.args.get('continuous'):
        status['Continuous mode'] = request.args.get('continuous')
    if request.args.get('last_pic'):
        status['Last picture'] = request.args.get('last_pic')
    if request.args.get('version'):
        status['Version'] = request.args.get('version')
    action = None
    if instance in global_camera_action:
        action = global_camera_action[instance]
        global_camera_action[instance] = None
    status['Heartbeat'] = datetime.datetime.now().replace(microsecond=0).strftime('%Y-%m-%d %H:%M:%S')
    with open('%s/%d' % (CAM_STATUS_DIR, instance), 'w', encoding = 'utf-8') as f:
        f.write(json.dumps(status))
    keepalive = int(os.environ['CAMERA_DEFAULT_KEEPALIVE'])
    pixel_threshold = int(os.environ['CAMERA_DEFAULT_PIXEL_THRESHOLD'])
    percent_threshold = int(os.environ['CAMERA_DEFAULT_PERCENT_THRESHOLD'])
    logger.info('Camera defaults: %d, %d, %d' % (keepalive, pixel_threshold, percent_threshold))
    with open(SLAGIOS_CAM_HEARTBEAT_FILE, 'w', encoding = 'utf-8') as f:
        f.write('OK\nUpdated|a=0')
    return jsonify(keepalive=keepalive,
                   pixel_threshold=pixel_threshold,
                   percent_threshold=percent_threshold,
                   action=action)

# Get camctl parameters, store status
@app.route('/camctl', methods=['GET'])
def get_camctl():
    if not is_camctl_request_valid(request):
        logger.info('Invalid camctl request. Aborting')
        return abort(403)
    logger.info('Camctl args %s' % request.args)
    status = {}
    cameras_on = False
    if request.args.get('cameras'):
        cameras_on = request.args.get('cameras')
        status['Cameras on'] = cameras_on
    if request.args.get('estop'):
        estop_on = request.args.get('estop')
        status['E-stop on'] = estop_on
    if request.args.get('version'):
        status['Version'] = request.args.get('version')
    global global_last_cameras_on
    if cameras_on != global_last_cameras_on:
        slack_write(':camera: Cameras are %s' % ('on' if cameras_on == '1' else 'off'))
        global_last_cameras_on = cameras_on
    global global_camctl_action
    action = global_camctl_action
    global_camctl_action = None
    global global_acs_camaction
    if not action:
        if global_acs_camaction:
            action = global_acs_camaction
            global_acs_camaction = None
    status['Heartbeat'] = datetime.datetime.now().replace(microsecond=0).strftime('%Y-%m-%d %H:%M:%S')
    with open(CAMCTL_STATUS_FILE, 'w', encoding = 'utf-8') as f:
        f.write(json.dumps(status))
    with open(SLAGIOS_CAMCTL_HEARTBEAT_FILE, 'w', encoding = 'utf-8') as f:
        f.write('OK\nUpdated|a=0')
    return jsonify(action=action)

# /doorstatus: Get door status
@app.route('/doorstatus', methods=['POST'])
def doorstatus():
    if not is_acs_request_valid(request):
        logger.info('Invalid doorstatus request. Aborting')
        return abort(403)
    return jsonify(get_acs_door_status())

# /spaceapi: SpaceAPI
@app.route('/spaceapi', methods=['GET'])
def spaceapi():
    info = {
        "api": "0.13",
        "api_compatibility": ["14"],
        "space": "Halk",
        "logo": "http://hal9k.dk/wp-content/uploads/2012/10/hal9k_log-sky2.png",
        "url": "http://hal9k.dk",
        "location": {
            "address": "Sofiendalsvej 80, 9000 Aalborg, Denmark",
            "lon": 9.8819234,
            "lat": 57.0187811,
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
                "currency": DKK,
                "billing_interval": "quarterly"
            },
            {
                "name": "Student membership",
                "value": 225,
                "currency": DKK,
                "billing_interval": "quarterly"
            }
        ]
    }
    return jsonify(info)

# Start the server on port 5000
if __name__ == '__main__':
    WSGIRequestHandler.protocol_version = "HTTP/1.1"
    app.run(host='0.0.0.0', port=5000)
