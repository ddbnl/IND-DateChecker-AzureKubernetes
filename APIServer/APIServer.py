import azure.core.exceptions
from flask import Flask, request
from azure.storage.queue import QueueClient
from azure.data.tables import TableServiceClient
import datetime
import time
import json
import uuid
from Common import connect_str, queue_name, table_name

app = Flask(__name__)

run_once_queue_client = QueueClient.from_connection_string(connect_str, queue_name)
table_service = TableServiceClient.from_connection_string(conn_str=connect_str)
table_client = table_service.get_table_client(table_name=table_name)


def send_message_to_queue(message):

    run_once_queue_client.send_message(message)


@app.route("/run_once", methods=['POST'])
def run_once():

    parameters = json.loads(request.json)
    run_id = uuid.uuid4()
    message = u"{},{},{},{},{},0".format(run_id, parameters['start_date'], parameters['end_date'],
                                    '+'.join(parameters['desks']), parameters['email'] or 'none')
    send_message_to_queue(message=message)
    return str(run_id)


@app.route("/run_continuous", methods=['POST'])
def run_continuous():

    parameters = json.loads(request.json)
    run_id = str(uuid.uuid4())
    entity = {
        'PartitionKey': 'ContinuousRun',
        'RowKey': run_id,
        'StartDate': parameters['start_date'],
        'EndDate': parameters['end_date'],
        'Desks': '+'.join(parameters['desks']),
        'Email': parameters['email'] or 'none',
        'LastRun': '',
        'ErrorCount': 0}
    table_client.create_entity(entity)
    return run_id


@app.route("/desks", methods=['GET'])
def desks():

    try:
        entity = table_client.get_entity('Desks', '0')
        if not datetime.datetime.now() - datetime.datetime.strptime(entity['CheckedAt'], '%d/%m/%Y %H:%M:%S') < \
           datetime.timedelta(minutes=60):
            table_client.delete_entity(partition_key='Desks', row_key='0')
            raise ValueError("Desks checked too long ago")
        desks = entity['Desks']
    except (azure.core.exceptions.ResourceNotFoundError, ValueError):
        send_message_to_queue(message="check_desks, 0")
        desks = _wait_for_desk_result()
    return desks


def _wait_for_desk_result():

    while True:
        timer = 1
        try:
            if timer > 120:
                return
            return table_client.get_entity('Desks', '0')['Desks']
        except azure.core.exceptions.ResourceNotFoundError:
            timer += 1
            time.sleep(1)
            continue


@app.route('/get_result')
def get_result():

    run_id = request.args['run_id']
    try:
        entity = table_client.get_entity('Result', run_id)
        return entity['Result']
    except azure.core.exceptions.ResourceNotFoundError:
        return ''


if __name__ == '__main__':

    app.run(host='0.0.0.0', port=5001)
