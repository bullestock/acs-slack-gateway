from flask import Flask, request, abort, jsonify
import datetime
import json
import logging
import os
import requests
import uuid

SLAGIOS_ACS_HEARTBEAT_FILE='/opt/service/monitoring/acs-heartbeat'
SLAGIOS_BACS_HEARTBEAT_FILE='/opt/service/monitoring/bacs-heartbeat'
SLAGIOS_BARNDOOR_HEARTBEAT_FILE='/opt/service/monitoring/barndoor-heartbeat'
SLAGIOS_CAM_HEARTBEAT_FILE='/opt/service/monitoring/cam-heartbeat'
SLAGIOS_CAMCTL_HEARTBEAT_FILE='/opt/service/monitoring/camctl-heartbeat'
STATUS_DIR='/opt/service/persistent'
CAM_STATUS_DIR=STATUS_DIR + '/cams'
CAMCTL_STATUS_FILE=STATUS_DIR + '/camctl.json'
ACS_STATUS_FILE=STATUS_DIR + '/acs'
BACS_STATUS_FILE=STATUS_DIR + '/bacs'
BARNDOOR_STATUS_FILE=STATUS_DIR + '/barndoor'
LOG_DIR='/opt/service/persistent/logs'

if not os.path.isfile(SLAGIOS_ACS_HEARTBEAT_FILE):
    with open(SLAGIOS_ACS_HEARTBEAT_FILE, 'w', encoding = 'utf-8') as f:
        f.write("OK\nStarting|a=0")

if not os.path.isfile(SLAGIOS_BACS_HEARTBEAT_FILE):
    with open(SLAGIOS_BACS_HEARTBEAT_FILE, 'w', encoding = 'utf-8') as f:
        f.write("OK\nStarting|a=0")

if not os.path.isfile(SLAGIOS_BARNDOOR_HEARTBEAT_FILE):
    with open(SLAGIOS_BARNDOOR_HEARTBEAT_FILE, 'w', encoding = 'utf-8') as f:
        f.write("OK\nStarting|a=0")

if not os.path.isfile(SLAGIOS_CAM_HEARTBEAT_FILE):
    with open(SLAGIOS_CAM_HEARTBEAT_FILE, 'w', encoding = 'utf-8') as f:
        f.write("OK\nStarting|a=0")

if not os.path.isfile(SLAGIOS_CAMCTL_HEARTBEAT_FILE):
    with open(SLAGIOS_CAMCTL_HEARTBEAT_FILE, 'w', encoding = 'utf-8') as f:
        f.write("OK\nStarting|a=0")

if not os.path.isfile(ACS_STATUS_FILE):
    with open(ACS_STATUS_FILE, 'w', encoding = 'utf-8') as f:
        f.write("{}")

if not os.path.isfile(BACS_STATUS_FILE):
    with open(BACS_STATUS_FILE, 'w', encoding = 'utf-8') as f:
        f.write("{}")

if not os.path.isfile(BARNDOOR_STATUS_FILE):
    with open(BARNDOOR_STATUS_FILE, 'w', encoding = 'utf-8') as f:
        f.write("{}")

if not os.path.isdir(CAM_STATUS_DIR):
    os.mkdir(CAM_STATUS_DIR)
        
if not os.path.isdir(LOG_DIR):
    os.mkdir(LOG_DIR)

global_acs_action = None
global_camera_action = {}
global_camctl_action = {}
global_acs_camaction = None
global_last_camctl_on = None

def slack_write(msg):
    try:
        body = { 'channel': "monitoring", 'icon_emoji': ":panopticon:", 'parse': "full", "text": msg }
        headers = {
                'content_type': "application/json",
                "Authorization": "Bearer %s" % os.environ['SLACK_WRITE_TOKEN']
            }
        r = requests.post(url = "https://slack.com/api/chat.postMessage", data = body, headers = headers)
    except Exception as e:
        print("%s Slack exception: %s" % (datetime.now, e))


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
        logger.info("Exception: %s" % e)
        return False
    return is_token_valid and is_team_id_valid    

