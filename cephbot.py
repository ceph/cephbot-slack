#!/usr/bin/python3

import json, os, rados, re, socket, subprocess
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
# Importing these for a k8s health check
from flask import Flask, make_response
import logging

# read config variables
SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN', '')
if SLACK_BOT_TOKEN == '':
  print("SLACK_BOT_TOKEN is not defined. This is needed to open a connection to the Slack API.")
  exit()

SLACK_APP_TOKEN = os.getenv('SLACK_APP_TOKEN', '')
if SLACK_APP_TOKEN == '':
  print("SLACK_APP_TOKEN is not defined. This is needed to open a connection to the Slack API.")
  exit()

SLACK_BOT_ID = os.getenv('SLACK_BOT_ID', '').strip().upper()
if SLACK_BOT_ID == '':
  print("SLACK_BOT_ID is not defined. This is needed to know when cephbot should listen.")
  exit()

EVENTS_SLACK_IDS_ = os.getenv('EVENTS_SLACK_IDS', None)
if EVENTS_SLACK_IDS_:
  EVENTS_SLACK_IDS = EVENTS_SLACK_IDS_.strip().upper().split()
EVENTS_SLACK_CHANNELS_ = os.getenv('EVENTS_SLACK_CHANNELS', None)
if EVENTS_SLACK_CHANNELS_:
  EVENTS_SLACK_CHANNELS = EVENTS_SLACK_CHANNELS_.strip().upper().split()
if EVENTS_SLACK_CHANNELS_ and EVENTS_SLACK_IDS_:
  EVENTS_ENABLED = True
else:
  EVENTS_ENABLED = False
  print("Either EVENTS_SLACK_CHANNELS or EVENTS_SLACK_IDS not set. No information will be added to alerting messages.")
EVENTS_DEBUG = os.getenv('EVENTS_DEBUG', False)

EVENTS_TRIGGER = os.getenv('EVENTS_TRIGGER', "FIRING").strip().lower()
EVENTS_COMMANDS = os.getenv('EVENTS_COMMANDS', "health, io, down osds, blocked requests").strip().lower().split(", ")

SLACK_USER_IDS = os.getenv('SLACK_USER_IDS', None)
if not SLACK_USER_IDS:
  print("Any user can talk to me. SLACK_USER_IDS is not defined or is empty.")

SLACK_CHANNEL_IDS = os.getenv('SLACK_CHANNEL_IDS', None)
if not SLACK_CHANNEL_IDS:
  print("I will respond in any channel. SLACK_CHANNEL_IDS is not defined or is empty.")

SLACK_USER_ACCESS_DENIED = os.getenv('SLACK_USER_ACCESS_DENIED', "You do not have permission to use me.")
SLACK_CHANNEL_ACCESS_DENIED = os.getenv('SLACK_CHANNEL_ACCESS_DENIED', "This channel does not have permission to use me.")

CEPH_CLUSTERS = {}
ceph_cluster_ids_regex = re.compile(r'^CEPH_CLUSTER_')
for key, value in os.environ.items():
  if ceph_cluster_ids_regex.search(key):
    CEPH_CLUSTERS[key.split("_")[2].strip().lower()] = value.strip().lower()

if not CEPH_CLUSTERS:
  CEPH_CLUSTERS['ceph'] = 'all prod'

ERRORS_ONLY_STRS = os.getenv('ERRORS_ONLY_STRS', "ERRORS ERROR ERR PROBLEMS PROBLEM PROBS PROB UNHEALTHY WARNINGS WARNING WARN").strip().lower()
GREP = os.getenv('GREP', "GREP").strip().lower()
GREPV = os.getenv('GREPV', "GREPV").strip().lower()

SCRIPTS_FOLDER = os.getenv('SCRIPTS_FOLDER', './scripts/')

CEPH_CONF = os.getenv('CEPH_CONF_FILE', "/etc/ceph/CLUSTER.conf")
CEPH_USER = os.getenv('CEPH_USER', "client.admin")
CEPH_KEYRING = os.getenv('CEPH_KEYRING_FILE', "/etc/ceph/CLUSTER.CEPH_USER.keyring")

FLASK_PORT = os.getenv('FLASK_PORT', "8080")

HELP_MSG = os.getenv('HELP_MSG', "health, health detail, status, osd stat, mon stat, pg stat, down osds, blocked requests, rgw stat").strip()
HELP_URL = os.getenv('HELP_URL', "https://github.com/ceph/cephbot-slack/")
TOO_LONG = os.getenv('TOO_LONG', 20)
TOO_LONG_MSG = os.getenv('TOO_LONG_MSG', "Long responses get threaded.")
ALWAYS_THREAD = os.getenv('ALWAYS_THREAD', 'false').lower() in ('true', '1', 't')
ALWAYS_SHOW_CLUSTER_ID = os.getenv('ALWAYS_SHOW_CLUSTER_ID', 'false').lower() in ('true', '1', 't')

