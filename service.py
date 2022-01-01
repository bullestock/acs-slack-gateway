from flask import Flask, request, abort, jsonify
import datetime
import logging
import os
import requests
import uuid

global_acs_status = None
global_camera_status = {}
global_acs_action = None
global_camera_action = {}

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
        username = request.form['user_name']
        logger.info("ACS action user: %s" % username)
        return username in os.environ['ACS_ACTION_USERS'].split(',')
    except Exception as e:
        logger.info("Exception: %s" % e)
        return False

# Validate user in /camaction
def is_cam_action_allowed(request):
    try:
        username = request.form['user_name']
        logger.info("Camera action user: %s" % username)
        return username in os.environ['CAM_ACTION_USERS'].split(',')
    except Exception as e:
        logger.info("Exception: %s" % e)
        return False

# Validate token in /acsquery
def is_acs_request_valid(request):
    try:
        is_token_valid = request.json['token'] == os.environ['ACS_VERIFICATION_TOKEN']
    except Exception as e:
        logger.info("Exception: %s" % e)
        return False
    return is_token_valid

# Validate token in /camera
def is_camera_request_valid(request):
    try:
        is_token_valid = request.headers.get('Authentication') == ("Bearer %s" % os.environ['CAMERA_VERIFICATION_TOKEN'])
    except Exception as e:
        logger.info("Exception: %s" % e)
        return False
    return is_token_valid

# Return ACS status set by most recent call to /acsstatus
def get_acs_status():
    global global_acs_status
    logger.info("Stored status: %s" % global_acs_status)
    if not global_acs_status:
        return "No status"
    status = ''
    for key in global_acs_status:
        if len(status) > 0:
            status = status + "\n"
        status = status + "%s: %s" % (key.capitalize(), global_acs_status[key])
    return status

# Return camera status set by most recent call to /camstatus
def get_camera_status():
    global global_camera_status
    logger.info("Stored status: %s" % global_camera_status)
    if not global_camera_status:
        return "No status"
    status = ''
    for key in global_camera_status:
        if len(status) > 0:
            status = status + "\n"
        status = status + "%s: " % key
        substatus = ''
        istatus = global_camera_status[key]
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
    else:
        return "Unknown command", 200

# /acsquery: Called by ACS to see if an action is pending
@app.route("/acsquery", methods=["POST"])
def query():
    logger.info("acsquery: %s" % request.json)
    if not is_acs_request_valid(request):
        logger.info("Invalid request. Aborting")
        return abort(403)
    global global_acs_action
    action = global_acs_action
    global_acs_action = None
    return jsonify(action=action)

# /acsstatus: Called by ACS to set status
@app.route("/acsstatus", methods=["POST"])
def status():
    logger.info("acsstatus: %s" % request.json)
    if not is_acs_request_valid(request):
        logger.info("Invalid request. Aborting")
        return abort(403)
    global global_acs_status
    global_acs_status = request.json['status']
    logger.info("Storing status: %s" % global_acs_status)
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
    global global_camera_status
    if instance in global_camera_status:
        status = global_camera_status[instance]
    if request.args.get('active'):
        status['Active'] = request.args.get('active')
    if request.args.get('continuous'):
        status['Continuous mode'] = request.args.get('continuous')
    if request.args.get('version'):
        status['Version'] = request.args.get('version')
    action = None
    if instance in global_camera_action:
        action = global_camera_action[instance]
        global_camera_action[instance] = None
    status['Heartbeat'] = datetime.datetime.now().replace(microsecond=0)
    global_camera_status[instance] = status
    keepalive = int(os.environ['CAMERA_DEFAULT_KEEPALIVE'])
    pixel_threshold = int(os.environ['CAMERA_DEFAULT_PIXEL_THRESHOLD'])
    percent_threshold = int(os.environ['CAMERA_DEFAULT_PERCENT_THRESHOLD'])
    logger.info("Camera defaults: %d, %d, %d" % (keepalive, pixel_threshold, percent_threshold))
    return jsonify(keepalive=keepalive,
                   pixel_threshold=pixel_threshold,
                   percent_threshold=percent_threshold,
                   action=action)

# Start the server on port 5000
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