# Validate user in /acsaction
def is_acs_action_allowed(request):
    try:
        userid = request.form['user_id']
        logger.info("ACS action user ID: %s" % userid)
        return userid in os.environ['ACS_ACTION_USERS'].split(',')
    except Exception as e:
        logger.info("Exception: %s" % e)
        return False

# Validate user in /camaction
def is_cam_action_allowed(request):
    try:
        userid = request.form['user_id']
        logger.info("Camera action user ID: %s" % userid)
        return userid in os.environ['CAM_ACTION_USERS'].split(',')
    except Exception as e:
        logger.info("Exception: %s" % e)
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
        logger.info("Exception: %s" % e)
    return False

# Validate token in /camera
def is_camera_request_valid(request):
    try:
        is_token_valid = request.headers.get('Authentication') == ("Bearer %s" % os.environ['CAMERA_VERIFICATION_TOKEN'])
    except Exception as e:
        logger.info("Exception: %s" % e)
        return False
    return is_token_valid

# Validate token in /camctl
def is_camctl_request_valid(request):
    try:
        is_token_valid = request.headers.get('Authentication') == ("Bearer %s" % os.environ['CAMCTL_VERIFICATION_TOKEN'])
    except Exception as e:
        logger.info("Exception: %s" % e)
        return False
    return is_token_valid

# Return ACS status set by most recent call to /acsstatus
def get_acs_status():
    with open(ACS_STATUS_FILE, 'r', encoding = 'utf-8') as f:
        j = json.loads(f.read())
        logger.info("Stored status: %s" % j)
        if not 'door' in j:
            return "No status"
        status = ''
        for key in j:
            if len(status) > 0:
                status = status + "\n"
            status = status + "%s: %s" % (key.capitalize(), j[key])
        with open(BACS_STATUS_FILE, 'r', encoding = 'utf-8') as f:
            j = json.loads(f.read())
            for key in j:
                if len(status) > 0:
                    status = status + "\n"
                status = status + "%s: %s" % (key.capitalize(), j[key])
        with open(BARNDOOR_STATUS_FILE, 'r', encoding = 'utf-8') as f:
            j = json.loads(f.read())
            for key in j:
                if len(status) > 0:
                    status = status + "\n"
                status = status + "%s: %s" % (key.capitalize(), j[key])
        return status

# Return camera status set by most recent call to /camstatus
def get_camera_status_dict():
    cam_status = {}
    dir = os.fsencode(CAM_STATUS_DIR)
    for file in os.listdir(dir):
        filename = os.fsdecode(file)
        path = "%s/%s" % (CAM_STATUS_DIR, filename)
        if filename.isdigit():
            with open(path, 'r', encoding = 'utf-8') as f:
                try:
                    j = json.loads(f.read())
                    cam_status[filename] = j
                except Exception as e:
                    logger.info("Exception reading %s: %s" % (path, e))
    with open(CAMCTL_STATUS_FILE, 'r', encoding = 'utf-8') as f:
        try:
            j = json.loads(f.read())
            cam_status['Power'] = j
        except Exception as e:
            logger.info("Exception reading %s: %s" % (path, e))
    return cam_status

def get_camera_status():
    cam_status = get_camera_status_dict()
    if not cam_status:
        return "No status"
    status = ''
    for key in cam_status:
        if len(status) > 0:
            status = status + "\n"
        status = status + "%s: " % key
        substatus = ''
        istatus = cam_status[key]
        for subkey in istatus:
            if len(substatus) > 0:
                substatus = substatus + ", "
            substatus = substatus + "%s: %s" % (subkey, istatus[subkey])
        status = status + substatus
    return status

