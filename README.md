# cephbot
Slack bot for Ceph. Slack Ops tool to help find Ceph cluster information from within Slack without needing to pull out a laptop and connect to a VPN. Common uses and commands are:
```
@cephbot ceph health
@cephbot ceph down osds
@cephbot ceph blocked requests
```

## Create the Slack Bot
To set up the bot in Slack, you can use this manifest
```
{
    "display_information": {
        "name": "cephbot",
        "description": "Slack bot for Ceph",
    },
    "features": {
        "bot_user": {
            "display_name": "cephbot",
            "always_online": false
        }
    },
    "oauth_config": {
        "scopes": {
            "bot": [
                "channels:history",
                "chat:write",
                "groups:history",
                "im:history",
                "im:write",
                "mpim:history"
            ]
        }
    },
    "settings": {
        "event_subscriptions": {
            "bot_events": [
                "message.channels",
                "message.groups",
                "message.im",
                "message.mpim"
            ]
        },
        "interactivity": {
            "is_enabled": true
        },
        "org_deploy_enabled": false,
        "socket_mode_enabled": true,
        "token_rotation_enabled": false
    }
}
```

### Page: Basic Information
On the Basic Information page you will need to generate an App-Level Token. Name the token whatever you'd like and give the token "connections:write". This will be used later as the SLACK_APP_TOKEN.
I also included a Ceph icon in this repo if you'd like to use it in the Display Information section on this page.

### Page: OAuth & Permissions
After you install the app, you can get the "Bot User OAuth Token" on the OAuth & Permissions page. This will be used as the SLACK_BOT_TOKEN.

### SLACK_BOT_ID
After installing the Slack Bot, you can retrieve the SLACK_BOT_ID from within Slack by looking at the details of the bot. Or a handful of other ways.

### Ceph auth
I generally create a client.cephbot user in Ceph for this `ceph auth get-or-create client.cephbot mon 'allow r' mgr 'allow r' > /etc/ceph/ceph.client.cephbot.keyring` which prevents people in Slack for figuring out ways of performing operations against the cluster. If you do enable cephbot to perform maintenance on the cluster, I highly recommend that you utilize the SLACK_USER_IDS and SLACK_CHANNEL_IDS config options to limit who can interact with cephbot.

## Additional considerations
Required non-standard Python libraries are slack_bolt and flask which can be installed via pip.

``` bash
pip install -r requirements.txt
```

The server this is running on needs to have ceph installed (for bash scripts in the scripts/ directory and the rados python library) and needs to be able to communicate with the mons, but otherwise does not require any other cluster integrations.

### Kubernetes
There are instructions for how to deploy this to kubernetes in the kubernetes folder of this repo.

### Multiple Clusters
Cephbot is now capable of communicating with multiple clusters in one instance. It will iterate through all environment variables that start with "CEPH_CLUSTER_". The value of the variables should be in the format "cluster: alias1 alias2 alias3". For CEPH_CONF and CEPH_KEYRING you can use CLUSTER as a literal string in the variable that will be replaced by the cluster name specified in CEPH_CLUSTER variables. If you are not running this in kubernetes, I would run it in a screen with the environment variables set up. Each instance can be configured for 1 or all of your clusters.

### RTM
If you are running multiple Ceph clusters in multiple datacenters and need to have cephbot running locally in each datacetner, then you need to use the RTM API. This code is archived in the RTM branch. Slack's API is deprecating the classic bot style in Mar 2026 and this will no longer work. The newer bot style and API will not allow multiple endpoints to retrieve all of the messages, only one endpoint will receive each message so all endpoints will need to be able to communciate with all Ceph clusters.

## Environment Variables
Including default values
### Required
You get the Tokens from the OAuth & Permissions page. The bot id can be found in many places, including from within Slack.
``` bash 
SLACK_BOT_TOKEN=
SLACK_APP_TOKEN=
SLACK_BOT_ID=
```

### Limit access to cephbot
This is only really useful if your keyring can perform administrative tasks.
``` bash
SLACK_USER_ACCESS_DENIED="You do not have permission to use me."
SLACK_USER_IDS=
SLACK_CHANNEL_ACCESS_DENIED="This channel does not have permission to use me."
SLACK_CHANNEL_IDS=
```

### Events
Using the EVENTS_* variables you can configure cephbot to monitor channels where zabbix, alertmanager, etc place notifications and it will parse messages in EVENTS_SLACK_CHANNELS from EVENTS_SLACK_IDS that include the EVENTS_TRIGGER for the cluster names and thread the EVENTS_COMMANDS in response to the event. This will give additional cluster information in slack in response to any events in the ceph cluster as the event triggers rather than later when engineers can sign in to investigate.
``` bash
EVENTS_SLACK_IDS=
EVENTS_SLACK_CHANNELS=
EVENTS_DEBUG=False
EVENTS_TRIGGER=FIRING
EVENTS_COMMANDS="health, io, down osds, blocked requests"
```

### Ceph configuration
For each ceph cluster you need to set up an environment variable with cluster name after "CEPH_CLUSTER_". The value of the variable are the aliases that the cluster will respond to. In this example the cluster's name is `ceph` and the alias are `all` and `prod`.
``` bash
CEPH_CLUSTER_ceph="all prod"
```
Can use CLUSTER as a variable that will be replaced with the responding clusters name
``` bash
CEPH_CONF_FILE="/etc/ceph/CLUSTER.conf"
CEPH_USER="client.admin"
```
Can use CLUSTER and CEPH_USER as variables that will be replaced with responding clusters name and the above specified CEPH_USER variable
``` bash
CEPH_KEYRING_FILE="/etc/ceph/CLUSTER.CEPH_USER.keyring"
```

### Flask port
This port is used for health checks
``` bash
FLASK_PORT=8080
```

### Misc
``` bash
SCRIPTS_FOLDER="./scripts"
HELP_MSG="health, health detail, status, osd stat, mon stat, pg stat, down osds, blocked requests, rgw stat"
HELP_URL="https://github.com/ceph/cephbot-slack/"
```
To keep down channel noise, messages longer than 20 lines will be threaded. To further keep down channel noise always thread responses regardless of length. Any time an alias is used, the responses will be threaded.
``` bash
TOO_LONG="20"
TOO_LONG_MSG = "Long responses get threaded."
ALWAYS_THREAD=False
```
When using aliases the reponding clusters will respond with their names. Sometimes its nice to just always have cephbot responses include the cluster name.
``` bash
ALWAYS_SHOW_CLUSTER_ID=False
```
Only report back if there is an unhealthy response from a cluster. Making it very easy to check overall health with large amounts of clusters.
``` bash
ERRORS_ONLY_STRS="ERRORS ERROR UNHEALTHY PROBLEMS PROBLEM"
```
Specifically grep for/or remove responses based on a provided string.
``` bash
GREP="GREP"
GREPV="GREPV"
```
If you have channels or person's DMs you would like to be notified that Cephbot has been started/restarted. Add the IDs as a space delimited list here.
``` bash
CONNECTED_NOTIFICATION_CHANNELS=
```

## Execution
If you want to run this locally without a container. Create an environment (ie .env) file with your values. Then execute the following. This works well in a screen.
``` bash
source .env
python cephbot.py
```
