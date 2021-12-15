from flask import Flask, request, abort, jsonify
import logging
import os
import requests
import uuid

app = Flask(__name__)

logger = logging.getLogger('werkzeug')
handler = logging.FileHandler('acsgw.log')
logger.addHandler(handler)
app.logger.addHandler(handler)

def is_request_valid(request):
    try:
        is_token_valid = request.form['token'] == os.environ['SLACK_VERIFICATION_TOKEN']
        is_team_id_valid = request.form['team_id'] == os.environ['SLACK_TEAM_ID']
    except Exception as e:
        logger.info("Exception: %s" % e)
        return False
    return is_token_valid and is_team_id_valid    

@app.route("/slash/<command>", methods=["POST"])
def command(command):
    logger.info("slash")
    if not is_request_valid(request):
        logger.info("Invalid request. Aborting")
        return abort(403)
    logger.info("Command received: %s" % command)
    # TODO
    return jsonify(
        response_type='in_channel',
        text='<https://youtu.be/frszEJb0aOo|General Kenobi!>',
    )    
    return "", 200


# Start the server on port 5000
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