# Handle Slack slash command.
# /acsaction will call /slash/action, etc.
@app.route("/slash/<command>", methods=["POST"])
def command(command):
    if not is_slack_request_valid(request):
        logger.info("Invalid Slack request. Aborting")
        return abort(403)
    logger.info("Command received: %s" % command)
    if command == 'status' or command == 'acsstatus':
        return jsonify(
            response_type='in_channel',
            text=get_acs_status(),
        )
    elif command == 'camstatus':
        return jsonify(
            response_type='in_channel',
            text=get_camera_status(),
        )
    elif command == 'action' or command == 'acsaction':
        logger.info("ACS action: %s" % command)
        if not is_acs_action_allowed(request):
            return jsonify(
                response_type='in_channel',
                text='You are not allowed to perform ACS actions'
            )
        action = request.form['text']
        if action in ['calibrate', 'lock', 'unlock']:
            global global_acs_action
            global_acs_action = action
            return jsonify(
                response_type='in_channel',
                text="ACS action '%s' queued" % action)
        else:
            return jsonify(
                response_type='in_channel',
                text="ACS action '%s' not supported" % action
        )
    elif command == 'camaction':
        logger.info("Camera action: %s" % command)
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
        logger.info("Camctl: %s" % command)
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
        if action in ['on', 'off']:
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
        return "Unknown command", 200

# /acsquery: Called by ACS to see if an action is pending
@app.route("/acsquery", methods=["POST"])
def query():
    if not is_acs_request_valid(request):
        logger.info("Invalid request. Aborting")
        return abort(403)
    global global_acs_action
    action = global_acs_action
    logger.info("acsquery: action %s" % action)
    global_acs_action = None
    return jsonify(action=action)

# /acsstatus: Called by ACS to set status
@app.route("/acsstatus", methods=["POST"])
def status():
    logger.info("acsstatus: %s" % request.json)
    if not is_acs_request_valid(request):
        logger.info("Invalid request. Aborting")
        return abort(403)
    status = request.json['status']
    status['last update'] = datetime.datetime.now().replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
    logger.info("Storing status: %s" % status)
    with open(ACS_STATUS_FILE, 'w', encoding = 'utf-8') as f:
        f.write(json.dumps(status))
    with open(SLAGIOS_ACS_HEARTBEAT_FILE, 'w', encoding = 'utf-8') as f:
        f.write("OK\nUpdated|a=0")
    return "", 200

# /acslog: Called by ACS to store a log entry
@app.route("/acslog", methods=["POST"])
def acslog():
    logger.info("acslog")
    if not is_acs_request_valid(request):
        logger.info("Invalid request. Aborting")
        logger.info("acslog: request %s" % request.json)
        return abort(403)
    stamp = request.json['timestamp']
    text = request.json['text']
    day = datetime.datetime.now().strftime("%Y-%m-%d-%H")
    logfilename = '%s/acs-%s.log' % (LOG_DIR, day)
    with open(logfilename, 'a+', encoding = 'utf-8') as f:
        f.write("%s %s\n" % (stamp, text))
    return "", 200

# /bacslog: Called by BACS to store a log entry
@app.route("/bacslog", methods=["POST"])
def bacslog():
    logger.info("bacslog")
    if not is_acs_request_valid(request):
        logger.info("Invalid request. Aborting")
        logger.info("bacslog: request %s" % request.json)
        return abort(403)
    stamp = request.json['timestamp']
    text = request.json['text']
    day = datetime.datetime.now().strftime("%Y-%m-%d-%H")
    ident = request.json['ident']
    if ident == 'woodshop':
        logfilename = '%s/bacs-%s.log' % (LOG_DIR, day)
    else:
        logfilename = '%s/barndoor-%s.log' % (LOG_DIR, day)
    with open(logfilename, 'a+', encoding = 'utf-8') as f:
        f.write("%s %s\n" % (stamp, text))
    return "", 200