HELP = "help"
AT_BOT = f"<@{SLACK_BOT_ID.lower()}>"
ERROR_PREFIX = "Something went wrong "

CONNECTED_NOTIFICATION_CHANNELS = os.getenv('CONNECTED_NOTIFICATION_CHANNELS', None)

slackApp = App(token=SLACK_BOT_TOKEN)
handler = SocketModeHandler(slackApp, SLACK_APP_TOKEN)
flaskApp = Flask(__name__)
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

@flaskApp.route("/health", methods=["GET"])
def cephbot_health():
  if handler.client is not None and handler.client.is_connected():
    return make_response("OK", 200)
  else:
    return make_response("Cephbot is inactive", 503)

def ceph_command(CLUSTER, command, thread, modifier):
  ceph_conf = CEPH_CONF.replace("CLUSTER", CLUSTER)
  ceph_keyring = CEPH_KEYRING.replace("CLUSTER", CLUSTER).replace("CEPH_USER", CEPH_USER)
  error_msg = f"{ERROR_PREFIX}while executing '{command}' on {CLUSTER}."
  return_error = None
  timeout = 5
  BASH_COMMAND = f"/bin/sh {SCRIPTS_FOLDER}/COMMAND --conf {ceph_conf} --user {CEPH_USER} --keyring {ceph_keyring}"
  bash_command = None
  cmd = {"prefix":command, "format":"plain"}
  healthy = "HEALTH_OK"

  cluster = rados.Rados(conffile=ceph_conf, conf=dict(keyring = ceph_keyring), name=CEPH_USER)
  mon_ips = cluster.conf_get('mon_host').split(',')
  mon_port = '3300'
  for mon_ip in mon_ips:
    try:
      sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
      sock.settimeout(timeout)
      result = sock.connect_ex((mon_ip, int(mon_port)))
    except:
      result = -1
    sock.close()
    if result == 0:
      break
    else:
      print(f"Unable to connect to the mon {mon_ip}:{mon_port}")
  if result != 0:
    return f"{ERROR_PREFIX}while connecting to all of the mons. Please check the firewall.", None

  try:
    # The timeout is currently ignored
    cluster.connect(timeout)
  except:
    print("Something prevented the connection to the Ceph cluster. Attempted using the following settings.")
    print(f"CEPH_CONF: {ceph_conf}")
    print(f"CEPH_USER: {CEPH_USER}")
    print(f"CEPH_KEYRING: {ceph_keyring}")
    return f"{ERROR_PREFIX}while connecting to {CLUSTER} using {ceph_conf}, {CEPH_USER}, {ceph_keyring}", None

  if command == "blocked requests":
    bash_command = BASH_COMMAND.replace("COMMAND","blocked_requests.sh")
    healthy = "No blocked requests"
  elif command == "io":
    bash_command = BASH_COMMAND.replace("COMMAND","io.sh")
  elif command.startswith("pool io"):
    bash_command = BASH_COMMAND.replace("COMMAND","pool_io.sh")
    opt_pool = command.split("pool io")[1].strip().lower()
    if opt_pool:
      bash_command += f" --pool {opt_pool}"
  elif command == "health detail":
    bash_command = BASH_COMMAND.replace("COMMAND","health_detail.sh")
    healthy = "HEALTH_OK"
  elif command == "rgw stat":
    bash_command = BASH_COMMAND.replace("COMMAND","rgw_stat.sh")
  elif command in "down osds,down osd".split(','):
    cmd = {"prefix":"osd tree", "format":"json"}

  if bash_command:
    try:
      output = subprocess.check_output(bash_command.split(), timeout=timeout)
    except:
      return_error = error_msg
  else:
    try:
      ret, output, errs = cluster.mon_command(json.dumps(cmd), b'', timeout=timeout)
    except:
      return_error = error_msg
  cluster.shutdown()

  if return_error:
    return return_error, None

  output = output.decode('utf-8').strip()

  if command in "down osds,down osd".split(','):
    output = json.loads(output)
    lastroot = None
    lasthost = None
    msg = ""
    for item in output['nodes']:
      if item['type'] == 'root':
        root = item['name']
      elif item['type'] == 'host':
        host = item['name']
      elif item['type'] == 'osd' and item['status'] == 'down':
        osd = item['name']
        if not root == lastroot:
          msg += f"\n{root}\n  {host}\n    {osd}"
        elif not host == lasthost:
          msg += f"\n  {host}\n    {osd}"
        else:
          msg += f", {osd}"
        lastroot = root
        lasthost = host
    output = msg.strip()
    if output == "":
      output = "All OSDs are up."
      healthy = output

  output = output.strip()
  if modifier:
    modifier_split = modifier.split(" ", 1)
  else:
    modifier_split = ["empty", "empty"]

  if not output:
    return error_msg, None
  elif modifier == "errors_only" and output == healthy:
    return None, None
  elif modifier_split[0] == GREP and modifier_split[1] not in output.lower() and not output.startswith(ERROR_PREFIX):
    return None, None
  elif modifier_split[0] == GREPV and modifier_split[1] in output.lower() and not output.startswith(ERROR_PREFIX):
    return None, None
  elif ( len(output.splitlines()) < int(TOO_LONG) or thread ):
    return output, None
  elif len(output.splitlines()) >= int(TOO_LONG):
    return TOO_LONG_MSG, output
  else:
    return error_msg, None

