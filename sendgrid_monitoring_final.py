
import json
import logging
import datetime
from sendgrid import SendGridAPIClient

logging.basicConfig(
    format='%(asctime)s %(name)s %(levelname)s: %(message)s',
    filename='sendgrid_limits_exporter.log',
    level=logging.WARNING)
logger = logging.getLogger(__name__)

keys_file = '/root/bin/clients_sendgrid_keys.json'
write_metric_path = '/var/lib/prometheus/node-exporter/check_sendgrid_limits.prom'

def read_sendgrid_keys(keys_file):
    with open(keys_file, 'r') as file:
        keys = json.load(file)
        return keys

def get_info(api_key, wlc_name):
    resp = {'total': -1, 'used': -1}
    try:
        sg = SendGridAPIClient(api_key)
        response = sg.client.user.credits.get()
        response = json.loads(response.body)
        resp = {'total': response['total'], 'used': response['used']}
    except Exception as err:
        error_string = 'Something gone wrong with API key of project' + ' ' + wlc_name + ' ' + str(err)
        logging.warning(error_string)
    return resp

def get_subaccount_email_stats(api_key, subaccounts, start_date, end_date):
    email_stats = {}
    try:
        sg = SendGridAPIClient(api_key)
        for subaccount in subaccounts:
            params = {'subusers': subaccount['username'], 'start_date': start_date, 'end_date': end_date}
            response = sg.client.subusers.stats.get(query_params=params)
            subaccount_stats = json.loads(response.body)
            if subaccount_stats:
                total_requests = sum([day_stats['stats'][0]['metrics'].get('requests', 0) for day_stats in subaccount_stats])
                total_delivered = sum([day_stats['stats'][0]['metrics'].get('delivered', 0) for day_stats in subaccount_stats])
                reputation_percent = (total_delivered / total_requests) * 100 if total_requests > 0 else 0

                email_stats[subaccount['username']] = {
                    'delivered': subaccount_stats[0]['stats'][0]['metrics'].get('delivered', 0),
                    'requests': total_requests,
                    'used': subaccount_stats[0]['stats'][0]['metrics'].get('used', 0),
                    'delivered_last_month': total_delivered,
                    'reputation_percent': reputation_percent,
                }
            else:
                email_stats[subaccount['username']] = {'delivered': 0, 'requests': 0, 'used': 0, 'delivered_last_month': 0, 'reputation_percent': 0}
        return email_stats
    except Exception as err:
        error_string = 'Error getting subaccount email stats: ' + str(err)
        logging.warning(error_string)
        return {}

def write_metric_info(info, subaccount_statuses):
    metrics_str = '''#HELP sendgrid_limits_metric gauge metric
#TYPE sendgrid_limits_metric gauge'''
    for wlc_name, metrics_value in info.items():
        added_str = f'''sendgrid_limits_metric{{project_name="{wlc_name}", mails="Total"}} {metrics_value["total"]}\n'''
        added_str += f'''sendgrid_limits_metric{{project_name="{wlc_name}", mails="Used"}} {metrics_value["used"]}'''

        if "subaccounts" in metrics_value:
            for subaccount in metrics_value["subaccounts"]:
                status_value = subaccount_statuses.get(wlc_name, {}).get(subaccount['username'], {})

                added_str += f'''\nsendgrid_limits_metric{{project_name="{wlc_name}", subaccount="{subaccount['username']}", metric="Status"}} "{status_value}"'''

                added_str += f'''\nsendgrid_limits_metric{{project_name="{wlc_name}", subaccount="{subaccount['username']}", metric="Requests"}} {metrics_value["subaccount_email_stats"].get(subaccount['username'], {}).get('requests', -1)}'''
                added_str += f'''\nsendgrid_limits_metric{{project_name="{wlc_name}", subaccount="{subaccount['username']}", metric="DeliveredLastMonth"}} {metrics_value["subaccount_email_stats"].get(subaccount['username'], {}).get('delivered_last_month', -1)}'''

                reputation_percent = round(metrics_value["subaccount_email_stats"].get(subaccount['username'], {}).get('reputation_percent', -1), 2)
                added_str += f'''\nsendgrid_limits_metric{{project_name="{wlc_name}", subaccount="{subaccount['username']}", metric="ReputationPercent"}} {reputation_percent}'''

        metrics_str += '\n' + added_str

    with open(write_metric_path, 'w', encoding='UTF-8') as write_limits_file:
        write_limits_file.write(metrics_str + '\n')

def get_subaccounts(api_key):
    try:
        sg = SendGridAPIClient(api_key)
        response = sg.client.subusers.get()
        subaccounts = json.loads(response.body)
        return subaccounts
    except Exception as err:
        error_string = 'Error getting subaccounts: ' + str(err)
        logging.warning(error_string)
        return []

def get_subaccounts_statuses(api_key, subaccounts):
    statuses = {}
    try:
        sg = SendGridAPIClient(api_key)
        for subaccount in subaccounts:
            data = {
                "disabled": False
            }
            # Обновляем статус
            sg.client.subusers._(subaccount['username']).patch(request_body=data)

            # Получаем актуальный статус после обновления
            response = sg.client.subusers._(subaccount['username']).get()
            subaccount_status = json.loads(response.body)
            status_value = "Enabled" if subaccount_status.get("disabled", True) is False else "Disabled"
            statuses[subaccount['username']] = status_value
        return statuses
    except Exception as err:
        error_string = 'Error getting subaccount statuses: ' + str(err)
        logging.warning(error_string)
        return {}

def sendgrid_requests():
    keys = read_sendgrid_keys(keys_file)
    all_metrics_response = {}
    all_subaccount_statuses = {}
    for wlc_name in keys:
        api_key = keys.get(wlc_name)

        sendgrid_response = get_info(api_key, wlc_name)
        subaccounts = get_subaccounts(api_key)
        subaccount_statuses = get_subaccounts_statuses(api_key, subaccounts)
        print(f"Subaccounts statuses for {wlc_name}: {subaccount_statuses}")

        current_date = datetime.datetime.now()
        start_date = current_date.replace(day=1).strftime('%Y-%m-%d')
        end_date = current_date.strftime('%Y-%m-%d')
        subaccount_email_stats = get_subaccount_email_stats(api_key, subaccounts, start_date, end_date)

        for subaccount in subaccounts:
            subaccount['used'] = subaccount_email_stats.get(subaccount['username'], {}).get('used', -1)
            subaccount['total'] = subaccount_email_stats.get(subaccount['username'], {}).get('delivered', -1)
            subaccount['delivered_last_month'] = subaccount_email_stats.get(subaccount['username'], {}).get('delivered_last_month', -1)
            subaccount['requests_last_month'] = subaccount_email_stats.get(subaccount['username'], {}).get('requests', -1)

        all_metrics_response[wlc_name] = {
            'total': sendgrid_response['total'],
            'used': sendgrid_response['used'],
            'subaccounts': subaccounts,
            'subaccount_email_stats': subaccount_email_stats
        }
        all_subaccount_statuses[wlc_name] = subaccount_statuses
        print(f"\nSubaccounts data for {wlc_name}:")
        for subaccount in subaccounts:
            print(subaccount)

    write_metric_info(all_metrics_response, all_subaccount_statuses)

sendgrid_requests()