# /acsheartbeat: Called by BACS
@app.route("/acsheartbeat", methods=["POST"])
def acsheartbeat():
    logger.info("acsheartbeat")
    if not is_acs_request_valid(request):
        logger.info("Invalid request. Aborting")
        return abort(403)
    status = {}
    ident = request.json['ident']
    status['%s last update' % ident] = datetime.datetime.now().replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
    if ident == 'woodshop':
        with open(BACS_STATUS_FILE, 'w', encoding = 'utf-8') as f:
            f.write(json.dumps(status))
            with open(SLAGIOS_BACS_HEARTBEAT_FILE, 'w', encoding = 'utf-8') as f:
                f.write("OK\nUpdated|a=0")
    else:
        with open(BARNDOOR_STATUS_FILE, 'w', encoding = 'utf-8') as f:
            f.write(json.dumps(status))
            with open(SLAGIOS_BARNDOOR_HEARTBEAT_FILE, 'w', encoding = 'utf-8') as f:
                f.write("OK\nUpdated|a=0")
    return "", 200

# /acscamctl: Called by ACS to control camera power
@app.route("/acscamctl", methods=["POST"])
def acscamctl():
    if not is_acs_request_valid(request):
        logger.info("Invalid request. Aborting")
        return abort(403)
    global global_acs_camaction
    global_acs_camaction = request.json['action']
    logger.info("acscamctl: action %s" % global_acs_camaction)
    return "", 200

# Get camera parameters
@app.route("/camera/<instance>", methods=["GET"])
def get_camera(instance):
    if not is_camera_request_valid(request):
        logger.info("Invalid camera request. Aborting")
        return abort(403)
    if not instance.isdigit():
        logger.info("Invalid camera instance. Aborting")
        return abort(400)
    instance = int(instance)
    logger.info("Camera %d parameter query, args %s" % (instance, request.args))
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
    status['Heartbeat'] = datetime.datetime.now().replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
    with open('%s/%d' % (CAM_STATUS_DIR, instance), 'w', encoding = 'utf-8') as f:
        f.write(json.dumps(status))
    keepalive = int(os.environ['CAMERA_DEFAULT_KEEPALIVE'])
    pixel_threshold = int(os.environ['CAMERA_DEFAULT_PIXEL_THRESHOLD'])
    percent_threshold = int(os.environ['CAMERA_DEFAULT_PERCENT_THRESHOLD'])
    logger.info("Camera defaults: %d, %d, %d" % (keepalive, pixel_threshold, percent_threshold))
    with open(SLAGIOS_CAM_HEARTBEAT_FILE, 'w', encoding = 'utf-8') as f:
        f.write("OK\nUpdated|a=0")
    return jsonify(keepalive=keepalive,
                   pixel_threshold=pixel_threshold,
                   percent_threshold=percent_threshold,
                   action=action)

# Get camctl parameters, store status
@app.route("/camctl", methods=["GET"])
def get_camctl():
    if not is_camctl_request_valid(request):
        logger.info("Invalid camctl request. Aborting")
        return abort(403)
    logger.info("Camctl args %s" % request.args)
    status = {}
    if request.args.get('active'):
        camctl_on = request.args.get('active')
        status['Active'] = camctl_on
        global global_last_camctl_on
        if camctl_on != global_last_camctl_on:
            slack_write(':camera: Cameras are %s' % ('on' if camctl_on == '1' else 'off'))
            global_last_camctl_on = camctl_on
    global global_camctl_action
    action = global_camctl_action
    global_camctl_action = None
    global global_acs_camaction
    if not action:
        if global_acs_camaction:
            action = global_acs_camaction
            global_acs_camaction = None
    status['Heartbeat'] = datetime.datetime.now().replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
    with open(CAMCTL_STATUS_FILE, 'w', encoding = 'utf-8') as f:
        f.write(json.dumps(status))
    with open(SLAGIOS_CAMCTL_HEARTBEAT_FILE, 'w', encoding = 'utf-8') as f:
        f.write("OK\nUpdated|a=0")
    return jsonify(action=action)

# Start the server on port 5000
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