@slackApp.event("message")
def slack_parse(event: dict, say):
  for_cephbot = False
  events_run = False
  modifier = None
  find_id = False
  clusters_matched = []

  if 'thread_ts' in event:
    thread = event['thread_ts']
  elif ALWAYS_THREAD:
    thread = event['ts']
  else:
    thread = None
  show_cluster_id = ALWAYS_SHOW_CLUSTER_ID

  if 'subtype' in event and event['subtype'] == "bot_message":
    command = event['attachments'][0]['title'].strip().lower()
    user = event['bot_id'].strip().upper()
  else:
    command = event['text'].strip().lower()
    user = event['user'].strip().upper()
  channel = event['channel'].strip().upper()

  if user == SLACK_BOT_ID:
    return
  elif EVENTS_ENABLED and channel in EVENTS_SLACK_CHANNELS and EVENTS_TRIGGER in command and ( user in EVENTS_SLACK_IDS or EVENTS_DEBUG ):
    for CLUSTER in CEPH_CLUSTERS.keys():
      if CLUSTER in command:
        for_cephbot = True
        clusters_matched.append(CLUSTER)
        if thread == None:
          thread = event['ts']
        if user in EVENTS_SLACK_IDS:
          events_run = True
        elif EVENTS_DEBUG:
          find_id = True
  elif AT_BOT in command:
    for_cephbot = True
    command = command.split(AT_BOT, 1)[1].strip()
  elif event['channel_type'] == "im":
    for_cephbot = True

  if not for_cephbot:
    return

  if not events_run and not find_id:
    if not command or command == HELP:
      clusters_matched.append("self")
    else:
      cluster_match = False
      cluster = command.split()[0]
      for CLUSTER in CEPH_CLUSTERS.keys():
        if cluster == CLUSTER:
          cluster_match = True
          clusters_matched.append(CLUSTER)
        else:
          CLUSTER_ALIASES = CEPH_CLUSTERS[CLUSTER].split()
          cluster_split = cluster.split('-')
          if all(item in CLUSTER_ALIASES for item in cluster_split):
            cluster_match = True
            clusters_matched.append(CLUSTER)
            show_cluster_id = True
      if cluster_match:
        command = command.split(cluster, 1)[1].strip().lower()

  commands = []
  if events_run or command == EVENTS_TRIGGER:
    commands = EVENTS_COMMANDS
  else:
    if command.startswith((f"{GREP} ", f"{GREPV} ")):
      command_split = command.split(" ", 2)
      modifier = " ".join(command_split[0:2])
      command = command_split[2]
    elif f" {GREP} " in command:
      command_split = command.split(f" {GREP} ")
      modifier = f"{GREP} {command_split[1]}"
      command = command_split[0]
    elif f" {GREPV} " in command:
      command_split = command.split(f" {GREPV} ")
      modifier = f"{GREPV} {command_split[1]}"
      command = command_split[0]
    else:
      for errors_only_str in ERRORS_ONLY_STRS.split():
        if command.startswith(f"{errors_only_str} "):
          command = command.split(f"{errors_only_str} ")[1].strip().lower()
          modifier = "errors_only"
          break
        elif command.endswith(f" {errors_only_str}"):
          command = command.split(f" {errors_only_str}")[0].strip().lower()
          modifier = "errors_only"
          break
    commands.append(command)

  for CLUSTER in clusters_matched:
    # Only show one error message per cluster.
    error = False
    for command in commands:
      channel_response = None
      user_response = None
      if SLACK_USER_IDS and not user in SLACK_USER_IDS:
        channel_response = None
        user_response = SLACK_USER_ACCESS_DENIED
      elif SLACK_CHANNEL_IDS and not event['channel_type'] == "im" and not channel in SLACK_CHANNEL_IDS:
        channel_response = SLACK_CHANNEL_ACCESS_DENIED
        user_response = None
      elif command == HELP:
        user_response = None
        show_cluster_id = True
        if CLUSTER == "self":
          show_cluster_id = False
          channel_response = f"Clusters: {' '.join(CEPH_CLUSTERS.keys())}\n{HELP_URL}"
        elif "ALIASES" in HELP_MSG:
          channel_response = HELP_MSG.replace("ALIASES", CEPH_CLUSTERS[CLUSTER])
        else:
          channel_response = HELP_MSG
      elif find_id or command == "whoami":
        channel_response = f"You are {user}"
        user_response = None
      elif command in "alias aliases".split():
        user_response = None
        channel_response = CEPH_CLUSTERS[CLUSTER]
      elif command in "diag diagnostic diagnostics".split():
        user_response = None
        try:
          # Slack settings
          channel_response = "### Slack Settings ###"
          channel_response += f"\nSLACK_BOT_ID: {str(SLACK_BOT_ID)}"
          channel_response += f"\nSLACK_USER_IDS: {str(SLACK_USER_IDS)}"
          channel_response += f"\nSLACK_CHANNEL_IDS: {str(SLACK_CHANNEL_IDS)}"
          channel_response += f"\nALWAYS_THREAD: {str(ALWAYS_THREAD)}"
          channel_response += f"\nALWAYS_SHOW_CLUSTER_ID: {str(ALWAYS_SHOW_CLUSTER_ID)}"
          # Ceph settings
          channel_response += "\n\n### Ceph Settings ###"
          channel_response += f"\nCEPH_CONF: {str(CEPH_CONF).replace('CLUSTER', CLUSTER)}"
          channel_response += f"\nCEPH_USER: {str(CEPH_USER)}"
          channel_response += f"\nCEPH_KEYRING: {str(CEPH_KEYRING).replace('CLUSTER', CLUSTER).replace('CEPH_USER', CEPH_USER)}"
          channel_response += f"\nAliases: {str(CEPH_CLUSTERS[CLUSTER])}"
          channel_response += f"\nSCRIPTS_FOLDER: {str(SCRIPTS_FOLDER)}"
          # Events settings
          channel_response += "\n\n### Events Settings ###"
          channel_response += f"\nEVENTS_ENABLED: {str(EVENTS_ENABLED)}"
          if EVENTS_ENABLED:
            channel_response += f"\nEVENTS_SLACK_IDS: {str(EVENTS_SLACK_IDS)}"
            channel_response += f"\nEVENTS_SLACK_CHANNELS: {str(EVENTS_SLACK_CHANNELS)}"
            channel_response += f"\nEVENTS_DEBUG: {str(EVENTS_DEBUG)}"
            channel_response += f"\nEVENTS_TRIGGER: {str(EVENTS_TRIGGER)}"
            channel_response += f"\nEVENTS_COMMANDS: {str(EVENTS_COMMANDS)}"
        except:
          print(channel_response)
      else:
        channel_response, user_response = ceph_command(CLUSTER, command, thread, modifier)
      response = None

      if channel_response and channel_response.startswith(ERROR_PREFIX):
        if error:
          continue
        else:
          error = True

      if channel_response and not ( event['channel_type'] == "im" and channel_response == TOO_LONG_MSG ):
        channel_response = f"```{channel_response}```"
        if show_cluster_id:
          if not channel_response.startswith(f"```{CLUSTER}"):
            channel_response = f"{CLUSTER}: {command}\n{channel_response}"
        if thread:
          say(
            channel=channel,
            thread_ts=thread,
            text=channel_response,
            as_user=True
          )
        else:
          response = say(
            channel=channel,
            text=channel_response,
            as_user=True
          )

      if user_response:
        user_response = f"```{user_response}```"
        if channel_response and channel_response == TOO_LONG_MSG and response:
          thread = response['ts']
        else:
          thread = None
        if show_cluster_id:
          if not user_response.startswith(f"```{CLUSTER}"):
            user_response = f"{CLUSTER}: {command}\n{user_response}"
        if thread:
          say(
            channel=channel,
            thread_ts=thread,
            text=user_response,
            as_user=True
          )
        else:
          say(
            channel=user,
            text=user_response,
            as_user=True
          )

if __name__ == "__main__":
  try:
    handler.connect()
  except:
    print("Connection failed. Invalid Slack token or bot ID?")
    exit()

  if handler.client.is_connected():
    if CONNECTED_NOTIFICATION_CHANNELS is not None:
      for connected_notification_channel in CONNECTED_NOTIFICATION_CHANNELS.split():
        try:
          slackApp.client.chat_postMessage(
            channel=connected_notification_channel,
            text=f"Connected: {' '.join(CEPH_CLUSTERS.keys())}",
            as_user=True
          )
        except:
          print(f"Failed to send message to {connected_notification_channel}")
    flaskApp.run(port=FLASK_PORT)
  else:
    print("Connection failed or disconnected.")
    exit()

  print("Good-Bye")

