from flask import Flask, request, abort, jsonify
import logging
import os
import requests
import uuid

global_status = None
global_action = None

app = Flask(__name__)

logger = logging.getLogger('werkzeug')
handler = logging.FileHandler('acsgw.log')
logger.addHandler(handler)
app.logger.addHandler(handler)

def is_slack_request_valid(request):
    try:
        is_token_valid = request.form['token'] == os.environ['SLACK_VERIFICATION_TOKEN']
        is_team_id_valid = request.form['team_id'] == os.environ['SLACK_TEAM_ID']
    except Exception as e:
        logger.info("Exception: %s" % e)
        return False
    return is_token_valid and is_team_id_valid    

def is_action_allowed(request):
    try:
        username = request.form['user_name']
        return username in os.environ['ACTION_USERS'].split(',')
    except Exception as e:
        logger.info("Exception: %s" % e)
        return False

def is_acs_request_valid(request):
    try:
        is_token_valid = request.json['token'] == os.environ['ACS_VERIFICATION_TOKEN']
    except Exception as e:
        logger.info("Exception: %s" % e)
        return False
    return is_token_valid

def get_acs_status():
    global global_status
    logger.info("Stored status: %s" % global_status)
    if not global_status:
        return "No status"
    status = ''
    for key in global_status:
        if len(status) > 0:
            status = status + "\n"
        status = status + "%s: %s" % (key.capitalize(), global_status[key])
    return status

@app.route("/slash/<command>", methods=["POST"])
def command(command):
    logger.info("slash")
    if not is_slack_request_valid(request):
        logger.info("Invalid request. Aborting")
        return abort(403)
    logger.info("Command received: %s" % command)
    logger.info("Form: %s" % request.form)
    if command == 'status':
        return jsonify(
            response_type='in_channel',
            text=get_acs_status(),
        )
    if command == 'action':
        if not is_action_allowed(request):
            return jsonify(
                response_type='in_channel',
                text='You are not allowed to perform actions'
            )
        action = request.form['text']
        if action == 'calibrate':
            global global_action
            global_action = action
            return jsonify(
                response_type='in_channel',
                text="Action '%s' queued" % action)
        else:
            return jsonify(
                response_type='in_channel',
                text="Action '%s' not supported" % action
        )
    else:
        return "Unknown command", 200

@app.route("/acsquery", methods=["POST"])
def query():
    logger.info("acsquery: %s" % request.json)
    if not is_acs_request_valid(request):
        logger.info("Invalid request. Aborting")
        return abort(403)
    global global_action
    return jsonify(action=global_action)

@app.route("/acsstatus", methods=["POST"])
def status():
    logger.info("acsstatus: %s" % request.json)
    if not is_acs_request_valid(request):
        logger.info("Invalid request. Aborting")
        return abort(403)
    global global_status
    global_status = request.json['status']
    logger.info("Storing status: %s" % global_status)
    return "", 200

# Start the server on port 5000
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
